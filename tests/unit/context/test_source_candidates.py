import asyncio
import difflib
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from labpilot.context.repository_source import (
    RepositoryMaterialization,
    RepositoryMaterializedEntry,
    RepositoryPathFact,
)
from labpilot.context.source_candidates import CandidateError, SourceCandidateService

BASE = b"""raise RuntimeError("Candidate must never be imported")

def train_model(training, *, learning_rate, epochs, seed):
    gradient = sum(training)
    return learning_rate * gradient

def accuracy():
    return 0.92
"""
CLIPPED = BASE.replace(
    b"    return learning_rate",
    b"    gradient = max(-1.0, min(1.0, gradient))\n    return learning_rate",
)


def patch(after: bytes, before: bytes = BASE) -> bytes:
    return (
        b"diff --git a/train.py b/train.py\n"
        + "".join(
            difflib.unified_diff(
                before.decode().splitlines(keepends=True),
                after.decode().splitlines(keepends=True),
                fromfile="a/train.py",
                tofile="b/train.py",
            )
        ).encode()
    )


def fixture(tmp_path: Path):
    project, snapshot = uuid4(), uuid4()
    source = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                project_id=project,
                is_clean=True,
                repository_snapshot_identity="a" * 64,
            )
        ),
        list_tree=AsyncMock(return_value=(RepositoryPathFact("train.py", "regular", "100644"),)),
    )

    async def materialize(*, repository_snapshot_id, destination, **kwargs):
        assert repository_snapshot_id == snapshot and not destination.exists()
        assert kwargs["max_total_bytes"] == 8 * 1024 * 1024
        destination.mkdir()
        (destination / "train.py").write_bytes(BASE)
        return RepositoryMaterialization(
            "v1",
            snapshot,
            "a" * 64,
            (
                RepositoryMaterializedEntry(
                    "train.py", "regular", "100644", sha256(BASE).hexdigest()
                ),
            ),
        )

    source.materialize_exact_source = AsyncMock(side_effect=materialize)
    service = SourceCandidateService(source, tmp_path / "records")
    return (
        service,
        source,
        dict(
            project_id=project,
            snapshot_id=snapshot,
            snapshot_identity="a" * 64,
            rationale="clip gradient",
            actor_id="user",
        ),
    )


async def test_static_clipping_idempotent_and_never_imports(tmp_path: Path):
    service, source, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(CLIPPED), **arguments)
    assert await service.submit(patch=patch(CLIPPED), **arguments) == ref
    first, second = await asyncio.gather(service.prepare(ref), service.prepare(ref))
    assert first == second
    source.materialize_exact_source.assert_awaited_once()
    view = await service.inspect(first)
    assert "gradient = max" in view["diff"]
    assert view["report"]["protected_source"] == "passed"
    assert view["report"]["runtime_smoke"] == "not_run"
    assert view["report"]["scientific_validation"] == "not_run"
    assert view["report"]["human_approval"] == "not_run"
    workspace = next(service.root.glob("candidate-*/source"))
    (workspace / "train.py").chmod(0o444)
    assert await service.inspect(first) == view  # Logical Git mode ignores writable bits.
    (workspace / "train.py").chmod(0o644)
    (workspace / "train.py").write_bytes(BASE)
    with pytest.raises(CandidateError, match="candidate_integrity_error"):
        await service.inspect(first)


@pytest.mark.parametrize(
    "replacement",
    [
        b"../train.py",
        b"/train.py",
        b"other.py",
        b".git/config",
    ],
)
async def test_reject_paths_before_materialization(tmp_path: Path, replacement: bytes):
    service, source, arguments = fixture(tmp_path)
    with pytest.raises(CandidateError, match="patch_scope_rejected"):
        await service.submit(patch=patch(CLIPPED).replace(b"train.py", replacement), **arguments)
    source.describe.assert_not_awaited()
    assert not list(service.root.glob("candidate-*"))


@pytest.mark.parametrize(
    "after",
    [
        CLIPPED.replace(b"def accuracy()", b"def other()"),
        CLIPPED.replace(b"epochs, seed", b"epochs, seed=0"),
        CLIPPED.replace(b"    return 0.92", b"    return 1.0"),
        CLIPPED.replace(b"    gradient = sum(training)", b"    gradient = ("),
        CLIPPED.replace(b"def accuracy()", b"def train_model()"),
        CLIPPED.replace(b"sum(training)", b"(" * 500 + b"1" + b")" * 500),
    ],
)
async def test_protected_or_invalid_source_never_published(tmp_path: Path, after: bytes):
    service, _, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(after), **arguments)
    with pytest.raises(CandidateError, match="static_check_rejected"):
        await service.prepare(ref)
    assert not list(service.root.glob("completed-*"))


