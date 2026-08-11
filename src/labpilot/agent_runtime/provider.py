from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

ProviderEgress = Literal["local", "remote"]
DataEgressPolicy = Literal["local_only", "remote_provider_allowed"]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    messages: tuple[Mapping[str, object], ...]
    semantic_parameters: Mapping[str, object]
    output_schema: Mapping[str, object]
    bound_toolset_hash: str


@dataclass(frozen=True, slots=True)
class ModelResponse:
    payload: Mapping[str, object]
    usage: Mapping[str, object]


class ModelProvider(Protocol):
    provider_name: str
    egress: ProviderEgress

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ProviderError(RuntimeError):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ProviderPolicyError(ProviderError):
    pass


def assert_provider_egress(policy: DataEgressPolicy, provider: ModelProvider) -> None:
    if policy == "local_only" and provider.egress == "remote":
        raise ProviderPolicyError("remote_provider_blocked")


class UnconfiguredModelProvider:
    provider_name = "unconfigured"
    egress: ProviderEgress = "local"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError("provider_unconfigured")
