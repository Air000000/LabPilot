from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.repository_service import RepositoryApplicationService
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot, RegisterLocalRepository
from labpilot.db.session import Database
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.project_types import CreateProject
from labpilot.settings import get_settings

_PROJECT_IDEMPOTENCY_KEY = "competition-demo-project-v1"
_REGISTER_IDEMPOTENCY_KEY = "competition-demo-register-v1"
_SNAPSHOT_IDEMPOTENCY_KEY = "competition-demo-snapshot-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create/reuse the competition Project, register the prepared demo repository, "
            "and capture a clean RepositorySnapshot."
        )
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path("/repositories/labpilot-demo-repo"),
        help="Canonical repository path as seen by the LabPilot API container.",
    )
    return parser.parse_args()


def _context() -> CommandContext:
    settings = get_settings()
    return CommandContext(
        installation_identity=settings.installation_identity,
        actor=CommandActor(actor_type="local_user", actor_id=settings.local_actor_id),
        interaction_correlation_id="competition-demo-bootstrap-v1",
    )


async def _assert_existing_registration_path(
    database: Database,
    registration_id: UUID,
    repository: Path,
) -> None:
    async with database.session() as session:
        row = await session.get(RepositoryRegistrationRow, registration_id)
    if row is None:
        raise RuntimeError("competition repository registration is missing")
    if Path(row.canonical_root_path) != repository:
        raise RuntimeError(
            "competition Project is already bound to a different repository: "
            f"{row.canonical_root_path}"
        )


async def bootstrap(repository: Path) -> None:
    settings = get_settings()
    repository = repository.resolve(strict=True)
    database = Database(settings.database_url)
    git = GitAdapter(
        git_executable=Path("/usr/bin/git"),
        timeout_seconds=settings.repository_git_timeout_seconds,
    )
    content_store = ContentAddressedStore(settings.repository_content_store_root)
    projects = ProjectApplicationService(database.session)
    repositories = RepositoryApplicationService(
        database.session,
        git_adapter=git,
        allowed_roots=settings.repository_allowed_roots,
        content_store=content_store,
    )
    source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=git,
        content_store=content_store,
    )

    try:
        project_outcome = await projects.create_project(
            context=_context(),
            idempotency_key=_PROJECT_IDEMPOTENCY_KEY,
            command=CreateProject(
                name="LabPilot Competition Demo",
                description="Prepared deterministic CPU classification Golden Path demo.",
                artifact_root_ref="competition-demo-artifacts:v1",
            ),
        )
        project_id = UUID(project_outcome.envelope.primary_result_id)
        project = await projects.get_project(project_id=project_id)

        registration_id = project.primary_repository_registration_id
        if registration_id is None:
            registration_outcome = await repositories.register_local_repository(
                context=_context(),
                idempotency_key=_REGISTER_IDEMPOTENCY_KEY,
                command=RegisterLocalRepository(
                    project_id=project_id,
                    user_path=repository,
                    expected_project_row_version=project.row_version,
                ),
            )
            registration_id = UUID(registration_outcome.envelope.primary_result_id)
        else:
            await _assert_existing_registration_path(database, registration_id, repository)

        snapshot_outcome = await repositories.capture_repository_snapshot(
            context=_context(),
            idempotency_key=_SNAPSHOT_IDEMPOTENCY_KEY,
            command=CaptureRepositorySnapshot(registration_id),
        )
        snapshot_id = UUID(snapshot_outcome.envelope.primary_result_id)
        descriptor = await source.describe(snapshot_id)
        if not descriptor.is_clean:
            raise RuntimeError("competition RepositorySnapshot is not clean")

        print(f"COMPETITION_PROJECT_ID={project_id}")
        print(f"COMPETITION_REPOSITORY_REGISTRATION_ID={registration_id}")
        print(f"COMPETITION_REPOSITORY_SNAPSHOT_ID={snapshot_id}")
        print(
            "COMPETITION_REPOSITORY_SNAPSHOT_IDENTITY="
            f"{descriptor.repository_snapshot_identity}"
        )
        print(f"COMPETITION_REPOSITORY_HEAD={descriptor.head_commit}")
        print("COMPETITION_REPOSITORY_CLEAN=YES")
        print("COMPETITION_BOOTSTRAP=PASS")
    finally:
        await database.dispose()


def main() -> int:
    args = parse_args()
    asyncio.run(bootstrap(args.repository))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
