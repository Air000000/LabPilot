import grp
import hashlib
import json
import os
import pwd
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from labpilot.common.canonical_json import CanonicalValue
from labpilot.common.hashing import namespaced_sha256

CANDIDATE_B_V2 = "candidate-b-v2"
TERMINAL_RECEIPT_SCHEMA = "labpilot-terminal-receipt-v1"
TERMINAL_RECEIPT_REPORTER_PATH = Path(
    "/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py"
)
TERMINAL_RECEIPT_BUNDLED_SOURCE_PATH = Path(
    "/opt/labpilot-runner/app/deploy/libexec/labpilot-terminal-receipt-v1.py"
)
TRUSTED_REPORTER_INTERPRETER = Path("/usr/bin/python3")
_TERMINAL_RECEIPT_REPORTER_SHA256 = (
    "5966c05fd18d6d16c0086fbd4f6906ac36f252cbdc5dfbf7c6df3002d43ac499"
)

SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "CUDA_HOME",
        "CUDA_VISIBLE_DEVICES",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "TOKENIZERS_PARALLELISM",
    }
)
_POLICY_KEYS = {
    "controller_user",
    "controller_group",
    "execution_user",
    "execution_group",
    "socket_path",
    "workspace_root",
    "runtime_profiles",
    "allowed_local_dataset_roots",
    "launch_profile_version",
    "max_runtime_seconds",
    "stop_timeout_seconds",
}


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    name: str
    runtime_path: str
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CandidateBV2ProfileManifest:
    launch_profile_version: str
    terminal_receipt_schema: str
    reporter_path: str
    reporter_sha256: str


def candidate_b_v2_profile_manifest(
    reporter_sha256: str,
) -> CandidateBV2ProfileManifest:
    if (
        len(reporter_sha256) != 64
        or any(character not in "0123456789abcdef" for character in reporter_sha256)
    ):
        raise ValueError("reporter_sha256_invalid")
    return CandidateBV2ProfileManifest(
        launch_profile_version=CANDIDATE_B_V2,
        terminal_receipt_schema=TERMINAL_RECEIPT_SCHEMA,
        reporter_path=str(TERMINAL_RECEIPT_REPORTER_PATH),
        reporter_sha256=reporter_sha256,
    )


def runner_policy_identity(
    policy_value: Mapping[str, object],
    profile_manifest: CandidateBV2ProfileManifest,
) -> str:
    manifest_value: dict[str, CanonicalValue] = {
        "launch_profile_version": profile_manifest.launch_profile_version,
        "terminal_receipt_schema": profile_manifest.terminal_receipt_schema,
        "reporter_path": profile_manifest.reporter_path,
        "reporter_sha256": profile_manifest.reporter_sha256,
    }
    identity_value: dict[str, CanonicalValue] = {
        "policy": cast(dict[str, CanonicalValue], dict(policy_value)),
        "profile_manifest": manifest_value,
    }
    return namespaced_sha256(
        "labpilot-runner-policy:v2",
        identity_value,
    )


_EMPTY_TERMINAL_RECEIPT = (
    b'{"active_state":null,"exec_main_code":null,'
    b'"exec_main_status":null,"result":null}\n'
)


@dataclass(frozen=True, slots=True)
class RunnerPolicy:
    controller_user: str
    controller_group: str
    controller_uid: int
    controller_gid: int
    execution_user: str
    execution_group: str
    socket_path: str
    workspace_root: str
    runtime_profiles: tuple[RuntimeProfile, ...]
    allowed_local_dataset_roots: tuple[str, ...]
    launch_profile_version: str
    max_runtime_seconds: int
    stop_timeout_seconds: int
    policy_identity: str

    def runtime_profile(self, name: str) -> RuntimeProfile:
        for profile in self.runtime_profiles:
            if profile.name == name:
                return profile
        raise ValueError("python_profile_not_admitted")


def load_runner_policy(path: Path) -> RunnerPolicy:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("policy_symlink_denied")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("policy_not_regular_file")
    if metadata.st_uid != 0:
        raise ValueError("policy_not_root_owned")
    if metadata.st_mode & 0o022:
        raise ValueError("policy_permissions_unsafe")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("policy_invalid_json") from error
    controller_user = _string(value, "controller_user")
    controller_group = _string(value, "controller_group")
    profile_manifest = validate_candidate_b_v2_deployment()
    return parse_runner_policy(
        value,
        controller_uid=pwd.getpwnam(controller_user).pw_uid,
        controller_gid=grp.getgrnam(controller_group).gr_gid,
        profile_manifest=profile_manifest,
    )


