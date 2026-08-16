from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.experiments.proposal_models import (
    DesignDecisionRow,
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
    ProposalVerificationRow,
)
from labpilot.experiments.proposal_ports import (
    ProposalRepositoryFactPort,
    ProposalSnapshotState,
)
from labpilot.experiments.proposal_repository import ProposalRepository
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    DatasetReferenceType,
    EnvironmentExpectation,
    ExperimentType,
    MetricDeclaration,
    ParameterValue,
    ProposalPayload,
    ReproducibilityLevel,
    ValidationRule,
    ValidationRuleKind,
    dataset_reference_hash,
    environment_expectation_hash,
    experiment_version_identity,
    proposal_payload_hash,
    validation_protocol_hash,
)
from labpilot.experiments.proposal_verifier import (
    ProposalVerificationResult,
    ProposalVerifier,
    VerificationFinding,
)


class ProposalNotFound(LabPilotError):
    pass


class ExperimentVersionNotFound(LabPilotError):
    pass


class ProposalInvariantViolation(LabPilotError):
    pass


class ProposalVerificationConflict(LabPilotError):
    pass


class ProposalApprovalNotReady(LabPilotError):
    pass


class ProposalDecisionConflict(LabPilotError):
    pass


@dataclass(frozen=True, slots=True)
class ProposalRevisionDetails:
    proposal_revision_id: UUID
    experiment_id: UUID
    revision_number: int
    origin_agent_step_id: UUID | None
    payload: ProposalPayload
    payload_hash: str
    created_by_actor_type: str
    created_by_actor_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProposalVerificationDetails:
    proposal_verification_id: UUID
    proposal_revision_id: UUID
    verifier_profile: str
    proposal_payload_hash: str
    result: ProposalVerificationResult
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DesignDecisionDetails:
    design_decision_id: UUID
    proposal_revision_id: UUID
    proposal_verification_id: UUID | None
    decision: str
    proposal_payload_hash: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    dataset_reference_hash: str
    environment_expectation_hash: str
    actor_type: str
    actor_id: str
    decided_at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class ExperimentVersionDetails:
    experiment_version_id: UUID
    experiment_id: UUID
    design_decision_id: UUID
    version_number: int
    experiment_version_identity: str
    proposal_revision_id: UUID
    proposal_payload_hash: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    dataset_reference: DatasetReference
    dataset_reference_hash: str
    environment_expectation: EnvironmentExpectation
    environment_expectation_hash: str
    validation_protocol: tuple[ValidationRule, ...]
    validation_protocol_hash: str
    created_at: datetime


