from __future__ import annotations

import importlib
import inspect
from uuid import uuid4

from labpilot.results_validation.types import (
    ResultProcessingDeferred,
    ValidationVerdict,
)


def _service_module():
    return importlib.import_module("labpilot.results_validation.service")


def test_task6_public_contract_and_deferred_identity() -> None:
    service = _service_module()

    result = service.ProcessAttemptResult(
        attempt_id=uuid4(),
        validation_report_id=uuid4(),
        verdict=ValidationVerdict.VALID,
        selected=True,
    )

    assert result.verdict is ValidationVerdict.VALID
    assert result.selected is True
    assert service.ResultProcessingDeferred is ResultProcessingDeferred
    assert inspect.iscoroutinefunction(service.ResultApplicationService.process_attempt)
