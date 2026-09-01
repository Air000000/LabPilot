import json
from decimal import Decimal
from typing import Any

from labpilot.results_validation.types import ArtifactSnapshot

EXTRACTOR_VERSION = "result-json-v1"


def extract_result_json_v1(
    snapshot: ArtifactSnapshot,
    requested_metrics: set[str],
) -> dict[str, Decimal]:
    try:
        value = json.loads(
            snapshot.content.decode("utf-8", errors="strict"),
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("result_json_invalid") from error

    if not isinstance(value, dict) or set(value) != {"schema_version", "metrics"}:
        raise ValueError("result_json_top_level_invalid")
    if value["schema_version"] != "v1":
        raise ValueError("result_json_schema_version_invalid")
    metrics = value["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != requested_metrics:
        raise ValueError("result_json_metric_set_invalid")

    extracted: dict[str, Decimal] = {}
    for name, metric in metrics.items():
        if isinstance(metric, bool) or not isinstance(metric, Decimal):
            raise ValueError("result_json_metric_not_numeric")
        if not metric.is_finite():
            raise ValueError("result_json_metric_nonfinite")
        extracted[name] = metric
    return extracted


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"result_json_nonfinite:{value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"result_json_duplicate_key:{key}")
        result[key] = value
    return result
