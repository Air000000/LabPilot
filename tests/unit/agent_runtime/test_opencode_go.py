from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest
from pydantic import SecretStr

from labpilot.agent_runtime.opencode_go import OpenCodeGoChatProvider
from labpilot.agent_runtime.provider import ModelRequest, ProviderError

ENDPOINT = "https://opencode.ai/zen/go/v1/chat/completions"


def _request(*, semantic_parameters: Mapping[str, object] | None = None) -> ModelRequest:
    return ModelRequest(
        model="go-model-raw-id",
        messages=(
            {"role": "system", "content": "system instruction"},
            {"role": "user", "content": "bounded context"},
        ),
        semantic_parameters=semantic_parameters or {},
        output_schema={"type": "object"},
        bound_toolset_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_posts_exact_minimal_chat_contract_and_extracts_usage() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"kind": "stop", "reason_code": "goal_satisfied"})
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr("sentinel-api-key"),
        timeout_seconds=60,
        max_response_bytes=1024 * 1024,
        transport=httpx.MockTransport(handler),
    )
    response = await provider.complete(_request())

    assert captured is not None
    assert captured.method == "POST"
    assert captured.url == httpx.URL(ENDPOINT)
    assert captured.headers["Authorization"] == "Bearer sentinel-api-key"
    assert captured.headers["Content-Type"] == "application/json"
    assert json.loads(captured.content) == {
        "model": "go-model-raw-id",
        "messages": [
            {"role": "system", "content": "system instruction"},
            {"role": "user", "content": "bounded context"},
        ],
    }
    assert response.payload == {"kind": "stop", "reason_code": "goal_satisfied"}
    assert response.usage == {"prompt_tokens": 4, "completion_tokens": 2}


@pytest.mark.asyncio
async def test_absent_usage_becomes_empty_object() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"kind":"stop","reason_code":"done"}'}}]},
        )
    )
    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr("key"),
        transport=transport,
    )

    response = await provider.complete(_request())

    assert response.usage == {}


@pytest.mark.asyncio
async def test_non_empty_semantic_parameters_are_rejected_without_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr("key"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError, match="provider_parameters_unsupported"):
        await provider.complete(_request(semantic_parameters={"temperature": 0}))

    assert calls == 0


@pytest.mark.asyncio
async def test_non_2xx_is_bounded_and_never_leaks_body_or_secret(caplog) -> None:
    secret = "sentinel-http-secret"
    response_secret = "sentinel-response-body"
    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text=response_secret)),
    )

    with pytest.raises(ProviderError, match="provider_http_error") as captured:
        await provider.complete(_request())

    rendered = f"{captured.value!r}\n{caplog.text}"
    assert secret not in rendered
    assert response_secret not in rendered


@pytest.mark.asyncio
async def test_timeout_has_bounded_error_code(caplog) -> None:
    secret = "sentinel-timeout-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout {secret}", request=request)

    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError, match="provider_timeout") as captured:
        await provider.complete(_request())

    assert secret not in f"{captured.value!r}\n{caplog.text}"


@pytest.mark.asyncio
async def test_response_size_is_bounded_before_json_decode(caplog) -> None:
    secret = "sentinel-oversized-secret"
    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr(secret),
        max_response_bytes=1024,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1025)),
    )

    with pytest.raises(ProviderError, match="provider_response_too_large") as captured:
        await provider.complete(_request())

    assert secret not in f"{captured.value!r}\n{caplog.text}"


@pytest.mark.parametrize(
    "body",
    [
        (
            b'{"choices":[{"message":{"content":"{\\"kind\\":\\"stop\\",'
            b'\\"reason_code\\":\\"done\\"}"}}],"usage":{"bad":NaN},'
            b'"raw":"sentinel-provider-body"}'
        ),
        (
            b'{"choices":[{"message":{"content":"{\\"kind\\":\\"tool_call\\",'
            b'\\"tool_name\\":\\"repository.read_text\\",\\"arguments\\":'
            b'{\\"value\\":Infinity}}"}}],"raw":"sentinel-provider-body"}'
        ),
    ],
    ids=["envelope_nan", "inner_content_infinity"],
)
@pytest.mark.asyncio
async def test_nonstandard_json_constants_are_malformed_and_redacted(
    body: bytes, caplog
) -> None:
    secret = "sentinel-json-constant-api-key"
    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )

    with pytest.raises(ProviderError, match="provider_malformed_response") as captured:
        await provider.complete(_request())

    rendered = f"{captured.value!r}\n{caplog.text}"
    assert secret not in rendered
    assert "sentinel-provider-body" not in rendered


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": 1}}]},
        {"choices": [{"message": {"content": "not-json"}}]},
        {"choices": [{"message": {"content": "[]"}}]},
        {"choices": [{"message": {"content": "{}"}}], "usage": []},
    ],
)
@pytest.mark.asyncio
async def test_malformed_envelopes_have_one_bounded_error(body: object, caplog) -> None:
    secret = "sentinel-malformed-secret"
    provider = OpenCodeGoChatProvider(
        model="go-model-raw-id",
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )

    with pytest.raises(ProviderError, match="provider_malformed_response") as captured:
        await provider.complete(_request())

    assert secret not in f"{captured.value!r}\n{caplog.text}"
