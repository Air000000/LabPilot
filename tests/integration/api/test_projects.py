from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from starlette.requests import Request
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.api.actor import (
    ActorProvider,
    ServerConfiguredActorProvider,
)
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.db.idempotency import (
    CommandResultEnvelope,
    IdempotentCommandOutcome,
)
from labpilot.db.idempotency_models import CommandReceiptRow
from labpilot.db.session import Database
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

ERROR_FIELDS = {
    "error_code",
    "human_message",
    "aggregate_type",
    "aggregate_id",
    "current_state",
    "expected_state",
    "retryable",
    "details",
}


def _request_with_spoofed_actor_headers() -> Request:
    return Request(
        {
            "type": "http",
            "headers": [
                (b"x-actor-id", b"mallory"),
                (b"x-actor-type", b"remote_admin"),
                (b"x-installation-identity", b"attacker-installation"),
                (b"x-correlation-id", b"attacker-correlation"),
            ],
        }
    )


def _actor_provider(
    *,
    installation_identity: str = "installation-1",
    actor_id: str = "air",
) -> ServerConfiguredActorProvider:
    return ServerConfiguredActorProvider(
        installation_identity=installation_identity,
        actor_id=actor_id,
    )


def _create_payload(*, name: str = "Causal Diffusion") -> dict[str, object]:
    return {
        "name": name,
        "description": "Counterfactual image experiments",
        "artifact_root_ref": "artifact-root:projects/causal-diffusion",
    }


def _app(
    database: Database,
    *,
    actor_provider: ActorProvider | None = None,
    project_service: ProjectApplicationService | None = None,
):
    return create_app(
        services=ApplicationServices(
            projects=project_service or ProjectApplicationService(database.session)
        ),
        actor_provider=actor_provider or _actor_provider(),
    )


@asynccontextmanager
async def _database_and_client(
    isolated_database_url: str,
    *,
    actor_provider: ActorProvider | None = None,
    project_service: ProjectApplicationService | None = None,
    raise_app_exceptions: bool = True,
):
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    app = _app(
        database,
        actor_provider=actor_provider,
        project_service=project_service,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(
                app=app,
                raise_app_exceptions=raise_app_exceptions,
            ),
            base_url="http://test",
        ) as client:
            yield database, app, client
    finally:
        await database.dispose()


async def _row_count(database: Database, row_type: type[object]) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(row_type)))


