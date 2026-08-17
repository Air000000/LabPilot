from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

CONFIG_ENV = "LABPILOT_SLICE2_ACCEPTANCE_CONFIG"
TESTED_SHA_ENV = "TESTED_SHA"
_CONFIG_KEYS = {
    "version",
    "dedicated_candidate_b_host",
    "expected_hostname",
    "repository_root",
    "workspace_root",
    "runner_policy_source",
    "evidence_root",
    "dataset_root",
    "database_url_env",
    "api_base_url",
    "compose_project",
    "python_profile",
}
_PATH_KEYS = (
    "repository_root",
    "workspace_root",
    "runner_policy_source",
    "evidence_root",
    "dataset_root",
)
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_COMPOSE_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_MAX_CONFIG_BYTES = 64 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}$")


class NativeOptInRequired(RuntimeError):
    pass


class NativeAcceptanceConfig(dict[str, object]):
    pass


def load_config(path: Path) -> NativeAcceptanceConfig:
    if not path.is_absolute():
        raise NativeOptInRequired("explicit native acceptance config path must be absolute")
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise NativeOptInRequired(
                "explicit native acceptance config must be a regular non-symlink file"
            )
        encoded = path.read_bytes()
        if len(encoded) > _MAX_CONFIG_BYTES:
            raise NativeOptInRequired("explicit native acceptance config is too large")
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise NativeOptInRequired("explicit native acceptance config is unreadable") from error
    if type(value) is not dict or set(value) != _CONFIG_KEYS:
        raise NativeOptInRequired("native acceptance config schema has missing or unknown fields")
    if type(value["version"]) is not int or value["version"] != 1:
        raise NativeOptInRequired("native acceptance config version must be 1")
    if (
        type(value["dedicated_candidate_b_host"]) is not bool
        or value["dedicated_candidate_b_host"] is not True
    ):
        raise NativeOptInRequired("dedicated Candidate B host authority must be true")
    for key in _CONFIG_KEYS - {"version", "dedicated_candidate_b_host"}:
        _config_string(value, key)
    for key in _PATH_KEYS:
        if not Path(_config_string(value, key)).is_absolute():
            raise NativeOptInRequired(f"explicit {key} must be an absolute native path")
    if _ENVIRONMENT_NAME.fullmatch(_config_string(value, "database_url_env")) is None:
        raise NativeOptInRequired("database_url_env must name an environment variable")
    try:
        api_url = urlsplit(_config_string(value, "api_base_url"))
        invalid_api_url = (
            api_url.scheme not in {"http", "https"}
            or not api_url.netloc
            or api_url.username is not None
            or api_url.password is not None
        )
    except ValueError as error:
        raise NativeOptInRequired("explicit API URL is invalid") from error
    if invalid_api_url:
        raise NativeOptInRequired("explicit API URL must not contain credentials")
    if _COMPOSE_PROJECT.fullmatch(_config_string(value, "compose_project")) is None:
        raise NativeOptInRequired("compose_project is invalid")
    return NativeAcceptanceConfig(value)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    execute: Callable[[object], Any] | None = None,
    hostname: Callable[[], str] = socket.gethostname,
    wsl_detector: Callable[[], bool] | None = None,
) -> int:
    config_path = environ.get(CONFIG_ENV)
    if not config_path:
        raise NativeOptInRequired(
            f"explicit opt-in requires {CONFIG_ENV} pointing to test-admin config"
        )
    config = load_config(Path(config_path))
    _verify_host_authority(
        config,
        actual_hostname=hostname(),
        is_wsl=(wsl_detector or _running_under_wsl)(),
    )
    database_variable = _config_string(config, "database_url_env")
    if not environ.get(database_variable):
        raise NativeOptInRequired(
            f"explicit database authority is missing from {database_variable}"
        )
    tested_sha = environ.get(TESTED_SHA_ENV, "")
    if _SHA.fullmatch(tested_sha) is None:
        raise NativeOptInRequired(f"explicit exact tested commit is missing from {TESTED_SHA_ENV}")

    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from tests.acceptance.slice2.scenarios import select_scenarios

    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--scenarios")
    choice.add_argument("--scenario")
    choice.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--phase", choices=("prepare", "verify"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    selected = () if args.preflight_only else select_scenarios(args.scenarios or args.scenario)
    if args.phase is not None and [scenario.name for scenario in selected] != ["F18"]:
        parser.error("--phase is valid only for F18")
    if args.phase is None and [scenario.name for scenario in selected] == ["F18"]:
        parser.error("F18 requires --phase prepare or verify")
    request = {
        "config": config,
        "scenarios": selected,
        "phase": args.phase or "run",
        "database_url": environ[database_variable],
        "tested_sha": tested_sha,
    }
    if args.preflight_only:
        request["phase"] = "preflight"
    if execute is None:
        from tests.acceptance.slice2.scenarios import run_native_request

        execute = run_native_request
    execute(request)
    return 0


def _verify_host_authority(
    config: Mapping[str, object], *, actual_hostname: str, is_wsl: bool
) -> None:
    expected = _config_string(config, "expected_hostname")
    if actual_hostname != expected:
        raise NativeOptInRequired(
            f"native acceptance hostname mismatch: expected {expected!r}, got {actual_hostname!r}"
        )
    if is_wsl:
        raise NativeOptInRequired("WSL is not a dedicated native acceptance environment")


def _running_under_wsl() -> bool:
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    for path in (Path("/proc/sys/kernel/osrelease"), Path("/proc/version")):
        try:
            if "microsoft" in path.read_text(encoding="ascii").lower():
                return True
        except (OSError, UnicodeError):
            continue
    return False


def _config_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item or len(item.encode("utf-8")) > 4096:
        raise NativeOptInRequired(f"explicit {key} must be a bounded non-empty string")
    return item


if __name__ == "__main__":
    raise SystemExit(main())
