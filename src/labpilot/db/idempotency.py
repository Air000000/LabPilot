from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar, cast
from unicodedata import category
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.common.canonical_json import (
    CanonicalObject,
    canonical_json_bytes,
)
from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.common.hashing import namespaced_sha256, namespaced_sha256_bytes
from labpilot.db.idempotency_models import CommandReceiptRow
from labpilot.db.uow import SessionContextFactory, UnitOfWork

MAX_RESULT_ENVELOPE_BYTES = 16 * 1024
MAX_NORMALIZED_REQUEST_BYTES = 64 * 1024
MAX_RELATED_RESULT_REFS = 64
MAX_RESULT_REF_BYTES = 300
MAX_OUTCOME_KIND_BYTES = 80
MAX_RESULT_TYPE_BYTES = 100
MAX_RESULT_ID_BYTES = 200
MAX_RESULT_IDENTITY_BYTES = 200
MAX_COMMITTED_STATE_BYTES = 80

_SCOPE_NAMESPACE = "labpilot-idempotency-scope:v1"
_REQUEST_NAMESPACE = "labpilot-command-request:v1"
_RESULT_NAMESPACE = "labpilot-command-result-envelope:v1"


@dataclass(frozen=True, slots=True)
class CommandIdentity:
    scope_identity: str
    command_name: str
    command_version: int
    idempotency_key: str
    normalized_request_hash: str


@dataclass(frozen=True, slots=True)
class CommandResultEnvelope:
    outcome_kind: str
    primary_result_type: str
    primary_result_id: str
    primary_result_identity: str | None
    related_result_refs: tuple[str, ...]
    committed_state: str

    def to_json(self) -> dict[str, object]:
        return _result_envelope_json(self)


def prepare_create_scope(context: CommandContext) -> str:
    return namespaced_sha256(
        _SCOPE_NAMESPACE,
        {
            "actor_id": context.actor.actor_id,
            "actor_type": context.actor.actor_type,
            "installation_identity": context.installation_identity,
            "scope_kind": "create",
            "scope_schema_version": 1,
        },
    )


def prepare_project_scope(
    context: CommandContext,
    *,
    project_id: UUID,
) -> str:
    return namespaced_sha256(
        _SCOPE_NAMESPACE,
        {
            "actor_id": context.actor.actor_id,
            "actor_type": context.actor.actor_type,
            "installation_identity": context.installation_identity,
            "project_id": str(project_id),
            "scope_kind": "project",
            "scope_schema_version": 1,
        },
    )


def prepare_command_identity(
    *,
    scope_identity: str,
    command_name: str,
    command_version: int,
    idempotency_key: str,
    normalized_request: CanonicalObject,
) -> CommandIdentity:
    _require_string(scope_identity, field="scope_identity", max_bytes=64)
    _require_string(command_name, field="command_name", max_bytes=120)
    if command_version <= 0:
        raise ValueError("command_version must be positive")
    _require_string(idempotency_key, field="idempotency_key", max_bytes=200)

    normalized_request_bytes = canonical_json_bytes(normalized_request)
    if len(normalized_request_bytes) > MAX_NORMALIZED_REQUEST_BYTES:
        raise ValueError(
            f"normalized_request exceeds {MAX_NORMALIZED_REQUEST_BYTES} bytes"
        )

    normalized_request_hash = namespaced_sha256(
        _REQUEST_NAMESPACE,
        {
            "command_name": command_name,
            "command_version": command_version,
            "normalized_request": normalized_request,
            "request_schema_version": 1,
            "scope_identity": scope_identity,
        },
    )
    return CommandIdentity(
        scope_identity=scope_identity,
        command_name=command_name,
        command_version=command_version,
        idempotency_key=idempotency_key,
        normalized_request_hash=normalized_request_hash,
    )


def prepare_result_envelope(
    envelope: CommandResultEnvelope,
) -> tuple[dict[str, object], str]:
    envelope_json = envelope.to_json()
    canonical_bytes = canonical_json_bytes(cast(CanonicalObject, envelope_json))
    if len(canonical_bytes) > MAX_RESULT_ENVELOPE_BYTES:
        raise ValueError(
            f"result envelope exceeds {MAX_RESULT_ENVELOPE_BYTES} bytes"
        )
    return envelope_json, namespaced_sha256_bytes(_RESULT_NAMESPACE, canonical_bytes)


