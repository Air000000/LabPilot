from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from labpilot.context.content_store import ContentStoreCorruption
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_source import (
    RepositorySnapshotSourceService,
    RepositorySourceReadError,
)
from labpilot.context.repository_types import (
    GitObject,
    GitTreeEntry,
    RepositoryGitCommandTimeout,
    RepositoryProbe,
    RepositorySnapshotInspectionBundleUnsupportedEntry,
    RepositoryUnavailable,
)

SNAPSHOT_ID = UUID("10000000-0000-4000-8000-000000000001")
REGISTRATION_ID = UUID("20000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@asynccontextmanager
async def _session():
    yield object()


class _Git:
    def __init__(self, tree: tuple[GitTreeEntry, ...], contents: dict[str, bytes]) -> None:
        self.tree = tree
        self.contents = contents
        self.roots: list[Path] = []
        self.probe_error: Exception | None = None
        self.probe_result = RepositoryProbe(
            canonical_root_path="/bound/repository",
            head_commit="a" * 40,
            current_branch="main",
            is_detached_head=False,
            tracked_tree_identity="b" * 40,
            status_entries=(),
            remote_origin_sanitized=None,
            default_branch=None,
            observation_profile_version="v1",
        )

    async def ls_tree(
        self, root: Path, commit: str, *, max_entries: int, max_output_bytes: int
    ) -> tuple[GitTreeEntry, ...]:
        self.roots.append(root)
        return self.tree[:max_entries]

    async def cat_file_batch(
        self,
        root: Path,
        object_oids: tuple[str, ...],
        *,
        max_object_bytes: int,
        max_total_bytes: int,
    ) -> tuple[GitObject, ...]:
        self.roots.append(root)
        return tuple(GitObject(oid, "blob", self.contents[oid]) for oid in object_oids)

    async def probe(self, root: Path) -> RepositoryProbe:
        if self.probe_error is not None:
            raise self.probe_error
        self.roots.append(root)
        return self.probe_result


class _Store:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.verified: list[tuple[str, str]] = []

    async def read_bytes(self, ref: str) -> bytes:
        return self.contents[ref]

    async def verify(self, ref: str, expected_hash: str) -> None:
        self.verified.append((ref, expected_hash))
        if sha256(self.contents[ref]).hexdigest() != expected_hash:
            raise ContentStoreCorruption("reference_expected_hash_mismatch")


class _Snapshots:
    def __init__(self, snapshot: object, entries: list[object]) -> None:
        self.snapshot = snapshot
        self.entries = entries

    async def get_snapshot(self, session: object, snapshot_id: UUID) -> object | None:
        return self.snapshot if snapshot_id == SNAPSHOT_ID else None

    async def get_bundle_by_snapshot(self, session: object, snapshot_id: UUID) -> object | None:
        return SimpleNamespace(inspection_bundle_id=UUID("40000000-0000-4000-8000-000000000001"))

    async def list_bundle_entries(self, session: object, bundle_id: UUID) -> list[object]:
        return self.entries


class _Registrations:
    async def get_registration(self, session: object, registration_id: UUID) -> object | None:
        if registration_id != REGISTRATION_ID:
            return None
        return SimpleNamespace(
            project_id=PROJECT_ID,
            canonical_root_path="/bound/repository",
            status="active",
        )


def _entry(
    path: str,
    *,
    kind: str,
    file_type: str = "regular",
    mode: str = "100644",
    content_ref: str | None = None,
    content: bytes | None = None,
    ordinal: int = 0,
) -> object:
    return SimpleNamespace(
        repository_path=path,
        entry_kind=kind,
        file_type=file_type,
        file_mode=mode,
        content_ref=content_ref,
        content_hash=None if content is None else sha256(content).hexdigest(),
        stable_ordinal=ordinal,
    )


def _source(*, entries: list[object] | None = None, store: _Store | None = None):
    tree = (
        GitTreeEntry("base.txt", "regular", "100644", "blob", "a" * 40),
        GitTreeEntry("dir/child.txt", "regular", "100644", "blob", "b" * 40),
        GitTreeEntry("link", "symlink", "120000", "blob", "c" * 40),
        GitTreeEntry("module", "submodule", "160000", "commit", "d" * 40),
    )
    git = _Git(tree, {"a" * 40: b"base\n", "b" * 40: b"child\n", "c" * 40: b"base.txt"})
    snapshot = SimpleNamespace(
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity="e" * 64,
        repository_registration_id=REGISTRATION_ID,
        canonical_root_path="/bound/repository",
        head_commit="a" * 40,
        current_branch="main",
        is_detached_head=False,
        is_clean=not entries,
        tracked_tree_identity="b" * 40,
    )
    source = RepositorySnapshotSourceService(
        _session, git_adapter=git, content_store=store or _Store({})
    )
    source._snapshots = _Snapshots(snapshot, entries or [])
    source._registrations = _Registrations()
    return source, git


@pytest.mark.asyncio
async def test_snapshot_source_clean_tree_descriptor_directory_and_read() -> None:
    source, git = _source()

    descriptor = await source.describe(SNAPSHOT_ID)
    listed = await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=10)
    text = await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=20)

    assert descriptor.project_id == PROJECT_ID
    assert [entry.relative_path for entry in listed] == [
        "base.txt",
        "dir/child.txt",
        "link",
        "module",
    ]
    assert text.text == "base\n"
    assert await source.directory_exists(SNAPSHOT_ID, "") is True
    assert await source.directory_exists(SNAPSHOT_ID, "dir") is True
    assert all(root == Path("/bound/repository") for root in git.roots)


