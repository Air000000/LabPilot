from pathlib import Path

import pytest

from labpilot.context.repository_paths import (
    normalize_repository_allowed_roots,
    resolve_repository_root,
)
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


def test_normalize_allowed_roots_canonicalizes_and_deduplicates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)

    assert normalize_repository_allowed_roots((root, alias)) == (root.resolve(),)


def test_normalize_allowed_roots_rejects_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(RepositoryAllowedRootUnavailable) as captured:
        normalize_repository_allowed_roots((missing,))

    assert captured.value.path == missing
    assert not missing.exists()


def test_normalize_allowed_roots_rejects_file(tmp_path: Path) -> None:
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RepositoryAllowedRootNotDirectory) as captured:
        normalize_repository_allowed_roots((file_path,))

    assert captured.value.path == file_path


def test_resolve_repository_root_accepts_directory_inside_root(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    repository = allowed / "project"
    repository.mkdir(parents=True)

    assert resolve_repository_root(
        repository,
        allowed_roots=normalize_repository_allowed_roots((allowed,)),
    ) == repository.resolve()


def test_resolve_repository_root_rejects_relative_path(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    allowed.mkdir()

    with pytest.raises(RepositoryPathMustBeAbsolute):
        resolve_repository_root(Path("project"), allowed_roots=(allowed.resolve(),))


def test_resolve_repository_root_rejects_lexical_outside_path(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(RepositoryPathOutsideAllowedRoot) as captured:
        resolve_repository_root(outside, allowed_roots=(allowed.resolve(),))

    assert captured.value.path == outside


def test_resolve_repository_root_rejects_dotdot_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    escaped = allowed / ".." / "outside"

    with pytest.raises(RepositoryPathTraversalEscape) as captured:
        resolve_repository_root(escaped, allowed_roots=(allowed.resolve(),))

    assert captured.value.path == escaped


def test_resolve_repository_root_rejects_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RepositorySymlinkEscape) as captured:
        resolve_repository_root(link, allowed_roots=(allowed.resolve(),))

    assert captured.value.path == link
    assert captured.value.resolved_path == outside.resolve()


@pytest.mark.parametrize("broken_symlink", [False, True])
def test_resolve_repository_root_rejects_unavailable_path(
    tmp_path: Path,
    *,
    broken_symlink: bool,
) -> None:
    allowed = tmp_path / "repositories"
    allowed.mkdir()
    missing = allowed / "missing"
    if broken_symlink:
        missing.symlink_to(allowed / "absent", target_is_directory=True)

    with pytest.raises(RepositoryUnavailable) as captured:
        resolve_repository_root(missing, allowed_roots=(allowed.resolve(),))

    assert captured.value.path == missing


def test_resolve_repository_root_rejects_file(tmp_path: Path) -> None:
    allowed = tmp_path / "repositories"
    allowed.mkdir()
    file_path = allowed / "file"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RepositoryNotDirectory) as captured:
        resolve_repository_root(file_path, allowed_roots=(allowed.resolve(),))

    assert captured.value.path == file_path
