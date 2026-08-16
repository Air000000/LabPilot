import importlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


def test_build_preparing_attempt_persists_exact_identity_and_workspace() -> None:
    repository = importlib.import_module("labpilot.execution.repository")
    attempt_id = UUID("33333333-3333-4333-8333-333333333333")

    row = repository.build_preparing_attempt(
        attempt_id=attempt_id,
        trial_id=UUID("44444444-4444-4444-8444-444444444444"),
        attempt_number=1,
        installation_identity="labpilot-prod-01",
        attempt_workspace_root=Path("/srv/labpilot/attempts"),
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert row.attempt_id == attempt_id
    assert row.status == "preparing"
    assert row.supervisor_execution_identity == (
        "labpilot-v1-55f402ae7fcfb369905d882c585b64e8a5a66dd92fa3d0377b1e7d0aa18f1fb9-"
        "33333333333343338333333333333333"
    )
    assert row.workspace_path == (
        "/srv/labpilot/attempts/33333333333343338333333333333333"
    )
    assert row.terminal_at is None