@pytest.mark.parametrize(
    "metadata",
    [
        b"old mode 100644\nnew mode 100755\n",
        b"new file mode 100644\n",
        b"rename from train.py\n",
        b"GIT binary patch\n",
    ],
)
async def test_mode_rename_binary_rejected(tmp_path: Path, metadata: bytes):
    service, _, arguments = fixture(tmp_path)
    value = patch(CLIPPED).replace(b"--- a/train.py", metadata + b"--- a/train.py")
    with pytest.raises(CandidateError):
        await service.submit(patch=value, **arguments)


async def test_noop_and_bad_hunk_rejected(tmp_path: Path):
    service, _, arguments = fixture(tmp_path)
    lines = BASE.decode().splitlines(keepends=True)
    value = (
        b"diff --git a/train.py b/train.py\n--- a/train.py\n+++ b/train.py\n"
        + f"@@ -1,{len(lines)} +1,{len(lines)} @@\n".encode()
        + "".join(
            ("-" + line + "+" + line) if "gradient = sum" in line else " " + line for line in lines
        ).encode()
    )
    ref = await service.submit(patch=value, **arguments)
    with pytest.raises(CandidateError, match="no_op_patch"):
        await service.prepare(ref)
    ref = await service.submit(
        patch=patch(CLIPPED).replace(b"sum(training)", b"sum(missing)"), **arguments
    )
    with pytest.raises(CandidateError, match="static_check_rejected"):
        await service.prepare(ref)
    assert not list(service.root.glob("completed-*"))


async def test_failed_publish_fresh_rebuild_and_blob_tampering(tmp_path: Path, monkeypatch):
    service, source, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(CLIPPED), **arguments)
    publish = service._publish

    def fail(*args):
        raise OSError("simulated interruption")

    monkeypatch.setattr(service, "_publish", fail)
    with pytest.raises(OSError):
        await service.prepare(ref)
    monkeypatch.setattr(service, "_publish", publish)
    result = await service.prepare(ref)
    assert source.materialize_exact_source.await_count == 2
    assert len(list(service.root.glob("candidate-*"))) == 2
    view = await service.inspect(result)
    report_ref = view["package"]["report_ref"]
    path = service.root / "blobs/sha256" / report_ref[7:9] / report_ref[9:]
    path.write_bytes(b'{"runtime_smoke":"passed"}')
    with pytest.raises(Exception, match="blob_digest_mismatch"):
        await service.inspect(result)


async def test_dynamic_globals_allowed_as_static_scope_only(tmp_path: Path):
    service, _, arguments = fixture(tmp_path)
    marker = tmp_path / "must-not-exist"
    after = BASE.replace(
        b"    gradient = sum(training)",
        f"    globals()['accuracy'] = lambda: 1.0\n    open({str(marker)!r}, 'w')\n".encode()
        + b"    gradient = sum(training)",
    )
    ref = await service.submit(patch=patch(after), **arguments)
    view = await service.inspect(await service.prepare(ref))
    assert view["report"]["runtime_smoke"] == "not_run"
    assert not marker.exists()


async def test_bad_base_limits_and_refs(tmp_path: Path):
    service, source, arguments = fixture(tmp_path)
    for change in [dict(project_id=uuid4()), dict(snapshot_identity="b" * 64)]:
        with pytest.raises(CandidateError, match="base_binding_mismatch"):
            await service.submit(patch=patch(CLIPPED), **(arguments | change))
    for value in [b"", b"x" * (64 * 1024 + 1)]:
        with pytest.raises(CandidateError):
            await service.submit(patch=value, **arguments)
    source.describe.return_value.is_clean = False
    with pytest.raises(CandidateError):
        await service.submit(patch=patch(CLIPPED), **arguments)
    source.describe.return_value.is_clean = True
    source.list_tree.return_value = (RepositoryPathFact("train.py", "symlink", "120000"),)
    with pytest.raises(CandidateError, match="base_profile_unsupported"):
        await service.submit(patch=patch(CLIPPED), **arguments)


async def test_inspect_rejects_extra_directory_and_symlink(tmp_path: Path):
    service, _, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(CLIPPED), **arguments)
    result = await service.prepare(ref)
    workspace = next(service.root.glob("candidate-*/source"))
    (workspace / "extra").mkdir()
    with pytest.raises(CandidateError, match="tree_directory_mismatch"):
        await service.inspect(result)
    (workspace / "extra").rmdir()
    (workspace / "link").symlink_to(tmp_path)
    with pytest.raises(CandidateError, match="tree_type_unsupported"):
        await service.inspect(result)


