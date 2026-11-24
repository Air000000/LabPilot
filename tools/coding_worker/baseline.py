"""Freeze a Git working tree in a separate private directory; never modify its source."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
from pathlib import Path, PurePosixPath


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        ["/usr/bin/git", "-c", f"safe.directory={root}", *arguments],
        cwd=root,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        },
        stderr=subprocess.PIPE,
        timeout=30,
    )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect(root: Path) -> tuple[dict, dict[str, bytes]]:
    head = git(root, "rev-parse", "HEAD").decode().strip()
    paths = git(root, "ls-files", "-z").decode().split("\0")
    entries, contents = [], {}
    for name in sorted(p for p in paths if p):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise ValueError("unsafe_tracked_path")
        file = root / name
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise ValueError("unsupported_link")
        if not file.exists():
            entries.append({"path": name, "deleted": True})
            continue
        mode = file.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("unsupported_file_type")
        data = file.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("source_file_limit")
        contents[name] = data
        # Git's tracked executable flag, not Windows mount permission bits.
        tracked = git(root, "ls-files", "--stage", "--", name).decode().split()[0]
        if tracked not in {"100644", "100755"}:
            raise ValueError("unsupported_git_mode")
        entries.append({"path": name, "sha256": digest(data), "git_mode": tracked})
    if len(entries) > 10_000 or sum(map(len, contents.values())) > 128 * 1024 * 1024:
        raise ValueError("source_tree_limit")
    index = Path(
        git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip()
    )
    return {
        "source_head": head,
        "tracked_status": git(root, "status", "--porcelain=v1", "-uno").decode(),
        "index_sha256": digest(index.read_bytes()),
        "entries": entries,
    }, contents


def prepare(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(strict=True), destination.resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("destination_must_be_new_and_outside_source")
    before, contents = inspect(source)
    destination.mkdir(mode=0o700)
    tree = destination / "source"
    tree.mkdir(mode=0o700)
    for entry in before["entries"]:
        if entry.get("deleted"):
            continue
        file = tree / entry["path"]
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(contents[entry["path"]])
        file.chmod(0o755 if entry["git_mode"] == "100755" else 0o644)
    after, _ = inspect(source)
    if before != after:
        raise ValueError("source_changed_during_copy")
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", "--force", "--all")
    git(
        tree,
        "-c",
        "user.name=LabPilot Local",
        "-c",
        "user.email=local@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "Freeze selected tracked working source",
    )
    manifest = {
        "schema_version": "labpilot.coding_baseline.v1",
        "source_repository": str(source),
        **before,
        "baseline_commit": git(tree, "rev-parse", "HEAD").decode().strip(),
        "selection": "tracked working files only; no untracked files or original Git metadata",
    }
    manifest["identity"] = digest(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )
    (destination / "baseline.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "baseline.json").chmod(0o600)
    # Public original HEAD used only as an independent numerical reference.
    (destination / "reference.tar").write_bytes(
        git(source, "archive", "--format=tar", before["source_head"])
    )
    (destination / "reference.tar").chmod(0o600)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.repository, args.output)
    print(
        json.dumps(
            {
                "identity": result["identity"],
                "baseline_commit": result["baseline_commit"],
                "source_head": result["source_head"],
                "files": len(result["entries"]),
            }
        )
    )
