from pathlib import Path

import pytest

from tests.architecture.dependency_rules import (
    DependencyViolation,
    find_dependency_violations,
)

pytestmark = pytest.mark.architecture


def write_module(root: Path, relative_path: str, source: str) -> Path:
    path = root / "src" / "labpilot" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def test_all_aliases_in_import_statement_are_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "import labpilot.common, labpilot.coordinator\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations == [
        DependencyViolation(
            path="agent_runtime/example.py",
            source_module="agent_runtime",
            target_module="coordinator",
            line=1,
        )
    ]


def test_absolute_from_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot.execution import contracts\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "execution"


def test_root_package_alias_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot import coordinator, execution\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert {violation.target_module for violation in violations} == {
        "coordinator",
        "execution",
    }


def test_relative_root_alias_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/sub/example.py",
        "from ... import coordinator\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_relative_import_is_resolved(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/sub/example.py",
        "from ...coordinator import worker\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_type_checking_import_is_not_ignored(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "context/example.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from labpilot.coordinator import worker\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_allowed_common_import_passes(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot.common import readiness\n",
    )
    assert find_dependency_violations(tmp_path / "src" / "labpilot") == []


def test_real_source_tree_respects_the_allowlist() -> None:
    violations = find_dependency_violations(Path("src/labpilot"))
    assert violations == []
