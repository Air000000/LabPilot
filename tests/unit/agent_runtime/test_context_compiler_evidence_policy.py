from __future__ import annotations

import json
from uuid import uuid4

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.context.repository_source import RepositorySourceDescriptor


def _descriptor() -> RepositorySourceDescriptor:
    return RepositorySourceDescriptor(
        repository_snapshot_id=uuid4(),
        repository_snapshot_identity="b" * 64,
        repository_registration_id=uuid4(),
        project_id=uuid4(),
        head_commit="c" * 40,
        current_branch="main",
        is_detached_head=False,
        is_clean=True,
    )


def test_context_requires_repository_evidence_and_cross_field_proposal_contract() -> None:
    compiled = ContextCompiler().compile(
        provider_name="scripted",
        model="test-model",
        goal="propose an experiment",
        snapshot=_descriptor(),
        definitions=(),
        prior_tool_results=(),
        prior_human_inputs=(),
    )

    system = json.loads(compiled.request.messages[0]["content"])
    decision_policy = "\n".join(system["decision_policy"])
    proposal_contract = "\n".join(system["proposal_contract"])

    assert "README" in decision_policy
    assert "repository evidence" in decision_policy
    assert "request_human_input" in decision_policy
    assert "artifacts[].name" in proposal_contract
    assert "Every required artifact needs artifact_exists coverage" in proposal_contract
    assert "{parameter:<key>}" in proposal_contract
    assert "{seed}" in proposal_contract
    assert "{dataset}" in proposal_contract
    assert "{output_dir}" in proposal_contract
    assert "eval_entrypoint" in proposal_contract
