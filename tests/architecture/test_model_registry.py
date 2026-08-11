import labpilot.db.model_registry  # noqa: F401
from labpilot.db.base import Base


def test_model_registry_registers_command_receipts() -> None:
    assert "command_receipts" in Base.metadata.tables


def test_model_registry_registers_proposal_authority_tables() -> None:
    assert {
        "experiments",
        "experiment_proposal_revisions",
        "experiment_proposal_verifications",
        "experiment_design_decisions",
        "experiment_versions",
    } <= set(Base.metadata.tables)


def test_model_registry_registers_tool_and_agent_runtime_tables() -> None:
    assert {
        "tool_calls",
        "tool_approvals",
        "agent_runs",
        "agent_steps",
    } <= set(Base.metadata.tables)
