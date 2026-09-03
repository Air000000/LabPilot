from __future__ import annotations

import importlib
import inspect
from uuid import uuid4


def _summary_module():
    return importlib.import_module("labpilot.results_validation.summary_service")


def test_task7_public_summary_contract_is_async_and_immutable() -> None:
    summary = _summary_module()

    result = summary.RefreshSummaryResult(
        batch_id=uuid4(),
        revision_id=None,
        created=False,
        presentation_status="not_ready",
    )

    assert result.revision_id is None
    assert result.created is False
    assert result.presentation_status == "not_ready"
    assert inspect.iscoroutinefunction(summary.BatchSummaryService.refresh_summary)
