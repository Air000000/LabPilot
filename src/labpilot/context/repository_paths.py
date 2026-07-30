from pathlib import Path

from labpilot.context.repository_types import (
    RepositoryAllowedRootNotDirectory,
    RepositoryAllowedRootUnavailable,
    RepositoryNotDirectory,
    RepositoryPathMustBeAbsolute,
    RepositoryPathOutsideAllowedRoot,
    RepositoryPathTraversalEscape,
    RepositorySymlinkEscape,
    RepositoryUnavailable,
)


def normalize_repository_allowed_roots(
    configured_roots: tuple[Path, ...],
) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for configured_root in configured_roots:
        try:
            root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise RepositoryAllowedRootUnavailable(configured_root) from exc
        if not root.is_dir():
            raise RepositoryAllowedRootNotDirectory(configured_root)
        if root not in normalized:
            normalized.append(root)
    return tuple(normalized)


def resolve_repository_root(
    user_path: Path,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path:
    if not user_path.is_absolute():
        raise RepositoryPathMustBeAbsolute(user_path)

    if not any(user_path.is_relative_to(root) for root in allowed_roots):
        raise RepositoryPathOutsideAllowedRoot(user_path)

    try:
        resolved_path = user_path.resolve(strict=True)
    except OSError as exc:
        raise RepositoryUnavailable(user_path) from exc

    if not any(resolved_path.is_relative_to(root) for root in allowed_roots):
        if ".." in user_path.parts:
            raise RepositoryPathTraversalEscape(user_path)
        raise RepositorySymlinkEscape(user_path, resolved_path)

    if not resolved_path.is_dir():
        raise RepositoryNotDirectory(user_path)
    return resolved_path
