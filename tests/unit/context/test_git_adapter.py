import asyncio
import os
import subprocess
from pathlib import Path

import pytest

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
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid")
    original = asyncio.create_subprocess_exec
    captured: dict[str, object] = {}

    async def capture(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    adapter = GitAdapter(git_executable=GIT, timeout_seconds=2)

    result = await adapter._run_git(
        repository,
        ("rev-parse", "--show-toplevel"),
    )

    assert Path(result.stdout.rstrip().decode("utf-8")) == repository
    assert captured["args"] == (str(GIT), "rev-parse", "--show-toplevel")
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    assert kwargs["cwd"] == str(repository)
    environment = kwargs["env"]
    assert environment == {
        "GIT_CONFIG_NOSYSTEM": "1",
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
async def test_cat_file_batch_reads_exact_blob_bytes_in_request_order(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "repository")
    first = repository / "first.bin"
    second = repository / "second.bin"
    first.write_bytes(b"first\x00bytes")
    second.write_bytes(b"second\nbytes")
    _commit_all(repository)
    first_oid = _git(repository, "rev-parse", "HEAD:first.bin").stdout.strip().decode("ascii")
    second_oid = _git(repository, "rev-parse", "HEAD:second.bin").stdout.strip().decode("ascii")

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
