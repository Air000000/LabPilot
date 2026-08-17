import ast
from pathlib import Path


def test_privileged_runner_modules_import_no_db_or_domain_orchestration() -> None:
    root = Path("src/labpilot/execution")
    for name in (
        "runner_policy.py",
        "runtime_probe.py",
        "systemd_supervisor.py",
        "runner_daemon.py",
    ):
        tree = ast.parse((root / name).read_text(), filename=name)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            imported.startswith(
                (
                    "labpilot.db",
                    "labpilot.experiments",
                    "labpilot.context",
                    "labpilot.coordinator",
                    "labpilot.api",
                )
            )
            for imported in imports
        ), name
