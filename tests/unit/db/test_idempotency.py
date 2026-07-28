from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest

from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalObject,
    canonical_json_bytes,
)
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.db.idempotency import (
    MAX_COMMITTED_STATE_BYTES,
    MAX_NORMALIZED_REQUEST_BYTES,
    MAX_OUTCOME_KIND_BYTES,
    MAX_RELATED_RESULT_REFS,
    MAX_RESULT_ENVELOPE_BYTES,
    MAX_RESULT_ID_BYTES,
    MAX_RESULT_IDENTITY_BYTES,
    MAX_RESULT_REF_BYTES,
    MAX_RESULT_TYPE_BYTES,
    CommandIdentity,
    CommandReceiptIntegrityError,
    CommandReceiptRepository,
    CommandResultEnvelope,
    prepare_command_identity,
    prepare_create_scope,
    prepare_project_scope,
    prepare_result_envelope,
)


def _context(*, interaction: str = "interaction-1") -> CommandContext:
    return CommandContext(
        installation_identity="installation-1",
        actor=CommandActor(actor_type="local_user", actor_id="air"),
        interaction_correlation_id=interaction,
    )


def _envelope(**overrides: object) -> CommandResultEnvelope:
    values: dict[str, object] = {
        "outcome_kind": "project_created",
        "primary_result_type": "project",
        "primary_result_id": "project-1",
        "primary_result_identity": "a" * 64,
        "related_result_refs": ("event:event-2", "event:event-1"),
        "committed_state": "active",
    }
    values.update(overrides)
    return CommandResultEnvelope(**values)  # type: ignore[arg-type]


def _expected_hash(namespace: str, payload: CanonicalObject) -> str:
    return sha256(
        namespace.encode("ascii") + b"\n" + canonical_json_bytes(payload)
    ).hexdigest()


def test_create_scope_uses_the_v1_namespace_and_stable_payload() -> None:
    assert prepare_create_scope(_context()) == _expected_hash(
        "labpilot-idempotency-scope:v1",
        {
            "actor_id": "air",
            "actor_type": "local_user",
            "installation_identity": "installation-1",
            "scope_kind": "create",
            "scope_schema_version": 1,
        },
    )


def test_project_scope_uses_the_v1_namespace_and_project_authority() -> None:
    project_id = UUID("11111111-1111-1111-1111-111111111111")

    assert prepare_project_scope(_context(), project_id=project_id) == _expected_hash(
        "labpilot-idempotency-scope:v1",
        {
            "actor_id": "air",
            "actor_type": "local_user",
            "installation_identity": "installation-1",
            "project_id": str(project_id),
            "scope_kind": "project",
            "scope_schema_version": 1,
        },
    )


def test_request_hash_uses_the_v1_namespace_and_stable_payload() -> None:
    normalized_request: CanonicalObject = {
        "name": "Causal Diffusion",
        "expected_row_version": 1,
    }

    identity = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request=normalized_request,
    )

    assert identity.normalized_request_hash == _expected_hash(
        "labpilot-command-request:v1",
        {
            "command_name": "CreateProject",
            "command_version": 1,
            "normalized_request": normalized_request,
            "request_schema_version": 1,
            "scope_identity": "s" * 64,
        },
    )


def test_request_dict_key_order_does_not_change_hash() -> None:
    first: CanonicalObject = {"name": "x", "expected_row_version": 1}
    second: CanonicalObject = {"expected_row_version": 1, "name": "x"}

    first_identity = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="ArchiveProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request=first,
    )
    second_identity = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="ArchiveProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request=second,
    )

    assert first_identity.normalized_request_hash == second_identity.normalized_request_hash


def test_expected_row_version_changes_request_hash() -> None:
    first = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="ArchiveProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request={"project_id": "project-1", "expected_row_version": 1},
    )
    second = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="ArchiveProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request={"project_id": "project-1", "expected_row_version": 2},
    )

    assert first.normalized_request_hash != second.normalized_request_hash


def test_idempotency_key_and_request_time_do_not_enter_request_hash() -> None:
    normalized_request: CanonicalObject = {"name": "x"}
    request_time_a = "2026-07-28T09:00:00Z"
    request_time_b = "2026-07-28T10:00:00Z"

    first = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request=normalized_request,
    )
    second = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-2",
        normalized_request=normalized_request,
    )

    assert request_time_a != request_time_b
    assert first.idempotency_key != second.idempotency_key
    assert first.normalized_request_hash == second.normalized_request_hash


