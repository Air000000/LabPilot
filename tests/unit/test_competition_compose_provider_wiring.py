from pathlib import Path


def test_compose_passes_remote_agent_provider_configuration() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    app_environment = compose.split("environment: &app-env", 1)[1].split(
        "    command:", 1
    )[0]

    assert (
        "LABPILOT_DATA_EGRESS_POLICY: ${LABPILOT_DATA_EGRESS_POLICY:-local_only}"
        in app_environment
    )
    assert (
        "LABPILOT_AGENT_PROVIDER_MODEL: ${LABPILOT_AGENT_PROVIDER_MODEL:-}"
        in app_environment
    )
    assert (
        "LABPILOT_AGENT_PROVIDER_API_KEY: ${LABPILOT_AGENT_PROVIDER_API_KEY:-}"
        in app_environment
    )