class ProposalApplicationService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        repository_facts: ProposalRepositoryFactPort,
        verifier: ProposalVerifier,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._repository_facts = repository_facts
        self._verifier = verifier
        self._repository = ProposalRepository()

    async def create_manual_revision(
        self,
        *,
        context: CommandContext,
        experiment_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionDetails:
        async with self._session_context_factory() as session:
            experiment = await self._repository.get_experiment(session, experiment_id)
            if experiment is None:
                raise ProposalNotFound(f"experiment not found: {experiment_id}")
            project_id = experiment.project_id
        await self._require_snapshot_binding(project_id=project_id, payload=payload)
        payload_hash = proposal_payload_hash(payload)
        actor_id = _actor_id(context)

        async with UnitOfWork(self._session_context_factory) as uow:
            experiment = await self._repository.get_experiment_for_update(
                uow.session,
                experiment_id,
            )
            if experiment is None:
                raise ProposalNotFound(f"experiment not found: {experiment_id}")
            if experiment.project_id != project_id:
                raise ProposalInvariantViolation("experiment Project changed")
            revision_number = (
                await self._repository.max_revision_number(uow.session, experiment_id)
            ) + 1
            row = ProposalRevisionRow(
                proposal_revision_id=uuid4(),
                experiment_id=experiment_id,
                revision_number=revision_number,
                origin_agent_step_id=None,
                repository_snapshot_id=payload.repository_snapshot_id,
                repository_snapshot_identity=payload.repository_snapshot_identity,
                payload_json=_payload_to_json(payload),
                payload_hash=payload_hash,
                created_by_actor_type=context.actor.actor_type,
                created_by_actor_id=actor_id,
                created_at=await _database_now(uow.session),
            )
            await self._repository.add_revision(uow, row)
            details = _revision_details(row)
            await uow.commit()
            return details
        raise AssertionError("UnitOfWork unexpectedly suppressed control flow")

    async def create_agent_revision(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        origin_agent_step_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionDetails:
        payload_hash = proposal_payload_hash(payload)
        async with self._session_context_factory() as session:
            existing = await self._repository.get_revision_by_origin_step(
                session,
                origin_agent_step_id,
            )
            if existing is not None:
                return await self._require_exact_agent_replay(
                    session,
                    existing,
                    payload_hash=payload_hash,
                    project_id=project_id,
                )

        await self._require_snapshot_binding(project_id=project_id, payload=payload)
        actor_id = _actor_id(context)
        try:
            async with UnitOfWork(self._session_context_factory) as uow:
                existing = await self._repository.get_revision_by_origin_step(
                    uow.session,
                    origin_agent_step_id,
                )
                if existing is not None:
                    details = await self._require_exact_agent_replay(
                        uow.session,
                        existing,
                        payload_hash=payload_hash,
                        project_id=project_id,
                    )
                    await uow.commit()
                    return details

                experiment = ExperimentRow(experiment_id=uuid4(), project_id=project_id)
                await self._repository.add_experiment(uow, experiment)
                row = ProposalRevisionRow(
                    proposal_revision_id=uuid4(),
                    experiment_id=experiment.experiment_id,
                    revision_number=1,
                    origin_agent_step_id=origin_agent_step_id,
                    repository_snapshot_id=payload.repository_snapshot_id,
                    repository_snapshot_identity=payload.repository_snapshot_identity,
                    payload_json=_payload_to_json(payload),
                    payload_hash=payload_hash,
                    created_by_actor_type=context.actor.actor_type,
                    created_by_actor_id=actor_id,
                    created_at=await _database_now(uow.session),
                )
                await self._repository.add_revision(uow, row)
                details = _revision_details(row)
                await uow.commit()
                return details
        except IntegrityError as exc:
            if not _is_origin_agent_step_conflict(exc):
                raise

        async with self._session_context_factory() as session:
            existing = await self._repository.get_revision_by_origin_step(
                session,
                origin_agent_step_id,
            )
            if existing is None:
                raise ProposalInvariantViolation(
                    "origin_agent_step_id conflict has no winning ProposalRevision"
                )
            return await self._require_exact_agent_replay(
                session,
                existing,
                payload_hash=payload_hash,
                project_id=project_id,
            )
        raise AssertionError("UnitOfWork unexpectedly suppressed control flow")

    async def get_revision(
        self,
        proposal_revision_id: UUID,
    ) -> ProposalRevisionDetails:
        async with self._session_context_factory() as session:
            row = await self._repository.get_revision(session, proposal_revision_id)
            if row is None:
                raise ProposalNotFound(
                    f"proposal revision not found: {proposal_revision_id}"
                )
            return _revision_details(row)

    async def get_version(
        self,
        experiment_version_id: UUID,
    ) -> ExperimentVersionDetails:
        async with self._session_context_factory() as session:
            row = await self._repository.get_version(session, experiment_version_id)
            if row is None:
                raise ExperimentVersionNotFound(
                    f"experiment version not found: {experiment_version_id}"
                )
            return _version_details(row)

    async def verify_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
    ) -> ProposalVerificationDetails:
        del context
        async with self._session_context_factory() as session:
            revision = await self._repository.get_revision(
                session,
                proposal_revision_id,
            )
            if revision is None:
                raise ProposalNotFound(
                    f"proposal revision not found: {proposal_revision_id}"
                )
            experiment = await self._repository.get_experiment(
                session,
                revision.experiment_id,
            )
            if experiment is None:
                raise ProposalInvariantViolation("owning Experiment is missing")
            payload = _payload_from_json(revision.payload_json)
            frozen_hash = revision.payload_hash
            project_id = experiment.project_id

        result = await self._verifier.verify(project_id=project_id, payload=payload)

        try:
            async with UnitOfWork(self._session_context_factory) as uow:
                revision = await self._repository.get_revision(
                    uow.session,
                    proposal_revision_id,
                )
                if revision is None:
                    raise ProposalNotFound(
                        f"proposal revision not found: {proposal_revision_id}"
                    )
                if revision.payload_hash != frozen_hash:
                    raise ProposalInvariantViolation(
                        "ProposalRevision mutated during verification"
                    )
                existing = await self._repository.get_verification_by_key(
                    uow.session,
                    proposal_revision_id=proposal_revision_id,
                    verifier_profile=result.profile,
                    proposal_payload_hash=frozen_hash,
                )
                if existing is not None:
                    details = _verification_details(existing)
                    await uow.commit()
                    return details
                row = ProposalVerificationRow(
                    proposal_verification_id=uuid4(),
                    proposal_revision_id=proposal_revision_id,
                    verifier_profile=result.profile,
                    proposal_payload_hash=frozen_hash,
                    result_json=_verification_result_to_json(result),
                    result_hash=result.result_hash,
                    passed=result.passed,
                    created_at=await _database_now(uow.session),
                )
                await self._repository.add_verification(uow, row)
                details = _verification_details(row)
                await uow.commit()
                return details
        except IntegrityError as exc:
            if not _is_named_constraint_conflict(
                exc,
                "uq_experiment_proposal_verifications_semantic_key",
            ):
                raise

        async with self._session_context_factory() as session:
            existing = await self._repository.get_verification_by_key(
                session,
                proposal_revision_id=proposal_revision_id,
                verifier_profile=result.profile,
                proposal_payload_hash=frozen_hash,
            )
            if existing is None:
                raise ProposalInvariantViolation(
                    "Verification semantic-key conflict has no winning row"
                )
            return _verification_details(existing)
        raise AssertionError("UnitOfWork unexpectedly suppressed control flow")

    async def approve_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
        verification_id: UUID,
    ) -> ExperimentVersionDetails:
        async with self._session_context_factory() as session:
            revision = await self._repository.get_revision(session, proposal_revision_id)
            verification = await self._repository.get_verification(
                session,
                verification_id,
            )
            if revision is None:
                raise ProposalNotFound(
                    f"proposal revision not found: {proposal_revision_id}"
                )
            if verification is None:
                raise ProposalVerificationConflict("verification not found")
            experiment = await self._repository.get_experiment(
                session,
                revision.experiment_id,
            )
            if experiment is None:
                raise ProposalInvariantViolation("owning Experiment is missing")
            snapshot_id = revision.repository_snapshot_id
            snapshot_identity = revision.repository_snapshot_identity
            project_id = experiment.project_id

        approval_state = await self._repository_facts.approval_source_state(snapshot_id)
        if (
            not approval_state.ready
            or approval_state.repository_snapshot_id != snapshot_id
            or approval_state.repository_snapshot_identity != snapshot_identity
            or approval_state.project_id != project_id
        ):
            raise ProposalApprovalNotReady(
                approval_state.reason_code or "approval source binding mismatch"
            )

        actor_id = _actor_id(context)
        async with UnitOfWork(self._session_context_factory) as uow:
            revision = await self._repository.get_revision(
                uow.session,
                proposal_revision_id,
            )
            verification = await self._repository.get_verification(
                uow.session,
                verification_id,
            )
            if revision is None or verification is None:
                raise ProposalVerificationConflict("revision or verification disappeared")
            _require_verification_binding(revision, verification)

            experiment = await self._repository.get_experiment_for_update(
                uow.session,
                revision.experiment_id,
            )
            if experiment is None:
                raise ProposalInvariantViolation("owning Experiment is missing")
            if experiment.project_id != project_id:
                raise ProposalInvariantViolation("Experiment Project binding changed")

            existing_decision = await self._repository.get_decision_by_revision(
                uow.session,
                proposal_revision_id,
            )
            if existing_decision is not None:
                details = await self._approved_retry(
                    uow.session,
                    existing_decision,
                    verification_id=verification_id,
                    actor_type=context.actor.actor_type,
                    actor_id=actor_id,
                )
                await uow.commit()
                return details

            payload = _payload_from_json(revision.payload_json)
            dataset_hash = dataset_reference_hash(payload.dataset_reference)
            environment_hash = environment_expectation_hash(
                payload.environment_expectation
            )
            validation_hash = validation_protocol_hash(payload.validation_rules)
            now = await _database_now(uow.session)
            decision = DesignDecisionRow(
                design_decision_id=uuid4(),
                proposal_revision_id=proposal_revision_id,
                proposal_verification_id=verification_id,
                decision="approved",
                proposal_payload_hash=revision.payload_hash,
                repository_snapshot_id=revision.repository_snapshot_id,
                repository_snapshot_identity=revision.repository_snapshot_identity,
                dataset_reference_hash=dataset_hash,
                environment_expectation_hash=environment_hash,
                actor_type=context.actor.actor_type,
                actor_id=actor_id,
                decided_at=now,
                reason=None,
            )
            await self._repository.add_decision(uow, decision)
            version_number = (
                await self._repository.max_version_number(
                    uow.session,
                    experiment.experiment_id,
                )
            ) + 1
            version = ExperimentVersionRow(
                experiment_version_id=uuid4(),
                experiment_id=experiment.experiment_id,
                design_decision_id=decision.design_decision_id,
                version_number=version_number,
                experiment_version_identity=experiment_version_identity(
                    experiment_id=experiment.experiment_id,
                    proposal_payload_hash_value=revision.payload_hash,
                    repository_snapshot_identity=revision.repository_snapshot_identity,
                    dataset_reference_hash_value=dataset_hash,
                    environment_expectation_hash_value=environment_hash,
                    validation_protocol_hash_value=validation_hash,
                ),
                proposal_revision_id=proposal_revision_id,
                proposal_payload_hash=revision.payload_hash,
                repository_snapshot_id=revision.repository_snapshot_id,
                repository_snapshot_identity=revision.repository_snapshot_identity,
                dataset_reference_json=_dataset_to_json(payload.dataset_reference),
                dataset_reference_hash=dataset_hash,
                environment_expectation_json=_environment_to_json(
                    payload.environment_expectation
                ),
                environment_expectation_hash=environment_hash,
                validation_protocol_json=_validation_to_json(payload.validation_rules),
                validation_protocol_hash=validation_hash,
                created_at=now,
            )
            await self._repository.add_version(uow, version)
            details = _version_details(version)
            await uow.commit()
            return details
        raise AssertionError("UnitOfWork unexpectedly suppressed control flow")

    async def reject_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
        verification_id: UUID | None,
        reason: str,
    ) -> DesignDecisionDetails:
        if not reason.strip():
            raise ValueError("reason must not be blank")
        actor_id = _actor_id(context)
        async with UnitOfWork(self._session_context_factory) as uow:
            revision = await self._repository.get_revision(
                uow.session,
                proposal_revision_id,
            )
            if revision is None:
                raise ProposalNotFound(
                    f"proposal revision not found: {proposal_revision_id}"
                )
            verification: ProposalVerificationRow | None = None
            if verification_id is not None:
                verification = await self._repository.get_verification(
                    uow.session,
                    verification_id,
                )
                if verification is None:
                    raise ProposalVerificationConflict("verification not found")
                if (
                    verification.proposal_revision_id != proposal_revision_id
                    or verification.proposal_payload_hash != revision.payload_hash
                ):
                    raise ProposalVerificationConflict("verification binding mismatch")

            existing = await self._repository.get_decision_by_revision(
                uow.session,
                proposal_revision_id,
            )
            if existing is not None:
                if (
                    existing.decision == "rejected"
                    and existing.proposal_verification_id == verification_id
                    and existing.actor_type == context.actor.actor_type
                    and existing.actor_id == actor_id
                    and existing.reason == reason
                ):
                    details = _decision_details(existing)
                    await uow.commit()
                    return details
                raise ProposalDecisionConflict("ProposalRevision already has a decision")

            if verification_id is None:
                if context.actor.actor_type != "local_user":
                    raise ProposalInvariantViolation(
                        "unverified rejection requires a local_user actor"
                    )
                if await self._repository.has_verification_for_revision(
                    uow.session,
                    proposal_revision_id,
                ):
                    raise ProposalVerificationConflict(
                        "ProposalRevision already has a Verification"
                    )

            payload = _payload_from_json(revision.payload_json)
            row = DesignDecisionRow(
                design_decision_id=uuid4(),
                proposal_revision_id=proposal_revision_id,
                proposal_verification_id=verification_id,
                decision="rejected",
                proposal_payload_hash=revision.payload_hash,
                repository_snapshot_id=revision.repository_snapshot_id,
                repository_snapshot_identity=revision.repository_snapshot_identity,
                dataset_reference_hash=dataset_reference_hash(
                    payload.dataset_reference
                ),
                environment_expectation_hash=environment_expectation_hash(
                    payload.environment_expectation
                ),
                actor_type=context.actor.actor_type,
                actor_id=actor_id,
                decided_at=await _database_now(uow.session),
                reason=reason,
            )
            await self._repository.add_decision(uow, row)
            details = _decision_details(row)
            await uow.commit()
            return details
        raise AssertionError("UnitOfWork unexpectedly suppressed control flow")

    async def _require_snapshot_binding(
        self,
        *,
        project_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalSnapshotState:
        state = await self._repository_facts.snapshot_state(
            payload.repository_snapshot_id
        )
        if (
            state.repository_snapshot_id != payload.repository_snapshot_id
            or state.repository_snapshot_identity
            != payload.repository_snapshot_identity
            or state.project_id != project_id
        ):
            raise ProposalInvariantViolation("Proposal Snapshot binding mismatch")
        return state

    async def _approved_retry(
        self,
        session: AsyncSession,
        decision: DesignDecisionRow,
        *,
        verification_id: UUID,
        actor_type: str,
        actor_id: str,
    ) -> ExperimentVersionDetails:
        if (
            decision.decision != "approved"
            or decision.proposal_verification_id != verification_id
            or decision.actor_type != actor_type
            or decision.actor_id != actor_id
        ):
            raise ProposalDecisionConflict("ProposalRevision already has another decision")
        version = await self._repository.get_version_by_revision(
            session,
            decision.proposal_revision_id,
        )
        if version is None:
            raise ProposalInvariantViolation("approved decision has no ExperimentVersion")
        return _version_details(version)

    async def _require_exact_agent_replay(
        self,
        session: AsyncSession,
        row: ProposalRevisionRow,
        *,
        payload_hash: str,
        project_id: UUID,
    ) -> ProposalRevisionDetails:
        if row.payload_hash != payload_hash:
            raise ProposalInvariantViolation(
                "origin_agent_step_id already binds a different Proposal payload"
            )
        experiment = await self._repository.get_experiment(session, row.experiment_id)
        if experiment is None or experiment.project_id != project_id:
            raise ProposalInvariantViolation(
                "origin_agent_step_id already binds a different Project"
            )
        return _revision_details(row)


def _is_named_constraint_conflict(
    error: IntegrityError,
    constraint_name: str,
) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "constraint_name", None) == constraint_name:
            return True
        current = current.__cause__
    return False


