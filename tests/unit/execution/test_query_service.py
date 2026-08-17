from types import SimpleNamespace


def _attempt(status: str, **values: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "status": status,
        "terminal_kind": None,
        "exit_code": None,
        "terminal_at": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_attempt_projection_preserves_unknown_and_identity_conflict() -> None:
    from labpilot.execution.query_service import attempt_status

    assert attempt_status(_attempt("unknown")) == "unknown"
    assert attempt_status(_attempt("identity_conflict")) == "identity_conflict"


def test_coordination_attention_is_derived_above_exact_attempt_status() -> None:
    from labpilot.execution.query_service import (
        derive_batch_presentation,
        derive_trial_presentation,
    )

    unknown = _attempt("unknown")
    conflict = _attempt("identity_conflict")

    assert derive_trial_presentation((unknown,), started=True, cancelled=False) == (
        "needs_attention"
    )
    assert derive_trial_presentation((conflict,), started=True, cancelled=False) == (
        "needs_attention"
    )
    assert derive_batch_presentation(
        ("needs_attention",), started=True, cancelled=False
    ) == "failed_to_coordinate"
