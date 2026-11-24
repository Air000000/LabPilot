"""Run the optional coding worker without adding its dependencies to the core API."""

import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    worker = root / "tools/coding_worker/.venv/bin/python"
    if not worker.is_file():
        print("Run: uv sync --project tools/coding_worker --locked", file=sys.stderr)
        return 1
    # Only the explicit model credential is inherited; candidate containers receive none.
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "DEEPSEEK_API_KEY",
        "DOCKER_HOST",
    }
    environment = {k: v for k, v in os.environ.items() if k in allowed}
    os.execve(
        worker,
        [str(worker), str(root / "tools/coding_worker/coding_task.py"), *sys.argv[1:]],
        environment,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