def _is_origin_agent_step_conflict(error: IntegrityError) -> bool:
    return _is_named_constraint_conflict(
        error,
        "uq_experiment_proposal_revisions_origin_agent_step",
    )


def _actor_id(context: CommandContext) -> str:
    if context.actor.actor_id is None:
        raise ProposalInvariantViolation("Proposal authority requires actor_id")
    return context.actor.actor_id


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(value, datetime)
    return value


def _require_verification_binding(
    revision: ProposalRevisionRow,
    verification: ProposalVerificationRow,
) -> None:
    if (
        verification.proposal_revision_id != revision.proposal_revision_id
        or verification.proposal_payload_hash != revision.payload_hash
    ):
        raise ProposalVerificationConflict("verification binding mismatch")
    if not verification.passed:
        raise ProposalVerificationConflict("verification did not pass")


def _revision_details(row: ProposalRevisionRow) -> ProposalRevisionDetails:
    return ProposalRevisionDetails(
        proposal_revision_id=row.proposal_revision_id,
        experiment_id=row.experiment_id,
        revision_number=row.revision_number,
        origin_agent_step_id=row.origin_agent_step_id,
        payload=_payload_from_json(row.payload_json),
        payload_hash=row.payload_hash,
        created_by_actor_type=row.created_by_actor_type,
        created_by_actor_id=row.created_by_actor_id,
        created_at=row.created_at,
    )


