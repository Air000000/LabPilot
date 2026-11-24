"""Thin mini-SWE Docker boundary: bounded output, frozen export, synchronous cleanup."""

from __future__ import annotations

import importlib
import io
import os
import re
import selectors
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4

# The upstream package loads its global .env at import time. Never inherit it.
if "minisweagent" in sys.modules:
    raise RuntimeError("worker_bootstrap_order_invalid")
_config_directory = TemporaryDirectory(prefix="labpilot-coding-config-")
os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = _config_directory.name
os.environ["MSWEA_SILENT_STARTUP"] = "1"
DockerEnvironment = importlib.import_module("minisweagent.environments.docker").DockerEnvironment


class BoundaryError(RuntimeError):
    pass


def bounded(
    argv: list[str], *, timeout: float, limit: int = 1024 * 1024, data: bytes | None = None
) -> tuple[int, bytes]:
    # No shell on the host. The container is the only place model commands run.
    with subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        try:
            assert process.stdout is not None
            output = bytearray()
            deadline = time.monotonic() + timeout
            sent = 0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                if data is not None:
                    assert process.stdin is not None
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BoundaryError("command_timeout")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.fileobj is process.stdin:
                            assert data is not None
                            try:
                                sent += os.write(key.fd, data[sent : sent + 65536])
                            except BrokenPipeError:
                                sent = len(data)
                            if sent == len(data):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > limit:
                            raise BoundaryError("command_output_limit")
            return process.wait(timeout=max(0.01, deadline - time.monotonic())), bytes(output)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def source_archive(files: dict[str, bytes], modes: dict[str, int] | None = None) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        directories = sorted(
            {str(p) for name in files for p in PurePosixPath(name).parents if str(p) != "."}
        )
        for directory in directories:
            path = PurePosixPath(directory)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
                raise BoundaryError("archive_path_invalid")
            info = tarfile.TarInfo(directory)
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
            archive.addfile(info)
        for name, data in sorted(files.items()):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
                raise BoundaryError("archive_path_invalid")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = (
                len(data),
                (modes or {}).get(name, 0o644),
                1000,
                1000,
            )
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def archive_files(data: bytes) -> dict[str, tuple[int, bytes]]:
    files: dict[str, tuple[int, bytes]] = {}
    seen: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for entry in archive:
            name = entry.name.removeprefix("./").rstrip("/")
            if name in {"", "."} and entry.isdir():
                continue
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or ".git" in path.parts
                or path.as_posix() != name
                or name in seen
                or entry.mode & 0o7000
            ):
                raise BoundaryError("archive_path_or_mode_invalid")
            seen.add(name)
            if len(seen) > 20_000:
                raise BoundaryError("archive_entry_limit")
            if entry.isdir():
                continue
            if not entry.isfile() or entry.mode not in {0o644, 0o755}:
                raise BoundaryError("archive_file_type_invalid")
            total += entry.size
            if len(files) >= 10_000 or entry.size > 4 * 1024 * 1024 or total > 128 * 1024 * 1024:
                raise BoundaryError("archive_size_limit")
            stream = archive.extractfile(entry)
            assert stream is not None
            contents = stream.read(entry.size + 1)
            files[name] = (entry.mode, contents)
            if len(contents) != entry.size:
                raise BoundaryError("archive_size_invalid")
    return files