@pytest.mark.asyncio
async def test_snapshot_source_materializes_and_verifies_exact_regular_and_symlink_bytes(
    tmp_path: Path,
) -> None:
    source, git = _source()
    git.tree = git.tree[:3]
    destination = tmp_path / "source"

    materialized = await source.materialize_exact_source(
        repository_snapshot_id=SNAPSHOT_ID,
        expected_repository_snapshot_identity="e" * 64,
        destination=destination,
    )

    assert [
        (entry.relative_path, entry.file_type, entry.file_mode)
        for entry in materialized.entries
    ] == [
        ("base.txt", "regular", "100644"),
        ("dir/child.txt", "regular", "100644"),
        ("link", "symlink", "120000"),
    ]
    assert [entry.content_sha256 for entry in materialized.entries] == [
        sha256(b"base\n").hexdigest(),
        sha256(b"child\n").hexdigest(),
        sha256(b"base.txt").hexdigest(),
    ]
    assert (destination / "base.txt").read_bytes() == b"base\n"
    assert (destination / "link").readlink() == Path("base.txt")

    assert await source.materialize_exact_source(
        repository_snapshot_id=SNAPSHOT_ID,
        expected_repository_snapshot_identity="e" * 64,
        destination=destination,
    ) == materialized

    (destination / "base.txt").write_bytes(b"tampered\n")
    with pytest.raises(RepositorySourceReadError) as captured:
        await source.materialize_exact_source(
            repository_snapshot_id=SNAPSHOT_ID,
            expected_repository_snapshot_identity="e" * 64,
            destination=destination,
        )
    assert captured.value.reason_code == "SOURCE_TAMPERED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tree_index", "content", "reason_code"),
    [
        (2, b"../../escape", "SOURCE_SYMLINK_ESCAPE"),
        (
            0,
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + b"a" * 64 + b"\nsize 1\n",
            "SOURCE_LFS_UNSUPPORTED",
        ),
    ],
)
async def test_snapshot_source_rejects_unsafe_materialization(
    tmp_path: Path,
    tree_index: int,
    content: bytes,
    reason_code: str,
) -> None:
    source, git = _source()
    git.tree = (git.tree[tree_index],)
    oid = git.tree[0].object_oid
    assert oid is not None
    git.contents[oid] = content

    with pytest.raises(RepositorySourceReadError) as captured:
        await source.materialize_exact_source(
            repository_snapshot_id=SNAPSHOT_ID,
            expected_repository_snapshot_identity="e" * 64,
            destination=tmp_path / reason_code,
        )

    assert captured.value.reason_code == reason_code


@pytest.mark.asyncio
async def test_snapshot_source_rejects_submodule_materialization(tmp_path: Path) -> None:
    source, git = _source()
    git.tree = (git.tree[3],)

    with pytest.raises(RepositorySourceReadError) as captured:
        await source.materialize_exact_source(
            repository_snapshot_id=SNAPSHOT_ID,
            expected_repository_snapshot_identity="e" * 64,
            destination=tmp_path / "source",
        )

    assert captured.value.reason_code == "SOURCE_SUBMODULE_UNSUPPORTED"


