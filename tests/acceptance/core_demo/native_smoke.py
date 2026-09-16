"""Explicit opt-in native Core Demo harness."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from tests.acceptance.slice3.native_smoke import (
    _SHA,
    CONFIG_ENV,
    TESTED_SHA_ENV,
    NativeOptInRequired,
    NativeSlice3Smoke,
    NativeSmokeFailure,
    _preflight_config,
    _running_gate_timeout_seconds,
    _verify_host,
    load_config,
    run_command,
    run_preflight_request,
)

CORE_DEMO_OPT_IN_ENV = "LABPILOT_RUN_CORE_DEMO_NATIVE_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class CoreDemoNativeResult:
    agent_run_id: str
    produced_proposal_revision_id: str
    experiment_version_id: str
    normal_result: dict[str, object]

    def value(self) -> dict[str, object]:
        return asdict(self)


def run_from_environment(environ: Mapping[str, str] = os.environ) -> CoreDemoNativeResult:
    if environ.get(CORE_DEMO_OPT_IN_ENV) != "1":
        raise NativeOptInRequired(f"explicit opt-in requires {CORE_DEMO_OPT_IN_ENV}=1")
    config_path = environ.get(CONFIG_ENV)
    tested_sha = environ.get(TESTED_SHA_ENV, "")
    if not config_path or _SHA.fullmatch(tested_sha) is None:
        raise NativeOptInRequired("native config or exact tested commit is missing")
    config = load_config(Path(config_path))
    _verify_host(config)
    if not environ.get(config.database_url_env):
        raise NativeOptInRequired("native test-admin database authority is missing")
    return NativeCoreDemoSmoke(
        config, tested_sha, running_gate_timeout_seconds=_running_gate_timeout_seconds(environ)
    ).run()


class NativeCoreDemoSmoke(NativeSlice3Smoke):
    def run(self) -> CoreDemoNativeResult:
        preflight = run_preflight_request(
            {
                "config": _preflight_config(self.config),
                "tested_sha": self.tested_sha,
                "database_url": os.environ[self.config.database_url_env],
            }
        )
        try:
            agent_run_id, proposal_revision_id, version_id = self._agent_proposal_version(preflight)
            normal = self._run_normal_result_loop(preflight, version_id=version_id)
            result = CoreDemoNativeResult(
                agent_run_id=agent_run_id,
                produced_proposal_revision_id=proposal_revision_id,
                experiment_version_id=str(version_id),
                normal_result=normal,
            )
        except BaseException as error:
            try:
                self._cleanup()
            except BaseException as cleanup_error:
                error.add_note(f"cleanup also failed: {cleanup_error!r}")
            raise
        self._cleanup()
        self._write_core_demo_result(result)
        return result

    def _agent_proposal_version(
        self, preflight: Mapping[str, object]
    ) -> tuple[str, str, UUID]:
        payload = self._payload(("0.82", "0.61"), "result.json")
        project_id = self._project_id(preflight)
        created = self.api.post_json(
            f"/projects/{project_id}/agent-runs",
            {
                "repository_snapshot_id": str(self.config.repository_snapshot_id),
                "goal": (
                    "First return a repository.list_tree tool_call with empty arguments. "
                    "After its observation, return a propose decision using exactly this proposal: "
                    + json.dumps(payload, separators=(",", ":"), sort_keys=True)
                ),
                "budget": {
                    "max_steps": 4,
                    "max_model_calls": 4,
                    "max_wall_clock_seconds": 180,
                    "max_tokens": 20_000,
                },
            },
        )
        agent_run_id = created.get("agent_run_id")
        row_version = created.get("row_version")
        if type(agent_run_id) is not str or type(row_version) is not int:
            raise NativeSmokeFailure("agent run creation response is invalid")
        advanced = self.api.post_json(
            f"/agent-runs/{agent_run_id}/advance", {"expected_row_version": row_version}
        )
        proposal_revision_id = advanced.get("produced_proposal_revision_id")
        if advanced.get("status") != "completed" or type(proposal_revision_id) is not str:
            raise NativeSmokeFailure("agent run did not produce a proposal")
        verification = self.api.post_json(f"/proposal-revisions/{proposal_revision_id}/verify", {})
        verification_result = verification.get("result")
        verification_id = verification.get("proposal_verification_id")
        if (
            not isinstance(verification_result, Mapping)
            or verification_result.get("passed") is not True
            or type(verification_id) is not str
        ):
            raise NativeSmokeFailure("agent proposal verification failed")
        approved = self.api.post_json(
            f"/proposal-revisions/{proposal_revision_id}/approve",
            {"proposal_verification_id": verification_id},
        )
        version_id = approved.get("experiment_version_id")
        if type(version_id) is not str:
            raise NativeSmokeFailure("agent proposal approval response is invalid")
        try:
            return agent_run_id, proposal_revision_id, UUID(version_id)
        except ValueError as error:
            raise NativeSmokeFailure(
                "agent proposal approval returned an invalid version"
            ) from error

    def _project_id(self, preflight: Mapping[str, object]) -> UUID:
        containers = cast(Mapping[str, object], preflight["containers"])
        postgres = cast(str, containers["postgres"])
        statement = (
            "SELECT registrations.project_id FROM repository_snapshots AS snapshots "
            "JOIN repository_registrations AS registrations "
            "ON registrations.repository_registration_id = snapshots.repository_registration_id "
            f"WHERE snapshots.repository_snapshot_id = '{self.config.repository_snapshot_id}'"
        )
        output = run_command(
            (
                "docker",
                "exec",
                postgres,
                "psql",
                "-U",
                "labpilot",
                "-d",
                "labpilot",
                "-At",
                "-c",
                statement,
            ),
            timeout_seconds=20,
        ).stdout.decode("ascii", "strict").splitlines()
        if len(output) != 1:
            raise NativeSmokeFailure("snapshot does not resolve to exactly one project")
        try:
            return UUID(output[0])
        except ValueError as error:
            raise NativeSmokeFailure("snapshot project identity is invalid") from error

    def _write_core_demo_result(self, result: CoreDemoNativeResult) -> None:
        root = self.config.evidence_root / "core-demo-native" / self.tested_sha / uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        (root / "result.json").write_text(
            json.dumps(result.value(), separators=(",", ":"), sort_keys=True), encoding="utf-8"
        )
