import asyncio
import os
import subprocess
from pathlib import Path

import pytest

import labpilot.context.git_adapter as git_adapter_module
from labpilot.context.git_adapter import GitAdapter, _parse_porcelain_v2
from labpilot.context.repository_types import (
    RepositoryGitCommandFailed,
    RepositoryGitCommandTimeout,
    RepositoryGitExecutableInvalid,
    RepositoryGitObjectTooLarge,
    RepositoryGitObjectTotalLimitExceeded,
    RepositoryGitObjectUnavailable,
    RepositoryGitOutputLimitExceeded,
    RepositoryHeadUnavailable,
    RepositorySnapshotInspectionBundleUnsupportedEntry,
)

GIT = Path("/usr/bin/git")


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [GIT, *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _init_repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    return path


def _commit_all(repository: Path, message: str = "initial") -> None:
    _git(repository, "add", "-A")
    _git(
        repository,
        "-c",
        "user.name=LabPilot Test",
        "-c",
        "user.email=labpilot@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )


def _executable_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-git"
    script.write_text(f"#!/usr/bin/python3\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_git_adapter_requires_controlled_absolute_executable(tmp_path: Path) -> None:
    with pytest.raises(RepositoryGitExecutableInvalid):
        GitAdapter(git_executable=Path("git"), timeout_seconds=1)

    with pytest.raises(RepositoryGitExecutableInvalid):
        GitAdapter(git_executable=tmp_path / "missing-git", timeout_seconds=1)


@pytest.mark.asyncio
async def test_git_runner_uses_argv_fixed_environment_and_no_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    monkeypatch.setenv("GIT_DIR", "/attacker/git-dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/attacker/work-tree")
    monkeypatch.setenv("GIT_ASKPASS", "/attacker/askpass")
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid")
    original = asyncio.create_subprocess_exec
    captured: dict[str, object] = {}

    async def capture(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        pass_fds = kwargs.get("pass_fds")
        captured["root_stat"] = os.fstat(pass_fds[0]) if pass_fds else None
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    result = await adapter._run_git(
        repository,
        ("rev-parse", "--show-toplevel"),
    )

    assert result.stdout.strip()
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    root_fd = kwargs["pass_fds"]
    assert isinstance(root_fd, tuple) and len(root_fd) == 1
    fd_path = f"/proc/self/fd/{root_fd[0]}"
    assert kwargs["cwd"] == fd_path
    assert captured["root_stat"] is not None
    root_stat = captured["root_stat"]
    assert (root_stat.st_dev, root_stat.st_ino) == (
        repository.stat().st_dev,
        repository.stat().st_ino,
    )
    arguments = captured["args"]
    assert isinstance(arguments, tuple)
    assert arguments[0] == str(GIT)
    safe_directory_argument = f"safe.directory={repository.resolve()}"
    safe_directory_index = arguments.index(safe_directory_argument)
    assert arguments[safe_directory_index - 1] == "-c"
    assert arguments.count(safe_directory_argument) == 1
    assert "safe.directory=*" not in arguments
    assert f"core.worktree={fd_path}" in arguments
    assert arguments[-2:] == ("rev-parse", "--show-toplevel")
    environment = kwargs["env"]
    assert environment == {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


@pytest.mark.asyncio
async def test_git_runner_reads_stdout_and_stderr_concurrently(tmp_path: Path) -> None:
    fake_git = _executable_script(
        tmp_path,
        "import os\nos.write(2, b'e' * 32768)\nos.write(1, b'o' * 32768)\n",
    )
    repository = _init_repository(tmp_path / "repository")
    adapter = GitAdapter(
        git_executable=fake_git,
        timeout_seconds=2,
        max_stdout_bytes=65536,
        max_stderr_bytes=65536,
    )

    result = await adapter._run_git(repository, ("rev-parse", "--show-toplevel"))

    assert result.stdout == b"o" * 32768
    assert result.stderr == b"e" * 32768


@pytest.mark.asyncio
async def test_git_runner_timeout_terminates_and_reaps_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    fake_git = _executable_script(
        tmp_path,
        f"import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
    )
    repository = _init_repository(tmp_path / "repository")
    adapter = GitAdapter(git_executable=fake_git, timeout_seconds=1)

    with pytest.raises(RepositoryGitCommandTimeout):
        await adapter._run_git(repository, ("rev-parse", "--show-toplevel"))

    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("stream", "descriptor"), [("stdout", 1), ("stderr", 2)])
async def test_git_runner_output_limit_terminates_process(
    tmp_path: Path,
    stream: str,
    descriptor: int,
) -> None:
    fake_git = _executable_script(
        tmp_path,
        f"import os, time\nos.write({descriptor}, b'x' * 65536)\ntime.sleep(60)\n",
    )
    repository = _init_repository(tmp_path / "repository")
    adapter = GitAdapter(
        git_executable=fake_git,
        timeout_seconds=2,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
    )

    with pytest.raises(RepositoryGitOutputLimitExceeded) as captured:
        await adapter._run_git(repository, ("rev-parse", "--show-toplevel"))

    assert captured.value.stream == stream
    assert captured.value.limit_bytes == 64


@pytest.mark.asyncio
async def test_git_runner_sanitizes_bounded_stderr_summary(tmp_path: Path) -> None:
    fake_git = _executable_script(
        tmp_path,
        "import os\n"
        "os.write(2, b'fatal: https://user:secret@example.com/repo?token=x#frag\\n"
        "bad\\x1b[31m')\n"
        "raise SystemExit(2)\n",
    )
    repository = _init_repository(tmp_path / "repository")
    adapter = GitAdapter(
        git_executable=fake_git,
        timeout_seconds=2,
        max_stderr_bytes=1024,
    )

    with pytest.raises(RepositoryGitCommandFailed) as captured:
        await adapter._run_git(repository, ("rev-parse", "--show-toplevel"))

    summary = captured.value.stderr_summary
    assert len(summary.encode("utf-8")) <= 512
    assert "secret" not in summary
    assert "token=x" not in summary
    assert "\x1b" not in summary
    assert captured.value.returncode == 2


@pytest.mark.asyncio
async def test_probe_observes_ordinary_status_and_file_metadata(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    for name in ("staged.txt", "unstaged.txt", "both.txt", "deleted.txt"):
        (repository / name).write_text("base\n", encoding="utf-8")
    executable = repository / "mode.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    link = repository / "tracked-link"
    link.symlink_to("staged.txt")
    _commit_all(repository)

    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "unstaged.txt").write_text("unstaged\n", encoding="utf-8")
    (repository / "both.txt").write_text("indexed\n", encoding="utf-8")
    _git(repository, "add", "both.txt")
    (repository / "both.txt").write_text("working\n", encoding="utf-8")
    (repository / "deleted.txt").unlink()
    executable.chmod(0o755)
    link.unlink()
    link.symlink_to("unstaged.txt")
    (repository / "untracked.txt").write_text("new\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("outside\n", encoding="utf-8")
    untracked_link = repository / "untracked-link"
    untracked_link.symlink_to(outside)

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)
    entries = {entry.relative_path: entry for entry in probe.status_entries}

    assert probe.canonical_root_path == str(repository.resolve())
    assert len(probe.head_commit) == 40
    assert probe.current_branch == "main"
    assert probe.is_detached_head is False
    assert len(probe.tracked_tree_identity) == 40
    assert probe.observation_profile_version == "wp1-a-git-observation:v1"
    assert entries["staged.txt"].index_status == "M"
    assert entries["staged.txt"].worktree_status == "."
    assert entries["unstaged.txt"].index_status == "."
    assert entries["unstaged.txt"].worktree_status == "M"
    assert entries["both.txt"].index_status == "M"
    assert entries["both.txt"].worktree_status == "M"
    assert entries["deleted.txt"].change_kind == "deletion"
    assert entries["mode.sh"].change_kind == "mode_change"
    assert entries["tracked-link"].file_type == "symlink"
    assert entries["untracked.txt"].change_kind == "untracked_file"
    assert entries["untracked.txt"].is_untracked is True
    assert entries["untracked-link"].file_type == "symlink"
    assert entries["untracked-link"].file_mode == "120000"
    assert not (repository / ".git" / "index.lock").exists()


@pytest.mark.asyncio
async def test_probe_disables_repository_local_fsmonitor_command(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    marker = tmp_path / "fsmonitor-executed"
    fsmonitor = tmp_path / "fsmonitor"
    fsmonitor.write_text(
        "#!/usr/bin/python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "print('0')\n",
        encoding="utf-8",
    )
    fsmonitor.chmod(0o755)
    _git(repository, "config", "core.fsmonitor", str(fsmonitor))

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert probe.canonical_root_path == str(repository.resolve())
    assert not marker.exists()


@pytest.mark.asyncio
async def test_probe_rejects_repository_local_core_worktree_escape(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    repository = _init_repository(allowed_root / "repository")
    (repository / "inside.txt").write_text("inside\n", encoding="utf-8")
    _commit_all(repository, "inside")
    outside = _init_repository(tmp_path / "outside")
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(outside, "outside")
    _git(repository, "config", "core.worktree", str(outside))

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_root_mismatch"


@pytest.mark.asyncio
async def test_probe_requires_the_anchored_root_to_be_repository_top_level(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    nested = repository / "nested"
    nested.mkdir()
    _commit_all(repository)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(nested)

    assert captured.value.reason_code == "repository_git_dir_escape"


@pytest.mark.asyncio
async def test_probe_rejects_repository_local_config_includes_for_origin(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    included_config = tmp_path / "included.gitconfig"
    included_config.write_text(
        '[remote "origin"]\n    url = https://user:secret@example.com/external/repo.git\n',
        encoding="utf-8",
    )
    _git(repository, "config", "include.path", str(included_config))

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_config_include_unsupported"


@pytest.mark.asyncio
async def test_probe_rejects_gitdir_file_pointing_outside(tmp_path: Path) -> None:
    outside = _init_repository(tmp_path / "outside")
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(outside, "outside")
    repository = tmp_path / "allowed"
    repository.mkdir()
    (repository / ".git").write_text(
        f"gitdir: {outside / '.git'}\n",
        encoding="utf-8",
    )

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_dir_escape"


@pytest.mark.asyncio
async def test_probe_rejects_gitdir_symlink_pointing_outside(tmp_path: Path) -> None:
    outside = _init_repository(tmp_path / "outside")
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(outside, "outside")
    repository = tmp_path / "allowed"
    repository.mkdir()
    (repository / ".git").symlink_to(outside / ".git", target_is_directory=True)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_dir_escape"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["probe", "ls-tree", "cat-file"])
async def test_observation_rejects_external_object_alternates(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    outside = _init_repository(tmp_path / "outside")
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(outside, "outside")
    outside_head = _git(outside, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    outside_blob = _git(outside, "rev-parse", "HEAD:outside.txt").stdout.strip().decode("ascii")
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(
        f"{outside / '.git' / 'objects'}\n",
        encoding="utf-8",
    )
    _git(repository, "update-ref", "refs/heads/main", outside_head)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        if operation == "probe":
            await adapter.probe(repository)
        elif operation == "ls-tree":
            await adapter.ls_tree(
                repository,
                outside_head,
                max_entries=10,
                max_output_bytes=4096,
            )
        else:
            await adapter.cat_file_batch(
                repository,
                (outside_blob,),
                max_object_bytes=64,
                max_total_bytes=64,
            )

    assert captured.value.reason_code == "repository_git_alternates_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("conditional", [False, True])
async def test_probe_rejects_local_config_includes_that_hide_untracked_files(
    tmp_path: Path,
    conditional: bool,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    hidden = repository / "hidden.txt"
    hidden.write_text("hidden\n", encoding="utf-8")
    excludes = tmp_path / "external-ignore"
    excludes.write_text("hidden.txt\n", encoding="utf-8")
    included_config = tmp_path / "included.gitconfig"
    included_config.write_text(
        f"[core]\n    excludesFile = {excludes}\n",
        encoding="utf-8",
    )
    key = f"includeIf.gitdir:{repository}/.path" if conditional else "include.path"
    _git(repository, "config", key, str(included_config))

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_config_include_unsupported"


@pytest.mark.asyncio
async def test_cat_file_batch_ignores_replacement_blob(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    original_content = b"original blob\n"
    replacement_content = b"replacement blob\n"
    original_path = repository / "original.bin"
    replacement_path = repository / "replacement.bin"
    original_path.write_bytes(original_content)
    replacement_path.write_bytes(replacement_content)
    original_oid = (
        _git(repository, "hash-object", "-w", str(original_path)).stdout.strip().decode("ascii")
    )
    replacement_oid = (
        _git(repository, "hash-object", "-w", str(replacement_path)).stdout.strip().decode("ascii")
    )
    _git(repository, "replace", original_oid, replacement_oid)

    (observed,) = await GitAdapter(
        git_executable=GIT,
        timeout_seconds=2,
    ).cat_file_batch(
        repository,
        (original_oid,),
        max_object_bytes=64,
        max_total_bytes=64,
    )

    assert observed.oid == original_oid
    assert observed.content == original_content
    assert observed.content != replacement_content


@pytest.mark.asyncio
async def test_probe_and_ls_tree_ignore_replacement_commit(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    original_path = repository / "original.txt"
    original_path.write_text("original\n", encoding="utf-8")
    _commit_all(repository, "original")
    original_commit = _git(repository, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    original_tree = _git(repository, "rev-parse", "HEAD^{tree}").stdout.strip().decode("ascii")

    original_path.unlink()
    (repository / "replacement.txt").write_text("replacement\n", encoding="utf-8")
    _commit_all(repository, "replacement")
    replacement_commit = _git(repository, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    _git(repository, "replace", original_commit, replacement_commit)
    _git(repository, "update-ref", "refs/heads/main", original_commit)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    probe = await adapter.probe(repository)
    entries = await adapter.ls_tree(
        repository,
        original_commit,
        max_entries=10,
        max_output_bytes=4096,
    )

    assert probe.head_commit == original_commit
    assert probe.tracked_tree_identity == original_tree
    assert tuple(entry.relative_path for entry in entries) == ("original.txt",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative_path",
    [
        Path("config"),
        Path("index"),
        Path("HEAD"),
        Path("packed-refs"),
        Path("info/exclude"),
        Path("info/attributes"),
    ],
)
async def test_probe_rejects_symlinked_git_control_file(
    tmp_path: Path,
    relative_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    control_path = repository / ".git" / relative_path
    outside_path = tmp_path / f"outside-{relative_path.as_posix().replace('/', '-')}"
    outside_path.write_bytes(control_path.read_bytes() if control_path.exists() else b"")
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.unlink(missing_ok=True)
    control_path.symlink_to(outside_path)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_metadata_symlink_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["probe", "ls-tree", "cat-file"])
async def test_observation_rejects_nested_object_directory_symlink(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    outside = _init_repository(tmp_path / "outside")
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(outside, "outside")
    outside_commit = _git(outside, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    outside_tree = _git(outside, "rev-parse", "HEAD^{tree}").stdout.strip().decode("ascii")
    outside_blob = _git(outside, "rev-parse", "HEAD:outside.txt").stdout.strip().decode("ascii")
    for oid in {outside_commit, outside_tree, outside_blob}:
        destination = repository / ".git" / "objects" / oid[:2]
        if not destination.exists():
            destination.symlink_to(
                outside / ".git" / "objects" / oid[:2],
                target_is_directory=True,
            )
    (repository / ".git" / "refs" / "heads" / "main").write_text(
        f"{outside_commit}\n",
        encoding="ascii",
    )
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        if operation == "probe":
            await adapter.probe(repository)
        elif operation == "ls-tree":
            await adapter.ls_tree(
                repository,
                outside_commit,
                max_entries=10,
                max_output_bytes=4096,
            )
        else:
            await adapter.cat_file_batch(
                repository,
                (outside_blob,),
                max_object_bytes=64,
                max_total_bytes=64,
            )

    assert captured.value.reason_code == "repository_git_metadata_symlink_unsupported"


@pytest.mark.asyncio
async def test_probe_rejects_git_metadata_entry_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    monkeypatch.setattr(
        git_adapter_module,
        "_MAX_GIT_METADATA_ENTRIES",
        1,
        raising=False,
    )

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_metadata_entry_limit_exceeded"


@pytest.mark.asyncio
async def test_probe_rejects_git_metadata_special_file(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    os.mkfifo(repository / ".git" / "metadata-fifo")

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry) as captured:
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert captured.value.reason_code == "repository_git_metadata_special_file_unsupported"


@pytest.mark.asyncio
async def test_probe_rejects_unborn_repository(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")

    with pytest.raises(RepositoryHeadUnavailable):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)


@pytest.mark.asyncio
async def test_probe_rejects_non_utf8_path(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    path = os.fsencode(repository) + b"/invalid-\xff"
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o644)
    os.close(descriptor)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)


@pytest.mark.parametrize(
    "payload",
    [
        b"u UU N... 100644 100644 100644 100644 "
        b"0123456789012345678901234567890123456789 "
        b"0123456789012345678901234567890123456789 path\0",
        b"! unknown\0",
        b"1 M. N... 100644 100644\0",
        b"? /absolute\0",
        b"? ../escape\0",
        b"? invalid-\xff\0",
        b"? truncated",
    ],
)
def test_porcelain_v2_parser_fails_closed(payload: bytes, tmp_path: Path) -> None:
    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        _parse_porcelain_v2(payload, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_renames", ["false", "true", "copies"])
async def test_probe_normalizes_rename_and_copy_independent_of_local_config(
    tmp_path: Path,
    configured_renames: str,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    old_path = repository / "old.txt"
    source_path = repository / "source.txt"
    old_path.write_text("old\n", encoding="utf-8")
    source_path.write_text("source\n", encoding="utf-8")
    _commit_all(repository)

    copied_path = repository / "copied.txt"
    copied_path.write_bytes(source_path.read_bytes())
    source_path.write_text("source changed\n", encoding="utf-8")
    _git(repository, "add", "source.txt", "copied.txt")
    _git(repository, "mv", "old.txt", "renamed.txt")
    (repository / "renamed.txt").write_text("renamed and changed\n", encoding="utf-8")
    _git(repository, "config", "status.renames", configured_renames)

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)
    entries = {entry.relative_path: entry for entry in probe.status_entries}

    assert entries["old.txt"].change_kind == "deletion"
    assert entries["old.txt"].index_status == "D"
    assert entries["renamed.txt"].change_kind == "tracked_overlay"
    assert entries["renamed.txt"].original_relative_path == "old.txt"
    assert entries["renamed.txt"].index_status == "R"
    assert entries["renamed.txt"].worktree_status == "M"
    assert entries["copied.txt"].change_kind == "tracked_overlay"
    assert entries["copied.txt"].original_relative_path == "source.txt"
    assert entries["copied.txt"].index_status == "C"
    assert entries["source.txt"].change_kind == "tracked_overlay"
    assert all(
        not (entry.relative_path == "source.txt" and entry.change_kind == "deletion")
        for entry in probe.status_entries
    )


def test_structured_repository_exceptions_allow_traceback_assignment() -> None:
    error = RepositorySnapshotInspectionBundleUnsupportedEntry("test")

    error.__traceback__ = None


@pytest.mark.asyncio
async def test_probe_sanitizes_remote_and_reads_default_branch(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    _git(
        repository,
        "remote",
        "add",
        "origin",
        "https://user:secret@example.com/org/repo.git?token=x#fragment",
    )
    _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(
        repository,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert probe.remote_origin_sanitized == "https://example.com/org/repo.git"
    assert probe.default_branch == "main"


@pytest.mark.asyncio
async def test_probe_sanitizes_scp_remote_and_allows_missing_origin_head(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    _git(repository, "remote", "add", "origin", "alice@example.com:org/repo.git")

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert probe.remote_origin_sanitized == "example.com:org/repo.git"
    assert probe.default_branch is None


@pytest.mark.asyncio
async def test_probe_reports_detached_head(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    _git(repository, "checkout", "--detach", "-q", "HEAD")

    probe = await GitAdapter(git_executable=GIT, timeout_seconds=2).probe(repository)

    assert probe.current_branch is None
    assert probe.is_detached_head is True
    assert probe.remote_origin_sanitized is None
    assert probe.default_branch is None


@pytest.mark.asyncio
async def test_ls_tree_returns_typed_regular_symlink_and_submodule_entries(
    tmp_path: Path,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "regular.txt").write_text("regular\n", encoding="utf-8")
    executable = repository / "script.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (repository / "link").symlink_to("regular.txt")
    _commit_all(repository)
    submodule_commit = _git(repository, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{submodule_commit},vendor/submodule",
    )
    _git(
        repository,
        "-c",
        "user.name=LabPilot Test",
        "-c",
        "user.email=labpilot@example.invalid",
        "commit",
        "-q",
        "-m",
        "add gitlink",
    )
    commit = _git(repository, "rev-parse", "HEAD").stdout.strip().decode("ascii")

    entries = await GitAdapter(git_executable=GIT, timeout_seconds=2).ls_tree(
        repository,
        commit,
        max_entries=10,
        max_output_bytes=4096,
    )
    by_path = {entry.relative_path: entry for entry in entries}

    assert by_path["regular.txt"].file_type == "regular"
    assert by_path["regular.txt"].file_mode == "100644"
    assert by_path["script.sh"].file_mode == "100755"
    assert by_path["link"].file_type == "symlink"
    assert by_path["link"].file_mode == "120000"
    assert by_path["vendor/submodule"].file_type == "submodule"
    assert by_path["vendor/submodule"].file_mode == "160000"


@pytest.mark.asyncio
async def test_ls_tree_rejects_invalid_revision_and_entry_limit(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "one.txt").write_text("one\n", encoding="utf-8")
    (repository / "two.txt").write_text("two\n", encoding="utf-8")
    _commit_all(repository)
    commit = _git(repository, "rev-parse", "HEAD").stdout.strip().decode("ascii")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        await adapter.ls_tree(
            repository,
            "--help",
            max_entries=10,
            max_output_bytes=4096,
        )
    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        await adapter.ls_tree(
            repository,
            commit,
            max_entries=1,
            max_output_bytes=4096,
        )


@pytest.mark.asyncio
async def test_cat_file_batch_reads_exact_blob_bytes_in_request_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    first = repository / "first.bin"
    second = repository / "second.bin"
    first.write_bytes(b"first\x00bytes")
    second.write_bytes(b"second\nbytes")
    _commit_all(repository)
    first_oid = _git(repository, "rev-parse", "HEAD:first.bin").stdout.strip().decode("ascii")
    second_oid = _git(repository, "rev-parse", "HEAD:second.bin").stdout.strip().decode("ascii")

    original = asyncio.create_subprocess_exec
    captured: list[tuple[object, ...]] = []

    async def capture(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        captured.append(args)
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    objects = await GitAdapter(git_executable=GIT, timeout_seconds=2).cat_file_batch(
        repository,
        (second_oid, first_oid),
        max_object_bytes=64,
        max_total_bytes=128,
    )

    assert tuple(value.oid for value in objects) == (second_oid, first_oid)
    assert tuple(value.object_type for value in objects) == ("blob", "blob")
    assert tuple(value.content for value in objects) == (
        b"second\nbytes",
        b"first\x00bytes",
    )
    worktree_argument = next(
        argument
        for argument in captured[-1]
        if isinstance(argument, str) and argument.startswith("core.worktree=")
    )
    assert worktree_argument.startswith("core.worktree=/proc/self/fd/")
    safe_prefix = (
        str(GIT),
        "-c",
        f"safe.directory={repository.resolve()}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        worktree_argument,
        "-c",
        "core.excludesFile=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.hooksPath=/dev/null",
    )
    assert captured
    assert all(arguments[: len(safe_prefix)] == safe_prefix for arguments in captured)
    assert all("safe.directory=*" not in arguments for arguments in captured)
    assert captured[-1] == (*safe_prefix, "cat-file", "--batch")


@pytest.mark.asyncio
async def test_cat_file_batch_trusts_the_canonical_anchored_repository_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _init_repository(tmp_path / "repository")
    payload = b"canonical-safe-directory\x00blob"
    (repository / "blob.bin").write_bytes(payload)
    _commit_all(repository)
    blob_oid = _git(repository, "rev-parse", "HEAD:blob.bin").stdout.strip().decode("ascii")

    original = asyncio.create_subprocess_exec
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def capture(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        captured.append((args, kwargs))
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    objects = await GitAdapter(git_executable=GIT, timeout_seconds=2).cat_file_batch(
        repository,
        (blob_oid,),
        max_object_bytes=64,
        max_total_bytes=64,
    )

    assert tuple(value.content for value in objects) == (payload,)
    arguments, kwargs = captured[-1]
    safe_directory_argument = f"safe.directory={repository.resolve()}"
    safe_directory_index = arguments.index(safe_directory_argument)
    assert arguments[safe_directory_index - 1] == "-c"
    assert arguments.count(safe_directory_argument) == 1
    assert "safe.directory=*" not in arguments
    root_fd = kwargs["pass_fds"]
    assert isinstance(root_fd, tuple) and len(root_fd) == 1
    fd_path = f"/proc/self/fd/{root_fd[0]}"
    assert kwargs["cwd"] == fd_path
    assert f"core.worktree={fd_path}" in arguments


@pytest.mark.asyncio
async def test_cat_file_batch_rejects_missing_and_invalid_oids(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _commit_all(repository)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        await adapter.cat_file_batch(
            repository,
            ("--help",),
            max_object_bytes=64,
            max_total_bytes=64,
        )
    with pytest.raises(RepositoryGitObjectUnavailable):
        await adapter.cat_file_batch(
            repository,
            ("0" * 40,),
            max_object_bytes=64,
            max_total_bytes=64,
        )


@pytest.mark.asyncio
async def test_cat_file_batch_enforces_object_and_total_limits(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    (repository / "first.bin").write_bytes(b"a" * 32)
    (repository / "second.bin").write_bytes(b"b" * 32)
    _commit_all(repository)
    first_oid = _git(repository, "rev-parse", "HEAD:first.bin").stdout.strip().decode("ascii")
    second_oid = _git(repository, "rev-parse", "HEAD:second.bin").stdout.strip().decode("ascii")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    with pytest.raises(RepositoryGitObjectTooLarge) as object_error:
        await adapter.cat_file_batch(
            repository,
            (first_oid,),
            max_object_bytes=16,
            max_total_bytes=64,
        )
    assert object_error.value.object_oid == first_oid
    with pytest.raises(RepositoryGitObjectTotalLimitExceeded):
        await adapter.cat_file_batch(
            repository,
            (first_oid, second_oid),
            max_object_bytes=32,
            max_total_bytes=63,
        )


@pytest.mark.asyncio
async def test_capture_snapshot_stores_dirty_bytes_and_symlink_target_without_following(
    tmp_path: Path,
) -> None:
    from hashlib import sha256
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"captured")
    target = repository / "target.txt"
    target.write_bytes(b"target-file-bytes")
    link = repository / "link.txt"
    link.symlink_to("target.txt")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    store = ContentAddressedStore(tmp_path / "content")

    observation = await adapter.capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=store,
    )

    by_path = {entry.relative_path: entry for entry in observation.bundle_entries}
    assert await store.read_bytes(by_path["tracked.txt"].content_ref) == b"captured"
    assert by_path["link.txt"].content_hash == sha256(b"target.txt").hexdigest()
    assert await store.read_bytes(by_path["link.txt"].content_ref) == b"target.txt"
    tracked.unlink()
    assert await store.read_bytes(by_path["tracked.txt"].content_ref) == b"captured"


@pytest.mark.asyncio
async def test_capture_snapshot_detects_lfs_pointer_in_clean_tree(tmp_path: Path) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    (repository / "large.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1\n"
    )
    _commit_all(repository)

    observation = await GitAdapter(
        git_executable=GIT,
        timeout_seconds=2,
    ).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )

    assert observation.bundle_entries == ()
    assert observation.lfs_pointer_presence is True


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (
            b"version https://git-lfs.github.com/spec/v1\n"
            + b"oid sha256:"
            + b"a" * 64
            + b"\nsize 1\n",
            True,
        ),
        (b"version https://git-lfs.github.com/spec/v1\n", False),
        (
            b"version https://git-lfs.github.com/spec/v1\n"
            + b"oid sha256:"
            + b"a" * 63
            + b"\nsize 1\n",
            False,
        ),
        (
            b"version https://git-lfs.github.com/spec/v1\r\n"
            + b"oid sha256:"
            + b"a" * 64
            + b"\r\nsize 1\r\n",
            False,
        ),
        (
            b"version https://git-lfs.github.com/spec/v1\n"
            + b"oid sha256:"
            + b"a" * 64
            + b"\nsize 1\nextra value\n",
            False,
        ),
        (b"version https://git-lfs.github.com/spec/v1\n" + b"x" * 1024, False),
    ),
)
def test_lfs_pointer_detection_requires_bounded_canonical_bytes(
    payload: bytes,
    expected: bool,
) -> None:
    assert git_adapter_module._is_lfs_pointer(payload) is expected


def test_lfs_pointer_detection_accepts_official_extension_pointer() -> None:
    payload = (
        b"version https://git-lfs.github.com/spec/v1\n"
        + b"ext-0-transform sha256:"
        + b"a" * 64
        + b"\noid sha256:"
        + b"b" * 64
        + b"\nsize 1\n"
    )

    assert git_adapter_module._is_lfs_pointer(payload) is True


def test_lfs_pointer_detection_accepts_1023_bytes_and_rejects_1024_bytes() -> None:
    prefix = b"version https://git-lfs.github.com/spec/v1\next-0-"
    suffix = b" sha256:" + b"a" * 64 + b"\noid sha256:" + b"b" * 64 + b"\nsize 1\n"
    pointer_1023 = prefix + b"x" * (1023 - len(prefix) - len(suffix)) + suffix
    pointer_1024 = prefix + b"x" * (1024 - len(prefix) - len(suffix)) + suffix

    assert len(pointer_1023) == 1023
    assert git_adapter_module._is_lfs_pointer(pointer_1023) is True
    assert len(pointer_1024) == 1024
    assert git_adapter_module._is_lfs_pointer(pointer_1024) is False


@pytest.mark.parametrize(
    "size",
    (b"-1", b"9223372036854775808", b"one", b"+1", b"01", b"1\xd9\xa1"),
)
def test_lfs_pointer_detection_rejects_noncanonical_or_out_of_range_size(size: bytes) -> None:
    payload = (
        b"version https://git-lfs.github.com/spec/v1\noid sha256:"
        + b"a" * 64
        + b"\nsize "
        + size
        + b"\n"
    )

    assert git_adapter_module._is_lfs_pointer(payload) is False


def test_lfs_pointer_detection_accepts_signed_int64_maximum_size() -> None:
    payload = (
        b"version https://git-lfs.github.com/spec/v1\noid sha256:"
        + b"a" * 64
        + b"\nsize 9223372036854775807\n"
    )

    assert git_adapter_module._is_lfs_pointer(payload) is True


@pytest.mark.parametrize(
    "payload",
    (
        b"version https://git-lfs.github.com/spec/v1\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\n",
        b"version https://example.invalid/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha1:" + b"a" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid SHA256:" + b"a" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"A" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 65 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"g" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256" + b"a" * 64 + b"\nsize 1\n",
        b"oid sha256:" + b"a" * 64 + b"\nversion https://git-lfs.github.com/spec/v1\nsize 1\n",
        b"version  https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\next-1-first sha256:"
        + b"a" * 64
        + b"\next-0-second sha256:"
        + b"b" * 64
        + b"\noid sha256:"
        + b"c" * 64
        + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\next-0-first sha256:"
        + b"a" * 64
        + b"\next-0-second sha256:"
        + b"b" * 64
        + b"\noid sha256:"
        + b"c" * 64
        + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\next--first sha256:"
        + b"a" * 64
        + b"\noid sha256:"
        + b"b" * 64
        + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\nunknown value\noid sha256:"
        + b"a" * 64
        + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1",
    ),
)
def test_lfs_pointer_detection_rejects_noncanonical_pointer_fields(payload: bytes) -> None:
    assert git_adapter_module._is_lfs_pointer(payload) is False


@pytest.mark.asyncio
async def test_capture_snapshot_does_not_treat_symlink_target_as_lfs_pointer(
    tmp_path: Path,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    (repository / "seed.txt").write_bytes(b"seed")
    _commit_all(repository)
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 1\n"
    (repository / "pointer-link").symlink_to(pointer)

    observation = await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )

    assert observation.lfs_pointer_presence is False


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_change_before_second_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"captured")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    original_probe = adapter.probe
    probe_count = 0

    async def probe(root: Path):
        nonlocal probe_count
        probe_count += 1
        if probe_count == 2:
            tracked.write_bytes(b"changed-after-stat-recheck")
        return await original_probe(root)

    monkeypatch.setattr(adapter, "probe", probe)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="captured_path_changed"):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_preserves_dirty_entry_semantics(tmp_path: Path) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    staged = repository / "staged.txt"
    unstaged = repository / "unstaged.txt"
    deleted = repository / "deleted.txt"
    mode_only = repository / "mode-only.sh"
    target = repository / "target.txt"
    staged.write_bytes(b"staged-base")
    unstaged.write_bytes(b"unstaged-base")
    deleted.write_bytes(b"deleted-base")
    mode_only.write_bytes(b"mode-base")
    target.write_bytes(b"target-bytes")
    _commit_all(repository)

    staged.write_bytes(b"staged-captured")
    _git(repository, "add", "staged.txt")
    unstaged.write_bytes(b"unstaged-captured")
    deleted.unlink()
    mode_only.chmod(0o755)
    _git(repository, "add", "mode-only.sh")
    (repository / "untracked.txt").write_bytes(b"untracked-captured")
    (repository / "link.txt").symlink_to("target.txt")

    store = ContentAddressedStore(tmp_path / "content")
    observation = await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=store,
    )

    entries = {entry.relative_path: entry for entry in observation.bundle_entries}
    for path, expected in (
        ("staged.txt", b"staged-captured"),
        ("unstaged.txt", b"unstaged-captured"),
        ("untracked.txt", b"untracked-captured"),
        ("link.txt", b"target.txt"),
    ):
        assert entries[path].content_ref is not None
        assert entries[path].content_hash is not None
        assert await store.read_bytes(entries[path].content_ref) == expected
    assert entries["deleted.txt"].entry_kind == "deletion"
    assert entries["deleted.txt"].content_ref is None
    assert entries["deleted.txt"].content_hash is None
    assert entries["mode-only.sh"].entry_kind == "mode_change"
    assert entries["mode-only.sh"].content_ref is None
    assert entries["mode-only.sh"].content_hash is None
    assert entries["link.txt"].file_type == "symlink"
    assert observation.bundle_identity is not None


@pytest.mark.asyncio
async def test_capture_snapshot_includes_content_when_mode_and_bytes_change(
    tmp_path: Path,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.sh"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.chmod(0o755)
    tracked.write_bytes(b"first dirty bytes")

    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    registration_id = UUID("00000000-0000-4000-8000-000000000001")
    first_store = ContentAddressedStore(tmp_path / "first-content")
    first = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=first_store,
    )
    first_entry = first.bundle_entries[0]
    assert first_entry.entry_kind == "tracked_overlay"
    assert first_entry.content_ref is not None
    assert await first_store.read_bytes(first_entry.content_ref) == b"first dirty bytes"

    tracked.write_bytes(b"second dirty bytes")
    second = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "second-content"),
    )
    assert second.bundle_entries[0].entry_kind == "tracked_overlay"
    assert second.snapshot_identity != first.snapshot_identity


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_mode_only_classification_when_bytes_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.sh"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.chmod(0o755)
    tracked.write_bytes(b"dirty")
    monkeypatch.setattr(
        git_adapter_module,
        "_parse_numstat_content_changes",
        lambda *args, **kwargs: frozenset(),
    )

    with pytest.raises(RepositorySnapshotCaptureConflict, match="mode_change_content_changed"):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_stores_self_verifying_canonical_manifests(
    tmp_path: Path,
) -> None:
    import json
    from collections.abc import Callable
    from uuid import UUID

    from labpilot.common.canonical_json import canonical_json_bytes
    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_identities import (
        RepositoryManifestEntryV1,
        dirty_worktree_manifest_hash,
        untracked_manifest_hash,
    )

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"dirty")
    (repository / "untracked.txt").write_bytes(b"untracked")

    store = ContentAddressedStore(tmp_path / "content")
    observation = await GitAdapter(
        git_executable=GIT,
        timeout_seconds=2,
    ).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=store,
    )
    expected_entry_keys = {
        "relative_path",
        "original_relative_path",
        "entry_kind",
        "file_type",
        "file_mode",
        "head_blob_oid",
        "index_blob_oid",
        "content_hash",
        "size_bytes",
        "content_identity_status",
    }
    manifests: tuple[
        tuple[str | None, str | None, Callable[[tuple[RepositoryManifestEntryV1, ...]], str]],
        ...,
    ] = (
        (
            observation.dirty_manifest_ref,
            observation.dirty_manifest_hash,
            dirty_worktree_manifest_hash,
        ),
        (
            observation.untracked_manifest_ref,
            observation.untracked_manifest_hash,
            untracked_manifest_hash,
        ),
    )
    for manifest_ref, expected_hash, hash_manifest in manifests:
        assert manifest_ref is not None
        raw_manifest = await store.read_bytes(manifest_ref)
        payload = json.loads(raw_manifest)
        assert raw_manifest == canonical_json_bytes(payload)
        assert set(payload) == {"entries", "manifest_schema_version"}
        assert payload["manifest_schema_version"] == 1
        assert all(set(entry) == expected_entry_keys for entry in payload["entries"])
        entries = tuple(RepositoryManifestEntryV1(**entry) for entry in payload["entries"])
        assert hash_manifest(entries) == expected_hash


@pytest.mark.asyncio
async def test_capture_snapshot_preserves_rename_copy_and_dirty_byte_identity(
    tmp_path: Path,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    old_path = repository / "old.txt"
    source_path = repository / "source.txt"
    tracked_path = repository / "tracked.txt"
    old_path.write_bytes(b"old")
    source_path.write_bytes(b"source")
    tracked_path.write_bytes(b"base")
    _commit_all(repository)

    (repository / "copied.txt").write_bytes(source_path.read_bytes())
    source_path.write_bytes(b"source changed")
    _git(repository, "add", "source.txt", "copied.txt")
    _git(repository, "mv", "old.txt", "renamed.txt")
    (repository / "renamed.txt").write_bytes(b"final destination bytes")
    tracked_path.write_bytes(b"first dirty bytes")

    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    registration_id = UUID("00000000-0000-4000-8000-000000000001")
    first_store = ContentAddressedStore(tmp_path / "first-content")
    first = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=first_store,
    )
    entries = {entry.relative_path: entry for entry in first.bundle_entries}
    assert entries["old.txt"].entry_kind == "deletion"
    assert entries["renamed.txt"].original_relative_path == "old.txt"
    assert entries["renamed.txt"].content_ref is not None
    assert (
        await first_store.read_bytes(entries["renamed.txt"].content_ref)
        == b"final destination bytes"
    )
    assert entries["copied.txt"].original_relative_path == "source.txt"
    assert entries["copied.txt"].content_ref is not None
    assert await first_store.read_bytes(entries["copied.txt"].content_ref) == b"source"
    assert entries["source.txt"].entry_kind == "tracked_overlay"

    tracked_path.write_bytes(b"second dirty bytes")
    second = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "second-content"),
    )
    assert second.probe.head_commit == first.probe.head_commit
    assert second.snapshot_identity != first.snapshot_identity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payloads", "limits"),
    (
        ((("first.txt", b"a"), ("second.txt", b"b")), {"max_entries": 1}),
        ((("large.txt", b"abc"),), {"max_file_bytes": 2}),
        (
            (("first.txt", b"aa"), ("second.txt", b"bb")),
            {"max_file_bytes": 2, "max_total_bytes": 3},
        ),
    ),
)
async def test_capture_snapshot_enforces_capture_limits(
    tmp_path: Path,
    payloads: tuple[tuple[str, bytes], ...],
    limits: dict[str, int],
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import (
        RepositorySnapshotInspectionBundleUnsupportedEntry,
    )

    repository = _init_repository(tmp_path / "repository")
    (repository / "base.txt").write_bytes(b"base")
    _commit_all(repository)
    for name, content in payloads:
        (repository / name).write_bytes(content)

    with pytest.raises(RepositorySnapshotInspectionBundleUnsupportedEntry):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
            **limits,
        )


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_intermediate_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import (
        GitStatusEntry,
        RepositorySnapshotCaptureConflict,
    )

    repository = _init_repository(tmp_path / "repository")
    (repository / "base.txt").write_bytes(b"base")
    _commit_all(repository)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_bytes(b"must-not-be-read")
    (repository / "nested").symlink_to(outside, target_is_directory=True)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    baseline = await adapter.probe(repository)
    forced_probe = replace(
        baseline,
        status_entries=(
            GitStatusEntry(
                relative_path="nested/file.txt",
                original_relative_path=None,
                change_kind="tracked_overlay",
                index_status="M",
                worktree_status=".",
                file_type="regular",
                file_mode="100644",
                head_blob_oid="a" * 40,
                index_blob_oid="a" * 40,
                is_untracked=False,
            ),
        ),
    )

    async def probe(*arguments: object, **keywords: object) -> object:
        return forced_probe

    async def ls_tree(*arguments: object, **keywords: object) -> tuple[object, ...]:
        return ()

    monkeypatch.setattr(adapter, "probe", probe)
    monkeypatch.setattr(adapter, "ls_tree", ls_tree)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="snapshot_path_unavailable"):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_parent_replaced_by_symlink_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import GitStatusEntry, RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    (repository / "base.txt").write_bytes(b"base")
    _commit_all(repository)
    nested = repository / "nested"
    nested.mkdir()
    (nested / "file.txt").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_bytes(b"must-not-be-read")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    baseline = await adapter.probe(repository)
    forced_probe = replace(
        baseline,
        status_entries=(
            GitStatusEntry(
                relative_path="nested/file.txt",
                original_relative_path=None,
                change_kind="tracked_overlay",
                index_status="M",
                worktree_status=".",
                file_type="regular",
                file_mode="100644",
                head_blob_oid="a" * 40,
                index_blob_oid="a" * 40,
                is_untracked=False,
            ),
        ),
    )

    async def probe(*arguments: object, **keywords: object) -> object:
        return forced_probe

    async def ls_tree(*arguments: object, **keywords: object) -> tuple[object, ...]:
        return ()

    original_open = os.open
    opened: list[str] = []
    replaced = False

    def open_path(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: object,
        **kwargs: object,
    ):
        nonlocal replaced
        opened.append(os.fspath(path))
        if not replaced:
            replaced = True
            nested.rename(repository / "nested-old")
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(adapter, "probe", probe)
    monkeypatch.setattr(adapter, "ls_tree", ls_tree)
    monkeypatch.setattr(git_adapter_module.os, "open", open_path)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="snapshot_path_unavailable"):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
    assert os.fspath(repository / "nested" / "file.txt") not in opened


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_canonical_root_parent_replacement_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hashlib import sha256
    from shutil import copytree
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore, ContentBlobMissing
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repository = _init_repository(allowed / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"inside-dirty")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_repository = outside / "repository"
    copytree(repository, outside_repository, symlinks=True)
    sentinel = b"protected-external-content"
    (outside_repository / "tracked.txt").write_bytes(sentinel)

    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    original_probe = adapter.probe
    probe_count = 0

    async def probe(root: Path):
        nonlocal probe_count
        result = await original_probe(root)
        probe_count += 1
        if probe_count == 1:
            allowed.rename(tmp_path / "allowed-before-replacement")
            allowed.symlink_to(outside, target_is_directory=True)
        return result

    store = ContentAddressedStore(tmp_path / "content")
    monkeypatch.setattr(adapter, "probe", probe)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="snapshot_path_unavailable"):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=store,
        )

    sentinel_ref = f"sha256:{sha256(sentinel).hexdigest()}"
    with pytest.raises(ContentBlobMissing):
        await store.read_bytes(sentinel_ref)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    (
        "before_probe_before",
        "between_probe_and_read",
        "before_probe_after",
        "after_probe_after",
    ),
)
async def test_capture_snapshot_keeps_one_root_authority_across_every_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repository = _init_repository(allowed / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"original-base")
    _commit_all(repository)
    tracked.write_bytes(b"original-dirty")

    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    replacement = _init_repository(replacement_parent / "repository")
    (replacement / "tracked.txt").write_bytes(b"replacement-base")
    _commit_all(replacement)
    (replacement / "tracked.txt").write_bytes(b"replacement-dirty")

    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    original_probe = adapter.probe
    original_read = git_adapter_module._read_snapshot_path
    original_subprocess = asyncio.create_subprocess_exec
    probe_count = 0
    replaced = False
    git_cwds: list[str] = []

    def replace_ancestor() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        allowed.rename(tmp_path / "anchored-original")
        allowed.symlink_to(replacement_parent, target_is_directory=True)

    async def probe(root: object):
        nonlocal probe_count
        if phase == "before_probe_before" and probe_count == 0:
            replace_ancestor()
        result = await original_probe(root)  # type: ignore[arg-type]
        probe_count += 1
        if phase == "between_probe_and_read" and probe_count == 1:
            replace_ancestor()
        if phase == "after_probe_after" and probe_count == 2:
            replace_ancestor()
        return result

    def read_path(*args: object, **kwargs: object):
        result = original_read(*args, **kwargs)
        if phase == "before_probe_after":
            replace_ancestor()
        return result

    async def create_subprocess(
        *args: object,
        **kwargs: object,
    ) -> asyncio.subprocess.Process:
        cwd = kwargs.get("cwd")
        if isinstance(cwd, str):
            git_cwds.append(cwd)
        return await original_subprocess(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter, "probe", probe)
    monkeypatch.setattr(git_adapter_module, "_read_snapshot_path", read_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)

    with pytest.raises(RepositorySnapshotCaptureConflict):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )

    assert git_cwds
    assert all(value.startswith("/proc/self/fd/") for value in git_cwds)
    assert len(set(git_cwds)) == 1


@pytest.mark.asyncio
async def test_capture_snapshot_keeps_fd_authority_through_final_overlay_git_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    (repository / "large.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n" + b"oid sha256:" + b"a" * 64 + b"\nsize 1\n"
    )
    _commit_all(repository)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    original_subprocess = asyncio.create_subprocess_exec
    observations: list[tuple[tuple[object, ...], str, tuple[int, ...]]] = []

    async def create_subprocess(
        *args: object,
        **kwargs: object,
    ) -> asyncio.subprocess.Process:
        cwd = kwargs.get("cwd")
        pass_fds = kwargs.get("pass_fds")
        assert isinstance(cwd, str)
        assert isinstance(pass_fds, tuple)
        observations.append((args, cwd, pass_fds))
        return await original_subprocess(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    observation = await adapter.capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )

    assert observation.lfs_pointer_presence is True
    assert observations
    fd_paths = {cwd for _, cwd, _ in observations}
    inherited = {pass_fds for _, _, pass_fds in observations}
    assert len(fd_paths) == len(inherited) == 1
    fd_path = fd_paths.pop()
    (pass_fds,) = inherited
    assert fd_path == f"/proc/self/fd/{pass_fds[0]}"
    flattened = [str(argument) for arguments, _, _ in observations for argument in arguments]
    assert "status" in flattened
    assert "ls-tree" in flattened
    assert "cat-file" in flattened


@pytest.mark.asyncio
async def test_capture_snapshot_detects_submodule_in_clean_tree(tmp_path: Path) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    source = _init_repository(tmp_path / "source")
    (source / "source.txt").write_bytes(b"source")
    _commit_all(source)
    repository = _init_repository(tmp_path / "repository")
    _git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(source),
        "module",
    )
    _commit_all(repository)

    observation = await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )

    assert observation.bundle_entries == ()
    assert observation.submodule_presence is True


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_probe_after_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"dirty")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    original_probe = adapter.probe
    probe_count = 0

    async def probe(root: Path):
        nonlocal probe_count
        probe_count += 1
        result = await original_probe(root)
        return replace(result, head_commit="f" * 40) if probe_count == 2 else result

    monkeypatch.setattr(adapter, "probe", probe)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="probe_changed"):
        await adapter.capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"dirty")
    original_stat = git_adapter_module._stat_token_from_metadata
    stat_count = 0

    def stat_token(metadata: os.stat_result):
        nonlocal stat_count
        stat_count += 1
        token = original_stat(metadata)
        if stat_count == 1:
            tracked.write_bytes(b"changed-during-read")
        return token

    monkeypatch.setattr(git_adapter_module, "_stat_token_from_metadata", stat_token)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="regular_file_changed"):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_fstat_timestamp_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"dirty")
    original_fstat = git_adapter_module.os.fstat

    def fstat(descriptor: int):
        metadata = original_fstat(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns + 1,
            st_ctime_ns=metadata.st_ctime_ns,
        )

    monkeypatch.setattr(git_adapter_module.os, "fstat", fstat)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="regular_file_changed"):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_fstat_ctime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"base")
    _commit_all(repository)
    tracked.write_bytes(b"dirty")
    original_fstat = git_adapter_module.os.fstat

    def fstat(descriptor: int):
        metadata = original_fstat(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns + 1,
        )

    monkeypatch.setattr(git_adapter_module.os, "fstat", fstat)
    with pytest.raises(RepositorySnapshotCaptureConflict, match="regular_file_changed"):
        await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
            repository,
            repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_identity_excludes_capture_time(tmp_path: Path) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    (repository / "tracked.txt").write_bytes(b"base")
    _commit_all(repository)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)
    registration_id = UUID("00000000-0000-4000-8000-000000000001")
    first = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "first-content"),
    )
    await asyncio.sleep(0.001)
    second = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "second-content"),
    )

    assert second.snapshot_identity == first.snapshot_identity