def _assert_error(response: Response, status_code: int, error_code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == ERROR_FIELDS
    assert payload["error_code"] == error_code
    assert isinstance(payload["human_message"], str)
    assert isinstance(payload["retryable"], bool)
    assert isinstance(payload["details"], dict)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_actor_provider_ignores_spoofed_headers() -> None:
    provider = _actor_provider()
    request = _request_with_spoofed_actor_headers()

    first = await provider.get_command_context(request)
    second = await provider.get_command_context(request)

    assert first.installation_identity == "installation-1"
    assert first.actor.actor_type == "local_user"
    assert first.actor.actor_id == "air"
    assert first.actor is second.actor
    assert first.interaction_correlation_id != second.interaction_correlation_id


@pytest.mark.integration
def test_server_actor_provider_rejects_invalid_actor_at_construction() -> None:
    with pytest.raises(ValueError):
        ServerConfiguredActorProvider(
            installation_identity="installation-1",
            actor_id="界" * 67,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_rejects_nonstandard_or_invalid_transport_authority(
    isolated_database_url: str,
) -> None:
    async with _database_and_client(isolated_database_url) as (
        database,
        _,
        client,
    ):
        missing = await client.post("/projects", json=_create_payload())
        _assert_error(missing, 422, "request_validation_error")

        x_header_only = await client.post(
            "/projects",
            headers={"X-Idempotency-Key": "not-standard"},
            json=_create_payload(),
        )
        _assert_error(x_header_only, 422, "request_validation_error")

        for invalid_key in ("", "x" * 201):
            invalid_header = await client.post(
                "/projects",
                headers={"Idempotency-Key": invalid_key},
                json=_create_payload(),
            )
            _assert_error(invalid_header, 422, "request_validation_error")

        for forbidden_field in (
            "actor_id",
            "actor_type",
            "installation_identity",
            "interaction_correlation_id",
            "idempotency_key",
        ):
            rejected_body = await client.post(
                "/projects",
                headers={"Idempotency-Key": f"reject-{forbidden_field}"},
                json={**_create_payload(), forbidden_field: "mallory-secret"},
            )
            _assert_error(rejected_body, 422, "request_validation_error")
            assert "mallory-secret" not in rejected_body.text

        assert await _row_count(database, ProjectRow) == 0
        assert await _row_count(database, CommandReceiptRow) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_replay_conflict_spoof_resistance_and_actor_scope_isolation(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        air_app = _app(database)
        async with AsyncClient(
            transport=ASGITransport(app=air_app),
            base_url="http://test",
        ) as air_client:
            headers = {
                "Idempotency-Key": "shared-create-key",
                "X-Actor-ID": "mallory",
                "X-Actor-Type": "remote_admin",
                "X-Installation-Identity": "attacker-installation",
                "X-Correlation-ID": "attacker-correlation",
            }
            first = await air_client.post(
                "/projects",
                headers=headers,
                json=_create_payload(),
            )
            assert first.status_code == 201
            assert first.json()["replayed"] is False
            assert first.json()["committed_state"] == "active"

            replay = await air_client.post(
                "/projects",
                headers=headers,
                json=_create_payload(),
            )
            assert replay.status_code == 200
            assert replay.json() == {**first.json(), "replayed": True}

            conflict = await air_client.post(
                "/projects",
                headers=headers,
                json=_create_payload(name="Different Project"),
            )
            _assert_error(conflict, 409, "idempotency_key_conflict")
            assert "shared-create-key" not in conflict.text
            assert "request hash" not in conflict.text.lower()
            assert "scope identity" not in conflict.text.lower()

        bob_app = _app(
            database,
            actor_provider=_actor_provider(
                installation_identity="installation-1",
                actor_id="bob",
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(app=bob_app),
            base_url="http://test",
        ) as bob_client:
            isolated = await bob_client.post(
                "/projects",
                headers={"Idempotency-Key": "shared-create-key"},
                json=_create_payload(),
            )

        assert isolated.status_code == 201
        assert isolated.json()["replayed"] is False
        assert isolated.json()["project_id"] != first.json()["project_id"]
        assert await _row_count(database, ProjectRow) == 2
        assert await _row_count(database, RuntimeEventRow) == 2
        assert await _row_count(database, OutboxRecordRow) == 2
        assert await _row_count(database, CommandReceiptRow) == 2
        async with database.session() as session:
            scopes = (
                await session.execute(
                    select(CommandReceiptRow.idempotency_scope_identity)
                )
            ).scalars().all()
        assert len(set(scopes)) == 2
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_archive_and_domain_errors_preserve_command_semantics(
    isolated_database_url: str,
) -> None:
    async with _database_and_client(isolated_database_url) as (
        database,
        _,
        client,
    ):
        created = await client.post(
            "/projects",
            headers={"Idempotency-Key": "create-before-archive"},
            json=_create_payload(),
        )
        assert created.status_code == 201
        project_id = created.json()["project_id"]

        counts_before_get = (
            await _row_count(database, CommandReceiptRow),
            await _row_count(database, RuntimeEventRow),
            await _row_count(database, OutboxRecordRow),
        )
        fetched = await client.get(f"/projects/{project_id}")
        assert fetched.status_code == 200
        assert fetched.json() == {
            "project_id": project_id,
            "project_identity": created.json()["project_identity"],
            "name": "Causal Diffusion",
            "description": "Counterfactual image experiments",
            "status": "active",
            "primary_repository_registration_id": None,
            "artifact_root_ref": "artifact-root:projects/causal-diffusion",
            "created_at": fetched.json()["created_at"],
            "updated_at": fetched.json()["updated_at"],
            "row_version": 1,
        }
        assert (
            await _row_count(database, CommandReceiptRow),
            await _row_count(database, RuntimeEventRow),
            await _row_count(database, OutboxRecordRow),
        ) == counts_before_get

        wrong_version = await client.post(
            f"/projects/{project_id}/archive",
            headers={"Idempotency-Key": "archive-wrong-version"},
            json={"expected_row_version": 2},
        )
        _assert_error(wrong_version, 409, "project_version_conflict")
        assert wrong_version.json()["retryable"] is True
        assert wrong_version.json()["details"] == {
            "expected_row_version": 2,
            "current_row_version": 1,
        }

        archived = await client.post(
            f"/projects/{project_id}/archive",
            headers={"Idempotency-Key": "archive-project"},
            json={"expected_row_version": 1},
        )
        assert archived.status_code == 200
        assert archived.json() == {
            **created.json(),
            "committed_state": "archived",
            "replayed": False,
        }

        replay = await client.post(
            f"/projects/{project_id}/archive",
            headers={"Idempotency-Key": "archive-project"},
            json={"expected_row_version": 1},
        )
        assert replay.status_code == 200
        assert replay.json() == {**archived.json(), "replayed": True}

        state_conflict = await client.post(
            f"/projects/{project_id}/archive",
            headers={"Idempotency-Key": "archive-new-key"},
            json={"expected_row_version": 2},
        )
        _assert_error(state_conflict, 409, "project_state_conflict")
        assert state_conflict.json()["current_state"] == "archived"
        assert state_conflict.json()["expected_state"] == "active"

        archived_fetch = await client.get(f"/projects/{project_id}")
        assert archived_fetch.status_code == 200
        assert archived_fetch.json()["status"] == "archived"
        assert archived_fetch.json()["row_version"] == 2

        unknown_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        unknown_get = await client.get(f"/projects/{unknown_id}")
        _assert_error(unknown_get, 404, "project_not_found")

        unknown_archive = await client.post(
            f"/projects/{unknown_id}/archive",
            headers={"Idempotency-Key": "archive-unknown"},
            json={"expected_row_version": 1},
        )
        _assert_error(unknown_archive, 404, "project_not_found")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_path_and_body_validation_errors_are_structured(
    isolated_database_url: str,
) -> None:
    async with _database_and_client(isolated_database_url) as (_, _, client):
        invalid_uuid = await client.get("/projects/not-a-uuid")
        _assert_error(invalid_uuid, 422, "request_validation_error")

        invalid_version = await client.post(
            "/projects/cccccccc-cccc-4ccc-8ccc-cccccccccccc/archive",
            headers={"Idempotency-Key": "invalid-version"},
            json={"expected_row_version": 0},
        )
        _assert_error(invalid_version, 422, "request_validation_error")


class _InvalidProjectService:
    async def create_project(self, **_: object) -> IdempotentCommandOutcome:
        return IdempotentCommandOutcome(
            envelope=CommandResultEnvelope(
                outcome_kind="project_created",
                primary_result_type="not-a-project",
                primary_result_id=str(
                    UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
                ),
                primary_result_identity="invalid-internal-identity",
                related_result_refs=(),
                committed_state="active",
            ),
            replayed=False,
        )


class _UnexpectedValueErrorProjectService:
    async def create_project(self, **_: object) -> IdempotentCommandOutcome:
        raise ValueError("internal serialization bug")

    async def archive_project(self, **_: object) -> IdempotentCommandOutcome:
        raise ValueError("internal serialization bug")


class _CountingProjectService:
    def __init__(self) -> None:
        self.create_calls = 0
        self.archive_calls = 0
        self.idempotency_keys: list[str] = []

    async def create_project(self, **values: object) -> IdempotentCommandOutcome:
        self.create_calls += 1
        self.idempotency_keys.append(cast(str, values["idempotency_key"]))
        return IdempotentCommandOutcome(
            envelope=CommandResultEnvelope(
                outcome_kind="project_created",
                primary_result_type="project",
                primary_result_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                primary_result_identity="project:counting-service",
                related_result_refs=(),
                committed_state="active",
            ),
            replayed=False,
        )

    async def archive_project(self, **values: object) -> IdempotentCommandOutcome:
        self.archive_calls += 1
        self.idempotency_keys.append(cast(str, values["idempotency_key"]))
        return IdempotentCommandOutcome(
            envelope=CommandResultEnvelope(
                outcome_kind="project_archived",
                primary_result_type="project",
                primary_result_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                primary_result_identity="project:counting-service",
                related_result_refs=(),
                committed_state="archived",
            ),
            replayed=False,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_unavailable_and_internal_invariant_are_safe_and_structured(
    isolated_database_url: str,
) -> None:
    async with _database_and_client(isolated_database_url) as (
        database,
        app,
        client,
    ):
        app.state.application_services = None
        unavailable = await client.post(
            "/projects",
            headers={"Idempotency-Key": "service-unavailable-secret"},
            json=_create_payload(),
        )
        _assert_error(unavailable, 503, "service_unavailable")
        assert unavailable.json()["retryable"] is True
        assert "service-unavailable-secret" not in unavailable.text

        invalid_app = _app(
            database,
            project_service=cast(
                ProjectApplicationService,
                _InvalidProjectService(),
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(
                app=invalid_app,
                raise_app_exceptions=False,
            ),
            base_url="http://test",
        ) as invalid_client:
            invariant = await invalid_client.post(
                "/projects",
                headers={"Idempotency-Key": "invariant-secret"},
                json=_create_payload(),
            )

        _assert_error(invariant, 500, "internal_invariant_error")
        assert invariant.json()["details"] == {}
        assert "invariant-secret" not in invariant.text
        assert "invalid-internal-identity" not in invariant.text
        assert "traceback" not in invariant.text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unexpected_service_value_error_is_safe_internal_error(
    isolated_database_url: str,
) -> None:
    service = cast(
        ProjectApplicationService,
        _UnexpectedValueErrorProjectService(),
    )
    async with _database_and_client(
        isolated_database_url,
        project_service=service,
        raise_app_exceptions=False,
    ) as (_, _, client):
        create_response = await client.post(
            "/projects",
            headers={"Idempotency-Key": "create-value-error-secret"},
            json=_create_payload(),
        )
        archive_response = await client.post(
            "/projects/cccccccc-cccc-4ccc-8ccc-cccccccccccc/archive",
            headers={"Idempotency-Key": "archive-value-error-secret"},
            json={"expected_row_version": 1},
        )

    for response in (create_response, archive_response):
        _assert_error(response, 500, "internal_invariant_error")
        assert response.json()["details"] == {}
        assert set(response.json()) == ERROR_FIELDS

    combined_response = create_response.text + archive_response.text
    assert "internal serialization bug" not in combined_response
    assert "traceback" not in combined_response.lower()
    assert "create-value-error-secret" not in combined_response
    assert "archive-value-error-secret" not in combined_response
    assert "installation-1" not in combined_response
    assert "air" not in combined_response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_ascii_idempotency_key_is_rejected_before_service(
    isolated_database_url: str,
) -> None:
    service = _CountingProjectService()
    async with _database_and_client(
        isolated_database_url,
        project_service=cast(ProjectApplicationService, service),
    ) as (_, _, client):
        raw_header = b"\xe7\x95\x8c"
        headers = [(b"idempotency-key", raw_header)]
        create_response = await client.post(
            "/projects",
            headers=headers,
            json=_create_payload(),
        )
        archive_response = await client.post(
            "/projects/cccccccc-cccc-4ccc-8ccc-cccccccccccc/archive",
            headers=headers,
            json={"expected_row_version": 1},
        )

    for response in (create_response, archive_response):
        _assert_error(response, 422, "request_validation_error")
        assert response.json()["details"] == {}

    transport_value = raw_header.decode("latin-1")
    assert transport_value not in create_response.text
    assert transport_value not in archive_response.text
    assert service.create_calls == 0
    assert service.archive_calls == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idempotency_key_ascii_byte_boundaries(
    isolated_database_url: str,
) -> None:
    valid_service = _CountingProjectService()
    valid_key = b"a" * 200
    async with _database_and_client(
        isolated_database_url,
        project_service=cast(ProjectApplicationService, valid_service),
    ) as (_, _, client):
        valid_response = await client.post(
            "/projects",
            headers=[(b"idempotency-key", valid_key)],
            json=_create_payload(),
        )

    assert valid_response.status_code == 201
    assert valid_service.create_calls == 1
    assert valid_service.archive_calls == 0
    assert valid_service.idempotency_keys == [valid_key.decode("ascii")]

    invalid_service = _CountingProjectService()
    invalid_key = b"a" * 201
    async with _database_and_client(
        isolated_database_url,
        project_service=cast(ProjectApplicationService, invalid_service),
    ) as (_, _, client):
        invalid_create = await client.post(
            "/projects",
            headers=[(b"idempotency-key", invalid_key)],
            json=_create_payload(),
        )
        invalid_archive = await client.post(
            "/projects/cccccccc-cccc-4ccc-8ccc-cccccccccccc/archive",
            headers=[(b"idempotency-key", invalid_key)],
            json={"expected_row_version": 1},
        )

    _assert_error(invalid_create, 422, "request_validation_error")
    _assert_error(invalid_archive, 422, "request_validation_error")
    assert invalid_service.create_calls == 0
    assert invalid_service.archive_calls == 0
    assert invalid_service.idempotency_keys == []
