from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.common.errors import LabPilotError
from labpilot.context.repository_source import RepositorySourceReadError
from labpilot.db.idempotency import IdempotencyKeyConflict
from labpilot.execution.batch_service import (
    BatchNotFound,
    BatchStateConflict,
    BatchVersionConflict,
)
from labpilot.execution.query_service import (
    AttemptQueryNotFound,
    BatchQueryNotFound,
    TrialQueryNotFound,
)
from labpilot.experiments.project_service import (
    ProjectNotFound,
    ProjectStateConflict,
    ProjectVersionConflict,
)
from labpilot.experiments.proposal_service import (
    ExperimentVersionNotFound,
    ProposalApprovalNotReady,
    ProposalDecisionConflict,
    ProposalInvariantViolation,
    ProposalNotFound,
    ProposalVerificationConflict,
)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    human_message: str
    aggregate_type: str | None
    aggregate_id: str | None
    current_state: str | None
    expected_state: str | None
    retryable: bool
    details: dict[str, object]


class ServiceUnavailableError(LabPilotError):
    """A required application service is not configured."""


class UnexpectedApplicationInvariant(LabPilotError):
    """An application service returned an invalid result."""


class RequestDomainValidationError(LabPilotError):
    """A parsed request violates a domain input contract."""


def _response(
    *,
    status_code: int,
    error_code: str,
    human_message: str,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    current_state: str | None = None,
    expected_state: str | None = None,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error_code=error_code,
        human_message=human_message,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        current_state=current_state,
        expected_state=expected_state,
        retryable=retryable,
        details=details or {},
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


async def _request_validation_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors: list[dict[str, object]] = []
    for error in exc.errors():
        errors.append(
            {
                "location": list(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return _response(
        status_code=422,
        error_code="request_validation_error",
        human_message="request validation failed",
        details={"errors": errors},
    )


async def _domain_validation_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RequestDomainValidationError)
    return _response(
        status_code=422,
        error_code="request_validation_error",
        human_message="request validation failed",
    )


async def _project_not_found_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, ProjectNotFound)
    return _response(
        status_code=404,
        error_code="project_not_found",
        human_message="project was not found",
        aggregate_type="project",
        aggregate_id=str(exc.project_id),
    )


async def _project_version_conflict_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, ProjectVersionConflict)
    return _response(
        status_code=409,
        error_code="project_version_conflict",
        human_message="project version does not match",
        aggregate_type="project",
        aggregate_id=str(exc.project_id),
        retryable=True,
        details={
            "expected_row_version": exc.expected_row_version,
            "current_row_version": exc.current_row_version,
        },
    )


async def _project_state_conflict_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, ProjectStateConflict)
    return _response(
        status_code=409,
        error_code="project_state_conflict",
        human_message="project state does not allow this command",
        aggregate_type="project",
        aggregate_id=str(exc.project_id),
        current_state=exc.current_state,
        expected_state=exc.expected_state,
    )


async def _execution_not_found_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    if isinstance(exc, BatchNotFound | BatchQueryNotFound):
        aggregate_type, aggregate_id = "batch", exc.batch_id
    elif isinstance(exc, TrialQueryNotFound):
        aggregate_type, aggregate_id = "trial", exc.trial_id
    else:
        assert isinstance(exc, AttemptQueryNotFound)
        aggregate_type, aggregate_id = "attempt", exc.attempt_id
    return _response(
        status_code=404,
        error_code=f"{aggregate_type}_not_found",
        human_message=f"{aggregate_type} was not found",
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
    )


async def _batch_conflict_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, BatchVersionConflict | BatchStateConflict)
    if isinstance(exc, BatchVersionConflict):
        return _response(
            status_code=409,
            error_code="batch_version_conflict",
            human_message="batch version does not match",
            aggregate_type="batch",
            aggregate_id=str(exc.batch_id),
            retryable=True,
            details={
                "expected_row_version": exc.expected_row_version,
                "current_row_version": exc.current_row_version,
            },
        )
    return _response(
        status_code=409,
        error_code="batch_state_conflict",
        human_message="batch state does not allow this command",
        aggregate_type="batch",
        aggregate_id=str(exc.batch_id),
        current_state=exc.current_state,
        expected_state=exc.expected_state,
    )


async def _idempotency_conflict_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, IdempotencyKeyConflict)
    return _response(
        status_code=409,
        error_code="idempotency_key_conflict",
        human_message="idempotency key is already used for another request",
    )


