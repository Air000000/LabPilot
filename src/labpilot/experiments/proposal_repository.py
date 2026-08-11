from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.db.uow import UnitOfWork
from labpilot.experiments.proposal_models import (
    DesignDecisionRow,
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
    ProposalVerificationRow,
)


class ProposalRepository:
    async def get_experiment(
        self,
        session: AsyncSession,
        experiment_id: UUID,
    ) -> ExperimentRow | None:
        return await session.get(ExperimentRow, experiment_id)

    async def get_experiment_for_update(
        self,
        session: AsyncSession,
        experiment_id: UUID,
    ) -> ExperimentRow | None:
        result = await session.execute(
            select(ExperimentRow)
            .where(ExperimentRow.experiment_id == experiment_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_experiment(self, uow: UnitOfWork, row: ExperimentRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def get_revision(
        self,
        session: AsyncSession,
        proposal_revision_id: UUID,
    ) -> ProposalRevisionRow | None:
        return await session.get(ProposalRevisionRow, proposal_revision_id)

    async def get_revision_by_origin_step(
        self,
        session: AsyncSession,
        origin_agent_step_id: UUID,
    ) -> ProposalRevisionRow | None:
        result = await session.execute(
            select(ProposalRevisionRow).where(
                ProposalRevisionRow.origin_agent_step_id == origin_agent_step_id
            )
        )
        return result.scalar_one_or_none()

    async def max_revision_number(
        self,
        session: AsyncSession,
        experiment_id: UUID,
    ) -> int:
        value = await session.scalar(
            select(func.max(ProposalRevisionRow.revision_number)).where(
                ProposalRevisionRow.experiment_id == experiment_id
            )
        )
        return int(value or 0)

    async def add_revision(self, uow: UnitOfWork, row: ProposalRevisionRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def get_verification(
        self,
        session: AsyncSession,
        proposal_verification_id: UUID,
    ) -> ProposalVerificationRow | None:
        return await session.get(ProposalVerificationRow, proposal_verification_id)

    async def get_verification_by_key(
        self,
        session: AsyncSession,
        *,
        proposal_revision_id: UUID,
        verifier_profile: str,
        proposal_payload_hash: str,
    ) -> ProposalVerificationRow | None:
        result = await session.execute(
            select(ProposalVerificationRow).where(
                ProposalVerificationRow.proposal_revision_id == proposal_revision_id,
                ProposalVerificationRow.verifier_profile == verifier_profile,
                ProposalVerificationRow.proposal_payload_hash == proposal_payload_hash,
            )
        )
        return result.scalar_one_or_none()

    async def has_verification_for_revision(
        self,
        session: AsyncSession,
        proposal_revision_id: UUID,
    ) -> bool:
        value = await session.scalar(
            select(ProposalVerificationRow.proposal_verification_id)
            .where(
                ProposalVerificationRow.proposal_revision_id == proposal_revision_id
            )
            .limit(1)
        )
        return value is not None

    async def add_verification(
        self,
        uow: UnitOfWork,
        row: ProposalVerificationRow,
    ) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def get_decision_by_revision(
        self,
        session: AsyncSession,
        proposal_revision_id: UUID,
    ) -> DesignDecisionRow | None:
        result = await session.execute(
            select(DesignDecisionRow).where(
                DesignDecisionRow.proposal_revision_id == proposal_revision_id
            )
        )
        return result.scalar_one_or_none()

    async def add_decision(self, uow: UnitOfWork, row: DesignDecisionRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def get_version_by_revision(
        self,
        session: AsyncSession,
        proposal_revision_id: UUID,
    ) -> ExperimentVersionRow | None:
        result = await session.execute(
            select(ExperimentVersionRow).where(
                ExperimentVersionRow.proposal_revision_id == proposal_revision_id
            )
        )
        return result.scalar_one_or_none()

    async def max_version_number(
        self,
        session: AsyncSession,
        experiment_id: UUID,
    ) -> int:
        value = await session.scalar(
            select(func.max(ExperimentVersionRow.version_number)).where(
                ExperimentVersionRow.experiment_id == experiment_id
            )
        )
        return int(value or 0)

    async def add_version(self, uow: UnitOfWork, row: ExperimentVersionRow) -> None:
        uow.session.add(row)
        await uow.session.flush()