async def test_request_corruption_never_rebuilt(tmp_path: Path):
    service, _, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(CLIPPED), **arguments)
    path = service.root / "blobs/sha256" / ref[7:9] / ref[9:]
    value = json.loads(path.read_bytes())
    value["snapshot_identity"] = "b" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(Exception, match="blob_digest_mismatch"):
        await service.prepare(ref)
    assert not list(service.root.glob("candidate-*"))


def test_cli_redacts_secret_configuration(tmp_path: Path):
    import os
    import subprocess
    import sys

    sentinel = "private-db-password-marker"
    environment = os.environ | {"LABPILOT_DATABASE_URL": sentinel}
    result = subprocess.run(
        [
            sys.executable,
            "scripts/source_candidate.py",
            "inspect",
            "--package-ref",
            "sha256:" + "a" * 64,
        ],
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode == 1
    assert sentinel.encode() not in result.stdout + result.stderr
    assert b"static_candidate_failed" in result.stderr


async def test_checker_version_change_requires_new_submission(tmp_path: Path, monkeypatch):
    import labpilot.context.source_candidates as module

    service, source, arguments = fixture(tmp_path)
    ref = await service.submit(patch=patch(CLIPPED), **arguments)
    original = module._checker
    monkeypatch.setattr(
        module,
        "_checker",
        lambda cwd: original(cwd).model_copy(update={"checker_sha256": "b" * 64}),
    )
    with pytest.raises(CandidateError, match="checker_version_mismatch"):
        await service.prepare(ref)
    source.materialize_exact_source.assert_not_awaited()


@pytest.mark.parametrize("statement", [b"break", b"continue", b"nonlocal missing_binding"])
async def test_ast_valid_but_uncompilable_candidate_rejected(tmp_path: Path, statement: bytes):
    import ast

    service, _, arguments = fixture(tmp_path)
    after = BASE.replace(b"    gradient = sum(training)", b"    " + statement)
    ast.parse(after)  # Grammar alone accepts these; no candidate execution.
    ref = await service.submit(patch=patch(after), **arguments)
    with pytest.raises(CandidateError, match="static_check_rejected"):
        await service.prepare(ref)
    assert not list(service.root.glob("completed-*"))


async def test_replay_rejects_other_requests_completed_package(tmp_path: Path):
    service, source, arguments = fixture(tmp_path)
    first = await service.submit(patch=patch(CLIPPED), **arguments)
    second = await service.submit(
        patch=patch(CLIPPED), **(arguments | {"rationale": "another request"})
    )
    first_package = await service.prepare(first)
    second_package = await service.prepare(second)
    assert first_package != second_package
    first_receipt = service.root / f"completed-{first[7:]}.json"
    second_receipt = service.root / f"completed-{second[7:]}.json"
    first_receipt.write_bytes(second_receipt.read_bytes())
    source.materialize_exact_source.reset_mock()
    with pytest.raises(CandidateError, match="package_request_mismatch"):
        await service.prepare(first)
    source.materialize_exact_source.assert_not_awaited()
    assert (await service.inspect(second_package))["package_ref"] == second_package


@pytest.mark.parametrize(
    "kind,detail",
    [
        ("candidate", "base_binding_mismatch"),
        ("missing", "content_missing"),
        ("unknown", "static_candidate_failed"),
        ("candidate_unknown", "static_candidate_failed"),
        ("corrupt", "content_integrity_error"),
    ],
)
def test_cli_known_failure_codes_and_unknown_secret_redaction(monkeypatch, capsys, kind, detail):
    import sys

    from labpilot.context.content_store import ContentBlobMissing, ContentStoreCorruption
    from scripts import source_candidate as cli

    secret = "private-exception-message-marker"

    async def fail(arguments):
        if kind == "candidate":
            raise CandidateError("base_binding_mismatch")
        if kind == "missing":
            raise ContentBlobMissing("blob_missing")
        if kind == "candidate_unknown":
            raise CandidateError(secret)
        if kind == "corrupt":
            raise ContentStoreCorruption(secret)
        raise RuntimeError(secret)

    monkeypatch.setattr(cli, "run", fail)
    monkeypatch.setattr(
        sys, "argv", ["source_candidate", "prepare", "--request-ref", "sha256:" + "a" * 64]
    )
    assert cli.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {"status": "rejected", "detail": detail}
    assert secret not in output.err + output.out
