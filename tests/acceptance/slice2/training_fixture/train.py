from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

_MAX_STARTS = 100
_MAX_DECIMAL_DIGITS = 1_000
_MAX_DECIMAL_ABSOLUTE_EXPONENT = 10_000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--spawn-child", action="store_true")
    parser.add_argument("--score")
    args = parser.parse_args(argv)
    score = _parse_score(parser, args.score)
    if not 0 <= args.duration <= 86_400 or not 0 <= args.exit_code <= 255:
        parser.error("duration or exit code is outside the bounded fixture contract")
    if any(len(value.encode("utf-8", "strict")) > 1024 for value in (args.value, args.dataset)):
        parser.error("fixture string argument is too large")

    output = args.output_dir.resolve(strict=True)
    if output.parts[-2:] != ("runtime", "output") or output.is_symlink():
        parser.error("output directory must be an existing runtime/output directory")
    oracle = output / "workload-test-oracle.json"
    child: subprocess.Popen[bytes] | None = None
    if args.spawn_child:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(86400)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    record = _record_start(
        oracle,
        seed=args.seed,
        value=args.value,
        dataset=args.dataset,
        child_pid=None if child is None else child.pid,
    )
    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline:
        time.sleep(min(0.1, deadline - time.monotonic()))
    if child is not None:
        child.terminate()
        child.wait(timeout=5)
    record["natural_exit"] = True
    record["exit_code"] = args.exit_code
    _write_locked(oracle, record)
    if score is not None:
        _write_result(output / "result.json", score)
    return int(args.exit_code)


def _parse_score(parser: argparse.ArgumentParser, value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        score = Decimal(value)
    except InvalidOperation:
        parser.error("score is outside the canonical decimal contract")
    try:
        _canonical_decimal_text(score)
    except ValueError:
        parser.error("score is outside the canonical decimal contract")
    return score


def _canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("score must be finite")
    if value.is_zero():
        return "0"
    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    if len(digits) > _MAX_DECIMAL_DIGITS:
        raise ValueError("score has too many decimal digits")
    if abs(exponent) > _MAX_DECIMAL_ABSOLUTE_EXPONENT:
        raise ValueError("score has an unsupported exponent")
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _write_result(path: Path, score: Decimal) -> None:
    payload = (
        '{"schema_version":"v1","metrics":{"score":' + _canonical_decimal_text(score) + "}}\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_start(
    path: Path,
    *,
    seed: int,
    value: str,
    dataset: str,
    child_pid: int | None,
) -> dict[str, object]:
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        content = handle.read()
        current = json.loads(content) if content else {}
        count = current.get("start_count", 0)
        if type(count) is not int or count >= _MAX_STARTS:
            raise RuntimeError("fixture start cardinality limit exceeded")
        record: dict[str, object] = {
            "schema_version": "v1",
            "seed": seed,
            "value": value,
            "dataset": dataset,
            "output_dir": str(path.parent),
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "pid": os.getpid(),
            "child_pid": child_pid,
            "start_count": count + 1,
            "natural_exit": False,
            "exit_code": None,
        }
        handle.seek(0)
        handle.truncate()
        json.dump(record, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        return record


def _write_locked(path: Path, value: object) -> None:
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        handle.truncate()
        json.dump(value, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