def _verification_details(
    row: ProposalVerificationRow,
) -> ProposalVerificationDetails:
    return ProposalVerificationDetails(
        proposal_verification_id=row.proposal_verification_id,
        proposal_revision_id=row.proposal_revision_id,
        verifier_profile=row.verifier_profile,
        proposal_payload_hash=row.proposal_payload_hash,
        result=_verification_result_from_json(row.result_json),
        created_at=row.created_at,
    )


def _decision_details(row: DesignDecisionRow) -> DesignDecisionDetails:
    return DesignDecisionDetails(
        design_decision_id=row.design_decision_id,
        proposal_revision_id=row.proposal_revision_id,
        proposal_verification_id=row.proposal_verification_id,
        decision=row.decision,
        proposal_payload_hash=row.proposal_payload_hash,
        repository_snapshot_id=row.repository_snapshot_id,
        repository_snapshot_identity=row.repository_snapshot_identity,
        dataset_reference_hash=row.dataset_reference_hash,
        environment_expectation_hash=row.environment_expectation_hash,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        decided_at=row.decided_at,
        reason=row.reason,
    )


def _version_details(row: ExperimentVersionRow) -> ExperimentVersionDetails:
    return ExperimentVersionDetails(
        experiment_version_id=row.experiment_version_id,
        experiment_id=row.experiment_id,
        design_decision_id=row.design_decision_id,
        version_number=row.version_number,
        experiment_version_identity=row.experiment_version_identity,
        proposal_revision_id=row.proposal_revision_id,
        proposal_payload_hash=row.proposal_payload_hash,
        repository_snapshot_id=row.repository_snapshot_id,
        repository_snapshot_identity=row.repository_snapshot_identity,
        dataset_reference=_dataset_from_json(row.dataset_reference_json),
        dataset_reference_hash=row.dataset_reference_hash,
        environment_expectation=_environment_from_json(
            row.environment_expectation_json
        ),
        environment_expectation_hash=row.environment_expectation_hash,
        validation_protocol=_validation_from_json(row.validation_protocol_json),
        validation_protocol_hash=row.validation_protocol_hash,
        created_at=row.created_at,
    )


