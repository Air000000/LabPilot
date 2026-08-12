from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _startup_composition(tmp_path: Path, provider_environment: dict[str, str]) -> dict[str, object]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("LABPILOT_AGENT_PROVIDER_"):
            environment.pop(name)
    environment.update(
        {
            "LABPILOT_ENVIRONMENT": "test",
            "LABPILOT_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:9/labpilot",
            "LABPILOT_INSTALLATION_IDENTITY": "test-installation",
            "LABPILOT_LOCAL_ACTOR_ID": "test-actor",
            "LABPILOT_REPOSITORY_ALLOWED_ROOTS": "[]",
            "LABPILOT_REPOSITORY_CONTENT_STORE_ROOT": str(tmp_path),
        }
    )
    environment.update(provider_environment)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from labpilot import main; import json; print(json.dumps({"
            "'provider_name': main.provider.provider_name, "
            "'provider_model_name': getattr(main.provider, 'model_name', None), "
            "'timeout_seconds': getattr(main.provider, '_timeout_seconds', None), "
            "'max_response_bytes': getattr(main.provider, '_max_response_bytes', None), "
            "'agent_model_name': main.agent_runs._model_name}))",
        ],
        check=True,
        capture_output=True,
        cwd=Path(__file__).parents[2],
        env=environment,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_startup_uses_opencode_provider_with_complete_configuration(tmp_path: Path) -> None:
    composition = _startup_composition(
        tmp_path,
        {
            "LABPILOT_AGENT_PROVIDER_MODEL": "deepseek-v4-flash",
            "LABPILOT_AGENT_PROVIDER_API_KEY": "unit-test-not-a-real-key",
            "LABPILOT_AGENT_PROVIDER_TIMEOUT_SECONDS": "17",
            "LABPILOT_AGENT_PROVIDER_MAX_RESPONSE_BYTES": "4096",
        },
    )

    assert composition == {
        "provider_name": "opencode_go_chat",
        "provider_model_name": "deepseek-v4-flash",
        "timeout_seconds": 17,
        "max_response_bytes": 4096,
        "agent_model_name": "deepseek-v4-flash",
    }


def test_startup_uses_unconfigured_provider_without_credentials(tmp_path: Path) -> None:
    composition = _startup_composition(tmp_path, {})

    assert composition == {
        "provider_name": "unconfigured",
        "provider_model_name": None,
        "timeout_seconds": None,
        "max_response_bytes": None,
        "agent_model_name": "unconfigured",
    }


@pytest.mark.parametrize(
    "provider_environment",
    [
        {"LABPILOT_AGENT_PROVIDER_MODEL": "deepseek-v4-flash"},
        {"LABPILOT_AGENT_PROVIDER_API_KEY": "unit-test-not-a-real-key"},
    ],
    ids=["model_only", "api_key_only"],
)
def test_startup_uses_unconfigured_provider_with_partial_credentials(
    tmp_path: Path, provider_environment: dict[str, str]
) -> None:
    composition = _startup_composition(tmp_path, provider_environment)

    assert composition["provider_name"] == "unconfigured"
    assert composition["agent_model_name"] == "unconfigured"
