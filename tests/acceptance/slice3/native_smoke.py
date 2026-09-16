"""Explicit-opt-in native acceptance harness for the Slice3 Result Plane.

This module is deliberately inert when imported.  A native operator supplies a
strict config file and the explicit opt-in environment flag; pytest then only
checks the structured result returned by :func:`run_from_environment`.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import stat
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _slice2_preflight() -> object:
    path = _REPOSITORY_ROOT / "tests" / "acceptance" / "slice2" / "native_preflight.py"
    spec = importlib.util.spec_from_file_location("task12_slice2_preflight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Slice2 native preflight helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_PREFLIGHT = _slice2_preflight()
compose_admin_command = _PREFLIGHT.compose_admin_command
exact_systemd_unit = _PREFLIGHT.exact_systemd_unit
run_command = _PREFLIGHT.run_command
run_preflight_request = _PREFLIGHT.run_preflight_request

CONFIG_ENV = "LABPILOT_SLICE3_ACCEPTANCE_CONFIG"
OPT_IN_ENV = "LABPILOT_RUN_SLICE3_NATIVE_ACCEPTANCE"
TESTED_SHA_ENV = "TESTED_SHA"
RUNNING_GATE_TIMEOUT_ENV = "LABPILOT_TASK12_RUNNING_GATE_TIMEOUT_SECONDS"
DEFAULT_RUNNING_GATE_TIMEOUT_SECONDS = 300.0
SECRET_SENTINEL = "TASK12_NATIVE_SENTINEL_NOT_A_SECRET"

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_CONFIG_KEYS = frozenset(
    {
        "version",
        "dedicated_candidate_b_host",
        "expected_hostname",
        "repository_root",
        "workspace_root",
        "runner_policy_source",
        "evidence_root",
        "dataset_root",
        "database_url_env",
        "api_base_url",
        "compose_project",
        "python_profile",
        "experiment_id",
        "repository_snapshot_id",
        "repository_snapshot_identity",
        "train_entrypoint",
        "workdir",
        "dataset_uri",
        "workload_duration_seconds",
    }
)


class NativeOptInRequired(RuntimeError):
    """Raised before any host action when native authority is absent."""


class NativeSmokeFailure(RuntimeError):
    """A diagnostic native-harness assertion failure."""


@dataclass(frozen=True, slots=True)
class NativeConfig:
    expected_hostname: str
    repository_root: Path
    workspace_root: Path
    runner_policy_source: Path
    evidence_root: Path
    dataset_root: Path
    database_url_env: str
    api_base_url: str
    compose_project: str
    python_profile: str
    experiment_id: UUID
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    workdir: str
    dataset_uri: str
    workload_duration_seconds: int


@dataclass(frozen=True, slots=True)
class Cardinalities:
    artifacts: int
    metrics: int
    reports: int
    selections: int
    summaries: int


@dataclass(frozen=True, slots=True)
class _TrackedAttempt:
    attempt_id: UUID
    supervisor_execution_identity: str
    workspace: Path


@dataclass(frozen=True, slots=True)
class SseCapture:
    event_types: tuple[str, ...]
    artifact_names: tuple[str, ...]
    raw: bytes


@dataclass(frozen=True, slots=True)
class NativeSmokeResult:
    candidate_commit: str
    batch_id: str
    trial_ids: tuple[str, str]
    attempt_ids: tuple[str, str]
    attempt_outcomes: dict[str, dict[str, object]]
    validation: dict[str, dict[str, object]]
    selections: dict[str, str | None]
    summary: dict[str, object]
    artifacts: dict[str, dict[str, object]]
    metrics: dict[str, dict[str, object]]
    recovery: dict[str, object]
    cardinalities_before_restart: Cardinalities
    cardinalities_after_restart: Cardinalities
    security: dict[str, object]

    def value(self) -> dict[str, object]:
        return asdict(self)


def load_config(path: Path) -> NativeConfig:
    if not path.is_absolute():
        raise NativeOptInRequired("native config path must be absolute")
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise NativeOptInRequired("native config must be a regular non-symlink file")
        encoded = path.read_bytes()
        if len(encoded) > 64 * 1024:
            raise NativeOptInRequired("native config is too large")
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise NativeOptInRequired("native config is unreadable") from error
    if type(value) is not dict or set(value) != _CONFIG_KEYS:
        raise NativeOptInRequired("native config schema has missing or unknown fields")
    if value.get("version") != 1 or value.get("dedicated_candidate_b_host") is not True:
        raise NativeOptInRequired("native config authority is invalid")
    strings = {
        key: _config_text(value, key)
        for key in _CONFIG_KEYS
        - {"version", "dedicated_candidate_b_host", "workload_duration_seconds"}
    }
    for key in (
        "repository_root",
        "workspace_root",
        "runner_policy_source",
        "evidence_root",
        "dataset_root",
    ):
        if not Path(strings[key]).is_absolute():
            raise NativeOptInRequired(f"{key} must be an absolute native path")
    if _ENVIRONMENT_NAME.fullmatch(strings["database_url_env"]) is None:
        raise NativeOptInRequired("database_url_env must name an environment variable")
    parsed = urlsplit(strings["api_base_url"])
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise NativeOptInRequired("api_base_url is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", strings["compose_project"]):
        raise NativeOptInRequired("compose_project is invalid")
    try:
        duration = value["workload_duration_seconds"]
        if type(duration) is not int or not 2 <= duration <= 15:
            raise ValueError
        config = NativeConfig(
            expected_hostname=strings["expected_hostname"],
            repository_root=Path(strings["repository_root"]),
            workspace_root=Path(strings["workspace_root"]),
            runner_policy_source=Path(strings["runner_policy_source"]),
            evidence_root=Path(strings["evidence_root"]),
            dataset_root=Path(strings["dataset_root"]),
            database_url_env=strings["database_url_env"],
            api_base_url=strings["api_base_url"].rstrip("/"),
            compose_project=strings["compose_project"],
            python_profile=strings["python_profile"],
            experiment_id=UUID(strings["experiment_id"]),
            repository_snapshot_id=UUID(strings["repository_snapshot_id"]),
            repository_snapshot_identity=strings["repository_snapshot_identity"],
            train_entrypoint=strings["train_entrypoint"],
            workdir=strings["workdir"],
            dataset_uri=strings["dataset_uri"],
            workload_duration_seconds=duration,
        )
    except (KeyError, ValueError) as error:
        raise NativeOptInRequired("native config identifiers are invalid") from error
    if any(
        Path(item).is_absolute() or ".." in Path(item).parts
        for item in (config.train_entrypoint, config.workdir)
    ):
        raise NativeOptInRequired("source paths must be relative and contained")
    return config


def run_from_environment(environ: Mapping[str, str] = os.environ) -> NativeSmokeResult:
    if environ.get(OPT_IN_ENV) != "1":
        raise NativeOptInRequired(f"explicit opt-in requires {OPT_IN_ENV}=1")
    config_path = environ.get(CONFIG_ENV)
    tested_sha = environ.get(TESTED_SHA_ENV, "")
    if not config_path or _SHA.fullmatch(tested_sha) is None:
        raise NativeOptInRequired("native config or exact tested commit is missing")
    config = load_config(Path(config_path))
    _verify_host(config)
    if not environ.get(config.database_url_env):
        raise NativeOptInRequired("native test-admin database authority is missing")
    return NativeSlice3Smoke(
        config, tested_sha, running_gate_timeout_seconds=_running_gate_timeout_seconds(environ)
    ).run()


class NativeSlice3Smoke:
    def __init__(
        self,
        config: NativeConfig,
        tested_sha: str,
        *,
        running_gate_timeout_seconds: float = DEFAULT_RUNNING_GATE_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.tested_sha = tested_sha
        self.api = _Api(config.api_base_url)
        self.running_gate_timeout_seconds = running_gate_timeout_seconds
        self._stopped_coordinator = False
        self._batches: list[UUID] = []
        self._attempts: dict[UUID, _TrackedAttempt] = {}

    def run(self) -> NativeSmokeResult:
        preflight = run_preflight_request(
            {
                "config": _preflight_config(self.config),
                "tested_sha": self.tested_sha,
                "database_url": os.environ[self.config.database_url_env],
            }
        )
        try:
            normal = self._run_normal_result_loop(preflight)
            security = self._run_security_cases(preflight, normal)
            result = NativeSmokeResult(security=security, **normal)
        except BaseException as error:
            try:
                self._cleanup()
            except BaseException as cleanup_error:
                error.add_note(f"cleanup also failed: {cleanup_error!r}")
            raise
        self._cleanup()
        self._write_result(result)
        return result

    def _run_normal_result_loop(
        self, preflight: Mapping[str, object], *, version_id: UUID | None = None
    ) -> dict[str, object]:
        if version_id is None:
            version_id = self._approve_version(("0.82", "0.61"), artifact_path="result.json")
        batch = self._create_and_start_batch(version_id)
        trial_ids = self._wait_for("two trials", lambda: self._trial_ids(batch), 30)
        attempts = self._wait_for(
            "running attempts",
            lambda: self._running_attempts(trial_ids),
            self.running_gate_timeout_seconds,
            diagnostic=lambda: self._running_gate_diagnostic(batch, trial_ids),
        )
        active_oracles = cast(
            dict[str, dict[str, object]],
            self._wait_for(
                "active physical workloads",
                lambda: self._active_workload_oracles(attempts),
                30,
            ),
        )
        first_restart = self._restart_coordinator(preflight)
        terminal = self._wait_for(
            "terminal attempts", lambda: self._terminal_attempts(attempts), 60
        )
        settled_oracles = cast(
            dict[str, dict[str, object]],
            self._wait_for(
                "settled workload oracles",
                lambda: self._finished_workload_oracles(attempts),
                30,
            ),
        )
        reports = self._wait_for("result reports", lambda: self._attempt_reports(attempts), 45)
        selections = {
            trial_id: self.api.get_json(f"/trials/{trial_id}/result")["selection"]
            for trial_id in trial_ids
        }
        summary = self._wait_for("batch summary", lambda: self._summary(batch), 45)
        self._assert_normal_contract(trial_ids, attempts, terminal, reports, selections, summary)
        before = self._cardinalities(preflight, attempts, batch)
        expected = Cardinalities(artifacts=4, metrics=2, reports=2, selections=1, summaries=1)
        if before != expected:
            raise NativeSmokeFailure(f"normal result cardinalities are not exact: {before!r}")
        second_restart = self._restart_coordinator(preflight)
        after = self._cardinalities(preflight, attempts, batch)
        if after != before:
            raise NativeSmokeFailure("coordinator restart duplicated persisted result facts")
        post_restart_oracles = cast(
            dict[str, dict[str, object]],
            self._wait_for(
                "settled workload oracles after coordinator restart",
                lambda: self._finished_workload_oracles(attempts),
                15,
            ),
        )
        if any(oracle["start_count"] != 1 for oracle in post_restart_oracles.values()):
            raise NativeSmokeFailure("coordinator restart duplicated a physical workload")
        artifacts, metrics = self._artifact_and_metric_facts(reports)
        return {
            "candidate_commit": self.tested_sha,
            "batch_id": str(batch),
            "trial_ids": cast(tuple[str, str], tuple(str(item) for item in trial_ids)),
            "attempt_ids": cast(tuple[str, str], tuple(str(item) for item in attempts)),
            "attempt_outcomes": terminal,
            "validation": reports,
            "selections": {str(key): _selection_id(value) for key, value in selections.items()},
            "summary": summary,
            "artifacts": artifacts,
            "metrics": metrics,
            "recovery": {
                "active_oracles_before_restart": active_oracles,
                "settled_oracles_after_recovery": settled_oracles,
                "settled_oracles_after_second_restart": post_restart_oracles,
                "first_restart_ready_generation": first_restart,
                "second_restart_ready_generation": second_restart,
            },
            "cardinalities_before_restart": before,
            "cardinalities_after_restart": after,
        }

    def _run_security_cases(
        self, preflight: Mapping[str, object], normal: Mapping[str, object]
    ) -> dict[str, object]:
        traversal = self._verify_traversal_rejected()
        malformed = self._run_pre_processing_case(preflight, mutation="malformed")
        symlink = self._run_pre_processing_case(preflight, mutation="symlink")
        post_validation = self._post_validation_mutation(normal)
        sse = self._assert_sse_redaction(UUID(cast(str, normal["batch_id"])))
        return {
            "traversal_rejected": traversal,
            "pre_processing_malformed": malformed,
            "pre_processing_symlink": symlink,
            "post_validation_mutation": post_validation,
            "sse_redaction": sse,
        }

    def _run_pre_processing_case(
        self, preflight: Mapping[str, object], *, mutation: str
    ) -> dict[str, object]:
        version = self._approve_version(("0.82",), artifact_path="result.json")
        batch = self._create_and_start_batch(version)
        trial = self._wait_for(
            "corrupt-case trial", lambda: self._trial_ids(batch, expected=1), 30
        )[0]
        attempt = self._wait_for(
            "corrupt-case active attempt", lambda: self._running_attempts((trial,), expected=1), 45
        )[0]
        self._stop_coordinator()
        oracle = cast(
            dict[str, object],
            self._wait_for(
                "finished workload while coordinator is stopped",
                lambda: self._finished_workload_oracle(attempt),
                60,
            ),
        )
        output = self._wait_for(
            "workload output while coordinator stopped", lambda: self._result_path(attempt), 60
        )
        if not self._result_processing_not_started(attempt):
            raise NativeSmokeFailure("result processing began before pre-processing corruption")
        if mutation == "malformed":
            output.write_bytes(b'{"schema_version":"v1","metrics":')
        elif mutation == "symlink":
            target = output.with_name("task12-untrusted-result.json")
            target.write_bytes(b'{"schema_version":"v1","metrics":{"score":0.82}}\n')
            output.unlink()
            output.symlink_to(target.name)
        else:
            raise ValueError("unsupported content mutation")
        self._start_coordinator()
        terminal = self._wait_for(
            "corrupt-case terminal attempt", lambda: self._terminal_attempts((attempt,)), 60
        )
        report = self._wait_for(
            "corrupt-case report", lambda: self._attempt_reports((attempt,)), 45
        )[str(attempt)]
        selection = self.api.get_json(f"/trials/{trial}/result")["selection"]
        reasons = _reason_codes(report)
        if (
            report["verdict"] != "error"
            or "VALIDATION_INPUT_INVALID" not in reasons
            or selection is not None
        ):
            raise NativeSmokeFailure("corrupt content was accepted as a scientific result")
        return {
            "mutation": mutation,
            "attempt": terminal[str(attempt)],
            "oracle": oracle,
            "verdict": report["verdict"],
            "reason_codes": reasons,
            "selection": selection,
            "result_processing_not_started": True,
        }

    def _post_validation_mutation(self, normal: Mapping[str, object]) -> dict[str, object]:
        trial_id = UUID(cast(tuple[str, str], normal["trial_ids"])[0])
        attempt_id = UUID(cast(tuple[str, str], normal["attempt_ids"])[0])
        before_trial = self.api.get_json(f"/trials/{trial_id}/result")
        before_summary = self.api.get_json(f"/batches/{normal['batch_id']}/summary")
        before_results = self.api.get_json(f"/attempts/{attempt_id}/results")
        path = self._result_path(attempt_id)
        path.write_bytes(b'{"schema_version":"v1","metrics":{"score":0.01}}\n')
        after_trial = self.api.get_json(f"/trials/{trial_id}/result")
        after_summary = self.api.get_json(f"/batches/{normal['batch_id']}/summary")
        after_results = self.api.get_json(f"/attempts/{attempt_id}/results")
        if (
            before_trial != after_trial
            or before_summary != after_summary
            or before_results["validation_reports"] != after_results["validation_reports"]
        ):
            raise NativeSmokeFailure(
                "post-validation mutation changed immutable scientific history"
            )
        result_artifact = next(
            (
                artifact
                for artifact in cast(list[dict[str, object]], after_results["artifacts"])
                if artifact["artifact_name"] == "result"
            ),
            None,
        )
        if result_artifact is None or result_artifact["availability"] != "corrupted":
            raise NativeSmokeFailure("post-validation mutation was not reported as corrupted")
        return {"availability": "corrupted", "historical_facts_unchanged": True}

    def _verify_traversal_rejected(self) -> dict[str, object]:
        revision = self.api.post_json(
            f"/experiments/{self.config.experiment_id}/proposal-revisions",
            {"payload": self._payload(("0.82",), "../result.json")},
        )
        verification = self.api.post_json(
            f"/proposal-revisions/{revision['proposal_revision_id']}/verify", {}
        )
        result = cast(Mapping[str, object], verification["result"])
        findings = cast(list[Mapping[str, object]], result["findings"])
        codes = sorted(cast(str, finding["code"]) for finding in findings)
        if result["passed"] or codes != ["artifact_path_invalid"]:
            raise NativeSmokeFailure(f"traversal findings are not exact: {codes!r}")
        return {"finding_codes": codes, "unrelated_blockers": []}

    def _approve_version(self, scores: tuple[str, ...], *, artifact_path: str) -> UUID:
        revision = self.api.post_json(
            f"/experiments/{self.config.experiment_id}/proposal-revisions",
            {"payload": self._payload(scores, artifact_path)},
        )
        verification = self.api.post_json(
            f"/proposal-revisions/{revision['proposal_revision_id']}/verify", {}
        )
        if not verification["result"]["passed"]:
            raise NativeSmokeFailure(
                f"proposal verification failed: {verification['result']['findings']}"
            )
        approved = self.api.post_json(
            f"/proposal-revisions/{revision['proposal_revision_id']}/approve",
            {"proposal_verification_id": verification["proposal_verification_id"]},
        )
        return UUID(cast(str, approved["experiment_version_id"]))

    def _payload(self, scores: tuple[str, ...], artifact_path: str) -> dict[str, object]:
        return {
            "goal": "Task12 native result loop",
            "experiment_type": "parameter_sweep" if len(scores) > 1 else "reproduction",
            "repository_snapshot_id": str(self.config.repository_snapshot_id),
            "repository_snapshot_identity": self.config.repository_snapshot_identity,
            "train_entrypoint": self.config.train_entrypoint,
            "eval_entrypoint": None,
            "workdir": self.config.workdir,
            "parameter_matrix": {"score": list(scores)},
            "seeds": [7],
            "dataset_reference": {
                "reference_type": "uri",
                "path_or_uri": self.config.dataset_uri,
                "stable_identity": "task12-dataset-v1",
                "reproducibility_level": "strict",
            },
            "environment_expectation": {
                "python_profile": self.config.python_profile,
                "framework_requirements": [],
                "device_requirement": None,
                "cuda_requirement": None,
            },
            "metrics": [{"name": "score", "source_artifact": "result", "split": "test"}],
            "artifacts": [
                {"name": "result", "relative_path": artifact_path, "required": True},
                {
                    "name": "workload-oracle",
                    "relative_path": "workload-test-oracle.json",
                    "required": True,
                },
            ],
            "validation_rules": [
                {"kind": "artifact_exists", "subject": "result"},
                {"kind": "artifact_exists", "subject": "workload-oracle"},
                {"kind": "metric_finite", "subject": "score"},
                {"kind": "metric_gte", "subject": "score", "threshold": "0.80"},
            ],
            "timeout_seconds": 60,
            "max_retries": 0,
            "train_argv_template": [
                "--seed",
                "{seed}",
                "--value",
                SECRET_SENTINEL,
                "--dataset",
                "{dataset}",
                "--output-dir",
                "{output_dir}",
                "--duration",
                str(self.config.workload_duration_seconds),
                "--exit-code",
                "0",
                "--score",
                "{parameter:score}",
            ],
        }

    def _create_and_start_batch(self, version_id: UUID) -> UUID:
        created = self.api.post_json(
            f"/experiment-versions/{version_id}/batches", {}, idempotency=True
        )
        batch = UUID(cast(str, created["batch_id"]))
        self._batches.append(batch)
        state = self.api.get_json(f"/batches/{batch}")
        self.api.post_json(
            f"/batches/{batch}/start",
            {"expected_row_version": state["row_version"]},
            idempotency=True,
        )
        return batch

    def _trial_ids(self, batch: UUID, *, expected: int = 2) -> tuple[UUID, ...] | None:
        value = self.api.get_json(f"/batches/{batch}")
        identifiers = tuple(UUID(item) for item in cast(list[str], value["trial_ids"]))
        return identifiers if len(identifiers) == expected else None

    def _running_attempts(
        self, trials: tuple[UUID, ...], *, expected: int = 2
    ) -> tuple[UUID, ...] | None:
        values = [self.api.get_json(f"/trials/{trial}") for trial in trials]
        attempt_ids = tuple(
            UUID(cast(list[str], item["attempt_ids"])[0]) for item in values if item["attempt_ids"]
        )
        if len(attempt_ids) != expected:
            return None
        attempts = [self.api.get_json(f"/attempts/{attempt}") for attempt in attempt_ids]
        if not all(
            self._track_attempt(attempt_id, attempt)
            for attempt_id, attempt in zip(attempt_ids, attempts, strict=True)
        ):
            return None
        return (
            attempt_ids
            if all(item["physical_started_at"] and item["terminal_at"] is None for item in attempts)
            else None
        )

    def _running_gate_diagnostic(
        self, batch: UUID, trials: tuple[UUID, ...]
    ) -> dict[str, object]:
        batch_state = self.api.get_json(f"/batches/{batch}")
        trial_states: list[dict[str, object]] = []
        for trial in trials:
            trial_state = self.api.get_json(f"/trials/{trial}")
            attempt_states: list[dict[str, object]] = []
            for attempt_id in cast(list[str], trial_state.get("attempt_ids", [])):
                attempt = self.api.get_json(f"/attempts/{attempt_id}")
                attempt_states.append(
                    {
                        "attempt_id": attempt_id,
                        "status": attempt.get("presentation_status", attempt.get("status")),
                        "prepared_at": attempt.get("prepared_at"),
                        "physical_started_at": attempt.get("physical_started_at"),
                        "terminal_at": attempt.get("terminal_at"),
                        "terminal_kind": attempt.get("terminal_kind"),
                        "exit_code": attempt.get("exit_code"),
                    }
                )
            trial_states.append(
                {
                    "trial_id": str(trial),
                    "status": trial_state.get("presentation_status", trial_state.get("status")),
                    "attempts": attempt_states,
                }
            )
        return {
            "batch_id": str(batch),
            "batch_status": batch_state.get("presentation_status", batch_state.get("status")),
            "trials": trial_states,
        }

    def _terminal_attempts(self, attempts: tuple[UUID, ...]) -> dict[str, dict[str, object]] | None:
        values = {
            str(identifier): self.api.get_json(f"/attempts/{identifier}") for identifier in attempts
        }
        return values if all(item["terminal_at"] is not None for item in values.values()) else None

    def _attempt_reports(self, attempts: tuple[UUID, ...]) -> dict[str, dict[str, object]] | None:
        values: dict[str, dict[str, object]] = {}
        for attempt in attempts:
            response = self.api.get_json(f"/attempts/{attempt}/results")
            reports = cast(list[dict[str, object]], response["validation_reports"])
            if len(reports) != 1:
                return None
            values[str(attempt)] = reports[0]
        return values

    def _result_processing_not_started(self, attempt: UUID) -> bool:
        results = self.api.get_json(f"/attempts/{attempt}/results")
        return all(not results[name] for name in ("artifacts", "metrics", "validation_reports"))

    def _summary(self, batch: UUID) -> dict[str, object] | None:
        value = self.api.get_json(f"/batches/{batch}/summary")
        latest = value["latest_summary"]
        return cast(dict[str, object] | None, latest)

    def _assert_normal_contract(
        self,
        trials: tuple[UUID, ...],
        attempts: tuple[UUID, ...],
        terminal: Mapping[str, Mapping[str, object]],
        reports: Mapping[str, Mapping[str, object]],
        selections: Mapping[UUID, object],
        summary: Mapping[str, object],
    ) -> None:
        if any(
            item["terminal_kind"] != "process_exited" or item["exit_code"] != 0
            for item in terminal.values()
        ):
            raise NativeSmokeFailure("normal workloads did not exit successfully")
        valid, invalid = reports[str(attempts[0])], reports[str(attempts[1])]
        if (
            valid["verdict"] != "valid"
            or invalid["verdict"] != "invalid"
            or _reason_codes(invalid) != ["METRIC_BELOW_THRESHOLD"]
        ):
            raise NativeSmokeFailure("normal validation verdict contract failed")
        if selections[trials[0]] is None or selections[trials[1]] is not None:
            raise NativeSmokeFailure("trial selection contract failed")
        selection_a = cast(Mapping[str, object], selections[trials[0]])
        if (
            selection_a["attempt_id"] != str(attempts[0])
            or selection_a["validation_report_id"] != valid["validation_report_id"]
        ):
            raise NativeSmokeFailure("selected trial does not reference its valid result")
        included = cast(list[Mapping[str, object]], summary["included"])
        excluded = cast(list[Mapping[str, object]], summary["excluded"])
        included_ids = {item.get("trial_id") for item in included}
        excluded_ids = {item.get("trial_id") for item in excluded}
        if (
            len(included) != 1
            or len(excluded) != 1
            or included_ids != {str(trials[0])}
            or excluded_ids != {str(trials[1])}
            or included_ids & excluded_ids
        ):
            raise NativeSmokeFailure("summary trial inclusion and exclusion are not exact")
        included_a = included[0]
        if (
            included_a.get("trial_id") != str(trials[0])
            or included_a.get("selected_attempt_id") != str(attempts[0])
            or included_a.get("validation_report_id") != valid["validation_report_id"]
        ):
            raise NativeSmokeFailure("summary included reference for Trial A is invalid")
        excluded_b = [item for item in excluded if item.get("trial_id") == str(trials[1])]
        if len(excluded_b) != 1 or excluded_b[0].get("exclusion_code") != "VALIDATION_INVALID":
            raise NativeSmokeFailure("summary did not exclude Trial B by validation")
        if excluded_b[0].get("validation_report_id") != invalid["validation_report_id"]:
            raise NativeSmokeFailure("summary exclusion does not reference Trial B report")
        if excluded_b[0].get("validation_reason_codes") != ["METRIC_BELOW_THRESHOLD"]:
            raise NativeSmokeFailure("summary exclusion lost Trial B reason codes")
        for trial, attempt in zip(trials, attempts, strict=True):
            attempts_value = self.api.get_json(f"/trials/{trial}")["attempt_ids"]
            if attempts_value != [str(attempt)]:
                raise NativeSmokeFailure("trial was replaced or received a duplicate attempt")
        aggregates = cast(dict[str, object], summary["aggregates"])
        if summary["presentation_status"] != "complete_with_invalid_results" or aggregates.get(
            "score"
        ) != {"count": 1, "mean": "0.82"}:
            raise NativeSmokeFailure("summary aggregate contract failed")

    def _artifact_and_metric_facts(
        self, reports: Mapping[str, Mapping[str, object]]
    ) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
        artifacts: dict[str, dict[str, object]] = {}
        metrics: dict[str, dict[str, object]] = {}
        for attempt_id in reports:
            response = self.api.get_json(f"/attempts/{attempt_id}/results")
            declared_artifacts = cast(list[dict[str, object]], response["artifacts"])
            if {artifact["artifact_name"] for artifact in declared_artifacts} != {
                "result",
                "workload-oracle",
            }:
                raise NativeSmokeFailure(
                    "declared normal artifacts were not registered exactly once"
                )
            artifact = next(
                item for item in declared_artifacts if item["artifact_name"] == "result"
            )
            metric = cast(list[dict[str, object]], response["metrics"])[0]
            if not artifact["content_sha256"] or metric["extractor_version"] != "result-json-v1":
                raise NativeSmokeFailure("public provenance facts are incomplete")
            artifacts[attempt_id] = artifact
            metrics[attempt_id] = metric
        return artifacts, metrics

    def _stop_coordinator(self) -> None:
        if not self._stopped_coordinator:
            run_command(
                compose_admin_command(self.config.compose_project, "coordinator", "stop"),
                timeout_seconds=30,
            )
            self._stopped_coordinator = True

    def _start_coordinator(self) -> None:
        if self._stopped_coordinator:
            run_command(
                compose_admin_command(self.config.compose_project, "coordinator", "start"),
                timeout_seconds=30,
            )
            self._stopped_coordinator = False

    def _restart_coordinator(self, preflight: Mapping[str, object]) -> int:
        before = self._coordinator_ready_generation(preflight)
        run_command(
            compose_admin_command(self.config.compose_project, "coordinator", "restart"),
            timeout_seconds=30,
        )
        return cast(
            int,
            self._wait_for(
                "new coordinator ready generation",
                lambda: self._new_ready_generation(preflight, before),
                30,
            ),
        )

    def _coordinator_ready_generation(self, preflight: Mapping[str, object]) -> int:
        containers = cast(Mapping[str, object], preflight["containers"])
        coordinator = cast(str, containers["coordinator"])
        output = run_command(
            ("docker", "logs", "--tail", "200", coordinator), timeout_seconds=20
        ).stdout.decode("utf-8", "strict")
        generation = 0
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value == {"event": "process_shell.ready", "process": "coordinator"}:
                generation += 1
        return generation

    def _new_ready_generation(self, preflight: Mapping[str, object], before: int) -> int | None:
        generation = self._coordinator_ready_generation(preflight)
        return generation if generation > before else None

    def _track_attempt(self, attempt: UUID, details: Mapping[str, object]) -> bool:
        identity = details.get("supervisor_execution_identity")
        if type(identity) is not str or not identity:
            raise NativeSmokeFailure("attempt supervisor execution identity is unavailable")
        workspace = self.config.workspace_root / attempt.hex
        tracked = self._attempts.get(attempt)
        if tracked is not None:
            if tracked.supervisor_execution_identity != identity:
                raise NativeSmokeFailure("attempt supervisor execution identity changed")
            if tracked.workspace != workspace:
                raise NativeSmokeFailure("attempt workspace identity changed")
            return True
        self._attempts[attempt] = _TrackedAttempt(attempt, identity, workspace)
        return True

    def _workspace(self, attempt: UUID) -> Path:
        tracked = self._attempts.get(attempt)
        if tracked is None:
            raise NativeSmokeFailure("attempt workspace was not registered")
        return tracked.workspace

    def _read_workload_oracle(self, attempt: UUID) -> dict[str, object] | None:
        oracle_path = (
            self._workspace(attempt) / "runtime" / "output" / "workload-test-oracle.json"
        )
        try:
            with oracle_path.open("r", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                try:
                    value = json.load(handle)
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, json.JSONDecodeError):
            return None
        if (
            type(value) is not dict
            or type(value.get("start_count")) is not int
            or type(value.get("natural_exit")) is not bool
            or (value.get("exit_code") is not None and type(value.get("exit_code")) is not int)
        ):
            return None
        return cast(dict[str, object], value)

    def _active_workload_oracles(
        self, attempts: tuple[UUID, ...]
    ) -> dict[str, dict[str, object]] | None:
        values = {str(attempt): self._read_workload_oracle(attempt) for attempt in attempts}
        if any(value is None for value in values.values()):
            return None
        output = {
            attempt: _oracle_evidence(cast(Mapping[str, object], value))
            for attempt, value in values.items()
        }
        return (
            output
            if all(
                value["start_count"] == 1 and value["natural_exit"] is False
                for value in output.values()
            )
            else None
        )

    def _finished_workload_oracle(self, attempt: UUID) -> dict[str, object] | None:
        value = self._read_workload_oracle(attempt)
        if value is None:
            return None
        evidence = _oracle_evidence(value)
        return evidence if (
            evidence["start_count"] == 1
            and evidence["natural_exit"] is True
            and evidence["exit_code"] == 0
        ) else None

    def _finished_workload_oracles(
        self, attempts: tuple[UUID, ...]
    ) -> dict[str, dict[str, object]] | None:
        values = {str(attempt): self._finished_workload_oracle(attempt) for attempt in attempts}
        if any(value is None for value in values.values()):
            return None
        return cast(dict[str, dict[str, object]], values)

    def _result_path(self, attempt: UUID) -> Path | None:
        workspace = self._workspace(attempt)
        result = workspace / "runtime" / "output" / "result.json"
        if result.is_file():
            return result
        return None

    def _cardinalities(
        self, preflight: Mapping[str, object], attempts: tuple[UUID, ...], batch: UUID
    ) -> Cardinalities:
        joined = ",".join(f"'{item}'" for item in attempts)
        statements = {
            "artifacts": f"SELECT count(*) FROM artifacts WHERE attempt_id IN ({joined})",
            "metrics": (
                "SELECT count(*) FROM metric_observations WHERE artifact_id IN "
                f"(SELECT artifact_id FROM artifacts WHERE attempt_id IN ({joined}))"
            ),
            "reports": f"SELECT count(*) FROM validation_reports WHERE attempt_id IN ({joined})",
            "selections": (
                f"SELECT count(*) FROM trial_result_selections WHERE attempt_id IN ({joined})"
            ),
            "summaries": f"SELECT count(*) FROM batch_summary_revisions WHERE batch_id = '{batch}'",
        }
        return Cardinalities(
            **{name: self._scalar(preflight, statement) for name, statement in statements.items()}
        )

    def _scalar(self, preflight: Mapping[str, object], statement: str) -> int:
        containers = cast(Mapping[str, object], preflight["containers"])
        postgres = cast(str, containers["postgres"])
        output = (
            run_command(
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
            )
            .stdout.decode("ascii", "strict")
            .strip()
        )
        try:
            return int(output)
        except ValueError as error:
            raise NativeSmokeFailure("database cardinality query was not scalar") from error

    def _assert_sse_redaction(self, batch: UUID) -> dict[str, object]:
        capture = self.api.read_sse(
            f"/batches/{batch}/events",
            required_event_types={
                "result.artifact_registered",
                "result.validation_completed",
                "result.trial_selected",
                "result.summary_created",
            },
        )
        if (
            SECRET_SENTINEL.encode("utf-8") in capture.raw
            or str(self.config.workspace_root).encode("utf-8") in capture.raw
        ):
            raise NativeSmokeFailure("runtime event stream leaked result content or host path")
        if capture.artifact_names.count("workload-oracle") != 2:
            raise NativeSmokeFailure("SSE did not exercise declared workload-oracle registration")
        return {
            "event_types": capture.event_types,
            "artifact_names": capture.artifact_names,
            "redacted": True,
        }

    def _write_result(self, result: NativeSmokeResult) -> None:
        root = self.config.evidence_root / "slice3-native" / self.tested_sha / uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        path = root / "result.json"
        path.write_text(
            json.dumps(result.value(), separators=(",", ":"), sort_keys=True), encoding="utf-8"
        )

    def _cleanup(self) -> None:
        errors: list[BaseException] = []
        try:
            self._start_coordinator()
        except BaseException as error:
            errors.append(error)
        try:
            self._discover_owned_attempts()
        except BaseException as error:
            errors.append(error)
        for batch in reversed(self._batches):
            try:
                self._cancel_batch_if_active(batch)
            except BaseException as error:
                errors.append(error)
        try:
            self._discover_owned_attempts()
        except BaseException as error:
            errors.append(error)
        for attempt in self._attempts.values():
            try:
                self._cleanup_attempt(attempt)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise NativeSmokeFailure(
                "cleanup failed: " + "; ".join(repr(error) for error in errors)
            )

    def _discover_owned_attempts(self) -> None:
        for batch in self._batches:
            batch_state = self.api.get_json(f"/batches/{batch}")
            trial_ids = batch_state.get("trial_ids")
            if not isinstance(trial_ids, list) or not all(
                isinstance(value, str) for value in trial_ids
            ):
                raise NativeSmokeFailure("owned batch trial discovery is invalid")
            for trial_text in trial_ids:
                trial_state = self.api.get_json(f"/trials/{trial_text}")
                attempt_ids = trial_state.get("attempt_ids")
                if not isinstance(attempt_ids, list) or not all(
                    isinstance(value, str) for value in attempt_ids
                ):
                    raise NativeSmokeFailure("owned trial attempt discovery is invalid")
                for attempt_text in attempt_ids:
                    attempt = UUID(attempt_text)
                    self._track_attempt(
                        attempt, self.api.get_json(f"/attempts/{attempt}")
                    )

    def _cancel_batch_if_active(self, batch: UUID) -> None:
        state = self.api.get_json(f"/batches/{batch}")
        if state["presentation_status"] in {"created", "pending", "running"}:
            self.api.post_json(
                f"/batches/{batch}/cancel",
                {"expected_row_version": state["row_version"]},
                idempotency=True,
            )

    def _cleanup_attempt(self, attempt: _TrackedAttempt) -> None:
        self.api.get_json(f"/attempts/{attempt.attempt_id}")
        try:
            self._wait_for(
                "cleanup settlement",
                lambda: self._attempt_settled(attempt),
                15,
            )
        except NativeSmokeFailure:
            if self._systemd_unit_active(attempt):
                run_command(
                    (
                        "systemctl",
                        "stop",
                        exact_systemd_unit(attempt.supervisor_execution_identity),
                    ),
                    timeout_seconds=30,
                )
            self._wait_for(
                "exact physical execution inactive",
                lambda: True if not self._systemd_unit_active(attempt) else None,
                15,
            )
        if self._systemd_unit_active(attempt):
            raise NativeSmokeFailure("exact physical execution remains active during cleanup")
        workspace = attempt.workspace
        if workspace.exists() or workspace.is_symlink():
            if (
                workspace.parent != self.config.workspace_root
                or not workspace.is_dir()
                or workspace.is_symlink()
            ):
                raise NativeSmokeFailure("cleanup workspace is not a tracked regular directory")
            shutil.rmtree(workspace)

    def _attempt_settled(self, attempt: _TrackedAttempt) -> bool | None:
        state = self.api.get_json(f"/attempts/{attempt.attempt_id}")
        return (
            True
            if state["terminal_at"] is not None and not self._systemd_unit_active(attempt)
            else None
        )

    def _systemd_unit_active(self, attempt: _TrackedAttempt) -> bool:
        output = run_command(
            (
                "systemctl",
                "show",
                exact_systemd_unit(attempt.supervisor_execution_identity),
                "--property=ActiveState",
            ),
            timeout_seconds=20,
            max_output_bytes=4096,
        ).stdout.decode("utf-8", "strict")
        properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        state = properties.get("ActiveState")
        if state in {"inactive", "failed"}:
            return False
        if state in {"active", "activating", "reloading", "deactivating"}:
            return True
        raise NativeSmokeFailure("exact systemd unit state is unsafe")

    def _wait_for(
        self,
        label: str,
        predicate: Callable[[], object | None],
        timeout_seconds: float,
        *,
        diagnostic: Callable[[], object] | None = None,
    ) -> object:
        deadline = time.monotonic() + timeout_seconds
        last: object | None = None
        while time.monotonic() < deadline:
            last = predicate()
            if last is not None:
                return last
            time.sleep(0.25)
        details: object | None = None
        if diagnostic is not None:
            try:
                details = diagnostic()
            except BaseException as error:
                details = {"diagnostic_error": type(error).__name__}
        raise NativeSmokeFailure(
            f"timed out waiting for {label}; last_state={last!r}; diagnostic={details!r}"
        )


class _Api:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def get_json(self, path: str) -> dict[str, object]:
        return self._json("GET", path, None, False)

    def post_json(
        self, path: str, body: Mapping[str, object], *, idempotency: bool = False
    ) -> dict[str, object]:
        return self._json("POST", path, body, idempotency)

    def _json(
        self, method: str, path: str, body: Mapping[str, object] | None, idempotency: bool
    ) -> dict[str, object]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency:
            headers["Idempotency-Key"] = f"task12-{uuid4()}"
        request = Request(f"{self._base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
            raise NativeSmokeFailure(
                f"API {method} {path} failed: {type(error).__name__}"
            ) from error
        if type(decoded) is not dict:
            raise NativeSmokeFailure(f"API {method} {path} returned non-object JSON")
        return cast(dict[str, object], decoded)

    def read_sse(self, path: str, *, required_event_types: set[str]) -> SseCapture:
        request = Request(
            f"{self._base_url}{path}",
            headers={"Accept": "text/event-stream", "Last-Event-ID": "0"},
        )
        raw_chunks: list[bytes] = []
        event_types: list[str] = []
        artifact_names: list[str] = []
        fields: dict[str, list[str]] = {}
        deadline = time.monotonic() + 15
        try:
            with urlopen(request, timeout=15) as response:
                while time.monotonic() < deadline and len(event_types) < 100:
                    line = response.readline(8192)
                    if not line:
                        break
                    raw_chunks.append(line)
                    text = line.decode("utf-8", "strict").rstrip("\r\n")
                    if not text:
                        event = fields.get("event", ["message"])[-1]
                        data = fields.get("data")
                        if data is not None:
                            event_types.append(event)
                            if event == "result.artifact_registered":
                                payload = json.loads("\n".join(data))
                                if (
                                    type(payload) is not dict
                                    or type(payload.get("artifact_name")) is not str
                                ):
                                    raise NativeSmokeFailure(
                                        "SSE artifact event payload is invalid"
                                    )
                                artifact_names.append(payload["artifact_name"])
                        fields = {}
                        if required_event_types.issubset(event_types):
                            return SseCapture(
                                tuple(event_types), tuple(artifact_names), b"".join(raw_chunks)
                            )
                        continue
                    if text.startswith(":"):
                        continue
                    name, separator, value = text.partition(":")
                    if separator and name in {"id", "event", "data"}:
                        fields.setdefault(name, []).append(value.lstrip(" "))
        except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
            raise NativeSmokeFailure(f"SSE read failed: {type(error).__name__}") from error
        missing = sorted(required_event_types - set(event_types))
        raise NativeSmokeFailure(f"SSE result event bound exhausted; missing={missing!r}")


def _config_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item or len(item.encode("utf-8")) > 4096:
        raise NativeOptInRequired(f"{key} must be a bounded non-empty string")
    return item


def _running_gate_timeout_seconds(environ: Mapping[str, str]) -> float:
    raw = environ.get(RUNNING_GATE_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_RUNNING_GATE_TIMEOUT_SECONDS
    try:
        timeout_seconds = float(raw)
    except ValueError as error:
        raise NativeOptInRequired(
            "running gate timeout must be a positive finite number"
        ) from error
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise NativeOptInRequired("running gate timeout must be a positive finite number")
    return timeout_seconds


def _preflight_config(config: NativeConfig) -> dict[str, object]:
    return {
        "repository_root": str(config.repository_root),
        "workspace_root": str(config.workspace_root),
        "compose_project": config.compose_project,
        "python_profile": config.python_profile,
        "runner_policy_source": str(config.runner_policy_source),
        "evidence_root": str(config.evidence_root),
        "dataset_root": str(config.dataset_root),
    }


def _verify_host(config: NativeConfig) -> None:
    if socket.gethostname() != config.expected_hostname:
        raise NativeOptInRequired("native acceptance hostname mismatch")
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        raise NativeOptInRequired("WSL is not a dedicated native acceptance environment")


def _reason_codes(report: Mapping[str, object]) -> list[str]:
    reasons = cast(list[Mapping[str, object]], report["reasons"])
    return sorted(cast(str, item["code"]) for item in reasons)


def _oracle_evidence(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "start_count": value["start_count"],
        "natural_exit": value["natural_exit"],
        "exit_code": value["exit_code"],
    }


def _selection_id(value: object) -> str | None:
    if value is None:
        return None
    return cast(str, cast(Mapping[str, object], value)["validation_report_id"])
