from uuid import UUID

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.commands import CommandContext
from labpilot.observability.events import RuntimeEventEnvelope
from labpilot.observability.payloads import (
    EventJsonObject,
    prepare_runtime_event_payload,
    register_runtime_event_payload_schema,
)

_PROJECT_CREATED_FIELDS = {"project_id", "project_identity", "status"}
_PROJECT_ARCHIVED_FIELDS = {
    "project_id",
    "project_identity",
    "previous_status",
    "status",
    "row_version",
}


def _require_exact_fields(payload: EventJsonObject, expected: set[str]) -> None:
    if set(payload) != expected:
        raise CanonicalizationError("project event payload fields do not match schema")
    if type(payload["project_id"]) is not str:
        raise CanonicalizationError("project_id must be a string")
    if type(payload["project_identity"]) is not str:
        raise CanonicalizationError("project_identity must be a string")


def _validate_project_created(payload: EventJsonObject) -> None:
    _require_exact_fields(payload, _PROJECT_CREATED_FIELDS)
    if payload["status"] != "active":
        raise CanonicalizationError("project.created status must be active")


def _validate_project_archived(payload: EventJsonObject) -> None:
    _require_exact_fields(payload, _PROJECT_ARCHIVED_FIELDS)
    if payload["previous_status"] != "active":
        raise CanonicalizationError("project.archived previous_status must be active")
    if payload["status"] != "archived":
        raise CanonicalizationError("project.archived status must be archived")
    row_version = payload["row_version"]
    if type(row_version) is not int or row_version <= 1:
        raise CanonicalizationError("project.archived row_version must exceed one")


register_runtime_event_payload_schema(
    "project.created",
    1,
    _validate_project_created,
)
register_runtime_event_payload_schema(
    "project.archived",
    1,
    _validate_project_archived,
)


def _event_envelope(
    *,
    context: CommandContext,
    project_id: UUID,
    event_type: str,
    payload: EventJsonObject,
) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        project_id=project_id,
        aggregate_type="project",
        aggregate_id=str(project_id),
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
        partition_key=f"project:{project_id}",
    )


def build_project_created_event(
    *,
    context: CommandContext,
    project_id: UUID,
    project_identity: str,
) -> RuntimeEventEnvelope:
    return _event_envelope(
        context=context,
        project_id=project_id,
        event_type="project.created",
        payload={
            "project_id": str(project_id),
            "project_identity": project_identity,
            "status": "active",
        },
    )


def build_project_archived_event(
    *,
    context: CommandContext,
    project_id: UUID,
    project_identity: str,
    row_version: int,
) -> RuntimeEventEnvelope:
    return _event_envelope(
        context=context,
        project_id=project_id,
        event_type="project.archived",
        payload={
            "project_id": str(project_id),
            "project_identity": project_identity,
            "previous_status": "active",
            "status": "archived",
            "row_version": row_version,
        },
    )
