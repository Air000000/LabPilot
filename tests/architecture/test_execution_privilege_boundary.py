import ast
from pathlib import Path

TERMINAL_RECEIPT_REPORTER = Path(
    "deploy/libexec/labpilot-terminal-receipt-v1.py"
)


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


def test_terminal_receipt_reporter_has_fixed_standalone_boundary() -> None:
    tree = ast.parse(
        TERMINAL_RECEIPT_REPORTER.read_text(),
        filename=str(TERMINAL_RECEIPT_REPORTER),
    )
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    functions = {
        node.name: tuple(argument.arg for argument in node.args.args)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    called_names = {call.id for call in calls if isinstance(call, ast.Name)}
    os_calls = {
        call.attr
        for call in calls
        if isinstance(call, ast.Attribute)
        and isinstance(call.value, ast.Name)
        and call.value.id == "os"
    }

    assert imports == {"collections.abc", "json", "os", "re", "sys"}
    assert functions == {
        "_optional_token": ("environ", "name"),
        "main": ("environ",),
    }
    assert called_names.isdisjoint(
        {"__import__", "compile", "eval", "exec", "open", "popen"}
    )
    assert os_calls == {"write"}
    assert sum(
        isinstance(call, ast.Attribute)
        and isinstance(call.value, ast.Name)
        and call.value.id == "os"
        and call.attr == "write"
        for call in calls
    ) == 1