def test_interaction_correlation_is_not_part_of_scope() -> None:
    assert prepare_create_scope(
        _context(interaction="interaction-1")
    ) == prepare_create_scope(_context(interaction="interaction-2"))


def test_result_envelope_uses_v1_namespace_and_sorts_related_refs() -> None:
    envelope_json, envelope_hash = prepare_result_envelope(_envelope())
    expected: CanonicalObject = {
        "committed_state": "active",
        "outcome_kind": "project_created",
        "primary_result_id": "project-1",
        "primary_result_identity": "a" * 64,
        "primary_result_type": "project",
        "related_result_refs": ["event:event-1", "event:event-2"],
    }

    assert envelope_json == expected
    assert envelope_hash == _expected_hash(
        "labpilot-command-result-envelope:v1",
        expected,
    )


def test_related_result_ref_input_order_does_not_change_hash() -> None:
    _, first_hash = prepare_result_envelope(
        _envelope(related_result_refs=("event:2", "event:1"))
    )
    _, second_hash = prepare_result_envelope(
        _envelope(related_result_refs=("event:1", "event:2"))
    )

    assert first_hash == second_hash


def test_command_identity_and_result_envelope_are_frozen_and_slotted() -> None:
    identity = prepare_command_identity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request={"name": "x"},
    )
    envelope = _envelope()

    with pytest.raises(FrozenInstanceError):
        identity.command_name = "Other"
    with pytest.raises(FrozenInstanceError):
        envelope.committed_state = "archived"
    assert not hasattr(identity, "__dict__")
    assert not hasattr(envelope, "__dict__")


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("outcome_kind", MAX_OUTCOME_KIND_BYTES),
        ("primary_result_type", MAX_RESULT_TYPE_BYTES),
        ("primary_result_id", MAX_RESULT_ID_BYTES),
        ("primary_result_identity", MAX_RESULT_IDENTITY_BYTES),
        ("committed_state", MAX_COMMITTED_STATE_BYTES),
    ],
)
def test_result_string_limits_use_utf8_bytes(field: str, limit: int) -> None:
    value = "界" * (limit // 3) + "a" * (limit % 3)
    prepare_result_envelope(_envelope(**{field: value}))

    with pytest.raises(ValueError, match=field):
        prepare_result_envelope(_envelope(**{field: value + "界"}))


def test_result_ref_limit_uses_utf8_bytes() -> None:
    exact = "界" * (MAX_RESULT_REF_BYTES // 3)
    prepare_result_envelope(_envelope(related_result_refs=(exact,)))

    with pytest.raises(ValueError, match="related_result_refs"):
        prepare_result_envelope(_envelope(related_result_refs=(exact + "界",)))


def test_result_ref_count_is_limited() -> None:
    with pytest.raises(ValueError, match="related_result_refs"):
        prepare_result_envelope(
            _envelope(
                related_result_refs=tuple(
                    f"result:{index}" for index in range(MAX_RELATED_RESULT_REFS + 1)
                )
            )
        )


def test_result_envelope_total_size_is_limited() -> None:
    refs = tuple(
        f"{index:02d}:" + "x" * (MAX_RESULT_REF_BYTES - 3)
        for index in range(MAX_RELATED_RESULT_REFS)
    )
    assert all(len(ref.encode("utf-8")) == MAX_RESULT_REF_BYTES for ref in refs)

    with pytest.raises(ValueError, match=str(MAX_RESULT_ENVELOPE_BYTES)):
        prepare_result_envelope(_envelope(related_result_refs=refs))


def test_normalized_request_exact_limit_is_accepted() -> None:
    normalized_request: CanonicalObject = {
        "value": "x" * (MAX_NORMALIZED_REQUEST_BYTES - len(b'{"value":""}'))
    }
    assert len(canonical_json_bytes(normalized_request)) == MAX_NORMALIZED_REQUEST_BYTES

    prepare_command_identity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request=normalized_request,
    )


def test_normalized_request_over_limit_is_rejected() -> None:
    normalized_request: CanonicalObject = {
        "value": "x" * (MAX_NORMALIZED_REQUEST_BYTES - len(b'{"value":""}') + 1)
    }

    with pytest.raises(ValueError, match=str(MAX_NORMALIZED_REQUEST_BYTES)):
        prepare_command_identity(
            scope_identity="s" * 64,
            command_name="CreateProject",
            command_version=1,
            idempotency_key="request-1",
            normalized_request=normalized_request,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_kind", "created\n"),
        ("primary_result_type", "project\x00"),
        ("primary_result_id", "project\t1"),
        ("primary_result_identity", "identity\x7f"),
        ("committed_state", "active\r"),
        ("related_result_refs", ("event:\x01",)),
    ],
)
def test_result_envelope_rejects_control_characters(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="control"):
        prepare_result_envelope(_envelope(**{field: value}))


