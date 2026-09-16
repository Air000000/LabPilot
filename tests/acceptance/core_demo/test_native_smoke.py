from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from tests.acceptance.core_demo import native_smoke
from tests.acceptance.core_demo.native_smoke import NativeCoreDemoSmoke


def test_agent_proposal_uses_public_api_and_reuses_native_payload(
    monkeypatch, tmp_path
) -> None:
    project_id, snapshot_id, agent_run_id, revision_id, version_id = (uuid4() for _ in range(5))
    smoke = NativeCoreDemoSmoke(
        SimpleNamespace(
            api_base_url="http://test",
            workspace_root=tmp_path,
            repository_snapshot_id=snapshot_id,
            repository_snapshot_identity="a" * 64,
            train_entrypoint="train.py",
            workdir=".",
            dataset_uri="file:///dataset",
            python_profile="python-3.12",
            workload_duration_seconds=2,
        ),
        "a" * 40,
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def post_json(
        path: str, body: dict[str, object], *, idempotency: bool = False
    ) -> dict[str, object]:
        assert idempotency is False
        calls.append((path, body))
        return {
            f"/projects/{project_id}/agent-runs": {
                "agent_run_id": str(agent_run_id),
                "row_version": 1,
            },
            f"/agent-runs/{agent_run_id}/advance": {
                "status": "completed",
                "produced_proposal_revision_id": str(revision_id),
            },
            f"/proposal-revisions/{revision_id}/verify": {
                "proposal_verification_id": str(uuid4()),
                "result": {"passed": True},
            },
            f"/proposal-revisions/{revision_id}/approve": {
                "experiment_version_id": str(version_id)
            },
        }[path]

    smoke.api = SimpleNamespace(post_json=post_json)
    monkeypatch.setattr(smoke, "_project_id", lambda _preflight: project_id)

    assert smoke._agent_proposal_version({}) == (str(agent_run_id), str(revision_id), version_id)
    assert [path for path, _ in calls] == [
        f"/projects/{project_id}/agent-runs",
        f"/agent-runs/{agent_run_id}/advance",
        f"/proposal-revisions/{revision_id}/verify",
        f"/proposal-revisions/{revision_id}/approve",
    ]
    created = calls[0][1]
    assert created["repository_snapshot_id"] == str(snapshot_id)
    goal = created["goal"]
    assert type(goal) is str
    assert goal.startswith("First return a repository.list_tree tool_call with empty arguments. ")
    payload = json.loads(goal.split("exactly this proposal: ", 1)[1])
    assert payload == smoke._payload(("0.82", "0.61"), "result.json")
    assert calls[1][1] == {"expected_row_version": 1}


def test_project_id_comes_from_the_bound_snapshot(monkeypatch, tmp_path) -> None:
    project_id, snapshot_id = uuid4(), uuid4()
    smoke = NativeCoreDemoSmoke(
        SimpleNamespace(
            api_base_url="http://test",
            workspace_root=tmp_path,
            repository_snapshot_id=snapshot_id,
        ),
        "a" * 40,
    )
    calls: list[tuple[str, ...]] = []

    def run_command(command: tuple[str, ...], *, timeout_seconds: int) -> SimpleNamespace:
        calls.append(command)
        assert timeout_seconds == 20
        return SimpleNamespace(stdout=f"{project_id}\n".encode("ascii"))

    monkeypatch.setattr(native_smoke, "run_command", run_command)

    assert smoke._project_id({"containers": {"postgres": "postgres-id"}}) == project_id
    assert calls[0][:3] == ("docker", "exec", "postgres-id")
    assert str(snapshot_id) in calls[0][-1]
