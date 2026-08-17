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


def test_api_has_no_physical_execution_authority() -> None:
    forbidden_imports = (
        "labpilot.execution.runner_client",
        "labpilot.execution.runner_daemon",
        "labpilot.execution.systemd_supervisor",
        "labpilot.execution.execution_service",
    )
    forbidden_calls = {"dispatch_attempt", "reconcile_attempt", "request_stop"}
    for path in Path("src/labpilot/api").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not any(name.startswith(forbidden_imports) for name in imports), path
        assert calls.isdisjoint(forbidden_calls), path