@pytest.mark.asyncio
async def test_source_operations_use_only_fake_git_and_cas_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, git = _source()

    def forbid_live_read(*args: object, **kwargs: object) -> None:
        pytest.fail("live worktree read is forbidden")

    for method in ("open", "read_bytes", "read_text"):
        monkeypatch.setattr(Path, method, forbid_live_read)

    assert (await source.describe(SNAPSHOT_ID)).project_id == PROJECT_ID
    assert git.roots == []
    listed = await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=10)
    fact = await source.path_fact(SNAPSHOT_ID, "base.txt")
    directory = await source.directory_exists(SNAPSHOT_ID, "dir")
    text = await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=20)
    hits = await source.search_text(
        SNAPSHOT_ID, "base", prefix=None, max_results=1, max_file_bytes=20
    )

    assert [entry.relative_path for entry in listed] == [
        "base.txt",
        "dir/child.txt",
        "link",
        "module",
    ]
    assert fact is not None and fact.relative_path == "base.txt"
    assert directory is True
    assert text.text == "base\n"
    assert hits[0].relative_path == "base.txt"
    assert all(root == Path("/bound/repository") for root in git.roots)


@pytest.mark.asyncio
async def test_snapshot_source_applies_overlay_deletion_mode_and_untracked_content() -> None:
    overlay = b"overlay\n"
    added = b"new\n"
    store = _Store({"overlay": overlay, "added": added})
    source, _ = _source(
        store=store,
        entries=[
            _entry("base.txt", kind="tracked_overlay", content_ref="overlay", content=overlay),
            _entry("dir/child.txt", kind="deletion", ordinal=1),
            _entry("new.txt", kind="untracked_file", content_ref="added", content=added, ordinal=3),
        ],
    )

    listed = await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=10)
    base = await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=20)
    added_text = await source.read_text(
        SNAPSHOT_ID, "new.txt", start_line=1, end_line=1, max_bytes=20
    )

    assert [(entry.relative_path, entry.file_mode) for entry in listed] == [
        ("base.txt", "100644"),
        ("link", "120000"),
        ("module", "160000"),
        ("new.txt", "100644"),
    ]
    assert base.text == "overlay\n"
    assert added_text.text == "new\n"
    assert await source.path_fact(SNAPSHOT_ID, "dir/child.txt") is None
    assert store.verified == [
        ("overlay", sha256(overlay).hexdigest()),
        ("added", sha256(added).hexdigest()),
    ]


@pytest.mark.asyncio
async def test_snapshot_source_preserves_symlink_target_and_rejects_submodule_and_non_utf8() -> (
    None
):
    bad = b"\xff"
    source, _ = _source(
        store=_Store({"bad": bad}),
        entries=[_entry("bad.txt", kind="untracked_file", content_ref="bad", content=bad)],
    )

    link = await source.read_text(SNAPSHOT_ID, "link", start_line=1, end_line=1, max_bytes=20)
    assert link.text == "base.txt"
    with pytest.raises(RepositorySourceReadError, match="submodule"):
        await source.read_text(SNAPSHOT_ID, "module", start_line=1, end_line=1, max_bytes=20)
    with pytest.raises(RepositorySourceReadError, match="non_utf8"):
        await source.read_text(SNAPSHOT_ID, "bad.txt", start_line=1, end_line=1, max_bytes=20)


@pytest.mark.asyncio
async def test_snapshot_source_rejects_unsafe_paths_and_enforces_bounds() -> None:
    source, _ = _source()

    for path in ("/absolute", "../escape", "dir\\file"):
        with pytest.raises(RepositorySourceReadError, match="unsafe_path"):
            await source.path_fact(SNAPSHOT_ID, path)
    with pytest.raises(RepositorySourceReadError, match="max_entries"):
        await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=0)
    with pytest.raises(RepositorySourceReadError, match="line_range"):
        await source.read_text(SNAPSHOT_ID, "base.txt", start_line=0, end_line=1, max_bytes=20)
    with pytest.raises(RepositorySourceReadError, match="line_range"):
        await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=501, max_bytes=20)
    with pytest.raises(RepositorySourceReadError, match="max_bytes"):
        await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=1)
    with pytest.raises(RepositorySourceReadError, match="max_results"):
        await source.search_text(SNAPSHOT_ID, "base", prefix=None, max_results=0, max_file_bytes=20)
    with pytest.raises(RepositorySourceReadError, match="max_file_bytes"):
        await source.search_text(SNAPSHOT_ID, "base", prefix=None, max_results=1, max_file_bytes=0)


