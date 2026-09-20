from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest
from pydantic import SecretStr

from labpilot.agent_runtime.deepseek import DeepSeekChatProvider
from labpilot.agent_runtime.provider import ModelRequest, ProviderError

ENDPOINT = "https://api.deepseek.com/chat/completions"


def _request(*, semantic_parameters: Mapping[str, object] | None = None) -> ModelRequest:
    return ModelRequest(
        model="deepseek-v4-flash",
        messages=(
            {
                "role": "system",
                "content": "Return exactly one JSON object matching the schema.",
            },
            {"role": "user", "content": "bounded context"},
        ),
        semantic_parameters=semantic_parameters or {},
        output_schema={"type": "object"},
        bound_toolset_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_posts_deepseek_json_non_thinking_contract_and_extracts_usage() -> None:
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
                            "content": json.dumps(
                                {"kind": "stop", "reason_code": "goal_satisfied"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    provider = DeepSeekChatProvider(
        model="deepseek-v4-flash",
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
    assert json.loads(captured.content) == {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": "Return exactly one JSON object matching the schema.",
            },
            {"role": "user", "content": "bounded context"},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    assert response.payload == {"kind": "stop", "reason_code": "goal_satisfied"}
    assert response.usage == {"prompt_tokens": 4, "completion_tokens": 2}


@pytest.mark.asyncio
async def test_non_empty_semantic_parameters_are_rejected_without_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = DeepSeekChatProvider(
        model="deepseek-v4-flash",
        api_key=SecretStr("key"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError, match="provider_parameters_unsupported"):
        await provider.complete(_request(semantic_parameters={"temperature": 0}))

    assert calls == 0


@pytest.mark.asyncio
async def test_non_2xx_never_leaks_secret_or_response_body(caplog) -> None:
    secret = "sentinel-http-secret"
    response_secret = "sentinel-response-body"
    provider = DeepSeekChatProvider(
        model="deepseek-v4-flash",
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text=response_secret)
        ),
    )

    with pytest.raises(ProviderError, match="provider_http_error") as captured:
        await provider.complete(_request())

    rendered = f"{captured.value!r}\n{caplog.text}"
    assert secret not in rendered
    assert response_secret not in rendered
