from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from labpilot.common.errors import LabPilotError
from labpilot.db.idempotency import IdempotencyKeyConflict
from labpilot.experiments.project_service import (
    ProjectNotFound,
    ProjectStateConflict,
    ProjectVersionConflict,
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
    app.add_exception_handler(
        IdempotencyKeyConflict,
        _idempotency_conflict_handler,
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