def _result_envelope_json(envelope: CommandResultEnvelope) -> dict[str, object]:
    _require_string(
        envelope.outcome_kind,
        field="outcome_kind",
        max_bytes=MAX_OUTCOME_KIND_BYTES,
    )
    _require_string(
        envelope.primary_result_type,
        field="primary_result_type",
        max_bytes=MAX_RESULT_TYPE_BYTES,
    )
    _require_string(
        envelope.primary_result_id,
        field="primary_result_id",
        max_bytes=MAX_RESULT_ID_BYTES,
    )
    if envelope.primary_result_identity is not None:
        _require_string(
            envelope.primary_result_identity,
            field="primary_result_identity",
            max_bytes=MAX_RESULT_IDENTITY_BYTES,
        )
    _require_string(
        envelope.committed_state,
        field="committed_state",
        max_bytes=MAX_COMMITTED_STATE_BYTES,
    )
    if type(envelope.related_result_refs) is not tuple:
        raise ValueError("related_result_refs must be a tuple of strings")
    if len(envelope.related_result_refs) > MAX_RELATED_RESULT_REFS:
        raise ValueError(
            f"related_result_refs exceeds {MAX_RELATED_RESULT_REFS} entries"
        )
    for ref in envelope.related_result_refs:
        _require_string(
            ref,
            field="related_result_refs",
            max_bytes=MAX_RESULT_REF_BYTES,
        )

    return {
        "outcome_kind": envelope.outcome_kind,
        "primary_result_type": envelope.primary_result_type,
        "primary_result_id": envelope.primary_result_id,
        "primary_result_identity": envelope.primary_result_identity,
        "related_result_refs": sorted(envelope.related_result_refs),
        "committed_state": envelope.committed_state,
    }


def _require_string(value: object, *, field: str, max_bytes: int) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if any(category(character) == "Cc" for character in value):
        raise ValueError(f"{field} contains a control character")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} UTF-8 bytes")


class CommandReceiptIntegrityError(LabPilotError):
    """Stored receipt does not match its canonical result envelope."""


