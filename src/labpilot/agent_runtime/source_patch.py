"""Bounded draft evidence; no source writes or execution authority."""

from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PATCH_BYTES = 32 * 1024
MAX_PATCH_SOURCE_LINES = 500


class SourcePatchContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    profile: Literal["demo-training-body-draft-v1"] = "demo-training-body-draft-v1"
    base_snapshot_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    train_py_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourcePatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    context: SourcePatchContext
    train_py: str

    @model_validator(mode="after")
    def complete_source(self) -> "SourcePatchInput":
        data = self.train_py.encode("utf-8", "strict")
        if (
            not data
            or len(data) > MAX_PATCH_BYTES
            or len(self.train_py.splitlines()) > MAX_PATCH_SOURCE_LINES
            or sha256(data).hexdigest() != self.context.train_py_sha256
        ):
            raise ValueError("source_patch_source_invalid")
        return self


SourcePatchLoader = Callable[[UUID, UUID, SourcePatchContext | None], Awaitable[SourcePatchInput]]
