import labpilot.db.model_registry  # noqa: F401
from labpilot.db.base import Base


def test_model_registry_registers_command_receipts() -> None:
    assert "command_receipts" in Base.metadata.tables