class CodingEnvironment(DockerEnvironment):
    def __init__(self, image: str, *, task_id: str | None = None, timeout: int = 60):
        self.task_id = task_id or uuid4().hex
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.task_id) is None:
            raise BoundaryError("task_identity_invalid")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise BoundaryError("image_digest_required")
        self.volume_name = "labpilot-coding-" + self.task_id
        self._owns_volume = False
        code, existing = bounded(
            [
                "/usr/bin/docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                "name=^" + self.volume_name + "$",
            ],
            timeout=10,
        )
        if code or existing.strip():
            raise BoundaryError("workspace_volume_unavailable")
        code, name = bounded(
            [
                "/usr/bin/docker",
                "volume",
                "create",
                "--driver=local",
                "--opt=type=tmpfs",
                "--opt=device=tmpfs",
                "--opt=o=size=512m,uid=1000,gid=1000",
                "--label=labpilot.coding.task=" + self.task_id,
                self.volume_name,
            ],
            timeout=10,
        )
        if code or name.decode().strip() != self.volume_name:
            raise BoundaryError("workspace_volume_create_failed")
        self._owns_volume = True
        try:
            super().__init__(
                image=image,
                cwd="/workspace",
                timeout=timeout,
                executable="/usr/bin/docker",
                container_timeout="20m",
                env={},
                forward_env=[],
                run_args=[
                    "--rm",
                    "--init",
                    "--read-only",
                    "--user=1000:1000",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--network=none",
                    "--cpus=2",
                    "--memory=2g",
                    "--memory-swap=2g",
                    "--pids-limit=128",
                    "--mount=type=volume,src=" + self.volume_name + ",dst=/workspace,volume-nocopy",
                    "--tmpfs=/tmp:rw,size=128m,uid=1000,gid=1000",
                    "--label=labpilot.coding.task=" + self.task_id,
                ],
            )

        except BaseException:
            self.cleanup()
            raise

    def load(self, files: dict[str, bytes], modes: dict[str, int] | None = None) -> None:
        payload = source_archive(files, modes)
        # Validate the same format at both trust boundaries, before copying any bytes.
        if archive_files(payload) != {
            name: ((modes or {}).get(name, 0o644), data) for name, data in files.items()
        }:
            raise BoundaryError("source_archive_mismatch")
        code, _ = bounded(
            [
                "/usr/bin/docker",
                "exec",
                "-i",
                self.container_id,
                "tar",
                "-xf",
                "-",
                "-C",
                "/workspace",
                "--no-same-owner",
            ],
            timeout=30,
            data=payload,
        )
        if code:
            raise BoundaryError("source_copy_failed")

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        if not self.container_id:
            raise BoundaryError("container_not_available")
        command = action.get("command")
        if not isinstance(command, str):
            raise BoundaryError("command_invalid")
        try:
            code, data = bounded(
                [
                    "/usr/bin/docker",
                    "exec",
                    "-w",
                    cwd or "/workspace",
                    self.container_id,
                    "bash",
                    "-c",
                    command,
                ],
                timeout=timeout or self.config.timeout,
            )
        except BaseException:
            self.cleanup()
            raise
        result = {
            "output": data.decode("utf-8", errors="replace"),
            "returncode": code,
            "exception_info": "",
        }
        self._check_finished(result)
        return result

    def export(self) -> dict[str, tuple[int, bytes]]:
        if not self.container_id:
            raise BoundaryError("container_not_available")
        code, _ = bounded(["/usr/bin/docker", "pause", self.container_id], timeout=10)
        if code:
            raise BoundaryError("container_freeze_failed")
        code, data = bounded(
            ["/usr/bin/docker", "cp", f"{self.container_id}:/workspace/.", "-"],
            timeout=30,
            limit=160 * 1024 * 1024,
        )
        if code:
            raise BoundaryError("container_export_failed")
        return archive_files(data)

    def cleanup(self) -> None:
        identity = getattr(self, "container_id", None)
        if identity:
            if re.fullmatch(r"[0-9a-f]{12,64}", identity) is None:
                raise BoundaryError("container_identity_invalid")
            bounded(["/usr/bin/docker", "rm", "-f", identity], timeout=15)
            code, remaining = bounded(
                ["/usr/bin/docker", "ps", "-aq", "--filter", "id=" + identity], timeout=10
            )
            if code != 0 or remaining.strip():
                raise BoundaryError("container_cleanup_failed")
            self.container_id = None
        if getattr(self, "_owns_volume", False):
            code, _ = bounded(["/usr/bin/docker", "volume", "rm", self.volume_name], timeout=10)
            if code:
                raise BoundaryError("workspace_volume_cleanup_failed")
            self._owns_volume = False

    def __del__(self):
        # Explicit cleanup is checked by the caller; destructors cannot certify completion.
        try:
            self.cleanup()
        except Exception:
            pass
