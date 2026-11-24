"""Local single-operator coding tasks; no LabPilot API or training authority."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import resource
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from baseline import git
from environment import BoundaryError, CodingEnvironment, bounded

MAX_CHANGED_FILES = 20
MAX_DIFF_BYTES = 512 * 1024
SYSTEM_TEMPLATE = (
    "Complete the code task inside /workspace. Use exactly one bash tool call per response. "
    "Pass the command in its command argument, not as JSON or code fences in prose. "
    "Keep responses concise and split large edits across commands. "
    "Read files, edit and run tests. This exported source is not a Git worktree. "
    "When finished call bash with echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT alone. "
    "Only declared paths may change; use /tmp for generated outputs. "
    "Preserve existing file modes: do not chmod or chown files. "
    "Changed files must be UTF-8 text; do not create symlinks, submodules or binary changes. "
    f"Change at most {MAX_CHANGED_FILES} files. Diff limit: {MAX_DIFF_BYTES} bytes. "
    "Submitted output is subject to these checks and separate independent validation. "
    "Frozen budgets: {{step_limit}} model calls (including format corrections and submission), "
    "{{token_budget}} cumulative response-accounted total_tokens (input, including cached input, "
    "plus output), output cap {{output_cap}} tokens per response, "
    "{{wall_time_limit_seconds}} seconds wall time. Token usage is charged after each response; "
    "a final response can exceed the cumulative threshold. "
    "Budget exhaustion is failure, not submission. Reserve a call to submit when ready; "
    "do not repeat passing tests without a reason."
)


def atomic(path: Path, data: dict):
    payload = json.dumps(data, sort_keys=True, indent=2).encode() + b"\n"
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".record-")
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fingerprint(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise BoundaryError("record_invalid")
    return json.loads(path.read_text())


def task_spec(root: Path) -> dict:
    spec = read(root / "task.json")
    identity = spec.pop("identity")
    if fingerprint(spec) != identity:
        raise BoundaryError("task_identity_mismatch")
    spec["identity"] = identity
    return spec


def baseline_files(spec: dict):
    root = Path(spec["baseline"])
    manifest = read(root / "baseline.json")
    if manifest["identity"] != spec["baseline_identity"]:
        raise BoundaryError("baseline_identity_mismatch")
    unsigned = {k: v for k, v in manifest.items() if k != "identity"}
    if fingerprint(unsigned) != manifest["identity"]:
        raise BoundaryError("baseline_manifest_changed")
    files, modes = {}, {}
    for entry in manifest["entries"]:
        if entry.get("deleted"):
            continue
        p = root / "source" / entry["path"]
        if p.is_symlink() or not p.resolve().is_relative_to(root / "source"):
            raise BoundaryError("baseline_path_invalid")
        value = p.read_bytes()
        if sha256(value).hexdigest() != entry["sha256"]:
            raise BoundaryError("baseline_content_changed")
        files[entry["path"]] = value
        modes[entry["path"]] = int(entry["git_mode"], 8) & 0o777
    return files, modes


def executor_identity():
    root = Path(__file__).parent
    return fingerprint(
        {
            p.name: sha256(p.read_bytes()).hexdigest()
            for p in [*sorted(root.glob("*.py")), root / "uv.lock", root / "pyproject.toml"]
        }
    )


def prepare(args):
    if not (
        1 <= args.calls <= 100
        and 1 <= args.tokens <= 1_000_000
        and 1 <= args.wall_seconds <= 3600
        and 1 <= args.output_cap <= 32768
    ):
        raise BoundaryError("task_budget_invalid")
    endpoint = urlsplit(args.api_base)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise BoundaryError("provider_endpoint_invalid")
    baseline = args.baseline.resolve(strict=True)
    manifest = read(baseline / "baseline.json")
    profile = read(args.profile)
    if set(profile) != {"image", "editable_paths", "ignored_paths", "validators"}:
        raise BoundaryError("profile_fields_invalid")
    for key in ("editable_paths", "ignored_paths"):
        if (
            not isinstance(profile[key], list)
            or (key == "editable_paths" and not profile[key])
            or any(
                not isinstance(p, str)
                or p.startswith("/")
                or ".." in p.split("/")
                or ".git" in p.split("/")
                for p in profile[key]
            )
        ):
            raise BoundaryError("profile_paths_invalid")
    if not profile["validators"] or len(profile["validators"]) > 4:
        raise BoundaryError("validators_invalid")
    images = [profile["image"], *[v.get("image", "") for v in profile["validators"]]]
    if any(
        type(image) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
        for image in images
    ):
        raise BoundaryError("profile_image_digest_required")
    for validator in profile["validators"]:
        if (
            set(validator) != {"image", "argv"}
            or not validator["argv"]
            or any(not isinstance(v, str) for v in validator["argv"])
        ):
            raise BoundaryError("validator_invalid")
    source_paths = [e["path"] for e in manifest["entries"]]
    if any(fnmatchcase(p, pattern) for p in source_paths for pattern in profile["ignored_paths"]):
        raise BoundaryError("ignored_path_matches_baseline")
    root = args.task.resolve()
    if (
        root.exists()
        or root.is_relative_to(baseline)
        or root.is_relative_to(Path(manifest["source_repository"]))
    ):
        raise BoundaryError("task_destination_invalid")
    binding = None
    if any((args.project_id, args.snapshot_id, args.snapshot_identity)):
        if not (
            args.project_id
            and args.snapshot_id
            and args.snapshot_identity
            and re.fullmatch(r"[0-9a-f]{64}", args.snapshot_identity)
        ):
            raise BoundaryError("snapshot_binding_invalid")
        binding = {
            "project_id": str(UUID(args.project_id)),
            "snapshot_id": str(UUID(args.snapshot_id)),
            "snapshot_identity": args.snapshot_identity,
        }
    root.mkdir(mode=0o700, parents=True)
    spec = {
        "schema_version": "labpilot.coding_task.v1",
        "task_id": uuid4().hex,
        "executor_identity": executor_identity(),
        "baseline": str(baseline),
        "baseline_identity": manifest["identity"],
        "snapshot_binding": binding,
        "goal": args.goal.read_text(),
        "profile": profile,
        "calls": args.calls,
        "tokens": args.tokens,
        "output_cap": args.output_cap,
        "wall_seconds": args.wall_seconds,
        "model": args.model,
        "api_base": args.api_base,
    }
    spec["identity"] = fingerprint(spec)
    baseline_files(spec)
    atomic(root / "task.json", spec)
    atomic(
        root / "status.json",
        {"status": "prepared", "task_id": spec["task_id"], "task_identity": spec["identity"]},
    )
    (root / "lock").touch(mode=0o600)
    print(
        json.dumps(
            {"status": "prepared", "task_id": spec["task_id"], "task_identity": spec["identity"]}
        )
    )


def filtered(tree: dict, profile: dict):
    return {
        name: value
        for name, value in tree.items()
        if not any(fnmatchcase(name, p) for p in profile["ignored_paths"])
    }


def preserve_export(root: Path, spec: dict, tree: dict) -> str:
    """Retain an unchecked frozen tree for audit; it never grants candidate authority."""
    destination = root / "exported"
    destination.mkdir(mode=0o700)
    entries = []
    for name, (mode, data) in sorted(tree.items()):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve logical modes in the manifest; audit copies stay non-executable/private.
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        entries.append({"path": name, "mode": mode, "sha256": sha256(data).hexdigest()})
    manifest = {
        "schema_version": "labpilot.coding_export.v1",
        "status": "unchecked",
        "task_identity": spec["identity"],
        "entries": entries,
    }
    manifest["identity"] = fingerprint(manifest)
    atomic(root / "export.json", manifest)
    return manifest["identity"]


def collect(root: Path, spec: dict, tree: dict):
    files, modes = baseline_files(spec)
    old = {k: (modes[k], v) for k, v in files.items()}
    tree = filtered(tree, spec["profile"])
    changed = [p for p in sorted(set(old) | set(tree)) if old.get(p) != tree.get(p)]
    if not changed:
        raise BoundaryError("candidate_no_changes")
    if len(changed) > MAX_CHANGED_FILES:
        raise BoundaryError("candidate_change_limit")
    for name in changed:
        if not any(fnmatchcase(name, p) for p in spec["profile"]["editable_paths"]):
            raise BoundaryError("candidate_outside_scope")
        if name in old and name in tree and old[name][0] != tree[name][0]:
            raise BoundaryError("candidate_mode_changed")
        for value in (old.get(name), tree.get(name)):
            if value:
                value[1].decode("utf-8")
    destination = root / "candidate"
    destination.mkdir(mode=0o700)
    entries = []
    for name, (mode, data) in sorted(tree.items()):
        p = destination / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(mode)
        entries.append({"path": name, "mode": mode, "sha256": sha256(data).hexdigest()})
    manifest = {
        "task_identity": spec["identity"],
        "base_identity": spec["baseline_identity"],
        "changed_paths": changed,
        "entries": entries,
    }
    manifest["identity"] = fingerprint(manifest)
    atomic(root / "candidate.json", manifest)
    # Git sees only the trusted temporary copy; candidate commands never run on the host.
    with tempfile.TemporaryDirectory(prefix="coding-diff-", dir=root) as directory:
        temp = Path(directory)
        for name, data in files.items():
            p = temp / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(modes[name])
        git(temp, "init", "-q", "-b", "main")
        git(temp, "add", "--force", "--all")
        git(
            temp,
            "-c",
            "user.name=LabPilot Local",
            "-c",
            "user.email=local@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "Frozen coding baseline",
        )
        for name in files:
            if name not in tree:
                (temp / name).unlink()
        for name, (mode, data) in tree.items():
            p = temp / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(mode)
        git(temp, "add", "--force", "--all")
        patch = git(temp, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv")
        if len(patch) > MAX_DIFF_BYTES:
            raise BoundaryError("candidate_diff_limit")
        (root / "candidate.diff").write_bytes(patch)
        # Replay on fresh baseline state and verify every candidate byte and mode.
        git(temp, "reset", "--hard", "-q", "HEAD")
        # Reset removes the newly added files because they were staged above.
        result = subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "apply", "--check", "-"],
            cwd=temp,
            input=patch,
            capture_output=True,
            timeout=10,
        )
        if result.returncode:
            raise BoundaryError("candidate_replay_failed")
        result = subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "apply", "-"],
            cwd=temp,
            input=patch,
            capture_output=True,
            timeout=10,
        )
        actual = {}
        for p in temp.rglob("*"):
            if ".git" in p.relative_to(temp).parts or not p.is_file():
                continue
            actual[p.relative_to(temp).as_posix()] = (
                0o755 if p.stat().st_mode & 0o111 else 0o644,
                p.read_bytes(),
            )
        if result.returncode or actual != tree:
            raise BoundaryError("candidate_replay_mismatch")
    return manifest


def candidate_tree(root: Path, spec: dict):
    candidate = read(root / "candidate.json")
    unsigned = {k: v for k, v in candidate.items() if k != "identity"}
    if fingerprint(unsigned) != candidate["identity"]:
        raise BoundaryError("candidate_manifest_changed")
    if candidate["task_identity"] != spec["identity"]:
        raise BoundaryError("candidate_task_mismatch")
    tree = {}
    for entry in candidate["entries"]:
        p = root / "candidate" / entry["path"]
        if p.is_symlink() or not p.resolve().is_relative_to(root / "candidate"):
            raise BoundaryError("candidate_path_invalid")
        data = p.read_bytes()
        if sha256(data).hexdigest() != entry["sha256"]:
            raise BoundaryError("candidate_content_changed")
        tree[entry["path"]] = (entry["mode"], data)
    return candidate, tree


def validate(root: Path, spec: dict):
    candidate, tree = candidate_tree(root, spec)
    reports = []
    validation_id = uuid4().hex
    for index, validator in enumerate(spec["profile"]["validators"]):
        env = CodingEnvironment(
            validator["image"], task_id=spec["task_id"] + "-validate-" + str(index)
        )
        try:
            env.load({p: v[1] for p, v in tree.items()}, {p: v[0] for p, v in tree.items()})
            started = time.monotonic()
            result = env.execute({"command": shlex.join(validator["argv"])})
            elapsed = time.monotonic() - started
            after = filtered(env.export(), spec["profile"])
            passed = result["returncode"] == 0 and after == tree
            (root / ("validation-" + validation_id + "-" + str(index) + ".log")).write_text(
                result["output"]
            )
            reports.append(
                {
                    "image": validator["image"],
                    "elapsed_seconds": elapsed,
                    "output_file": "validation-" + validation_id + "-" + str(index) + ".log",
                    "argv": validator["argv"],
                    "exit_code": result["returncode"],
                    "source_unchanged": after == tree,
                    "passed": passed,
                    "output_sha256": sha256(result["output"].encode()).hexdigest(),
                }
            )
        finally:
            env.cleanup()
    report = {
        "candidate_identity": candidate["identity"],
        "task_identity": spec["identity"],
        "passed": all(r["passed"] for r in reports),
        "checks": reports,
    }
    report["identity"] = fingerprint(report)
    atomic(root / ("validation-" + validation_id + ".json"), report)
    atomic(root / "validation.json", report)
    return report


def execute(root: Path, actions: Path | None, allow_model: bool):
    from budget import BudgetModel
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.models.test_models import DeterministicModel, make_output

    spec = task_spec(root)
    if not actions and (not allow_model or not spec["model"]):
        raise BoundaryError("live_model_not_enabled")
    state = {
        "status": "running",
        "task_id": spec["task_id"],
        "task_identity": spec["identity"],
        "calls": [],
    }

    def record(item):
        state["calls"].append(item)
        atomic(root / "status.json", state)

    env = None
    try:
        files, modes = baseline_files(spec)
        env = CodingEnvironment(spec["profile"]["image"], task_id=spec["task_id"])
        state["container_id"] = env.container_id
        state["volume_name"] = env.volume_name
        atomic(root / "status.json", state)
        env.load(files, modes)
        if actions:
            commands = json.loads(actions.read_text())
            inner = DeterministicModel(
                outputs=[
                    make_output("Offline fixture action", [{"command": c}], 0.0) for c in commands
                ],
                cost_per_call=0.0,
            )
            model = inner
            state["mode"] = "offline_scripted_fixture"
        else:
            if not allow_model or not spec["model"]:
                raise BoundaryError("live_model_not_enabled")
            from minisweagent.config import builtin_config_dir, get_config_from_spec
            from minisweagent.models.litellm_model import LitellmModel

            config = get_config_from_spec(builtin_config_dir / "mini.yaml")
            inner = LitellmModel(
                model_name=spec["model"],
                cost_tracking="ignore_errors",
                observation_template=config["model"]["observation_template"]
                + "\nRuntime budget: calls used={{n_model_calls}}/{{step_limit}}, "
                "remaining={{step_limit - n_model_calls}}; "
                "elapsed={{elapsed_seconds}}/{{wall_time_limit_seconds}} seconds. "
                "Reserve a call to submit when ready.",
                model_kwargs={
                    "api_base": spec["api_base"],
                    "num_retries": 0,
                    "timeout": 90,
                    # LiteLLM 1.106.2 otherwise imports proxy/MCP extras even for function tools.
                    "_skip_mcp_handler": True,
                },
            )
            model = BudgetModel(
                inner,
                calls=spec["calls"],
                tokens=spec["tokens"],
                output_cap=spec["output_cap"],
                record=record,
            )
            state["mode"] = "live_model"
        goal = (
            spec["goal"]
            + "\nAllowed changed paths: "
            + ", ".join(spec["profile"]["editable_paths"])
        )
        agent = DefaultAgent(
            model,
            env,
            system_template=SYSTEM_TEMPLATE,
            instance_template="{{task}}",
            step_limit=spec["calls"],
            cost_limit=0,
            wall_time_limit_seconds=spec["wall_seconds"],
            output_path=root / "trajectory.json",
        )
        result = agent.run(goal, token_budget=spec["tokens"], output_cap=spec["output_cap"])
        if result["exit_status"] != "Submitted":
            raise BoundaryError("agent_not_submitted")
        exported = env.export()
        state["export_identity"] = preserve_export(root, spec, exported)
        atomic(root / "status.json", state)
        candidate = collect(root, spec, exported)
        env.cleanup()
        env = None
        report = validate(root, spec)
        state.update(
            status="candidate_ready" if report["passed"] else "validation_failed",
            candidate_identity=candidate["identity"],
            validation_identity=report["identity"],
            provider_calls=model.calls if isinstance(model, BudgetModel) else 0,
            scripted_steps=agent.n_calls if actions else 0,
            total_tokens=model.tokens if isinstance(model, BudgetModel) else 0,
        )
        atomic(root / "status.json", state)
    except BaseException as error:
        reason = str(error) if isinstance(error, BoundaryError) else type(error).__name__
        state.update(status="interrupted", detail=reason)
        # A budget/provider failure must not discard an unsubmitted workspace.
        if env and not (root / "export.json").exists():
            try:
                state["export_identity"] = preserve_export(root, spec, env.export())
            except BaseException:
                state["export_status"] = "unavailable"
        atomic(root / "status.json", state)
        raise
    finally:
        if env:
            env.cleanup()


def worker_limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3,) * 2)


def cancel(_signum, _frame):
    raise KeyboardInterrupt


def run(args):
    root = args.task.resolve(strict=True)
    spec = task_spec(root)
    if spec["executor_identity"] != executor_identity():
        raise BoundaryError("executor_identity_changed")
    if not args.actions and (not args.allow_model or not spec["model"]):
        raise BoundaryError("live_model_not_enabled")
    with (root / "lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BoundaryError("task_already_running") from None
        if read(root / "status.json")["status"] != "prepared":
            raise BoundaryError("task_not_prepared")
        atomic(
            root / "status.json",
            {"status": "running", "task_id": spec["task_id"], "task_identity": spec["identity"]},
        )
        argv = [sys.executable, str(Path(__file__).resolve()), "_execute", "--task", str(root)]
        if args.actions:
            argv.extend(["--actions", str(args.actions.resolve(strict=True))])
        if args.allow_model:
            argv.append("--allow-model")
        with (root / "worker.log").open("wb") as log:
            process = subprocess.Popen(
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=worker_limits,
            )
            try:
                process.wait(timeout=spec["wall_seconds"] + 45)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                cleanup(root, spec)
                state = read(root / "status.json")
                state.update(status="interrupted", detail="watchdog_or_cancel")
                atomic(root / "status.json", state)
            finally:
                try:
                    cleanup(root, spec)
                except BoundaryError:
                    state = read(root / "status.json")
                    state.update(status="interrupted", detail="task_cleanup_unconfirmed")
                    atomic(root / "status.json", state)
                    raise
                state = read(root / "status.json")
                if state["status"] == "running":
                    state.update(status="interrupted", detail="worker_exited_outcome_unknown")
                    atomic(root / "status.json", state)
        print(json.dumps(read(root / "status.json")))
        return process.returncode


def cleanup(root: Path, spec: dict):
    # Only exact labels derived from the frozen task; existing project resources are excluded.
    labels = [
        spec["task_id"],
        *[
            spec["task_id"] + "-validate-" + str(i)
            for i in range(len(spec["profile"]["validators"]))
        ],
    ]
    for label in labels:
        for kind in ("container", "volume"):
            argv = (
                ["/usr/bin/docker", "ps", "-aq"]
                if kind == "container"
                else ["/usr/bin/docker", "volume", "ls", "-q"]
            )
            code, output = bounded(
                [*argv, "--filter", "label=labpilot.coding.task=" + label], timeout=10
            )
            if code:
                raise BoundaryError("task_cleanup_observation_failed")
            for identity in output.decode().split():
                action = ["rm", "-f"] if kind == "container" else ["volume", "rm"]
                code, _ = bounded(["/usr/bin/docker", *action, identity], timeout=15)
                if code:
                    raise BoundaryError("task_cleanup_failed")


def main():
    os.umask(0o077)
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "1"
    os.environ.pop("DOCKER_CONTEXT", None)
    os.environ.setdefault("DOCKER_HOST", "unix:///run/labpilot-docker.sock")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--goal", type=Path, required=True)
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--task", type=Path, required=True)
    p.add_argument("--project-id")
    p.add_argument("--snapshot-id")
    p.add_argument("--snapshot-identity")
    p.add_argument("--calls", type=int, default=20)
    p.add_argument("--tokens", type=int, default=200000)
    p.add_argument("--output-cap", type=int, default=4096)
    p.add_argument("--wall-seconds", type=int, default=900)
    p.add_argument("--model", default="")
    p.add_argument("--api-base", default="https://api.deepseek.com")
    for name in ("run", "_execute", "inspect", "validate", "cleanup"):
        p = commands.add_parser(name)
        p.add_argument("--task", type=Path, required=True)
        if name in ("run", "_execute"):
            p.add_argument("--actions", type=Path)
            p.add_argument("--allow-model", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args)
        elif args.command == "run":
            signal.signal(signal.SIGTERM, cancel)
            return run(args)
        elif args.command == "_execute":
            execute(args.task, args.actions, args.allow_model)
            return 0 if read(args.task / "status.json")["status"] == "candidate_ready" else 1
        elif args.command == "inspect":
            current = read(args.task / "status.json")
            if current["status"] == "candidate_ready":
                candidate_tree(args.task, task_spec(args.task))
            print(json.dumps(current))
        elif args.command == "validate":
            with (args.task / "lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BoundaryError("task_already_running") from None
                spec = task_spec(args.task)
                if spec["executor_identity"] != executor_identity():
                    raise BoundaryError("executor_identity_changed")
                state = read(args.task / "status.json")
                if state["status"] not in {"candidate_ready", "validation_failed"}:
                    raise BoundaryError("task_not_validatable")
                result = validate(args.task, spec)
                state.setdefault("initial_validation_identity", state["validation_identity"])
                state.update(
                    status="candidate_ready" if result["passed"] else "validation_failed",
                    validation_identity=result["identity"],
                )
                atomic(args.task / "status.json", state)
            print(json.dumps(result))
            return 0 if result["passed"] else 1
        elif args.command == "cleanup":
            with (args.task / "lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BoundaryError("active_task_use_ctrl_c_to_cancel") from None
                spec = task_spec(args.task)
                cleanup(args.task, spec)
                state = read(args.task / "status.json")
                if state["status"] == "running":
                    state.update(status="interrupted", detail="orphan_cleaned_outcome_unknown")
                    atomic(args.task / "status.json", state)
    except Exception as error:
        reason = str(error) if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "rejected", "detail": reason}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
