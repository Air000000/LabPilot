from dataclasses import dataclass


def _require_text(value: str, *, field: str, max_bytes: int | None = None) -> None:
    if not value:
        raise ValueError(f"{field} must not be empty")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    if max_bytes is not None and size > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} UTF-8 bytes")


@dataclass(frozen=True, slots=True)
class CommandActor:
    actor_type: str
    actor_id: str | None

    def __post_init__(self) -> None:
        _require_text(self.actor_type, field="actor_type")
        if self.actor_id is not None:
            _require_text(self.actor_id, field="actor_id", max_bytes=200)


@dataclass(frozen=True, slots=True)
class CommandContext:
    installation_identity: str
    actor: CommandActor
    interaction_correlation_id: str

    def __post_init__(self) -> None:
        _require_text(self.installation_identity, field="installation_identity")
        _require_text(
            self.interaction_correlation_id,
            field="interaction_correlation_id",
        )
