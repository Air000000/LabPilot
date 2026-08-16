from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from labpilot.experiments.proposal_models import ProposalRevisionRow
from labpilot.experiments.proposal_types import proposal_payload_hash
from tests.integration.experiments.test_proposal_service import (
    _agent_revision,
    _payload,
    _proposal_system,
)


@pytest.mark.integration
async def test_legacy_absent_argv_key_round_trips_without_rehash(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        payload = replace(_payload(system), train_argv_template=None)
        historical_hash = proposal_payload_hash(payload)

        created = await _agent_revision(system, payload=payload)
        async with system.database.session() as session:
            row = (
                await session.execute(
                    select(ProposalRevisionRow).where(
                        ProposalRevisionRow.proposal_revision_id
                        == created.proposal_revision_id
                    )
                )
            ).scalar_one()

        loaded = await system.service.get_revision(created.proposal_revision_id)

        assert "train_argv_template" not in row.payload_json
        assert loaded.payload.train_argv_template is None
        assert loaded.payload_hash == historical_hash
        assert proposal_payload_hash(loaded.payload) == historical_hash
