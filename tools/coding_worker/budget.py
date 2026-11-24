"""One concrete accounting wrapper; model parsing remains mini-SWE's responsibility."""

from __future__ import annotations

import json
from hashlib import sha256

from environment import BoundaryError
from minisweagent.exceptions import FormatError

SAFE_ERROR_TYPES = frozenset(
    "BadRequestError UnsupportedParamsError ContextWindowExceededError NotFoundError "
    "AuthenticationError PermissionDeniedError RateLimitError APIConnectionError "
    "APIError InternalServerError ServiceUnavailableError Timeout TimeoutError "
    "ConnectionError ValueError TypeError AttributeError ImportError ModuleNotFoundError "
    "OSError RuntimeError MemoryError KeyboardInterrupt".split()
)


class BudgetModel:
    def __init__(self, inner, *, calls: int, tokens: int, output_cap: int, record):
        self.inner, self.max_calls, self.max_tokens = inner, calls, tokens
        self.output_cap, self.record = output_cap, record
        self.calls, self.tokens = 0, 0
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def query(self, messages, **kwargs):
        if self.closed:
            raise BoundaryError("model_previous_outcome_unknown")
        if self.calls >= self.max_calls or self.tokens >= self.max_tokens:
            raise BoundaryError("model_budget_exhausted")
        self.calls += 1
        cap = min(self.output_cap, self.max_tokens - self.tokens)
        request_hash = sha256(
            json.dumps(messages, sort_keys=True, default=str).encode()
        ).hexdigest()
        self.record(
            {
                "ordinal": self.calls,
                "status": "reserved",
                "request_hash": request_hash,
                "output_cap": cap,
            }
        )
        try:
            message = self.inner.query(messages, max_tokens=cap, **kwargs)
        except FormatError as error:
            self._account(error.messages[0] if error.messages else {})
            raise
        except BaseException as error:
            self.closed = True
            error_type = type(error).__name__
            error_type = error_type if error_type in SAFE_ERROR_TYPES else "unknown"
            response = getattr(error, "response", None) if error_type != "unknown" else None
            http_status = getattr(response, "status_code", None)
            self.record(
                {
                    "ordinal": self.calls,
                    "status": "outcome_unknown",
                    "error_type": error_type,
                    "http_status": http_status
                    if type(http_status) is int and 100 <= http_status <= 599
                    else None,
                }
            )
            raise BoundaryError("model_call_outcome_unknown") from None
        self._account(message)
        actions = message.get("extra", {}).get("actions", [])
        if (
            not isinstance(actions, list)
            or len(actions) != 1
            or not isinstance(actions[0], dict)
            or not isinstance(actions[0].get("command"), str)
            or not actions[0]["command"].strip()
        ):
            self.closed = True
            self.record({"ordinal": self.calls, "status": "action_rejected"})
            raise BoundaryError("model_action_invalid")
        return message

    def _account(self, message):
        extra = message.get("extra", {}) if isinstance(message, dict) else {}
        extra = extra if isinstance(extra, dict) else {}
        response = extra.get("response", {})
        choices = response.get("choices", []) if isinstance(response, dict) else []
        reason = (
            choices[0].get("finish_reason")
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
            else None
        )
        metadata = {
            "choice_count": len(choices) if isinstance(choices, list) else None,
            "finish_reason": reason
            if isinstance(reason, str)
            and reason in {"stop", "tool_calls", "length", "content_filter", "function_call"}
            else "unknown",
        }
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        values = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if (
            any(type(v) is not int or v < 0 for v in values)
            or values[2] != values[0] + values[1]
            or values[2] <= 0
        ):
            self.closed = True
            self.record({"ordinal": self.calls, "status": "usage_unknown", **metadata})
            raise BoundaryError("model_usage_unknown")
        self.tokens += values[2]
        self.record(
            {
                "ordinal": self.calls,
                "status": "response_received",
                **metadata,
                "usage": {
                    k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                },
                "cumulative_total_tokens": self.tokens,
                "cost_usd": extra.get("cost") or None,
            }
        )
        if metadata["finish_reason"] not in {"stop", "tool_calls"}:
            self.closed = True
            if metadata["finish_reason"] == "length":
                raise BoundaryError("model_output_truncated")
            if metadata["finish_reason"] == "content_filter":
                raise BoundaryError("model_output_filtered")
            raise BoundaryError("model_completion_unknown")
