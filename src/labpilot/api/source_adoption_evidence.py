"""Read immutable accepted evidence, independent of workspace and current checker."""

import json
from typing import cast

from pydantic import BaseModel, JsonValue

from labpilot.agent_runtime.source_binding import MAX_SOURCE_DIFF_BYTES, SourceChangeEvidence
from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.context.content_store import (
    ContentAddressedStore,
    ContentBlobMissing,
    ContentReferenceInvalid,
    ContentStoreCorruption,
)
from labpilot.context.project_source_candidates import (
    ProjectCandidatePackage,
    ProjectCheckReport,
    ProjectSourceRequest,
)
from labpilot.context.source_candidates import (
    CandidateManifest,
    CandidatePackage,
    SourceChangeRequest,
    SourceCheckReport,
)


async def _record[RecordT: BaseModel](
    store: ContentAddressedStore, ref: str, model: type[RecordT], *, max_bytes: int = 256 * 1024
) -> RecordT:
    data = await store.read_bytes(ref, max_bytes=max_bytes)
    record = model.model_validate_json(data)
    if canonical_json_bytes(cast(CanonicalValue, record.model_dump(mode="json"))) != data:
        raise AgentRuntimeError("source_adoption_evidence_invalid")
    return record


async def read_adoption_evidence(
    store: ContentAddressedStore,
    *,
    package_ref: str,
    project_id: str,
    base_id: str,
    base_identity: str,
) -> SourceChangeEvidence:
    try:
        package: CandidatePackage | ProjectCandidatePackage
        request: SourceChangeRequest | ProjectSourceRequest
        report: SourceCheckReport | ProjectCheckReport
        raw = await store.read_bytes(package_ref, max_bytes=256 * 1024)
        header = json.loads(raw)
        if not isinstance(header, dict):
            raise AgentRuntimeError("source_adoption_evidence_invalid")
        is_project = header.get("schema_version") == "labpilot.candidate_package.v2"
        if is_project:
            package = await _record(store, package_ref, ProjectCandidatePackage)
            request = await _record(store, package.request_ref, ProjectSourceRequest)
            report = await _record(store, package.report_ref, ProjectCheckReport)
            checker_matches = (
                report.checker_sha256 == request.checker_sha256
                and report.validation_ref == request.validation_ref
                and package.diff_ref == request.patch_ref
            )
        else:
            package = await _record(store, package_ref, CandidatePackage)
            request = await _record(store, package.request_ref, SourceChangeRequest)
            report = await _record(store, package.report_ref, SourceCheckReport)
            checker_matches = report.checker == request.checker
        manifest = await _record(
            store,
            package.manifest_ref,
            CandidateManifest,
            max_bytes=4 * 1024 * 1024 if is_project else 256 * 1024,
        )
        if (
            str(request.project_id) != project_id
            or str(request.snapshot_id) != base_id
            or request.snapshot_identity != base_identity
            or manifest.base_identity != base_identity
            or manifest.request_ref != package.request_ref
            or report.request_ref != package.request_ref
            or report.manifest_ref != package.manifest_ref
            or not checker_matches
        ):
            raise AgentRuntimeError("source_adoption_evidence_invalid")
        diff = (await store.read_bytes(package.diff_ref, max_bytes=MAX_SOURCE_DIFF_BYTES)).decode(
            "utf-8", "strict"
        )
        return SourceChangeEvidence(
            diff_ref=package.diff_ref,
            report_ref=package.report_ref,
            rationale=request.rationale,
            diff=diff,
            static_report=cast(dict[str, JsonValue], report.model_dump(mode="json")),
        )
    except (
        OSError,
        ValueError,
        AgentRuntimeError,
        ContentBlobMissing,
        ContentStoreCorruption,
        ContentReferenceInvalid,
    ) as exc:
        reason = (
            "source_adoption_evidence_too_large"
            if isinstance(exc, ContentStoreCorruption) and exc.reason_code == "blob_size_limit"
            else "source_adoption_evidence_invalid"
        )
        raise AgentRuntimeError(reason) from None