def test_idempotency_key_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="control"):
        prepare_command_identity(
            scope_identity="s" * 64,
            command_name="CreateProject",
            command_version=1,
            idempotency_key="request\n1",
            normalized_request={"name": "x"},
        )


def test_non_json_native_normalized_request_is_rejected() -> None:
    request = cast(CanonicalObject, {"value": 1.5})

    with pytest.raises(CanonicalizationError, match="unsupported type"):
        prepare_command_identity(
            scope_identity="s" * 64,
            command_name="CreateProject",
            command_version=1,
            idempotency_key="request-1",
            normalized_request=request,
        )


def test_non_string_result_ref_is_rejected() -> None:
    ref = cast(str, object())

    with pytest.raises(ValueError, match="related_result_refs"):
        prepare_result_envelope(_envelope(related_result_refs=(ref,)))


def test_result_envelope_rejects_extra_metadata() -> None:
    with pytest.raises(TypeError, match="metadata"):
        CommandResultEnvelope(
            outcome_kind="project_created",
            primary_result_type="project",
            primary_result_id="project-1",
            primary_result_identity=None,
            related_result_refs=(),
            committed_state="active",
            metadata={"raw_path": "/secret"},  # type: ignore[call-arg]
        )


def test_result_envelope_json_has_exactly_six_fields() -> None:
    envelope_json, _ = prepare_result_envelope(_envelope())

    assert set(envelope_json) == {
        "outcome_kind",
        "primary_result_type",
        "primary_result_id",
        "primary_result_identity",
        "related_result_refs",
        "committed_state",
    }


def test_replacing_related_refs_does_not_mutate_original_envelope() -> None:
    envelope = _envelope(related_result_refs=("event:2", "event:1"))

    prepare_result_envelope(envelope)

    assert envelope == replace(envelope)
    assert envelope.related_result_refs == ("event:2", "event:1")


def test_command_identity_type_is_public() -> None:
    identity = CommandIdentity(
        scope_identity="s" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request_hash="h" * 64,
    )

    assert identity.command_version == 1


class _ScalarResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _ReadingSession:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.statement: object | None = None

    async def execute(self, statement: object) -> _ScalarResult:
        self.statement = statement
        return _ScalarResult(self.row)


class _WritingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1


class _FakeUow:
    def __init__(self, session: _WritingSession) -> None:
        self.session = session


def _identity(*, request_hash: str = "b" * 64) -> CommandIdentity:
    return CommandIdentity(
        scope_identity="a" * 64,
        command_name="CreateProject",
        command_version=1,
        idempotency_key="request-1",
        normalized_request_hash=request_hash,
    )


def _receipt_row(**overrides: object) -> object:
    from uuid import uuid4

    from labpilot.db.idempotency_models import CommandReceiptRow

    envelope_json, envelope_hash = prepare_result_envelope(_envelope())
    values: dict[str, object] = {
        "command_receipt_id": uuid4(),
        "idempotency_scope_identity": "a" * 64,
        "command_name": "CreateProject",
        "command_version": 1,
        "idempotency_key": "request-1",
        "normalized_request_hash": "b" * 64,
        "outcome_kind": "project_created",
        "primary_result_type": "project",
        "primary_result_id": "project-1",
        "result_envelope_schema_version": 1,
        "result_envelope_json": envelope_json,
        "result_envelope_hash": envelope_hash,
    }
    values.update(overrides)
    return CommandReceiptRow(**values)  # type: ignore[arg-type]


async def _get_receipt(row: object) -> object:
    session = _ReadingSession(row)
    result = await CommandReceiptRepository().get(  # type: ignore[arg-type]
        session,
        _identity(),
    )
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_receipt_get_queries_the_four_column_unique_key() -> None:
    session = _ReadingSession(None)

    await CommandReceiptRepository().get(  # type: ignore[arg-type]
        session,
        _identity(request_hash="c" * 64),
    )

    assert session.statement is not None
    where_clause = str(session.statement).split("WHERE", maxsplit=1)[1]
    assert "idempotency_scope_identity" in where_clause
    assert "command_name" in where_clause
    assert "command_version" in where_clause
    assert "idempotency_key" in where_clause
    assert "normalized_request_hash" not in where_clause


