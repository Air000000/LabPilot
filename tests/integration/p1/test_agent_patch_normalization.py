import asyncio
import json
import os
import re
import sys
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from tests.integration.p1.test_agent_source_patch import (
    advance,
    counts,
    create,
)
from tests.integration.p1.test_agent_source_patch import (
    draft as draft_fixture,
)
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.api.source_patch_adapter import export_agent_patch, normalize_agent_patch
from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.source_candidates import SourceCandidateService

pytestmark = pytest.mark.integration
draft = draft_fixture


def malformed():
    return re.sub(
        rb"@@ -(\d+),(\d+) \+(\d+),(\d+) @@",
        lambda m: b"@@ -"
        + m[1]
        + b","
        + str(int(m[2]) + 1).encode()
        + b" +"
        + m[3]
        + b","
        + str(int(m[4]) + 1).encode()
        + b" @@",
        patch(CLIPPED),
    )


async def completed(d, raw):
    d.provider.script = [{"kind": "source_patch", "patch": raw.decode()}]
    return await advance(d, (await create(d)).json())


async def cli(d, command, run_id):
    root = Path(__file__).resolve().parents[3]
    store = d.tmp / "normalizer-must-not-initialize"
    env = os.environ | {
        "LABPILOT_DATABASE_URL": d.url,
        "LABPILOT_ENVIRONMENT": "test",
        "LABPILOT_INSTALLATION_IDENTITY": "normalization-test",
        "LABPILOT_LOCAL_ACTOR_ID": "human",
        "LABPILOT_REPOSITORY_ALLOWED_ROOTS": json.dumps([str(d.tmp)]),
        "LABPILOT_REPOSITORY_CONTENT_STORE_ROOT": str(store),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts/source_candidate.py"),
        command,
        "--agent-run-id",
        run_id,
        cwd=root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
    assert not store.exists()
    return proc.returncode, stdout, stderr


async def test_normalize_exact_draft_to_explicit_static_handoff_preserves_raw_and_pending(draft):
    d = draft
    raw = malformed()
    r = await completed(d, raw)
    run_id = UUID(r["agent_run_id"])
    before_counts = await counts(d)
    before = (await d.client.get(f"/agent-runs/{run_id}/trace")).json()
    canonical, receipt = await normalize_agent_patch(d.system.database.session, d.source, run_id)
    assert canonical == patch(CLIPPED)
    assert receipt["agent_run_id"] == str(run_id)
    assert receipt["agent_step_id"] == before["steps"][-1]["agent_step_id"]
    assert receipt["repository_snapshot_identity"] == d.system.snapshot_identity
    assert receipt["raw_patch_sha256"] == sha256(raw).hexdigest()
    assert receipt["canonical_patch_sha256"] == sha256(canonical).hexdigest()
    assert receipt["status"] == "derived_patch_not_submitted"
    assert len(json.dumps(receipt).encode()) <= 4096
    assert await export_agent_patch(d.system.database.session, run_id) == raw
    assert (await d.client.get(f"/agent-runs/{run_id}/trace")).json() == before
    assert len(d.provider.calls) == 1
    assert await counts(d) == before_counts
    # Only this explicit human action publishes P2-A records; old pending requests still work.
    candidates = SourceCandidateService(d.source, d.tmp / "manual-candidates")
    args = dict(
        project_id=d.project,
        snapshot_id=d.system.snapshot_id,
        snapshot_identity=d.system.snapshot_identity,
        actor_id="human",
    )
    pending = await candidates.submit(patch=patch(CLIPPED), rationale="old pending", **args)
    assert await normalize_agent_patch(d.system.database.session, d.source, run_id) == (
        canonical,
        receipt,
    )
    old_package = await candidates.prepare(pending)
    old_view = await candidates.inspect(old_package)
    request = await candidates.submit(patch=canonical, rationale="explicit derived review", **args)
    package = await candidates.prepare(request)
    view = await candidates.inspect(package)
    assert view["request"]["patch_ref"] == "sha256:" + receipt["canonical_patch_sha256"]
    assert view["report"]["runtime_smoke"] == view["report"]["scientific_validation"] == "not_run"
    assert await candidates.inspect(old_package) == old_view
    assert (d.system.repository / "train.py").read_bytes() == BASE


async def test_real_cli_canonical_stdout_receipt_stderr_and_repeat_no_publication(draft):
    d = draft
    raw = malformed()
    r = await completed(d, raw)
    before = (await d.client.get(f"/agent-runs/{r['agent_run_id']}/trace")).json()
    first = await cli(d, "normalize-agent-patch", r["agent_run_id"])
    second = await cli(d, "normalize-agent-patch", r["agent_run_id"])
    assert first == second and first[0] == 0
    assert first[1] == patch(CLIPPED)
    receipt = json.loads(first[2])
    assert receipt["raw_patch_sha256"] == sha256(raw).hexdigest()
    assert receipt["canonical_patch_sha256"] == sha256(first[1]).hexdigest()
    assert len(first[2]) <= 4096
    exported = await cli(d, "export-agent-patch", r["agent_run_id"])
    assert exported == (0, raw, b"")
    assert len(d.provider.calls) == 1
    assert (await d.client.get(f"/agent-runs/{r['agent_run_id']}/trace")).json() == before


@pytest.mark.parametrize(
    "change,reason",
    [
        ("revoked", "source_patch_source_not_active"),
        ("worktree", "source_patch_source_changed"),
        ("hash", "source_patch_source_changed"),
        ("context", "source_patch_context_invalid"),
    ],
)
async def test_cli_fail_closed_without_output_or_candidate_directory(draft, change, reason):
    from labpilot.agent_runtime.models import AgentRunRow

    d = draft
    r = await completed(d, malformed())
    if change == "revoked":
        registration = (await d.source.describe(d.system.snapshot_id)).repository_registration_id
        async with d.system.database.session() as session:
            row = await session.get(RepositoryRegistrationRow, registration)
            row.status = "revoked"
            await session.commit()
    elif change == "worktree":
        (d.system.repository / "train.py").write_bytes(CLIPPED)
    else:
        async with d.system.database.session() as session:
            row = await session.get(AgentRunRow, UUID(r["agent_run_id"]))
            row.source_patch_context = (
                {"profile": "secret-untrusted-metadata"}
                if change == "context"
                else row.source_patch_context | {"train_py_sha256": "0" * 64}
            )
            await session.commit()
    code, stdout, stderr = await cli(d, "normalize-agent-patch", r["agent_run_id"])
    assert code == 1 and stdout == b""
    assert json.loads(stderr) == {"status": "rejected", "detail": reason}
    assert b"secret-untrusted-metadata" not in stderr
    assert len(d.provider.calls) == 1
    if change in {"revoked", "worktree"}:
        code, raw, stderr = await cli(d, "export-agent-patch", r["agent_run_id"])
        assert code == 0 and raw == malformed() and stderr == b""


async def test_cli_invalid_patch_is_not_automatically_repaired_or_submitted(draft):
    d = draft
    raw = patch(CLIPPED).replace(b"train.py", b"other.py")
    r = await completed(d, raw)
    code, stdout, stderr = await cli(d, "normalize-agent-patch", r["agent_run_id"])
    assert code == 1 and stdout == b""
    assert json.loads(stderr) == {"status": "rejected", "detail": "patch_scope_rejected"}
    assert len(d.provider.calls) == 1


async def test_cli_stop_cannot_be_normalized_as_draft(draft):
    d = draft
    d.provider.script = [{"kind": "stop", "reason": "source_patch_drafted"}]
    r = await advance(d, (await create(d)).json())
    code, stdout, stderr = await cli(d, "normalize-agent-patch", r["agent_run_id"])
    assert code == 1 and stdout == b""
    assert json.loads(stderr) == {"status": "rejected", "detail": "source_patch_not_ready"}