@pytest.mark.asyncio
async def test_capture_snapshot_excludes_deleted_submodule_from_final_presence(
    tmp_path: Path,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    source = _init_repository(tmp_path / "source")
    (source / "source.txt").write_bytes(b"source")
    _commit_all(source)
    repository = _init_repository(tmp_path / "repository")
    _git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(source),
        "module",
    )
    _commit_all(repository)
    _git(repository, "rm", "-q", "-f", "module")

    observation = await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )

    entries = {entry.relative_path: entry for entry in observation.bundle_entries}
    assert entries["module"].entry_kind == "deletion"
    assert observation.submodule_presence is False


@pytest.mark.asyncio
async def test_capture_snapshot_derives_lfs_presence_from_final_overlay(tmp_path: Path) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    pointer = (
        b"version https://git-lfs.github.com/spec/v1\n" + b"oid sha256:" + b"a" * 64 + b"\nsize 1\n"
    )
    repository = _init_repository(tmp_path / "repository")
    tracked = repository / "large.bin"
    tracked.write_bytes(pointer)
    _commit_all(repository)
    registration_id = UUID("00000000-0000-4000-8000-000000000001")
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    tracked.unlink()
    deleted = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "deleted-content"),
    )
    assert deleted.lfs_pointer_presence is False

    _git(repository, "reset", "--hard", "-q", "HEAD")
    tracked.write_bytes(b"ordinary bytes")
    replaced = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "replaced-content"),
    )
    assert replaced.lfs_pointer_presence is False

    _git(repository, "reset", "--hard", "-q", "HEAD")
    (repository / "introduced.bin").write_bytes(pointer)
    introduced = await adapter.capture_snapshot(
        repository,
        repository_registration_id=registration_id,
        content_store=ContentAddressedStore(tmp_path / "introduced-content"),
    )
    assert introduced.lfs_pointer_presence is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payloads", "limits", "expected_total"),
    (
        (
            (("first.txt", b"a"), ("second.txt", b"b")),
            {"max_entries": 2},
            2,
        ),
        (
            (("exact-file.txt", b"abc"),),
            {"max_file_bytes": 3, "max_total_bytes": 3},
            3,
        ),
        (
            (("first.txt", b"aa"), ("second.txt", b"bb")),
            {"max_file_bytes": 2, "max_total_bytes": 4},
            4,
        ),
    ),
)
async def test_capture_snapshot_accepts_exact_capture_limits(
    tmp_path: Path,
    payloads: tuple[tuple[str, bytes], ...],
    limits: dict[str, int],
    expected_total: int,
) -> None:
    from uuid import UUID

    from labpilot.context.content_store import ContentAddressedStore

    repository = _init_repository(tmp_path / "repository")
    (repository / "base.txt").write_bytes(b"base")
    _commit_all(repository)
    for name, content in payloads:
        (repository / name).write_bytes(content)

    observation = await GitAdapter(git_executable=GIT, timeout_seconds=2).capture_snapshot(
        repository,
        repository_registration_id=UUID("00000000-0000-4000-8000-000000000001"),
        content_store=ContentAddressedStore(tmp_path / "content"),
        **limits,
    )

    assert len(observation.bundle_entries) == len(payloads)
    assert sum(entry.size_bytes for entry in observation.bundle_entries) == expected_total
