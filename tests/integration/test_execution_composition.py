from datetime import timedelta
from pathlib import Path

import pytest

from labpilot.db.session import Database
from labpilot.settings import Settings


def _settings(tmp_path: Path, **execution: object) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(),
        repository_content_store_root=tmp_path,
        **execution,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "missing",
    (
        "attempt_workspace_root",
        "runner_socket_path",
        "controller_uid",
        "controller_gid",
        "train_gid",
    ),
)
def test_coordinator_composition_fails_closed_without_execution_authority(
    tmp_path: Path, missing: str
) -> None:
    from labpilot.coordinator.worker import build_coordinator

    workspace = tmp_path / "attempts"
    workspace.mkdir()
    execution: dict[str, object] = {
        "attempt_workspace_root": workspace,
        "runner_socket_path": Path("/run/labpilot-runner/control.sock"),
        "controller_uid": 1234,
        "controller_gid": 2345,
        "train_gid": 3456,
    }
    execution.pop(missing)
    with pytest.raises(RuntimeError, match="Coordinator execution configuration"):
        build_coordinator(
            _settings(tmp_path, **execution),
            Database("postgresql+asyncpg://user:pass@db:5432/labpilot"),
        )


@pytest.mark.integration
def test_coordinator_composition_uses_exact_train_gid(tmp_path: Path) -> None:
    from labpilot.coordinator.worker import build_coordinator

    workspace = tmp_path / "attempts"
    workspace.mkdir()
    coordinator = build_coordinator(
        _settings(
            tmp_path,
            attempt_workspace_root=workspace,
            runner_socket_path=Path("/run/labpilot-runner/control.sock"),
            runner_request_timeout_seconds=12,
            unknown_attention_grace_seconds=62,
            controller_uid=1234,
            controller_gid=2345,
            train_gid=3456,
        ),
        Database("postgresql+asyncpg://user:pass@db:5432/labpilot"),
    )

    assert coordinator._preparation._execution_gid == 3456
    assert coordinator._execution._runner._socket_path == (
        "/run/labpilot-runner/control.sock"
    )
    assert coordinator._execution._runner._timeout_seconds == 12
    assert coordinator._execution._unknown_grace == timedelta(seconds=62)


@pytest.mark.integration
def test_compose_grants_workspace_read_access_but_execution_authority_only_to_coordinator() -> None:
    compose = Path("compose.yaml").read_text()
    api = compose.split("\n  api:\n", 1)[1].split("\n  coordinator:\n", 1)[0]
    coordinator = compose.split("\n  coordinator:\n", 1)[1].split(
        "\n  projection:\n", 1
    )[0]

    workspace_root = (
        "@@D@@{LABPILOT_ATTEMPT_WORKSPACE_ROOT:"
        "?LABPILOT_ATTEMPT_WORKSPACE_ROOT is required}"
    ).replace("@@D@@", "$")
    assert (
        "- type: bind\n"
        f"        source: {workspace_root}\n"
        f"        target: {workspace_root}\n"
        "        read_only: true\n"
        "        bind:\n"
        "          create_host_path: false"
    ) in api
    for value in (
        "LABPILOT_RUNNER_SOCKET_PATH",
        "LABPILOT_TRAIN_GID",
        "/run/labpilot-runner/control.sock",
        "group_add:",
        "user:",
    ):
        assert value not in api
        assert value in coordinator
    for name in (
        "LABPILOT_CONTROLLER_UID",
        "LABPILOT_CONTROLLER_GID",
        "LABPILOT_TRAIN_GID",
        "LABPILOT_ATTEMPT_WORKSPACE_ROOT",
    ):
        assert f"${{{name}:?" in coordinator
    assert (
        "LABPILOT_CONTROLLER_UID: ${LABPILOT_CONTROLLER_UID:?" in coordinator
    )
    assert (
        "LABPILOT_CONTROLLER_GID: ${LABPILOT_CONTROLLER_GID:?" in coordinator
    )
    assert (
        'user: "${LABPILOT_CONTROLLER_UID:?LABPILOT_CONTROLLER_UID is required}:'
        '${LABPILOT_CONTROLLER_GID:?LABPILOT_CONTROLLER_GID is required}"'
        in coordinator
    )
    assert (
        "- /run/labpilot-runner/control.sock:/run/labpilot-runner/control.sock"
        not in coordinator
    )
    for value in (
        "source: /run/labpilot-runner",
        "target: /run/labpilot-runner",
    ):
        assert value in coordinator
        assert value not in api
    for value in ("read_only: true", "create_host_path: false"):
        assert value in coordinator