def validate_candidate_b_v2_deployment(
    reporter_path: Path = TERMINAL_RECEIPT_REPORTER_PATH,
    interpreter_path: Path = TRUSTED_REPORTER_INTERPRETER,
) -> CandidateBV2ProfileManifest:
    reporter_digest = _trusted_file_sha256(reporter_path)
    bundled_digest = _trusted_file_sha256(TERMINAL_RECEIPT_BUNDLED_SOURCE_PATH)
    if (
        reporter_digest != bundled_digest
        or reporter_digest != _TERMINAL_RECEIPT_REPORTER_SHA256
    ):
        raise ValueError("terminal_receipt_reporter_digest_mismatch")

    _validate_directory_chain(interpreter_path)
    try:
        resolved_interpreter = interpreter_path.resolve(strict=True)
    except OSError as error:
        raise ValueError("reporter_interpreter_unavailable") from error
    _validate_directory_chain(resolved_interpreter)
    _validate_regular_metadata(
        resolved_interpreter.lstat(), kind="reporter_interpreter"
    )

    try:
        result = subprocess.run(
            [str(interpreter_path), "-I", "-B", str(reporter_path)],
            capture_output=True,
            check=False,
            env={},
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("terminal_receipt_reporter_sanity_failed") from error
    if (
        result.returncode != 0
        or result.stdout != _EMPTY_TERMINAL_RECEIPT
        or result.stderr != b""
    ):
        raise ValueError("terminal_receipt_reporter_sanity_failed")
    return candidate_b_v2_profile_manifest(reporter_digest)


def _validate_directory_chain(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("deployment_path_not_absolute")
    directories: list[Path] = []
    parent = path.parent
    while parent != parent.parent:
        directories.append(parent)
        parent = parent.parent
    for directory in reversed(directories):
        try:
            metadata = directory.lstat()
        except OSError as error:
            raise ValueError("deployment_directory_unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("deployment_directory_invalid")
        if metadata.st_uid != 0:
            raise ValueError("deployment_directory_not_root_owned")
        if metadata.st_mode & 0o022:
            raise ValueError("deployment_directory_permissions_unsafe")


def _validate_regular_metadata(metadata: os.stat_result, *, kind: str) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{kind}_symlink_denied")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{kind}_not_regular_file")
    if metadata.st_uid != 0:
        raise ValueError(f"{kind}_not_root_owned")
    if metadata.st_mode & 0o022:
        raise ValueError(f"{kind}_permissions_unsafe")


def _trusted_file_sha256(path: Path) -> str:
    _validate_directory_chain(path)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError("terminal_receipt_reporter_unavailable") from error
    _validate_regular_metadata(metadata, kind="terminal_receipt_reporter")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("terminal_receipt_reporter_open_failed") from error
    try:
        opened = os.fstat(descriptor)
        _validate_regular_metadata(opened, kind="terminal_receipt_reporter")
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("terminal_receipt_reporter_changed")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 65536):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def parse_runner_policy(
    value: object,
    *,
    controller_uid: int,
    controller_gid: int,
    profile_manifest: CandidateBV2ProfileManifest | None = None,
) -> RunnerPolicy:
    if type(value) is not dict or set(value) != _POLICY_KEYS:
        raise ValueError("policy_schema_invalid")
    if (
        value["controller_user"],
        value["controller_group"],
        value["execution_user"],
        value["execution_group"],
    ) != (
        "labpilot-controller",
        "labpilot-control",
        "labpilot-train",
        "labpilot-train",
    ):
        raise ValueError("policy_identity_mismatch")
    launch_profile_version = _string(value, "launch_profile_version")
    if (
        profile_manifest is not None
        and launch_profile_version != profile_manifest.launch_profile_version
    ):
        raise ValueError("launch_profile_version_unsupported")
    profiles_value = value["runtime_profiles"]
    if type(profiles_value) is not dict or not profiles_value:
        raise ValueError("policy_schema_invalid")
    profiles: list[RuntimeProfile] = []
    for name, profile_value in profiles_value.items():
        if (
            type(name) is not str
            or type(profile_value) is not dict
            or set(profile_value) != {"runtime_path", "environment"}
        ):
            raise ValueError("policy_schema_invalid")
        runtime_path = _absolute_string(profile_value, "runtime_path")
        environment_value = profile_value["environment"]
        if type(environment_value) is not dict or any(
            type(key) is not str or key not in SAFE_ENVIRONMENT_KEYS or type(item) is not str
            for key, item in environment_value.items()
        ):
            raise ValueError("policy_environment_invalid")
        profiles.append(
            RuntimeProfile(name, runtime_path, tuple(sorted(environment_value.items())))
        )
    roots = value["allowed_local_dataset_roots"]
    if type(roots) is not list or any(
        type(root) is not str or not Path(root).is_absolute() for root in roots
    ):
        raise ValueError("policy_schema_invalid")
    max_runtime = value["max_runtime_seconds"]
    stop_timeout = value["stop_timeout_seconds"]
    if type(max_runtime) is not int or max_runtime <= 0:
        raise ValueError("policy_schema_invalid")
    if type(stop_timeout) is not int or stop_timeout <= 0:
        raise ValueError("policy_schema_invalid")
    return RunnerPolicy(
        controller_user=_string(value, "controller_user"),
        controller_group=_string(value, "controller_group"),
        controller_uid=controller_uid,
        controller_gid=controller_gid,
        execution_user=_string(value, "execution_user"),
        execution_group=_string(value, "execution_group"),
        socket_path=_absolute_string(value, "socket_path"),
        workspace_root=_absolute_string(value, "workspace_root"),
        runtime_profiles=tuple(sorted(profiles, key=lambda profile: profile.name)),
        allowed_local_dataset_roots=tuple(sorted(set(roots))),
        launch_profile_version=launch_profile_version,
        max_runtime_seconds=max_runtime,
        stop_timeout_seconds=stop_timeout,
        # ponytail: no-manifest mode keeps legacy v1 unit fixtures; production load binds v2.
        policy_identity=(
            namespaced_sha256("labpilot-runner-policy:v1", value)
            if profile_manifest is None
            else runner_policy_identity(value, profile_manifest)
        ),
    )


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item:
        raise ValueError("policy_schema_invalid")
    return item


def _absolute_string(value: dict[str, object], key: str) -> str:
    item = _string(value, key)
    if not Path(item).is_absolute():
        raise ValueError("policy_path_not_absolute")
    return item
