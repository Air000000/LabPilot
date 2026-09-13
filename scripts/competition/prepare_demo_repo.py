from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize the competition workload as a standalone clean Git repository."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="WSL-local destination path; must not already contain files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.destination.expanduser()
    if not destination.is_absolute():
        destination = (Path.cwd() / destination).resolve()
    if str(destination).startswith("/mnt/"):
        raise SystemExit("destination must be on the WSL Linux filesystem, not /mnt/*")
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"destination is not empty: {destination}")

    repository_root = Path(__file__).resolve().parents[2]
    template = repository_root / "demo" / "competition_workload" / "repo_template"
    if not template.is_dir():
        raise SystemExit(f"competition workload template is missing: {template}")

    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(template.iterdir()):
        target = destination / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    run(sys.executable, "generate_dataset.py", cwd=destination)
    run("git", "init", "-b", "main", cwd=destination)
    run("git", "config", "user.name", "LabPilot Competition Demo", cwd=destination)
    run("git", "config", "user.email", "labpilot-demo@local.invalid", cwd=destination)
    run("git", "add", "README.md", "train.py", "generate_dataset.py", "data/classification.csv", cwd=destination)
    run(
        "git",
        "commit",
        "-m",
        "demo: freeze deterministic classification workload",
        cwd=destination,
    )

    status = run("git", "status", "--porcelain", cwd=destination)
    if status:
        raise SystemExit(f"prepared demo repository is not clean:\n{status}")
    commit = run("git", "rev-parse", "HEAD", cwd=destination)
    dataset_sha = run("sha256sum", "data/classification.csv", cwd=destination).split()[0]

    print(f"DEMO_REPOSITORY_PATH={destination}")
    print(f"DEMO_REPOSITORY_COMMIT={commit}")
    print(f"DEMO_DATASET_SHA256={dataset_sha}")
    print("DEMO_REPOSITORY_CLEAN=YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
