from __future__ import annotations

import json
import logging
from collections.abc import Mapping

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
        if request.semantic_parameters:
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


def _decode_response(content: bytes) -> ModelResponse:
    try:
        envelope = json.loads(content, parse_constant=_reject_json_constant)
        if type(envelope) is not dict:
            raise ValueError
        choices = envelope["choices"]
        if type(choices) is not list or not choices or type(choices[0]) is not dict:
            raise ValueError
        message = choices[0]["message"]
        if type(message) is not dict:
            raise ValueError
        raw_payload = message["content"]
        if type(raw_payload) is not str:
            raise ValueError
        payload = json.loads(raw_payload, parse_constant=_reject_json_constant)
        if type(payload) is not dict:
            raise ValueError
        usage = envelope.get("usage", {})
        if type(usage) is not dict:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        _log_failure("provider_malformed_response")
        raise ProviderError("provider_malformed_response") from None
    return ModelResponse(payload=payload, usage=usage)


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


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
