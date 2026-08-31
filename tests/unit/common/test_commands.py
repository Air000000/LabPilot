from dataclasses import FrozenInstanceError

import pytest

from labpilot.common.commands import CommandActor, CommandContext


def test_command_context_keeps_server_authoritative_actor() -> None:
    actor = CommandActor(actor_type="local_user", actor_id="air")
    context = CommandContext(
        installation_identity="installation-1",
        actor=actor,
        interaction_correlation_id="interaction-1",
    )

    assert context.actor.actor_id == "air"


def test_command_context_rejects_empty_installation_identity() -> None:
    with pytest.raises(ValueError, match="installation_identity"):
        CommandContext(
            installation_identity="",
            actor=CommandActor(actor_type="local_user", actor_id="air"),
            interaction_correlation_id="interaction-1",
        )


def test_command_actor_rejects_empty_actor_type() -> None:
    with pytest.raises(ValueError, match="actor_type"):
        CommandActor(actor_type="", actor_id="air")


def test_command_actor_rejects_actor_id_over_200_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="actor_id"):
        CommandActor(actor_type="local_user", actor_id="界" * 67)


def test_command_contracts_are_frozen() -> None:
    actor = CommandActor(actor_type="local_user", actor_id="air")

    with pytest.raises(FrozenInstanceError):
        actor.actor_id = "other"


def test_command_contracts_use_slots() -> None:
    actor = CommandActor(actor_type="local_user", actor_id="air")
    context = CommandContext(
        installation_identity="installation-1",
        actor=actor,
        interaction_correlation_id="interaction-1",
    )

    assert not hasattr(actor, "__dict__")
    assert not hasattr(context, "__dict__")
