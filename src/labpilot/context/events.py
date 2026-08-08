from uuid import UUID

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.commands import CommandContext
from labpilot.observability.events import RuntimeEventEnvelope
from labpilot.observability.payloads import (
    EventJsonObject,
    prepare_runtime_event_payload,
    register_runtime_event_payload_schema,
)

_CREATED = "repository.registration.created"
_AVAILABILITY_CHANGED = "repository.registration.availability_changed"
_REVOKED = "repository.registration.revoked"
_STATUS = {"active", "unavailable", "invalid_git_repository", "revoked"}


def _require_fields(payload: EventJsonObject, expected: set[str]) -> None:
    if set(payload) != expected:
        raise CanonicalizationError("repository event payload fields do not match schema")
    for field in ("project_id", "repository_registration_id"):
        if type(payload[field]) is not str:
            raise CanonicalizationError(f"repository event requires {field}")
    if type(payload["row_version"]) is not int or payload["row_version"] < 1:
        raise CanonicalizationError("repository event requires positive row_version")


def _validate_created(payload: EventJsonObject) -> None:
    _require_fields(
        payload,
        {"project_id", "repository_registration_id", "root_path_identity", "status", "row_version"},
    )
    if type(payload["root_path_identity"]) is not str or len(payload["root_path_identity"]) != 64:
        raise CanonicalizationError("created event requires root_path_identity")
    if payload["status"] != "active":
        raise CanonicalizationError("created event status must be active")


def _validate_availability_changed(payload: EventJsonObject) -> None:
    _require_fields(
        payload,
        {
            "project_id",
            "repository_registration_id",
            "old_status",
            "new_status",
            "reason_code",
            "row_version",
        },
    )
    if payload["old_status"] not in _STATUS or payload["new_status"] not in _STATUS:
        raise CanonicalizationError("availability event has invalid status")
    if payload["old_status"] == payload["new_status"]:
        raise CanonicalizationError("availability event status must change")
    if type(payload["reason_code"]) is not str or not payload["reason_code"]:
        raise CanonicalizationError("availability event requires reason_code")


def _validate_revoked(payload: EventJsonObject) -> None:
    _require_fields(
        payload,
        {
            "project_id",
            "repository_registration_id",
            "root_path_identity",
            "previous_status",
            "status",
            "row_version",
        },
    )
    if type(payload["root_path_identity"]) is not str or len(payload["root_path_identity"]) != 64:
        raise CanonicalizationError("revoked event requires root_path_identity")
    if payload["previous_status"] not in _STATUS - {"revoked"} or payload["status"] != "revoked":
        raise CanonicalizationError("revoked event has invalid status")


register_runtime_event_payload_schema(_CREATED, 1, _validate_created)
register_runtime_event_payload_schema(_AVAILABILITY_CHANGED, 1, _validate_availability_changed)
register_runtime_event_payload_schema(_REVOKED, 1, _validate_revoked)


def _event(
    *,
    context: CommandContext,
    project_id: UUID,
    repository_registration_id: UUID,
    event_type: str,
    payload: EventJsonObject,
) -> RuntimeEventEnvelope:
    registration_id = str(repository_registration_id)
    return RuntimeEventEnvelope(
        project_id=project_id,
        aggregate_type="repository_registration",
        aggregate_id=registration_id,
        event_type=event_type,
        event_schema_version=1,
        payload=prepare_runtime_event_payload(
            event_type=event_type,
            event_schema_version=1,
            payload=payload,
        ),
        occurred_at=None,
        interaction_correlation_id=context.interaction_correlation_id,
        workflow_correlation_id=None,
        causation_id=None,
        actor_type=context.actor.actor_type,
        actor_id=context.actor.actor_id,
        visibility_class="internal",
        topic="runtime-events",
        partition_key=f"repository_registration:{registration_id}",
    )


def build_repository_registration_created_event(
    *,
    context: CommandContext,
    project_id: UUID,
    repository_registration_id: UUID,
    root_path_identity: str,
    row_version: int,
) -> RuntimeEventEnvelope:
    return _event(
        context=context,
        project_id=project_id,
        repository_registration_id=repository_registration_id,
        event_type=_CREATED,
        payload={
            "project_id": str(project_id),
            "repository_registration_id": str(repository_registration_id),
            "root_path_identity": root_path_identity,
            "status": "active",
            "row_version": row_version,
        },
    )


def build_repository_registration_availability_changed_event(
    *,
    context: CommandContext,
    project_id: UUID,
    repository_registration_id: UUID,
    old_status: str,
    new_status: str,
    reason_code: str,
    row_version: int,
) -> RuntimeEventEnvelope:
    return _event(
        context=context,
        project_id=project_id,
        repository_registration_id=repository_registration_id,
        event_type=_AVAILABILITY_CHANGED,
        payload={
            "project_id": str(project_id),
            "repository_registration_id": str(repository_registration_id),
            "old_status": old_status,
            "new_status": new_status,
            "reason_code": reason_code,
            "row_version": row_version,
        },
    )


def build_repository_registration_revoked_event(
    *,
    context: CommandContext,
    project_id: UUID,
    repository_registration_id: UUID,
    previous_status: str,
    root_path_identity: str,
    row_version: int,
) -> RuntimeEventEnvelope:
    return _event(
        context=context,
        project_id=project_id,
        repository_registration_id=repository_registration_id,
        event_type=_REVOKED,
        payload={
            "project_id": str(project_id),
            "repository_registration_id": str(repository_registration_id),
            "root_path_identity": root_path_identity,
            "previous_status": previous_status,
            "status": "revoked",
            "row_version": row_version,
        },
    )
