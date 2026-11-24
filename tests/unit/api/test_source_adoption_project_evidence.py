"""Reject malformed package headers using the closed reader error vocabulary."""

import json

import pytest

from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.api.source_adoption_evidence import read_adoption_evidence
from labpilot.context.content_store import ContentAddressedStore


@pytest.mark.parametrize("header", [[], None, "invalid"])
async def test_non_object_package_is_a_controlled_rejection(tmp_path, header):
    store = ContentAddressedStore(tmp_path / "content")
    ref = await store.put_bytes(json.dumps(header).encode())
    with pytest.raises(AgentRuntimeError, match="source_adoption_evidence_invalid"):
        await read_adoption_evidence(
            store, package_ref=ref, project_id="project", base_id="base", base_identity="a" * 64
        )
