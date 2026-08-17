import grp
import json
import pwd
import stat
from dataclasses import dataclass
from pathlib import Path

from labpilot.common.hashing import namespaced_sha256

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
    return parse_runner_policy(
        value,
        controller_uid=pwd.getpwnam(controller_user).pw_uid,
        controller_gid=grp.getgrnam(controller_group).gr_gid,
    )


def parse_runner_policy(value: object, *, controller_uid: int, controller_gid: int) -> RunnerPolicy:
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
        launch_profile_version=_string(value, "launch_profile_version"),
        max_runtime_seconds=max_runtime,
        stop_timeout_seconds=stop_timeout,
        policy_identity=namespaced_sha256("labpilot-runner-policy:v1", value),
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