async def _repository_source_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RepositorySourceReadError)
    return _response(
        status_code=404 if exc.reason_code == "snapshot_not_found" else 409,
        error_code="resource_not_found"
        if exc.reason_code == "snapshot_not_found"
        else "repository_source_conflict",
        human_message="requested resource was not found"
        if exc.reason_code == "snapshot_not_found"
        else "repository source is unavailable",
    )


async def _agent_runtime_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AgentRuntimeError)
    if exc.reason_code == "agent_run_not_found":
        return _response(
            status_code=404,
            error_code=exc.reason_code,
            human_message="agent run was not found",
            aggregate_type="agent_run",
        )
    if exc.reason_code == "remote_provider_blocked":
        return _response(
            status_code=403,
            error_code=exc.reason_code,
            human_message="provider policy denied this command",
        )
    if exc.reason_code.endswith(("_blank", "_invalid_utf8", "_too_large")):
        return _response(
            status_code=422,
            error_code="request_validation_error",
            human_message="request validation failed",
        )
    return _response(
        status_code=409,
        error_code=exc.reason_code,
        human_message="agent run state does not allow this command",
        retryable=exc.reason_code == "row_version_conflict",
    )


async def _proposal_not_found_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, ProposalNotFound | ExperimentVersionNotFound)
    return _response(
        status_code=404,
        error_code="resource_not_found",
        human_message="requested resource was not found",
    )


async def _proposal_conflict_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(
        exc,
        ProposalApprovalNotReady
        | ProposalDecisionConflict
        | ProposalInvariantViolation
        | ProposalVerificationConflict,
    )
    return _response(
        status_code=409,
        error_code="proposal_conflict",
        human_message="proposal state does not allow this command",
    )


async def _service_unavailable_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, ServiceUnavailableError)
    return _response(
        status_code=503,
        error_code="service_unavailable",
        human_message="application service is unavailable",
        retryable=True,
    )


async def _internal_invariant_handler(
    _: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, UnexpectedApplicationInvariant)
    return _response(
        status_code=500,
        error_code="internal_invariant_error",
        human_message="an internal application invariant failed",
    )


async def _unexpected_exception_handler(
    _: Request,
    __: Exception,
) -> JSONResponse:
    return _response(
        status_code=500,
        error_code="internal_invariant_error",
        human_message="an internal application invariant failed",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(
        RequestDomainValidationError,
        _domain_validation_handler,
    )
    app.add_exception_handler(ProjectNotFound, _project_not_found_handler)
    app.add_exception_handler(
        ProjectVersionConflict,
        _project_version_conflict_handler,
    )
    app.add_exception_handler(
        ProjectStateConflict,
        _project_state_conflict_handler,
    )
    for error_type in (
        BatchNotFound,
        BatchQueryNotFound,
        TrialQueryNotFound,
        AttemptQueryNotFound,
    ):
        app.add_exception_handler(error_type, _execution_not_found_handler)
    app.add_exception_handler(BatchVersionConflict, _batch_conflict_handler)
    app.add_exception_handler(BatchStateConflict, _batch_conflict_handler)
    app.add_exception_handler(
        IdempotencyKeyConflict,
        _idempotency_conflict_handler,
    )
    app.add_exception_handler(
        RepositorySourceReadError,
        _repository_source_handler,
    )
    app.add_exception_handler(AgentRuntimeError, _agent_runtime_handler)
    app.add_exception_handler(ProposalNotFound, _proposal_not_found_handler)
    app.add_exception_handler(
        ExperimentVersionNotFound,
        _proposal_not_found_handler,
    )
    app.add_exception_handler(
        ProposalApprovalNotReady,
        _proposal_conflict_handler,
    )
    app.add_exception_handler(
        ProposalDecisionConflict,
        _proposal_conflict_handler,
    )
    app.add_exception_handler(
        ProposalInvariantViolation,
        _proposal_conflict_handler,
    )
    app.add_exception_handler(
        ProposalVerificationConflict,
        _proposal_conflict_handler,
    )
    app.add_exception_handler(
        ServiceUnavailableError,
        _service_unavailable_handler,
    )
    app.add_exception_handler(
        UnexpectedApplicationInvariant,
        _internal_invariant_handler,
    )
    app.add_exception_handler(Exception, _unexpected_exception_handler)