@pytest.mark.asyncio
async def test_receipt_get_rejects_result_envelope_hash_mismatch() -> None:
    with pytest.raises(CommandReceiptIntegrityError, match="hash"):
        await _get_receipt(_receipt_row(result_envelope_hash="f" * 64))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_kind", "other"),
        ("primary_result_type", "other"),
        ("primary_result_id", "other"),
    ],
)
@pytest.mark.asyncio
async def test_receipt_get_rejects_top_level_envelope_mismatch(
    field: str,
    value: str,
) -> None:
    with pytest.raises(CommandReceiptIntegrityError, match=field):
        await _get_receipt(_receipt_row(**{field: value}))


@pytest.mark.asyncio
async def test_receipt_get_rejects_envelope_over_16_kib() -> None:
    refs = [
        f"{index:02d}:" + "x" * (MAX_RESULT_REF_BYTES - 3)
        for index in range(MAX_RELATED_RESULT_REFS)
    ]
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "related_result_refs": refs,
    }

    with pytest.raises(CommandReceiptIntegrityError, match="result envelope"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_get_rejects_more_than_64_related_refs() -> None:
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "related_result_refs": [
            f"result:{index}" for index in range(MAX_RELATED_RESULT_REFS + 1)
        ],
    }

    with pytest.raises(CommandReceiptIntegrityError, match="related_result_refs"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_get_rejects_result_ref_over_300_bytes() -> None:
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "related_result_refs": ["界" * (MAX_RESULT_REF_BYTES // 3 + 1)],
    }

    with pytest.raises(CommandReceiptIntegrityError, match="related_result_refs"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_get_rejects_control_characters() -> None:
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "committed_state": "active\n",
    }

    with pytest.raises(CommandReceiptIntegrityError, match="control"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_get_rejects_non_json_native_values() -> None:
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "related_result_refs": [object()],
    }

    with pytest.raises(CommandReceiptIntegrityError, match="related_result_refs"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_get_rejects_extra_envelope_fields() -> None:
    row = _receipt_row()
    row.result_envelope_json = {  # type: ignore[attr-defined]
        **row.result_envelope_json,  # type: ignore[attr-defined]
        "metadata": {"raw_path": "/secret"},
    }

    with pytest.raises(CommandReceiptIntegrityError, match="fields"):
        await _get_receipt(row)


@pytest.mark.asyncio
async def test_receipt_add_computes_envelope_and_flushes_without_commit() -> None:
    session = _WritingSession()

    row = await CommandReceiptRepository().add(  # type: ignore[arg-type]
        _FakeUow(session),
        identity=_identity(),
        envelope=_envelope(),
    )

    expected_json, expected_hash = prepare_result_envelope(_envelope())
    assert session.added == [row]
    assert session.flushes == 1
    assert row.result_envelope_json == expected_json
    assert row.result_envelope_hash == expected_hash
    assert row.outcome_kind == expected_json["outcome_kind"]
    assert row.primary_result_type == expected_json["primary_result_type"]
    assert row.primary_result_id == expected_json["primary_result_id"]
    assert not hasattr(session, "commit")


@pytest.mark.asyncio
async def test_oversized_request_stops_before_prepare_and_operation() -> None:
    calls = {"prepare": 0, "operation": 0}
    normalized_request = cast(
        CanonicalObject,
        {
            "value": "x"
            * (MAX_NORMALIZED_REQUEST_BYTES - len(b'{"value":""}') + 1)
        },
    )

    async def prepare() -> None:
        calls["prepare"] += 1

    async def operation() -> None:
        calls["operation"] += 1

    async def invoke() -> None:
        prepare_command_identity(
            scope_identity="s" * 64,
            command_name="CreateProject",
            command_version=1,
            idempotency_key="request-1",
            normalized_request=normalized_request,
        )
        await prepare()
        await operation()

    with pytest.raises(ValueError, match=str(MAX_NORMALIZED_REQUEST_BYTES)):
        await invoke()
    assert calls == {"prepare": 0, "operation": 0}


@pytest.mark.asyncio
async def test_async_noop_prepare_returns_none() -> None:
    from labpilot.db.idempotency import async_noop_prepare

    assert await async_noop_prepare() is None


def test_idempotent_command_outcome_is_frozen_and_slotted() -> None:
    from labpilot.db.idempotency import IdempotentCommandOutcome

    outcome = IdempotentCommandOutcome(envelope=_envelope(), replayed=False)

    with pytest.raises(FrozenInstanceError):
        outcome.replayed = True
    assert not hasattr(outcome, "__dict__")