def _dataset_to_json(value: DatasetReference) -> dict[str, object]:
    return {
        "reference_type": value.reference_type,
        "path_or_uri": value.path_or_uri,
        "stable_identity": value.stable_identity,
        "reproducibility_level": value.reproducibility_level,
    }


def _dataset_from_json(value: dict[str, object]) -> DatasetReference:
    return DatasetReference(
        reference_type=cast(DatasetReferenceType, value["reference_type"]),
        path_or_uri=cast(str, value["path_or_uri"]),
        stable_identity=cast(str | None, value["stable_identity"]),
        reproducibility_level=cast(
            ReproducibilityLevel,
            value["reproducibility_level"],
        ),
    )


def _environment_to_json(value: EnvironmentExpectation) -> dict[str, object]:
    return {
        "python_profile": value.python_profile,
        "framework_requirements": list(value.framework_requirements),
        "device_requirement": value.device_requirement,
        "cuda_requirement": value.cuda_requirement,
    }


def _environment_from_json(value: dict[str, object]) -> EnvironmentExpectation:
    return EnvironmentExpectation(
        python_profile=cast(str, value["python_profile"]),
        framework_requirements=tuple(
            cast(list[str], value["framework_requirements"])
        ),
        device_requirement=cast(str | None, value["device_requirement"]),
        cuda_requirement=cast(str | None, value["cuda_requirement"]),
    )


