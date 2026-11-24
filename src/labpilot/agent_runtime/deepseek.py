from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import cast

import httpx
from pydantic import SecretStr

from labpilot.agent_runtime.provider import (
    ModelRequest,
    ModelResponse,
    ProviderEgress,
    ProviderError,
)

_ENDPOINT = "https://api.deepseek.com/chat/completions"
_ALLOWED_ROLES = {"system", "user", "assistant"}

logger = logging.getLogger(__name__)


class DeepSeekChatProvider:
    provider_name = "deepseek_chat"
    egress: ProviderEgress = "remote"

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        timeout_seconds: int = 60,
        max_response_bytes: int = 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not model:
            raise ValueError("model must not be empty")
        if timeout_seconds < 1 or timeout_seconds > 300:
            raise ValueError("timeout_seconds out of range")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes below minimum")
        self.model_name = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    async def complete(self, request: ModelRequest) -> ModelResponse:
        parameters = dict(request.semantic_parameters)
        if parameters and (
            set(parameters) != {"max_tokens"}
            or type(parameters["max_tokens"]) is not int
            or not 1 <= parameters["max_tokens"] <= 8192
        ):
            raise ProviderError("provider_parameters_unsupported")
        if request.model != self.model_name:
            raise ProviderError("provider_model_mismatch")
        messages = _wire_messages(request.messages)
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                outbound = client.build_request(
                    "POST",
                    _ENDPOINT,
                    headers=headers,
                    json={
                        "model": self.model_name,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "stream": False,
                        **parameters,
                    },
                )
                response = await client.send(outbound, stream=True)
                try:
                    if response.status_code < 200 or response.status_code >= 300:
                        _log_failure("provider_http_error", response.status_code)
                        raise ProviderError("provider_http_error")
                    content = await _bounded_content(response, self._max_response_bytes)
                finally:
                    await response.aclose()
        except ProviderError:
            raise
        except httpx.TimeoutException:
            _log_failure("provider_timeout")
            raise ProviderError("provider_timeout") from None
        except httpx.HTTPError:
            _log_failure("provider_http_error")
            raise ProviderError("provider_http_error") from None
        return _decode_response(content)


async def _bounded_content(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > maximum:
            _log_failure("provider_response_too_large")
            raise ProviderError("provider_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _wire_messages(messages: tuple[Mapping[str, object], ...]) -> list[dict[str, str]]:
    rendered: list[dict[str, str]] = []
    for message in messages:
        if set(message) != {"role", "content"}:
            raise ProviderError("provider_request_invalid")
        role = message["role"]
        content = message["content"]
        if type(role) is not str or role not in _ALLOWED_ROLES or type(content) is not str:
            raise ProviderError("provider_request_invalid")
        rendered.append({"role": role, "content": content})
    return rendered


class _AmbiguousJSON(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _AmbiguousJSON
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _AmbiguousJSON


def _json(value: bytes | str) -> object:
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)


def _normalize_usage(value: object) -> tuple[dict[str, object], str | None]:
    if type(value) is not dict:
        return {}, "provider_usage_unknown" if value is None else "provider_usage_invalid"
    safe: dict[str, object] = {}
    invalid = False
    for key in (
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        if key not in value:
            continue
        counter = value[key]
        if type(counter) is int and counter >= 0:
            safe[key] = counter
        elif key in {"total_tokens", "prompt_tokens", "completion_tokens"}:
            invalid = True
    components = "prompt_tokens" in safe and "completion_tokens" in safe
    if components and "total_tokens" in safe:
        if safe["total_tokens"] != cast(int, safe["prompt_tokens"]) + cast(
            int, safe["completion_tokens"]
        ):
            safe.pop("total_tokens")
            invalid = True
    if invalid:
        return safe, "provider_usage_invalid"
    if "total_tokens" not in safe and not components:
        return safe, "provider_usage_unknown"
    return safe, None


def _decode_response(content: bytes) -> ModelResponse:
    try:
        envelope = _json(content)
        if type(envelope) is not dict:
            raise ValueError
    except (ValueError, UnicodeDecodeError, RecursionError):
        _log_failure("provider_malformed_response")
        raise ProviderError("provider_malformed_response") from None
    usage, usage_error = _normalize_usage(envelope.get("usage"))
    if usage_error is not None:
        raise ProviderError(usage_error, usage=usage)
    choices = envelope.get("choices")
    if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
        raise ProviderError("provider_choices_invalid", usage=usage)
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish != "stop":
        reason = {
            "length": "provider_output_truncated",
            "content_filter": "provider_output_filtered",
        }.get(finish if type(finish) is str else "", "provider_completion_unknown")
        raise ProviderError(reason, usage=usage)
    message = choice.get("message")
    if type(message) is not dict or type(message.get("content")) is not str:
        raise ProviderError("provider_malformed_response", usage=usage)
    if message.get("tool_calls") or message.get("function_call"):
        raise ProviderError("provider_wire_action_invalid", usage=usage)
    try:
        payload = _json(message["content"])
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderError("provider_invalid_json", usage=usage, response_completed=True) from None
    except (_AmbiguousJSON, RecursionError, ValueError):
        raise ProviderError("provider_ambiguous_output", usage=usage) from None
    if type(payload) is not dict:
        raise ProviderError("provider_output_not_object", usage=usage, response_completed=True)
    return ModelResponse(payload=payload, usage=usage, response_completed=True)


def _log_failure(error_code: str, status_code: int | None = None) -> None:
    logger.warning(
        "model_provider_failed",
        extra={
            "event": "model_provider_failed",
            "provider_name": DeepSeekChatProvider.provider_name,
            "error_code": error_code,
            "status_code": status_code,
        },
    )
