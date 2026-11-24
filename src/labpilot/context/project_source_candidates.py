"""Explicit project-python-v1 import of a local trusted coding-worker receipt.

No Docker/model authority lives here. Receipts are operator attestations with integrity
checks, not cryptographic proof of who ran a test or scientific approval.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import sys
import tempfile
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import Field, ValidationError

from labpilot.common.canonical_json import CanonicalValue
from labpilot.context.repository_identities import validate_repository_relative_path
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.source_candidates import (
    CandidateEntry,
    CandidateError,
    CandidateManifest,
    Digest,
    Ref,
    SourceCandidateService,
    _bytes,
    _Record,
    _run,
)

LIMITS = (10_000, 4 * 1024 * 1024, 128 * 1024 * 1024)


class ProjectSourceRequest(_Record):
    schema_version: Literal["labpilot.source_change_request.v2"] = (
        "labpilot.source_change_request.v2"
    )
    profile: Literal["project-python-v1"] = "project-python-v1"
    project_id: UUID
    snapshot_id: UUID
    snapshot_identity: Digest
    task_ref: Ref
    worker_manifest_ref: Ref
    validation_ref: Ref
    patch_ref: Ref
    checker_sha256: Digest
    rationale: str = Field(min_length=1, max_length=16000)


class ProjectCheckReport(_Record):
    schema_version: Literal["labpilot.source_check_report.v2"] = "labpilot.source_check_report.v2"
    request_ref: Ref
    manifest_ref: Ref
    validation_ref: Ref
    checker_sha256: Digest
    output_refs: tuple[Ref, ...]
    static_checks: Literal["passed"] = "passed"
    runtime_checks: Literal["passed_operator_receipt"] = "passed_operator_receipt"
    scientific_validation: Literal["not_run"] = "not_run"
    human_approval: Literal["not_run"] = "not_run"


class ProjectCandidatePackage(_Record):
    schema_version: Literal["labpilot.candidate_package.v2"] = "labpilot.candidate_package.v2"
    request_ref: Ref
    manifest_ref: Ref
    diff_ref: Ref
    report_ref: Ref


def _identity(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _signed(value: dict[str, Any]) -> None:
    if _identity({k: v for k, v in value.items() if k != "identity"}) != value.get("identity"):
        raise CandidateError("coding_record_identity_mismatch")


def _local_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise CandidateError("coding_record_invalid")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise CandidateError("coding_record_invalid")
    return cast(dict[str, Any], value)


def _local_patch(path: Path) -> bytes:
    if path.is_symlink() or path.stat().st_size > 512 * 1024:
        raise CandidateError("patch_limit_or_empty")
    return path.read_bytes()


class ProjectSourceCandidateService:
    def __init__(self, source: RepositorySnapshotSourceService, root: Path) -> None:
        # Reuse the existing CAS, private-root checks, lock and atomic publication.
        self.records = SourceCandidateService(source, root)
        self.source, self.root, self.store = source, root, self.records.store

    async def _read(self, ref: str, model: type[_Record]) -> _Record:
        try:
            data = await self.store.read_bytes(ref, max_bytes=4 * 1024 * 1024)
            record = model.model_validate_json(data)
            if _bytes(record) != data:
                raise CandidateError("record_not_canonical")
            return record
        except (ValidationError, ValueError) as error:
            raise CandidateError("record_invalid") from error

    async def _tree(self, root: Path, *, persist: bool = False) -> tuple[CandidateEntry, ...]:
        result: list[CandidateEntry] = []
        total = 0
        directories = set()
        for path in sorted(root.rglob("*")):
            name = path.relative_to(root).as_posix()
            validate_repository_relative_path(name)
            mode = path.lstat().st_mode
            # Private content-store directories may inherit their parent's setgid bit.
            special_bits = 0o5000 if stat.S_ISDIR(mode) else 0o7000
            if ".git" in Path(name).parts or stat.S_ISLNK(mode) or mode & special_bits:
                raise CandidateError("tree_type_unsupported")
            if stat.S_ISDIR(mode):
                directories.add(name)
                if len(directories) > LIMITS[0] * 4:
                    raise CandidateError("tree_directory_limit")
                continue
            if (
                not stat.S_ISREG(mode)
                or len(result) >= LIMITS[0]
                or path.stat().st_size > LIMITS[1]
            ):
                raise CandidateError("tree_limit_or_type")
            data = path.read_bytes()
            total += len(data)
            if total > LIMITS[2]:
                raise CandidateError("tree_limit_or_type")
            ref = f"sha256:{sha256(data).hexdigest()}"
            if persist:
                await self.store.put_bytes(data)
            result.append(
                CandidateEntry(
                    path=name, git_mode="100755" if mode & 0o111 else "100644", content_ref=ref
                )
            )
        parents = {str(p) for e in result for p in Path(e.path).parents if str(p) != "."}
        if directories != parents:
            raise CandidateError("tree_directory_mismatch")
        return tuple(result)

    async def import_task(self, task_root: Path, *, actor_id: str) -> str:
        task_root = task_root.resolve(strict=True)
        task = _local_record(task_root / "task.json")
        worker_manifest = _local_record(task_root / "candidate.json")
        validation = _local_record(task_root / "validation.json")
        state = _local_record(task_root / "status.json")
        for record in (task, worker_manifest, validation):
            _signed(record)
        if (
            task.get("schema_version") != "labpilot.coding_task.v1"
            or worker_manifest.get("task_identity") != task["identity"]
            or worker_manifest.get("base_identity") != task["baseline_identity"]
            or validation.get("task_identity") != task["identity"]
            or validation.get("candidate_identity") != worker_manifest["identity"]
            or state.get("task_identity") != task["identity"]
            or state.get("status") != "candidate_ready"
            or state.get("validation_identity") != validation["identity"]
            or validation.get("passed") is not True
        ):
            raise CandidateError("coding_receipt_binding_mismatch")
        binding = task.get("snapshot_binding")
        if not isinstance(binding, dict):
            raise CandidateError("coding_snapshot_binding_required")
        profile = task["profile"]
        validators = profile["validators"]
        checks = validation["checks"]
        if not validators or len(validators) > 4 or len(checks) != len(validators):
            raise CandidateError("coding_validator_mismatch")
        output_refs = []
        for expected, check in zip(validators, checks, strict=True):
            if (
                check.get("image") != expected["image"]
                or check.get("argv") != expected["argv"]
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected["image"])
                or check.get("exit_code") != 0
                or check.get("passed") is not True
                or check.get("source_unchanged") is not True
            ):
                raise CandidateError("coding_validator_mismatch")
            name = check.get("output_file", "")
            if not re.fullmatch(r"validation-[a-f0-9]{32}-[0-3]\.log", name):
                raise CandidateError("coding_validator_mismatch")
            path = task_root / name
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise CandidateError("coding_validator_mismatch")
            output = path.read_bytes()
            if sha256(output).hexdigest() != check.get("output_sha256"):
                raise CandidateError("coding_validator_mismatch")
            output_refs.append(await self.store.put_bytes(output))
        request = ProjectSourceRequest(
            project_id=binding["project_id"],
            snapshot_id=binding["snapshot_id"],
            snapshot_identity=binding["snapshot_identity"],
            task_ref=await self.store.put_bytes(json.dumps(task, sort_keys=True).encode()),
            worker_manifest_ref=await self.store.put_bytes(
                json.dumps(worker_manifest, sort_keys=True).encode()
            ),
            validation_ref=await self.store.put_bytes(
                json.dumps(validation, sort_keys=True).encode()
            ),
            patch_ref=await self.store.put_bytes(_local_patch(task_root / "candidate.diff")),
            checker_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            rationale=task["goal"],
        )
        descriptor = await self.source.describe(request.snapshot_id)
        if (
            descriptor.project_id != request.project_id
            or not descriptor.is_clean
            or descriptor.repository_snapshot_identity != request.snapshot_identity
        ):
            raise CandidateError("base_binding_mismatch")
        patch = await self.store.read_bytes(request.patch_ref, max_bytes=512 * 1024)
        if not patch or b"\0" in patch:
            raise CandidateError("patch_limit_or_empty")
        request_ref = await self.store.put_bytes(_bytes(request))
        async with self.records._lock():
            receipt = self.records._receipt_path("completed", request_ref)
            if receipt.exists():
                package_ref = str(_local_record(receipt)["package_ref"])
                await self.inspect(package_ref)
                return package_ref
            staging = Path(tempfile.mkdtemp(prefix="project-candidate-", dir=self.root))
            destination = staging / "source"
            await self.source.materialize_exact_source(
                repository_snapshot_id=request.snapshot_id,
                expected_repository_snapshot_identity=request.snapshot_identity,
                destination=destination,
                max_entries=LIMITS[0],
                max_file_bytes=LIMITS[1],
                max_total_bytes=LIMITS[2],
            )
            before = await self._tree(destination, persist=True)
            baseline = _local_record(Path(task["baseline"]) / "baseline.json")
            _signed(baseline)
            if baseline["identity"] != task["baseline_identity"]:
                raise CandidateError("coding_base_mismatch")
            base_entries = sorted(
                (e["path"], e["git_mode"], e["sha256"])
                for e in baseline["entries"]
                if not e.get("deleted")
            )
            if base_entries != sorted((e.path, e.git_mode, e.content_ref[7:]) for e in before):
                raise CandidateError("coding_base_mismatch")
            await asyncio.to_thread(
                _run, ["/usr/bin/git", "apply", "--check", "-"], cwd=destination, data=patch
            )
            await asyncio.to_thread(
                _run, ["/usr/bin/git", "apply", "-"], cwd=destination, data=patch
            )
            entries = await self._tree(destination, persist=True)
            old, new = {e.path: e for e in before}, {e.path: e for e in entries}
            changed = [p for p in sorted(set(old) | set(new)) if old.get(p) != new.get(p)]
            if not changed or len(changed) > 20 or changed != worker_manifest["changed_paths"]:
                raise CandidateError("tree_scope_changed")
            for name in changed:
                if not any(fnmatchcase(name, p) for p in profile["editable_paths"]):
                    raise CandidateError("tree_scope_changed")
                if name in old and name in new and old[name].git_mode != new[name].git_mode:
                    raise CandidateError("tree_scope_changed")
                for entry in (old.get(name), new.get(name)):
                    if entry:
                        (await self.store.read_bytes(entry.content_ref)).decode("utf-8")
                if name.endswith(".py") and name in new:
                    await asyncio.to_thread(
                        _run,
                        [
                            sys.executable,
                            "-I",
                            "-c",
                            "import resource,sys;"
                            "resource.setrlimit(resource.RLIMIT_AS,(536870912,)*2);"
                            "resource.setrlimit(resource.RLIMIT_CPU,(2,)*2);compile(sys.stdin.buffer.read(),'candidate','exec')",
                        ],
                        cwd=destination,
                        data=(destination / name).read_bytes(),
                    )
            if any(e["mode"] not in {0o644, 0o755} for e in worker_manifest["entries"]):
                raise CandidateError("tree_type_unsupported")
            expected_tree = sorted(
                (e["path"], "100755" if e["mode"] == 0o755 else "100644", e["sha256"])
                for e in worker_manifest["entries"]
            )
            if expected_tree != sorted((e.path, e.git_mode, e.content_ref[7:]) for e in entries):
                raise CandidateError("candidate_integrity_error")
            # Verify the exported task bytes too, rather than trusting only a ready flag.
            if await self._tree(task_root / "candidate") != entries:
                raise CandidateError("candidate_integrity_error")
            manifest = CandidateManifest(
                request_ref=request_ref, base_identity=request.snapshot_identity, entries=entries
            )
            manifest_ref = await self.store.put_bytes(_bytes(manifest))
            report = ProjectCheckReport(
                request_ref=request_ref,
                manifest_ref=manifest_ref,
                validation_ref=request.validation_ref,
                checker_sha256=request.checker_sha256,
                output_refs=tuple(output_refs),
            )
            package = ProjectCandidatePackage(
                request_ref=request_ref,
                manifest_ref=manifest_ref,
                diff_ref=request.patch_ref,
                report_ref=await self.store.put_bytes(_bytes(report)),
            )
            package_ref = await self.store.put_bytes(_bytes(package))
            await asyncio.to_thread(self._sync_workspace, staging)
            self.records._publish(
                "completed",
                request_ref,
                {"package_ref": package_ref, "workspace": staging.name, "actor_id": actor_id},
            )
        await self.inspect(package_ref)
        return package_ref

    def _sync_workspace(self, staging: Path) -> None:
        for path in staging.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            else:
                self.records._sync_directory(path)
        self.records._sync_directory(staging)

    async def adoption_evidence(
        self, package_ref: str
    ) -> tuple[ProjectSourceRequest, CandidateManifest]:
        await self.inspect(package_ref)
        package = cast(
            ProjectCandidatePackage, await self._read(package_ref, ProjectCandidatePackage)
        )
        request = cast(
            ProjectSourceRequest,
            await self._read(package.request_ref, ProjectSourceRequest),
        )
        if request.checker_sha256 != sha256(Path(__file__).read_bytes()).hexdigest():
            raise CandidateError("checker_version_mismatch")
        manifest = cast(
            CandidateManifest, await self._read(package.manifest_ref, CandidateManifest)
        )
        return request, manifest

    async def inspect(self, package_ref: str) -> dict[str, CanonicalValue]:
        package = cast(
            ProjectCandidatePackage, await self._read(package_ref, ProjectCandidatePackage)
        )
        request = cast(
            ProjectSourceRequest,
            await self._read(package.request_ref, ProjectSourceRequest),
        )
        manifest = cast(
            CandidateManifest, await self._read(package.manifest_ref, CandidateManifest)
        )
        report = cast(ProjectCheckReport, await self._read(package.report_ref, ProjectCheckReport))
        if (
            manifest.request_ref != package.request_ref
            or manifest.base_identity != request.snapshot_identity
            or report.request_ref != package.request_ref
            or report.manifest_ref != package.manifest_ref
            or report.validation_ref != request.validation_ref
            or report.checker_sha256 != request.checker_sha256
            or package.diff_ref != request.patch_ref
        ):
            raise CandidateError("package_binding_mismatch")
        receipt = _local_record(self.records._receipt_path("completed", package.request_ref))
        if receipt["package_ref"] != package_ref or not re.fullmatch(
            r"project-candidate-[A-Za-z0-9_]+", receipt["workspace"]
        ):
            raise CandidateError("package_receipt_mismatch")
        destination = self.root / receipt["workspace"] / "source"
        if (
            destination.is_symlink()
            or destination.parent.is_symlink()
            or await self._tree(destination) != manifest.entries
        ):
            raise CandidateError("candidate_integrity_error")
        for entry in manifest.entries:
            await self.store.read_bytes(entry.content_ref)
        for ref in (
            request.task_ref,
            request.worker_manifest_ref,
            request.validation_ref,
            *report.output_refs,
        ):
            await self.store.read_bytes(ref)
        diff = await self.store.read_bytes(package.diff_ref, max_bytes=512 * 1024)
        return {
            "status": "project_candidate_validated",
            "package_ref": package_ref,
            "request": cast(CanonicalValue, request.model_dump(mode="json")),
            "package": cast(CanonicalValue, package.model_dump(mode="json")),
            "report": cast(CanonicalValue, report.model_dump(mode="json")),
            "diff": diff.decode("utf-8"),
        }
