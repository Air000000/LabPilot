import importlib
from decimal import Decimal
from hashlib import sha256

import pytest


def _extractor():
    return importlib.import_module("labpilot.results_validation.extractor")


def _snapshot(content: bytes):
    types = importlib.import_module("labpilot.results_validation.types")
    return types.ArtifactSnapshot(
        relative_path="metrics.json",
        content=content,
        content_sha256=sha256(content).hexdigest(),
        byte_size=len(content),
        media_type="application/json",
    )


def test_result_json_v1_extracts_decimal_metrics_from_captured_bytes() -> None:
    snapshot = _snapshot(b'{"schema_version":"v1","metrics":{"score":0.82}}')

    metrics = _extractor().extract_result_json_v1(snapshot, {"score"})

    assert metrics == {"score": Decimal("0.82")}
    assert _extractor().EXTRACTOR_VERSION == "result-json-v1"


@pytest.mark.parametrize(
    "content",
    (
        b"\xff",
        b'{"schema_version":"v1","metrics":{"score":1,"score":2}}',
        b'{"schema_version":"v1","metrics":{"score":NaN}}',
        b'{"schema_version":"v1","metrics":{"score":Infinity}}',
        b'{"schema_version":"v1","metrics":{"score":-Infinity}}',
        b'{"schema_version":"v1","metrics":{"score":true}}',
        b'{"schema_version":"v2","metrics":{"score":1}}',
        b'{"schema_version":"v1","metrics":[]}',
        b'{"schema_version":"v1","metrics":{"score":1},"extra":null}',
    ),
)
def test_result_json_v1_rejects_invalid_content(content: bytes) -> None:
    with pytest.raises(ValueError):
        _extractor().extract_result_json_v1(_snapshot(content), {"score"})


def test_result_json_v1_rejects_duplicate_keys_at_any_object_level() -> None:
    duplicate_top_level = b'{"schema_version":"v1","schema_version":"v1","metrics":{"score":1}}'
    duplicate_metrics = b'{"schema_version":"v1","metrics":{"score":1,"score":2}}'

    for content in (duplicate_top_level, duplicate_metrics):
        with pytest.raises(ValueError):
            _extractor().extract_result_json_v1(_snapshot(content), {"score"})


@pytest.mark.parametrize(
    "metrics, requested",
    (
        (b'{"schema_version":"v1","metrics":{"score":1,"extra":2}}', {"score"}),
        (b'{"schema_version":"v1","metrics":{"other":1}}', {"score"}),
        (b'{"schema_version":"v1","metrics":{"score":"0.82"}}', {"score"}),
    ),
)
def test_result_json_v1_requires_exact_declared_metric_set(
    metrics: bytes,
    requested: set[str],
) -> None:
    with pytest.raises(ValueError):
        _extractor().extract_result_json_v1(_snapshot(metrics), requested)
