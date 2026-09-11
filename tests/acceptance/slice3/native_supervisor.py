"""Durable, system-service-friendly supervisor for the next Task12 native run.

This module has no default child command.  It is deliberately exercised only
with explicit fake commands until a separately authorized native invocation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

MAX_LOG_BYTES = 1_048_576
_SHA = re.compile(r"^[0-9a-f]{40}$")
_INVOCATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SupervisorError(RuntimeError):
    """The supervisor refused to launch a child."""


def run_supervisor(
    *,
    invocation_id: str,
    evidence_root: Path,
    child_argv: Sequence[str],
    product_candidate_sha: str,
    acceptance_harness_sha: str,
    config_path: str,
    fixture_path: str,
    systemd_unit_identity: str,
) -> int:
    """Run one explicit child and persist only safe durable outcome facts."""
    _validate(
        invocation_id, evidence_root, child_argv, product_candidate_sha, acceptance_harness_sha
    )
    invocation = evidence_root / invocation_id
    invocation.mkdir(mode=0o700, parents=True, exist_ok=True)
    started = invocation / "started.json"
    _write_exclusive_json(
        started,
        {
            "invocation_id": invocation_id,
            "started_at": _now(),
            "product_candidate_sha": product_candidate_sha,
            "acceptance_harness_sha": acceptance_harness_sha,
            "config_path": config_path,
            "fixture_path": fixture_path,
            "systemd_unit_identity": systemd_unit_identity,
        },
    )

    child_started_at: str | None = None
    child_exit_code = 125
    child_outcome = "launch_error"
    log_truncated = False
    try:
        with (invocation / "native.log").open("xb") as log:
            try:
                child = subprocess.Popen(
                    list(child_argv),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            except OSError:
                child = None
            if child is not None:
                child_started_at = _now()
                assert child.stdout is not None
                log_truncated = _copy_bounded(child.stdout, log)
                child_exit_code = child.wait()
                child_outcome = "returned_normally" if child_exit_code == 0 else "nonzero_exit"
            log.flush()
            os.fsync(log.fileno())
    finally:
        _atomic_json(
            invocation / "supervisor-result.json",
            {
                "invocation_id": invocation_id,
                "child_started_at": child_started_at,
                "child_finished_at": _now(),
                "child_exit_code": child_exit_code,
                "child_outcome": child_outcome,
                "supervisor_finished_at": _now(),
                "receipt_status": "captured",
                "native_log_truncated": log_truncated,
            },
        )
    return child_exit_code


def _validate(
    invocation_id: str,
    evidence_root: Path,
    child_argv: Sequence[str],
    product_candidate_sha: str,
    acceptance_harness_sha: str,
) -> None:
    if _INVOCATION.fullmatch(invocation_id) is None:
        raise SupervisorError("invocation id is invalid")
    if not evidence_root.is_absolute():
        raise SupervisorError("evidence root must be absolute")
    if not child_argv or not all(isinstance(item, str) and item for item in child_argv):
        raise SupervisorError("child command is invalid")
    if (
        _SHA.fullmatch(product_candidate_sha) is None
        or _SHA.fullmatch(acceptance_harness_sha) is None
    ):
        raise SupervisorError("candidate identities must be exact commits")
    forbidden = ("postgres", "password", "token", "labpilot_task12_database_url")
    if any(marker in item.lower() for item in child_argv for marker in forbidden):
        raise SupervisorError("child argv must not contain database credentials")


def _write_exclusive_json(path: Path, value: dict[str, object]) -> None:
    encoded = _encoded(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise SupervisorError(f"invocation already started: {path.parent.name}") from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _copy_bounded(source: BinaryIO, destination: BinaryIO) -> bool:
    written = 0
    truncated = False
    while chunk := source.read(65_536):
        remaining = MAX_LOG_BYTES - written
        if remaining > 0:
            destination.write(chunk[:remaining])
            written += min(len(chunk), remaining)
        truncated = truncated or len(chunk) > remaining
    return truncated


def _encoded(value: dict[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--product-candidate-sha", required=True)
    parser.add_argument("--acceptance-harness-sha", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--fixture-path", required=True)
    parser.add_argument("--systemd-unit-identity", required=True)
    parser.add_argument("--child-command-file", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        command = json.loads(arguments.child_command_file.read_text(encoding="utf-8"))
        if (
            type(command) is not dict
            or set(command) != {"argv"}
            or type(command["argv"]) is not list
        ):
            raise SupervisorError("child command file schema is invalid")
        return run_supervisor(
            invocation_id=arguments.invocation_id,
            evidence_root=arguments.evidence_root,
            child_argv=command["argv"],
            product_candidate_sha=arguments.product_candidate_sha,
            acceptance_harness_sha=arguments.acceptance_harness_sha,
            config_path=arguments.config_path,
            fixture_path=arguments.fixture_path,
            systemd_unit_identity=arguments.systemd_unit_identity,
        )
    except (OSError, json.JSONDecodeError, SupervisorError) as error:
        print(f"task12 supervisor refused launch: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
