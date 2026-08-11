import ast
from dataclasses import dataclass
from pathlib import Path

MODEL_REGISTRY_PATH = Path("db/model_registry.py")
MODEL_REGISTRY_ALLOWED_IMPORTS = frozenset(
    {
        "labpilot.context.models",
        "labpilot.experiments.models",
        "labpilot.experiments.proposal_models",
        "labpilot.observability.models",
    }
)

ALLOWED_INTERNAL_IMPORTS: dict[str, set[str]] = {
    "common": {"common"},
    "db": {"common", "db"},
    "observability": {"common", "db", "observability"},
    "experiments": {"common", "db", "observability", "experiments"},
    "context": {"common", "db", "observability", "experiments", "context"},
    "tools_policy": {"common", "db", "observability", "context", "tools_policy"},
    "agent_runtime": {
        "common",
        "db",
        "observability",
        "experiments",
        "context",
        "tools_policy",
        "agent_runtime",
    },
    "execution": {"common", "db", "observability", "execution"},
    "results_validation": {
        "common",
        "db",
        "observability",
        "experiments",
        "execution",
        "results_validation",
    },
    "coordinator": {
        "common",
        "db",
        "observability",
        "experiments",
        "execution",
        "results_validation",
        "coordinator",
    },
    "api": {
        "common",
        "db",
        "observability",
        "experiments",
        "context",
        "tools_policy",
        "agent_runtime",
        "execution",
        "results_validation",
        "coordinator",
        "api",
    },
}

ROOT_MODULE_CLASSIFICATION = {
    "__init__": "common",
    "settings": "common",
    "main": "api",
}


@dataclass(frozen=True, slots=True, order=True)
class DependencyViolation:
    path: str
    source_module: str
    target_module: str
    line: int


def classify_source_module(relative_path: Path) -> str:
    if len(relative_path.parts) == 1:
        return ROOT_MODULE_CLASSIFICATION[relative_path.stem]
    return relative_path.parts[0]


def classify_absolute_target(module_name: str) -> str | None:
    parts = module_name.split(".")
    if not parts or parts[0] != "labpilot":
        return None
    if len(parts) == 1:
        return "common"
    return ROOT_MODULE_CLASSIFICATION.get(parts[1], parts[1])


def is_path_specific_import_allowed(
    *,
    relative_path: Path,
    target_module_name: str | None,
) -> bool:
    return (
        relative_path == MODEL_REGISTRY_PATH
        and target_module_name in MODEL_REGISTRY_ALLOWED_IMPORTS
    )


def resolve_relative_target(
    *,
    relative_path: Path,
    level: int,
    module_name: str | None,
) -> str:
    package_parts = list(relative_path.parent.parts)
    ascend = level - 1
    if ascend > len(package_parts):
        raise AssertionError(
            f"invalid relative import level={level} in {relative_path.as_posix()}"
        )
    base = package_parts[: len(package_parts) - ascend]
    suffix = module_name.split(".") if module_name else []
    resolved = base + suffix
    if not resolved:
        return "common"
    return ROOT_MODULE_CLASSIFICATION.get(resolved[0], resolved[0])


def imported_targets(
    tree: ast.AST, relative_path: Path
) -> list[tuple[str, str | None, int]]:
    targets: list[tuple[str, str | None, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = classify_absolute_target(alias.name)
                if target is not None:
                    targets.append((target, alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module == "labpilot":
                for alias in node.names:
                    target_module_name = f"labpilot.{alias.name}"
                    target = classify_absolute_target(target_module_name)
                    if target is not None:
                        targets.append((target, target_module_name, node.lineno))
                continue
            if node.level > 0 and node.module is None:
                root_target = resolve_relative_target(
                    relative_path=relative_path,
                    level=node.level,
                    module_name=None,
                )
                if root_target == "common":
                    for alias in node.names:
                        targets.append(
                            (
                                ROOT_MODULE_CLASSIFICATION.get(alias.name, alias.name),
                                None,
                                node.lineno,
                            )
                        )
                else:
                    targets.append((root_target, None, node.lineno))
                continue
            target_module_name: str | None = None
            if node.level > 0:
                target = resolve_relative_target(
                    relative_path=relative_path,
                    level=node.level,
                    module_name=node.module,
                )
            else:
                target_module_name = node.module or ""
                target = classify_absolute_target(target_module_name)
            if target is not None:
                targets.append((target, target_module_name, node.lineno))
    return targets


def find_dependency_violations(source_root: Path) -> list[DependencyViolation]:
    violations: list[DependencyViolation] = []
    for path in sorted(source_root.rglob("*.py")):
        relative_path = path.relative_to(source_root)
        source_module = classify_source_module(relative_path)
        allowed = ALLOWED_INTERNAL_IMPORTS[source_module]
        tree = ast.parse(path.read_text(), filename=relative_path.as_posix())
        for target_module, target_module_name, line in imported_targets(
            tree, relative_path
        ):
            if target_module not in allowed and not is_path_specific_import_allowed(
                relative_path=relative_path,
                target_module_name=target_module_name,
            ):
                violations.append(
                    DependencyViolation(
                        path=relative_path.as_posix(),
                        source_module=source_module,
                        target_module=target_module,
                        line=line,
                    )
                )
    return sorted(violations)
