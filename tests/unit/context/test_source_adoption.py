from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.source_adoption import AdoptionError, AdoptSource, SourceAdoptionService
from scripts.source_candidate import failure_detail


def test_exact_command_boundary_and_closed_diagnostics():
    data = dict(
        project_id=uuid4(),
        package_ref="sha256:" + "a" * 64,
        target_snapshot_id=uuid4(),
        target_snapshot_identity="b" * 64,
    )
    for bad in [
        dict(data, path="/private"),
        dict(data, package_ref="latest"),
        dict(data, target_snapshot_identity="S2"),
    ]:
        with pytest.raises(ValidationError):
            AdoptSource(**bad)
    assert failure_detail(AdoptionError("adoption_content_mismatch")) == "adoption_content_mismatch"
    secret = "password=secret Authorization=private-token"
    assert secret not in failure_detail(AdoptionError(secret))
    assert secret not in failure_detail(RuntimeError(secret))


async def test_agent_rejected_before_evidence_or_database_access():
    service = SourceAdoptionService(None, None)
    context = CommandContext("installation", CommandActor("agent", "agent"), "test")
    cmd = AdoptSource(
        project_id=uuid4(),
        package_ref="sha256:" + "a" * 64,
        target_snapshot_id=uuid4(),
        target_snapshot_identity="b" * 64,
    )
    for actor in [context.actor, CommandActor("user", None)]:
        with pytest.raises(AdoptionError, match="adoption_user_required"):
            await service.adopt(
                context=replace(context, actor=actor), idempotency_key="key", command=cmd
            )
