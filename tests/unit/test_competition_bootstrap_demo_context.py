from uuid import UUID

from scripts.competition.bootstrap_demo_context import _normalize_uuid


class DriverUUID(UUID):
    """Models asyncpg's UUID subclass at the database boundary."""


def test_normalize_uuid_subclass_to_exact_stdlib_uuid() -> None:
    value = DriverUUID("3ced4de0-0960-45d8-9aaa-e6d72794bb51")

    assert isinstance(value, UUID)
    assert type(value) is not UUID

    normalized = _normalize_uuid(value)

    assert normalized == UUID("3ced4de0-0960-45d8-9aaa-e6d72794bb51")
    assert type(normalized) is UUID