@pytest.mark.asyncio
async def test_snapshot_source_search_prefix_truncation_and_regular_mode_change() -> None:
    source, _ = _source(entries=[_entry("base.txt", kind="mode_change", mode="100755")])

    listed = await source.list_tree(SNAPSHOT_ID, prefix="dir", max_entries=10)
    limited = await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=1)
    mode_changed = await source.read_text(
        SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=20
    )
    hits = await source.search_text(
        SNAPSHOT_ID, "base", prefix=None, max_results=1, max_file_bytes=20
    )

    assert [entry.relative_path for entry in listed] == ["dir/child.txt"]
    assert [entry.relative_path for entry in limited] == ["base.txt"]
    assert mode_changed.text == "base\n"
    fact = await source.path_fact(SNAPSHOT_ID, "base.txt")
    assert fact is not None and fact.file_mode == "100755"
    assert [(hit.relative_path, hit.line_number, hit.line_text) for hit in hits] == [
        ("base.txt", 1, "base")
    ]


@pytest.mark.asyncio
async def test_snapshot_source_rejects_invalid_numeric_bounds_and_cas_hash_mismatch() -> None:
    source, _ = _source()

    for invalid in (True, 1.0, float("nan")):
        with pytest.raises(RepositorySourceReadError, match="max_entries"):
            await source.list_tree(SNAPSHOT_ID, prefix=None, max_entries=invalid)
        with pytest.raises(RepositorySourceReadError, match="max_results"):
            await source.search_text(
                SNAPSHOT_ID, "base", prefix=None, max_results=invalid, max_file_bytes=20
            )
        with pytest.raises(RepositorySourceReadError, match="max_file_bytes"):
            await source.search_text(
                SNAPSHOT_ID, "base", prefix=None, max_results=1, max_file_bytes=invalid
            )
    with pytest.raises(RepositorySourceReadError, match="max_results"):
        await source.search_text(
            SNAPSHOT_ID, "base", prefix=None, max_results=1001, max_file_bytes=20
        )

    content = b"overlay\n"
    source, _ = _source(
        store=_Store({"overlay": content}),
        entries=[
            _entry("base.txt", kind="tracked_overlay", content_ref="overlay", content=content)
        ],
    )
    source._snapshots.entries[0].content_hash = "0" * 64
    with pytest.raises(ContentStoreCorruption):
        await source.read_text(SNAPSHOT_ID, "base.txt", start_line=1, end_line=1, max_bytes=20)


@pytest.mark.asyncio
async def test_snapshot_source_search_fails_closed_and_maps_probe_failures() -> None:
    source, _ = _source(
        store=_Store({"bad": b"\xffbase"}),
        entries=[_entry("bad.txt", kind="untracked_file", content_ref="bad", content=b"\xffbase")],
    )
    with pytest.raises(RepositorySourceReadError, match="non_utf8"):
        await source.search_text(SNAPSHOT_ID, "base", prefix=None, max_results=1, max_file_bytes=20)

    for error in (
        RepositoryGitCommandTimeout("probe", 1),
        RepositorySnapshotInspectionBundleUnsupportedEntry("probe_unsupported"),
    ):
        source, git = _source()
        git.probe_error = error
        assert (
            await source.check_approval_readiness(SNAPSHOT_ID)
        ).status == "repository_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_ready", "expected_reason"),
    [
        ("ready", True, None),
        ("changed", False, "repository_changed"),
        ("unavailable", False, "repository_unavailable"),
    ],
)
async def test_proposal_adapter_maps_readiness_without_reconstructing_source(
    state: str,
    expected_ready: bool,
    expected_reason: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, git = _source()

    async def no_tree(*args: object, **kwargs: object) -> tuple[GitTreeEntry, ...]:
        pytest.fail("adapter must not reconstruct the snapshot tree")

    monkeypatch.setattr(git, "ls_tree", no_tree)
    if state == "changed":
        git.probe_result = replace(git.probe_result, current_branch="other")
    elif state == "unavailable":
        git.probe_error = RepositoryUnavailable(Path("/bound/repository"))

    adapter = ProposalRepositoryFactAdapter(source)
    snapshot = await adapter.snapshot_state(SNAPSHOT_ID)
    approval = await adapter.approval_source_state(SNAPSHOT_ID)

    assert snapshot.project_id == PROJECT_ID
    assert approval.project_id == PROJECT_ID
    assert approval.ready is expected_ready
    assert approval.reason_code == expected_reason
