"""Local manual source CLI. Patch bytes arrive on stdin; paths come from Settings."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.api.source_patch_adapter import export_agent_patch, normalize_agent_patch
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import (
    ContentAddressedStore,
    ContentBlobMissing,
    ContentReferenceInvalid,
    ContentStoreCorruption,
)
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.project_source_candidates import ProjectSourceCandidateService
from labpilot.context.repository_service import RepositoryApplicationService
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot, RegisterLocalRepository
from labpilot.context.source_adoption import AdoptionError, AdoptSource, SourceAdoptionService
from labpilot.context.source_candidates import CandidateError, SourceCandidateService
from labpilot.db.idempotency import IdempotencyKeyConflict
from labpilot.db.session import Database
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.project_types import CreateProject
from labpilot.settings import Settings


async def run(
    arguments: argparse.Namespace,
) -> bytes | tuple[bytes, dict[str, str]] | None:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        if arguments.command == "export-agent-patch":
            return await export_agent_patch(database.session, arguments.agent_run_id)
        source = RepositorySnapshotSourceService(
            database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=5),
            content_store=ContentAddressedStore(settings.repository_content_store_root),
        )
        if arguments.command == "normalize-agent-patch":
            return await normalize_agent_patch(database.session, source, arguments.agent_run_id)
        if arguments.command in {"create-project", "register-repository", "capture-snapshot"}:
            context = CommandContext(
                settings.installation_identity,
                CommandActor("user", settings.local_actor_id),
                "source-setup-cli",
            )
            repositories = RepositoryApplicationService(
                database.session,
                git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=5),
                allowed_roots=settings.repository_allowed_roots,
                content_store=ContentAddressedStore(settings.repository_content_store_root),
            )
            if arguments.command == "create-project":
                outcome = await ProjectApplicationService(database.session).create_project(
                    context=context,
                    idempotency_key=arguments.idempotency_key,
                    command=CreateProject(arguments.name, None, arguments.artifact_root_ref),
                )
            elif arguments.command == "register-repository":
                outcome = await repositories.register_local_repository(
                    context=context,
                    idempotency_key=arguments.idempotency_key,
                    command=RegisterLocalRepository(
                        arguments.project_id,
                        arguments.repository,
                        arguments.expected_project_row_version,
                    ),
                )
            else:
                outcome = await repositories.capture_repository_snapshot(
                    context=context,
                    idempotency_key=arguments.idempotency_key,
                    command=CaptureRepositorySnapshot(arguments.registration_id),
                )
            result = {**outcome.envelope.to_json(), "replayed": outcome.replayed}
            if arguments.command == "capture-snapshot":
                descriptor = await source.describe(UUID(outcome.envelope.primary_result_id))
                result["is_clean"] = descriptor.is_clean
            print(json.dumps(result))
            return None
        candidates: SourceCandidateService | ProjectSourceCandidateService = SourceCandidateService(
            source,
            settings.repository_content_store_root / "source-candidates",
        )
        if arguments.profile == "project-python-v1":
            project_candidates = ProjectSourceCandidateService(
                source, settings.repository_content_store_root / "source-candidates"
            )
            if arguments.command == "import-task":
                ref = await project_candidates.import_task(
                    arguments.task, actor_id=settings.local_actor_id
                )
                print(json.dumps({"package_ref": ref, "status": "validated_candidate"}))
                return None
            if arguments.command not in {"inspect", "adopt"}:
                raise CandidateError("project_profile_command_invalid")
            candidates = project_candidates
        elif arguments.command == "import-task":
            raise CandidateError("project_profile_required")
        if arguments.command == "submit":
            assert isinstance(candidates, SourceCandidateService)
            ref = await candidates.submit(
                project_id=arguments.project_id,
                snapshot_id=arguments.snapshot_id,
                snapshot_identity=arguments.snapshot_identity,
                patch=sys.stdin.buffer.read(64 * 1024 + 1),
                rationale=arguments.rationale,
                actor_id=settings.local_actor_id,
            )
            print(json.dumps({"request_ref": ref, "status": "submitted_static_only"}))
        elif arguments.command == "prepare":
            assert isinstance(candidates, SourceCandidateService)
            ref = await candidates.prepare(arguments.request_ref)
            print(json.dumps({"package_ref": ref, "status": "prepared_static_only"}))
        elif arguments.command == "adopt":
            outcome = await SourceAdoptionService(database.session, candidates).adopt(
                context=CommandContext(
                    installation_identity=settings.installation_identity,
                    actor=CommandActor("user", settings.local_actor_id),
                    interaction_correlation_id="source-adoption-cli",
                ),
                idempotency_key=arguments.idempotency_key,
                command=AdoptSource(
                    project_id=arguments.project_id,
                    package_ref=arguments.package_ref,
                    target_snapshot_id=arguments.target_snapshot_id,
                    target_snapshot_identity=arguments.target_snapshot_identity,
                ),
            )
            print(
                json.dumps(
                    {
                        "status": "source_adoption_recorded_not_approved",
                        "replayed": outcome.replayed,
                        **outcome.envelope.to_json(),
                    }
                )
            )
        else:
            print(json.dumps(await candidates.inspect(arguments.package_ref), ensure_ascii=False))
        return None
    finally:
        await database.dispose()


# Closed public vocabulary: arbitrary exception text must never reach diagnostics.
_SAFE_CANDIDATE_DETAILS = frozenset(
    {
        "project_profile_command_invalid",
        "project_profile_required",
        "coding_record_identity_mismatch",
        "coding_record_invalid",
        "coding_receipt_binding_mismatch",
        "coding_snapshot_binding_required",
        "coding_validator_mismatch",
        "coding_base_mismatch",
        "normalization_text_invalid",
        "patch_normalization_failed",
        "base_binding_mismatch",
        "base_profile_limit",
        "base_profile_unsupported",
        "patch_limit_or_empty",
        "patch_scope_rejected",
        "rationale_invalid",
        "checker_version_mismatch",
        "base_materialization_mismatch",
        "tree_scope_changed",
        "no_op_patch",
        "static_check_rejected",
        "static_tool_unavailable_or_timeout",
        "source_limit",
        "tree_limit_or_type",
        "tree_type_unsupported",
        "tree_directory_limit",
        "tree_directory_mismatch",
        "record_root_invalid",
        "record_root_not_private",
        "record_limit",
        "record_invalid",
        "record_not_canonical",
        "ref_invalid",
        "package_request_mismatch",
        "package_binding_mismatch",
        "package_receipt_mismatch",
        "workspace_invalid",
        "candidate_integrity_error",
        "diff_limit",
    }
)


def failure_detail(error: Exception) -> str:
    if isinstance(error, AgentRuntimeError) and error.reason_code in {
        "source_patch_not_ready",
        "source_patch_context_invalid",
        "source_patch_source_invalid",
        "source_patch_source_not_active",
        "source_patch_source_changed",
    }:
        return error.reason_code
    if isinstance(error, IdempotencyKeyConflict):
        return "idempotency_key_conflict"
    if isinstance(error, AdoptionError) and str(error) in {
        "adoption_user_required",
        "adoption_project_mismatch",
        "adoption_registration_mismatch",
        "adoption_snapshot_mismatch",
        "adoption_parent_mismatch",
        "adoption_content_mismatch",
        "adoption_source_not_ready",
        "adoption_target_conflict",
    }:
        return str(error)
    if isinstance(error, CandidateError) and str(error) in _SAFE_CANDIDATE_DETAILS:
        return str(error)
    if isinstance(error, ContentBlobMissing):
        return "content_missing"
    if isinstance(error, ContentStoreCorruption):
        return "content_integrity_error"
    if isinstance(error, ContentReferenceInvalid):
        return "ref_invalid"
    return "static_candidate_failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["demo-training-body-v1", "project-python-v1"],
        default="demo-training-body-v1",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("create-project", help="explicitly create a research project")
    setup.add_argument("--name", required=True)
    setup.add_argument("--artifact-root-ref", required=True)
    setup.add_argument("--idempotency-key", required=True)
    setup = commands.add_parser("register-repository", help="bind an existing independent Git copy")
    setup.add_argument("--project-id", type=UUID, required=True)
    setup.add_argument("--repository", type=Path, required=True)
    setup.add_argument("--expected-project-row-version", type=int, required=True)
    setup.add_argument("--idempotency-key", required=True)
    setup = commands.add_parser(
        "capture-snapshot", help="capture source without execution or approval"
    )
    setup.add_argument("--registration-id", type=UUID, required=True)
    setup.add_argument("--idempotency-key", required=True)
    task = commands.add_parser(
        "import-task", help="import a local trusted worker validation receipt"
    )
    task.add_argument("--task", type=Path, required=True)
    export = commands.add_parser(
        "export-agent-patch", help="read an unchecked persisted Agent draft"
    )
    export.add_argument("--agent-run-id", type=UUID, required=True)
    normalize = commands.add_parser(
        "normalize-agent-patch", help="derive a canonical patch; never submit or execute it"
    )
    normalize.add_argument("--agent-run-id", type=UUID, required=True)
    submit = commands.add_parser("submit", help="read a manual UTF-8 Git patch from stdin")
    submit.add_argument("--project-id", type=UUID, required=True)
    submit.add_argument("--snapshot-id", type=UUID, required=True)
    submit.add_argument("--snapshot-identity", required=True)
    submit.add_argument("--rationale", required=True)
    prepare = commands.add_parser(
        "prepare", help="apply and statically check; never execute candidate"
    )
    prepare.add_argument("--request-ref", required=True)
    inspect = commands.add_parser(
        "inspect", help="verify pinned package and show actual diff/not_run"
    )
    inspect.add_argument("--package-ref", required=True)
    adopt = commands.add_parser(
        "adopt", help="record human selection of an already captured Snapshot"
    )
    adopt.add_argument("--project-id", type=UUID, required=True)
    adopt.add_argument("--package-ref", required=True)
    adopt.add_argument("--target-snapshot-id", type=UUID, required=True)
    adopt.add_argument("--target-snapshot-identity", required=True)
    adopt.add_argument("--idempotency-key", required=True)
    arguments = parser.parse_args()
    try:
        data = asyncio.run(run(arguments))
        if isinstance(data, tuple):
            patch, receipt = data
            print(json.dumps(receipt), file=sys.stderr)
            sys.stdout.buffer.write(patch)
        elif data is not None:
            sys.stdout.buffer.write(data)
    except Exception as error:
        print(json.dumps({"status": "rejected", "detail": failure_detail(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
