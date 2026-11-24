"""Manual static source candidates; no execution or scientific authority."""

from __future__ import annotations

import asyncio
import difflib
import fcntl
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.common.hashing import namespaced_sha256_bytes
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.repository_identities import validate_repository_relative_path
from labpilot.context.repository_source import RepositorySnapshotSourceService

Ref = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_PATCH_LIMIT = 64 * 1024
_TREE_LIMIT = 8 * 1024 * 1024
_CHECKER = Path(__file__).with_name("source_candidate_check.py")


class CandidateError(Exception):
    """Fixed reason codes only; never echo patch, environment or subprocess stderr."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Checker(_Record):
    profile: Literal["demo-training-body-v1"] = "demo-training-body-v1"
    checker_sha256: Digest
    git_version: str
    python_version: str


class SourceChangeRequest(_Record):
    schema_version: Literal["labpilot.source_change_request.v1"] = (
        "labpilot.source_change_request.v1"
    )
    project_id: UUID
    snapshot_id: UUID
    snapshot_identity: Digest
    patch_ref: Ref
    rationale: str
    checker: Checker


class CandidateEntry(_Record):
    path: str
    file_type: Literal["regular"] = "regular"
    git_mode: Literal["100644", "100755"]
    content_ref: Ref


class CandidateManifest(_Record):
    schema_version: Literal["labpilot.candidate_source.v1"] = "labpilot.candidate_source.v1"
    request_ref: Ref
    base_identity: Digest
    entries: tuple[CandidateEntry, ...]


class SourceCheckReport(_Record):
    schema_version: Literal["labpilot.source_check_report.v1"] = "labpilot.source_check_report.v1"
    request_ref: Ref
    manifest_ref: Ref
    checker: Checker
    base_binding: Literal["passed"] = "passed"
    patch_applicability: Literal["passed"] = "passed"
    path_and_type: Literal["passed"] = "passed"
    protected_source: Literal["passed"] = "passed"
    syntax: Literal["passed"] = "passed"
    manifest_integrity: Literal["passed"] = "passed"
    runtime_smoke: Literal["not_run"] = "not_run"
    performance: Literal["not_run"] = "not_run"
    scientific_validation: Literal["not_run"] = "not_run"
    human_approval: Literal["not_run"] = "not_run"


class CandidatePackage(_Record):
    schema_version: Literal["labpilot.candidate_package.v1"] = "labpilot.candidate_package.v1"
    request_ref: Ref
    manifest_ref: Ref
    diff_ref: Ref
    report_ref: Ref


def _bytes(record: _Record) -> bytes:
    return canonical_json_bytes(cast(CanonicalValue, record.model_dump(mode="json")))


def _run(argv: list[str], *, cwd: Path, data: bytes = b"") -> bytes:
    # ponytail: fixed Linux tools and one bounded demo profile; no plugin/command registry.
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(cwd),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CEILING_DIRECTORIES": str(cwd.parent),
    }
    try:
        result = subprocess.run(
            argv,
            input=data,
            cwd=cwd,
            env=environment,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidateError("static_tool_unavailable_or_timeout") from exc
    if result.returncode != 0 or len(result.stdout) + len(result.stderr) > 256 * 1024:
        raise CandidateError("static_check_rejected")
    return result.stdout


def _checker(cwd: Path) -> Checker:
    return Checker(
        checker_sha256=namespaced_sha256_bytes(
            "labpilot.static-checker.v1",
            canonical_json_bytes(
                {
                    "worker": sha256(_CHECKER.read_bytes()).hexdigest(),
                    "service": sha256(Path(__file__).read_bytes()).hexdigest(),
                }
            ),
        ),
        git_version=_run(["/usr/bin/git", "--version"], cwd=cwd).decode().strip(),
        python_version=platform.python_version(),
    )


def _patch_scope(patch: bytes) -> None:
    if not patch or len(patch) > _PATCH_LIMIT or b"\0" in patch:
        raise CandidateError("patch_limit_or_empty")
    lines = patch.splitlines()
    if [line for line in lines if line.startswith(b"diff --git ")] != [
        b"diff --git a/train.py b/train.py"
    ] or lines[0] != b"diff --git a/train.py b/train.py":
        raise CandidateError("patch_scope_rejected")
    if [line for line in lines if line.startswith(b"--- ")] != [b"--- a/train.py"]:
        raise CandidateError("patch_scope_rejected")
    if [line for line in lines if line.startswith(b"+++ ")] != [b"+++ b/train.py"]:
        raise CandidateError("patch_scope_rejected")
    forbidden = (
        b"old mode ",
        b"new mode ",
        b"new file ",
        b"deleted file ",
        b"rename ",
        b"copy ",
        b"similarity ",
        b"dissimilarity ",
        b"GIT binary patch",
        b"Binary files ",
    )
    if any(line.startswith(forbidden) for line in lines):
        raise CandidateError("patch_scope_rejected")


class SourceCandidateService:
    def __init__(self, source: RepositorySnapshotSourceService, root: Path) -> None:
        if not root.is_absolute() or root.resolve() != root:
            raise CandidateError("record_root_invalid")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise CandidateError("record_root_not_private")
        self.source = source
        self.root = root
        self.store = ContentAddressedStore(root / "blobs")

    async def _base(self, request: SourceChangeRequest) -> None:
        descriptor = await self.source.describe(request.snapshot_id)
        if (
            descriptor.project_id != request.project_id
            or not descriptor.is_clean
            or descriptor.repository_snapshot_identity != request.snapshot_identity
        ):
            raise CandidateError("base_binding_mismatch")
        tree = await self.source.list_tree(request.snapshot_id, prefix=None, max_entries=129)
        if (
            not tree
            or len(tree) > 128
            or not any(item.relative_path == "train.py" for item in tree)
        ):
            raise CandidateError("base_profile_limit")
        for item in tree:
            validate_repository_relative_path(item.relative_path)
            if (
                ".git" in Path(item.relative_path).parts
                or item.file_type != "regular"
                or item.file_mode not in {"100644", "100755"}
            ):
                raise CandidateError("base_profile_unsupported")

    async def submit(
        self,
        *,
        project_id: UUID,
        snapshot_id: UUID,
        snapshot_identity: str,
        patch: bytes,
        rationale: str,
        actor_id: str,
    ) -> str:
        _patch_scope(patch)
        if not rationale.strip() or len(rationale.encode("utf-8")) > 1000:
            raise CandidateError("rationale_invalid")
        request = SourceChangeRequest(
            project_id=project_id,
            snapshot_id=snapshot_id,
            snapshot_identity=snapshot_identity,
            patch_ref=f"sha256:{sha256(patch).hexdigest()}",
            rationale=rationale,
            checker=await asyncio.to_thread(_checker, self.root),
        )
        await self._base(request)
        await self.store.put_bytes(patch)
        ref = await self.store.put_bytes(_bytes(request))
        async with self._lock():
            self._publish(
                "submission",
                ref,
                {
                    "request_ref": ref,
                    "actor_id": actor_id,
                    "submitted_at": datetime.now(UTC).isoformat(),
                },
            )
        return ref

    @asynccontextmanager
    async def _lock(self) -> AsyncIterator[None]:
        # ponytail: one installation lock; per-request locks only if local throughput matters.
        with (self.root / ".lock").open("a+b") as stream:
            async with asyncio.timeout(10):
                while True:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _receipt_path(self, kind: str, ref: str) -> Path:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", ref) is None:
            raise CandidateError("ref_invalid")
        return self.root / f"{kind}-{ref[7:]}.json"

    def _publish(self, kind: str, ref: str, value: dict[str, CanonicalValue]) -> None:
        path = self._receipt_path(kind, ref)
        if path.exists():
            return
        data = canonical_json_bytes(value)
        descriptor, temporary = tempfile.mkstemp(dir=self.root, prefix=".receipt-")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def _read(self, ref: str, model: type[_Record]) -> _Record:
        try:
            data = await self.store.read_bytes(ref)
            if len(data) > 256 * 1024:
                raise CandidateError("record_limit")
            record = model.model_validate_json(data)
            if _bytes(record) != data:
                raise CandidateError("record_not_canonical")
            return record
        except (ValidationError, ValueError) as exc:
            raise CandidateError("record_invalid") from exc

    async def prepare(self, request_ref: str) -> str:
        request = cast(SourceChangeRequest, await self._read(request_ref, SourceChangeRequest))
        async with self._lock():
            receipt_path = self._receipt_path("completed", request_ref)
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_bytes())
                package = cast(
                    CandidatePackage, await self._read(receipt["package_ref"], CandidatePackage)
                )
                if package.request_ref != request_ref:
                    raise CandidateError("package_request_mismatch")
                await self.inspect(receipt["package_ref"])
                return str(receipt["package_ref"])
            if request.checker != await asyncio.to_thread(_checker, self.root):
                raise CandidateError("checker_version_mismatch")
            await self._base(request)
            patch = await self.store.read_bytes(request.patch_ref)
            _patch_scope(patch)
            staging = Path(tempfile.mkdtemp(prefix="candidate-", dir=self.root))
            # Existing destination means verify-only, so do not pre-create it.
            destination = staging / "source"
            materialized = await self.source.materialize_exact_source(
                repository_snapshot_id=request.snapshot_id,
                expected_repository_snapshot_identity=request.snapshot_identity,
                destination=destination,
                max_entries=128,
                max_file_bytes=_TREE_LIMIT,
                max_total_bytes=_TREE_LIMIT,
            )
            before_entries = await self._tree(destination)
            expected = {
                (e.relative_path, e.file_mode, e.content_sha256) for e in materialized.entries
            }
            if {(e.path, e.git_mode, e.content_ref[7:]) for e in before_entries} != expected:
                raise CandidateError("base_materialization_mismatch")
            before = (destination / "train.py").read_bytes()
            await asyncio.to_thread(self._apply_and_check, destination, patch, before)
            entries = await self._tree(destination, persist=True)
            old = {e.path: e for e in before_entries}
            if (
                set(old) != {e.path for e in entries}
                or any(e != old[e.path] for e in entries if e.path != "train.py")
                or any(e.git_mode != old[e.path].git_mode for e in entries)
            ):
                raise CandidateError("tree_scope_changed")
            after = (destination / "train.py").read_bytes()
            if before == after:
                raise CandidateError("no_op_patch")
            manifest = CandidateManifest(
                request_ref=request_ref,
                base_identity=request.snapshot_identity,
                entries=entries,
            )
            manifest_ref = await self.store.put_bytes(_bytes(manifest))
            diff = "".join(
                difflib.unified_diff(
                    before.decode().splitlines(keepends=True),
                    after.decode().splitlines(keepends=True),
                    fromfile="a/train.py",
                    tofile="b/train.py",
                )
            ).encode()
            report = SourceCheckReport(
                request_ref=request_ref,
                manifest_ref=manifest_ref,
                checker=request.checker,
            )
            package = CandidatePackage(
                request_ref=request_ref,
                manifest_ref=manifest_ref,
                diff_ref=await self.store.put_bytes(diff),
                report_ref=await self.store.put_bytes(_bytes(report)),
            )
            package_ref = await self.store.put_bytes(_bytes(package))
            # Persist source files as well as CAS refs before publishing the ready receipt.
            for path in destination.rglob("*"):
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
                else:
                    self._sync_directory(path)
            self._sync_directory(destination)
            self._sync_directory(staging)
            self._publish(
                "completed",
                request_ref,
                {
                    "package_ref": package_ref,
                    "workspace": staging.name,
                },
            )
            return package_ref

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _apply_and_check(destination: Path, patch: bytes, before: bytes) -> None:
        if len(before) > 128 * 1024:
            raise CandidateError("source_limit")
        _run(["/usr/bin/git", "apply", "--check", "-"], cwd=destination, data=patch)
        _run(["/usr/bin/git", "apply", "-"], cwd=destination, data=patch)
        after = (destination / "train.py").read_bytes()
        if len(after) > 128 * 1024:
            raise CandidateError("source_limit")
        _run(
            [sys.executable, "-I", str(_CHECKER)],
            cwd=destination,
            data=json.dumps({"before": before.hex(), "after": after.hex()}).encode(),
        )

    async def _tree(
        self, destination: Path, *, persist: bool = False
    ) -> tuple[CandidateEntry, ...]:
        entries: list[CandidateEntry] = []
        total = 0
        directories: set[str] = set()
        for path in sorted(destination.rglob("*")):
            mode = path.lstat().st_mode
            relative = path.relative_to(destination).as_posix()
            validate_repository_relative_path(relative)
            if ".git" in Path(relative).parts or stat.S_ISLNK(mode):
                raise CandidateError("tree_type_unsupported")
            if stat.S_ISDIR(mode):
                directories.add(relative)
                if len(directories) > 128:
                    raise CandidateError("tree_directory_limit")
                continue
            if not stat.S_ISREG(mode) or len(entries) >= 128 or path.stat().st_size > _TREE_LIMIT:
                raise CandidateError("tree_limit_or_type")
            data = path.read_bytes()
            total += len(data)
            if total > _TREE_LIMIT:
                raise CandidateError("tree_limit_or_type")
            ref = f"sha256:{sha256(data).hexdigest()}"
            if persist:
                await self.store.put_bytes(data)
            entries.append(
                CandidateEntry(
                    path=relative,
                    git_mode="100755" if mode & 0o111 else "100644",
                    content_ref=ref,
                )
            )
        parents = {
            str(parent)
            for entry in entries
            for parent in Path(entry.path).parents
            if str(parent) != "."
        }
        if directories != parents:
            raise CandidateError("tree_directory_mismatch")
        return tuple(entries)

    async def adoption_evidence(
        self, package_ref: str
    ) -> tuple[SourceChangeRequest, CandidateManifest]:
        """First-adoption evidence; ordinary inspect still accepts historical checker reports."""
        await self.inspect(package_ref)
        package = cast(CandidatePackage, await self._read(package_ref, CandidatePackage))
        request = cast(
            SourceChangeRequest, await self._read(package.request_ref, SourceChangeRequest)
        )
        if request.checker != await asyncio.to_thread(_checker, self.root):
            raise CandidateError("checker_version_mismatch")
        manifest = cast(
            CandidateManifest, await self._read(package.manifest_ref, CandidateManifest)
        )
        return request, manifest

    async def inspect(self, package_ref: str) -> dict[str, CanonicalValue]:
        package = cast(CandidatePackage, await self._read(package_ref, CandidatePackage))
        request = cast(
            SourceChangeRequest, await self._read(package.request_ref, SourceChangeRequest)
        )
        manifest = cast(
            CandidateManifest, await self._read(package.manifest_ref, CandidateManifest)
        )
        report = cast(SourceCheckReport, await self._read(package.report_ref, SourceCheckReport))
        if (
            manifest.request_ref != package.request_ref
            or manifest.base_identity != request.snapshot_identity
            or report.request_ref != package.request_ref
            or report.manifest_ref != package.manifest_ref
            or report.checker != request.checker
        ):
            raise CandidateError("package_binding_mismatch")
        receipt = json.loads(self._receipt_path("completed", package.request_ref).read_bytes())
        if (
            receipt["package_ref"] != package_ref
            or re.fullmatch(r"candidate-[A-Za-z0-9_]+", receipt["workspace"]) is None
        ):
            raise CandidateError("package_receipt_mismatch")
        destination = self.root / receipt["workspace"] / "source"
        if destination.is_symlink() or destination.parent.is_symlink():
            raise CandidateError("workspace_invalid")
        if await self._tree(destination) != manifest.entries:
            raise CandidateError("candidate_integrity_error")
        for entry in manifest.entries:
            await self.store.read_bytes(entry.content_ref)
        await self.store.read_bytes(request.patch_ref)
        diff = await self.store.read_bytes(package.diff_ref)
        if len(diff) > 256 * 1024:
            raise CandidateError("diff_limit")
        return {
            "status": "static_checks_passed_candidate_not_executed",
            "package_ref": package_ref,
            "request": cast(CanonicalValue, request.model_dump(mode="json")),
            "candidate_identity": namespaced_sha256_bytes(
                "labpilot.candidate-source.v1", _bytes(manifest)
            ),
            "package": cast(CanonicalValue, package.model_dump(mode="json")),
            "report": cast(CanonicalValue, report.model_dump(mode="json")),
            "diff": diff.decode("utf-8"),
        }