def _validation_to_json(
    values: tuple[ValidationRule, ...],
) -> list[dict[str, object]]:
    return [{"kind": value.kind, "subject": value.subject} for value in values]


def _validation_from_json(
    values: list[dict[str, object]],
) -> tuple[ValidationRule, ...]:
    return tuple(
        ValidationRule(
            kind=cast(ValidationRuleKind, value["kind"]),
            subject=cast(str, value["subject"]),
        )
        for value in values
    )


def _payload_to_json(payload: ProposalPayload) -> dict[str, object]:
    value: dict[str, object] = {
        "goal": payload.goal,
        "experiment_type": payload.experiment_type,
        "repository_snapshot_id": str(payload.repository_snapshot_id),
        "repository_snapshot_identity": payload.repository_snapshot_identity,
        "train_entrypoint": payload.train_entrypoint,
        "eval_entrypoint": payload.eval_entrypoint,
        "workdir": payload.workdir,
        "parameter_matrix": {
            key: list(values) for key, values in payload.parameter_matrix.items()
        },
        "seeds": list(payload.seeds),
        "dataset_reference": _dataset_to_json(payload.dataset_reference),
        "environment_expectation": _environment_to_json(
            payload.environment_expectation
        ),
        "metrics": [
            {
                "name": metric.name,
                "source_artifact": metric.source_artifact,
                "split": metric.split,
            }
            for metric in payload.metrics
        ],
        "artifacts": [
            {
                "name": artifact.name,
                "relative_path": artifact.relative_path,
                "required": artifact.required,
            }
            for artifact in payload.artifacts
        ],
        "validation_rules": _validation_to_json(payload.validation_rules),
        "timeout_seconds": payload.timeout_seconds,
        "max_retries": payload.max_retries,
    }
    if payload.train_argv_template is not None:
        value["train_argv_template"] = list(payload.train_argv_template)
    return value