class CommandReceiptRepository:
    async def get(
        self,
        session: AsyncSession,
        identity: CommandIdentity,
    ) -> CommandReceiptRow | None:
        result = await session.execute(
            select(CommandReceiptRow).where(
                CommandReceiptRow.idempotency_scope_identity
                == identity.scope_identity,
                CommandReceiptRow.command_name == identity.command_name,
                CommandReceiptRow.command_version == identity.command_version,
                CommandReceiptRow.idempotency_key == identity.idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            _envelope_from_receipt(row)
        return row

    async def add(
        self,
        uow: UnitOfWork,
        *,
        identity: CommandIdentity,
        envelope: CommandResultEnvelope,
    ) -> CommandReceiptRow:
        envelope_json, envelope_hash = prepare_result_envelope(envelope)
        _require_matching_top_level_fields(envelope, envelope_json)
        row = CommandReceiptRow(
            command_receipt_id=uuid4(),
            idempotency_scope_identity=identity.scope_identity,
            command_name=identity.command_name,
            command_version=identity.command_version,
            idempotency_key=identity.idempotency_key,
            normalized_request_hash=identity.normalized_request_hash,
            outcome_kind=envelope.outcome_kind,
            primary_result_type=envelope.primary_result_type,
            primary_result_id=envelope.primary_result_id,
            result_envelope_schema_version=1,
            result_envelope_json=envelope_json,
            result_envelope_hash=envelope_hash,
        )
        uow.session.add(row)
        await uow.session.flush()
        return row


def _require_matching_top_level_fields(
    envelope: CommandResultEnvelope,
    envelope_json: dict[str, object],
) -> None:
    for field in ("outcome_kind", "primary_result_type", "primary_result_id"):
        if envelope_json.get(field) != getattr(envelope, field):
            raise CommandReceiptIntegrityError(
                f"{field} does not match result envelope"
            )


def _envelope_from_receipt(row: CommandReceiptRow) -> CommandResultEnvelope:
    try:
        if row.result_envelope_schema_version != 1:
            raise ValueError("unsupported result envelope schema version")
        payload = row.result_envelope_json
        expected_fields = {
            "outcome_kind",
            "primary_result_type",
            "primary_result_id",
            "primary_result_identity",
            "related_result_refs",
            "committed_state",
        }
        if type(payload) is not dict or set(payload) != expected_fields:
            raise ValueError("result envelope fields do not match schema")

        outcome_kind = payload["outcome_kind"]
        primary_result_type = payload["primary_result_type"]
        primary_result_id = payload["primary_result_id"]
        primary_result_identity = payload["primary_result_identity"]
        related_result_refs = payload["related_result_refs"]
        committed_state = payload["committed_state"]
        if type(outcome_kind) is not str:
            raise ValueError("outcome_kind must be a string")
        if type(primary_result_type) is not str:
            raise ValueError("primary_result_type must be a string")
        if type(primary_result_id) is not str:
            raise ValueError("primary_result_id must be a string")
        if primary_result_identity is not None and type(
            primary_result_identity
        ) is not str:
            raise ValueError("primary_result_identity must be a string or null")
        if type(related_result_refs) is not list or any(
            type(ref) is not str for ref in related_result_refs
        ):
            raise ValueError("related_result_refs must be a list of strings")
        if type(committed_state) is not str:
            raise ValueError("committed_state must be a string")

        envelope = CommandResultEnvelope(
            outcome_kind=outcome_kind,
            primary_result_type=primary_result_type,
            primary_result_id=primary_result_id,
            primary_result_identity=primary_result_identity,
            related_result_refs=tuple(cast(list[str], related_result_refs)),
            committed_state=committed_state,
        )
        expected_json, expected_hash = prepare_result_envelope(envelope)
        if payload != expected_json:
            raise ValueError("result envelope is not in canonical field order")
        if row.result_envelope_hash != expected_hash:
            raise ValueError("result envelope hash mismatch")
        for field in ("outcome_kind", "primary_result_type", "primary_result_id"):
            if getattr(row, field) != getattr(envelope, field):
                raise CommandReceiptIntegrityError(
                    f"{field} does not match result envelope"
                )
        return envelope
    except CommandReceiptIntegrityError:
        raise
    except (TypeError, ValueError) as exc:
        raise CommandReceiptIntegrityError(str(exc)) from exc


class IdempotencyKeyConflict(LabPilotError):
    """An idempotency key was already committed for another request."""


@dataclass(frozen=True, slots=True)
class IdempotentCommandOutcome:
    envelope: CommandResultEnvelope
    replayed: bool


PreparedT = TypeVar("PreparedT")


async def execute_idempotent_command(
    *,
    session_context_factory: SessionContextFactory,
    identity: CommandIdentity,
    prepare: Callable[[], Awaitable[PreparedT]],
    operation: Callable[
        [UnitOfWork, PreparedT],
        Awaitable[CommandResultEnvelope],
    ],
) -> IdempotentCommandOutcome:
    repository = CommandReceiptRepository()
    async with session_context_factory() as session:
        existing = await repository.get(session, identity)
        if existing is not None:
            return _replay_receipt(existing, identity)

    prepared = await prepare()
    try:
        async with UnitOfWork(session_context_factory) as uow:
            envelope = await operation(uow, prepared)
            receipt = await repository.add(
                uow,
                identity=identity,
                envelope=envelope,
            )
            canonical_envelope = _envelope_from_receipt(receipt)
            await uow.commit()
        return IdempotentCommandOutcome(
            envelope=canonical_envelope,
            replayed=False,
        )
    except IntegrityError as exc:
        if _postgres_constraint_name(exc) != "uq_command_receipt_idempotency_key":
            raise

    async with session_context_factory() as session:
        winner = await repository.get(session, identity)
        if winner is None:
            raise CommandReceiptIntegrityError(
                "receipt race winner could not be reloaded"
            )
        return _replay_receipt(winner, identity)


async def async_noop_prepare() -> None:
    return None


def _replay_receipt(
    row: CommandReceiptRow,
    identity: CommandIdentity,
) -> IdempotentCommandOutcome:
    if row.normalized_request_hash != identity.normalized_request_hash:
        raise IdempotencyKeyConflict(
            "idempotency key is already committed for a different request"
        )
    return IdempotentCommandOutcome(
        envelope=_envelope_from_receipt(row),
        replayed=True,
    )


def _postgres_constraint_name(error: BaseException) -> str | None:
    pending: list[object] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str):
            return name
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return None
