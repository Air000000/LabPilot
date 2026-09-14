from pathlib import Path
from runpy import run_path
from uuid import UUID

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/competition/bootstrap_demo_context.py"


class DriverUUID(UUID):
    """Models asyncpg's UUID subclass at the database boundary."""


def test_normalize_uuid_subclass_to_exact_stdlib_uuid() -> None:
    namespace = run_path(str(SCRIPT))
    normalize_uuid = namespace["_normalize_uuid"]

    value = DriverUUID("3ced4de0-0960-45d8-9aaa-e6d72794bb51")

    assert isinstance(value, UUID)
    assert type(value) is not UUID

    normalized = normalize_uuid(value)

    assert normalized == UUID("3ced4de0-0960-45d8-9aaa-e6d72794bb51")
    assert type(normalized) is UUID