def _payload_from_json(value: dict[str, object]) -> ProposalPayload:
    matrix = cast(dict[str, list[ParameterValue]], value["parameter_matrix"])
    metrics = cast(list[dict[str, object]], value["metrics"])
    artifacts = cast(list[dict[str, object]], value["artifacts"])
    return ProposalPayload(
        goal=cast(str, value["goal"]),
        experiment_type=cast(ExperimentType, value["experiment_type"]),
        repository_snapshot_id=UUID(cast(str, value["repository_snapshot_id"])),
        repository_snapshot_identity=cast(
            str,
            value["repository_snapshot_identity"],
        ),
        train_entrypoint=cast(str, value["train_entrypoint"]),
        eval_entrypoint=cast(str | None, value["eval_entrypoint"]),
        workdir=cast(str, value["workdir"]),
        parameter_matrix={key: tuple(values) for key, values in matrix.items()},
        seeds=tuple(cast(list[int], value["seeds"])),
        dataset_reference=_dataset_from_json(
            cast(dict[str, object], value["dataset_reference"])
        ),
        environment_expectation=_environment_from_json(
            cast(dict[str, object], value["environment_expectation"])
        ),
        metrics=tuple(
            MetricDeclaration(
                name=cast(str, metric["name"]),
                source_artifact=cast(str, metric["source_artifact"]),
                split=cast(str | None, metric["split"]),
            )
            for metric in metrics
        ),
        artifacts=tuple(
            ArtifactDeclaration(
                name=cast(str, artifact["name"]),
                relative_path=cast(str, artifact["relative_path"]),
                required=cast(bool, artifact["required"]),
            )
            for artifact in artifacts
        ),
        validation_rules=_validation_from_json(
            cast(list[dict[str, object]], value["validation_rules"])
        ),
        timeout_seconds=cast(int, value["timeout_seconds"]),
        max_retries=cast(int, value["max_retries"]),
        train_argv_template=(
            tuple(cast(list[str], value["train_argv_template"]))
            if value.get("train_argv_template") is not None
            else None
        ),
    )


def _verification_result_to_json(
    result: ProposalVerificationResult,
) -> dict[str, object]:
    return {
        "profile": result.profile,
        "passed": result.passed,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "field": finding.field,
                "message": finding.message,
            }
            for finding in result.findings
        ],
        "result_hash": result.result_hash,
    }


def _verification_result_from_json(
    value: dict[str, object],
) -> ProposalVerificationResult:
    findings = cast(list[dict[str, object]], value["findings"])
    return ProposalVerificationResult(
        profile=cast(str, value["profile"]),
        passed=cast(bool, value["passed"]),
        findings=tuple(
            VerificationFinding(
                code=cast(str, finding["code"]),
                severity=cast(str, finding["severity"]),  # type: ignore[arg-type]
                field=cast(str | None, finding["field"]),
                message=cast(str, finding["message"]),
            )
            for finding in findings
        ),
        result_hash=cast(str, value["result_hash"]),
    )
