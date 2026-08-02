from collections.abc import Callable
from hashlib import sha256
from uuid import UUID

import pytest

from labpilot.context.repository_identities import (
    ArtifactCandidateV1,
    BundleEntryManifestItemV1,
    CandidateEntrypointV1,
    ConfigurationCandidateV1,
    DatasetReferenceCandidateV1,
    DetectedCommandV1,
    EnvironmentCandidateV1,
    InspectionLimitsProfileV1,
    InspectionResourceLimitExceeded,
    InspectorIdentity,
    MetricCandidateV1,
    PersistedTextInvalid,
    RepositoryIdentityInputInvalid,
    RepositoryManifestEntryV1,
    UnresolvedCandidateValueV1,
    UnresolvedQuestionV1,
    dirty_worktree_manifest_hash,
    inspection_limits_profile_identity,
    inspector_registry_identity,
    repository_evidence_identity,
    repository_evidence_manifest_entries_hash,
    repository_evidence_manifest_hash,
    repository_inspection_bundle_entries_manifest_hash,
    repository_inspection_bundle_entry_identity,
    repository_inspection_bundle_identity,
    repository_inspection_request_identity,
    repository_inspection_result_hash,
    repository_registration_identity,
    repository_snapshot_identity,
    repository_state_digest,
    untracked_manifest_hash,
    validate_persisted_text,
    validate_repository_relative_path,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
OID_1 = "1" * 40
OID_2 = "2" * 40
DIRTY_HASH = "3aef9eed6519a4ef138a7b12415460b83452fc66923c9a38c3e765c62b22b099"
UNTRACKED_HASH = "7979e0f46411717ba96992784c335b1366eb5f6aa8874e56c0283cb10f6a490b"
STATE_HASH = "655b62a092165453675ed5182c67a8854f162884a2a5ce4bece09a099ec68df8"
SNAPSHOT_HASH = "d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12"
BUNDLE_ENTRIES_HASH = "77b1cf5c0ab3590461dc8b01639aa1e225a80b65b70efa10053cebbb6d9b295f"
EVIDENCE_HASH = "d7f05cbeac16a6202123f775be3e2e29f8c08394ec4ffc1d1aebb235d080b5b8"
EVIDENCE_ENTRIES_HASH = "9dcf8c6647b3d8367e78e5f434d6e450959e221fc7f30cb9cf8d83699fdb9d93"
REGISTRY_HASH = "b79266a491a075c01bf4311b6ac743ab0362314c2bb906eb7d572f2bc7467eff"
LIMITS_HASH = "8e26df5c1ec65da59ee4e6b923ee3a87cd9e35721f58c78c8b41533f194ad28b"
REQUEST_HASH = "4ae56685557d956e8755ef91d56c93d12f2b9576ddb1be57a1182f3f0a45dc3f"


def dirty_entry(content_hash: str = SHA_A) -> RepositoryManifestEntryV1:
    return RepositoryManifestEntryV1(
        "src/train.py",
        None,
        "tracked_overlay",
        "regular",
        "100644",
        OID_1,
        OID_2,
        content_hash,
        12,
        "verified",
    )


def untracked_entry(path: str = "notes.txt") -> RepositoryManifestEntryV1:
    return RepositoryManifestEntryV1(
        path,
        None,
        "untracked_file",
        "regular",
        "100644",
        None,
        None,
        SHA_C,
        5,
        "verified",
    )


def inspectors(version: str = "v1") -> tuple[InspectorIdentity, ...]:
    return (
        InspectorIdentity("file_inventory", "v1"),
        InspectorIdentity("project_manifests", "v1"),
        InspectorIdentity("python_static_signals", version),
    )


def limits(**changes: int) -> InspectionLimitsProfileV1:
    values: dict[str, object] = {
        "limits_profile_id": "wp1-a-default-limits:v1",
        "max_files": 2048,
        "max_file_bytes": 1048576,
        "max_total_bytes": 67108864,
        "max_ast_nodes_per_file": 100000,
        "max_total_ast_nodes": 500000,
        "max_structural_depth": 64,
        "max_candidates_per_collection": 128,
        "max_total_candidates": 512,
        "max_evidence_identities_per_candidate": 16,
        "max_evidence_entries": 4096,
        "max_manifest_entries": 4096,
        "max_unresolved_questions": 128,
        "max_candidate_values_per_question": 16,
        "max_result_canonical_bytes": 4000000,
        "profile_schema_version": 1,
    }
    values.update(changes)
    return InspectionLimitsProfileV1(**values)  # type: ignore[arg-type]


def entrypoint(path: str = "src/train.py") -> CandidateEntrypointV1:
    return CandidateEntrypointV1(path, "python_main_guard", None, (EVIDENCE_HASH,))


def evidence(line: int = 10) -> str:
    return repository_evidence_identity(
        repository_snapshot_identity=SNAPSHOT_HASH,
        repository_path="src/train.py",
        evidence_kind="main_guard",
        line_start=line,
        line_end=line,
        structural_locator=f"python-ast:If:{line}:0",
        symbol_name=None,
        file_mode="100644",
        content_hash=SHA_D,
        excerpt_hash=SHA_E,
        declared_or_inferred="deterministic_inference",
        inspector_identity=InspectorIdentity("python_static_signals", "v1"),
    )


def result(path: str = "src/train.py") -> str:
    return repository_inspection_result_hash(
        inspection_request_identity=REQUEST_HASH,
        result_status="complete",
        candidate_entrypoints=(entrypoint(path),),
        configuration_candidates=(),
        metric_candidates=(),
        artifact_candidates=(),
        dataset_reference_candidates=(),
        environment_candidates=(),
        detected_commands=(),
        unresolved_questions=(),
        completed_inspector_identities=inspectors(),
        result_schema_version=1,
    )


Golden = tuple[str, Callable[[], str], Callable[[], str], str, str]


def golden_cases() -> list[Golden]:
    def registration(root):
        return repository_registration_identity(
            canonical_root_path=root, repository_type="local_git"
        )

    def state(dirty):
        return repository_state_digest(
            head_commit=OID_1,
            tracked_tree_identity=OID_2,
            is_clean=False,
            dirty_worktree_manifest_hash=dirty,
            untracked_manifest_hash=UNTRACKED_HASH,
        )

    def snapshot(registration_id):
        return repository_snapshot_identity(
            repository_registration_id=UUID(registration_id),
            repository_state_digest=STATE_HASH,
            current_branch="main",
            is_detached_head=False,
        )

    def bundle_entry(mode):
        return repository_inspection_bundle_entry_identity(
            repository_path="src/train.py",
            original_repository_path=None,
            entry_kind="tracked_overlay",
            file_type="regular",
            file_mode=mode,
            head_blob_oid=OID_1,
            index_blob_oid=OID_2,
            content_hash=SHA_A,
            size_bytes=12,
            content_identity_status="verified",
        )

    def bundle_items(readme_identity):
        return (
            BundleEntryManifestItemV1(
                "a.py",
                "tracked_overlay",
                "regular",
                "0215a8af8592b5ee90f77eedd61596d5d6a5fca9912fd5fa5ee92a77d95dd7c2",
            ),
            BundleEntryManifestItemV1(
                "README.md",
                "tracked_overlay",
                "regular",
                readme_identity,
            ),
        )

    def bundle(tool):
        return repository_inspection_bundle_identity(
            repository_snapshot_identity=SNAPSHOT_HASH,
            base_commit_sha=OID_1,
            entries_manifest_hash=BUNDLE_ENTRIES_HASH,
            bundle_schema_version=1,
            capture_tool_version=tool,
        )

    def evidence_manifest(result_hash):
        return repository_evidence_manifest_hash(
            repository_snapshot_identity=SNAPSHOT_HASH,
            inspection_result_hash=result_hash,
            entries_manifest_hash=EVIDENCE_ENTRIES_HASH,
            manifest_schema_version=1,
        )

    def registry(version):
        return inspector_registry_identity(
            profile_id="wp1-a-default:v1",
            required_inspectors=inspectors(version),
            optional_inspectors=(),
            registry_schema_version=1,
        )

    def request(profile_hash):
        return repository_inspection_request_identity(
            repository_snapshot_identity=SNAPSHOT_HASH,
            inspector_profile_id="wp1-a-default:v1",
            inspector_registry_identity=REGISTRY_HASH,
            inspection_limits_profile_identity=profile_hash,
            request_schema_version=1,
        )

    return [
        (
            "registration",
            lambda: registration("/srv/labpilot/repos/demo"),
            lambda: registration("/srv/labpilot/repos/demo-moved"),
            "ebccef3f53f569c02fd3d955dc456154297295e851e89ba3d3a28c3789a78ac1",
            "adeb8097b98b1a2d559cde9276677a425bbe5ba13ddb293d0258842ff583eea1",
        ),
        (
            "dirty",
            lambda: dirty_worktree_manifest_hash((dirty_entry(),)),
            lambda: dirty_worktree_manifest_hash((dirty_entry(SHA_B),)),
            DIRTY_HASH,
            "fb63b67a19a9f71b18cf0fabe97a9d41d97f3061bb7fd6c0f8c9647c945ac46d",
        ),
        (
            "untracked",
            lambda: untracked_manifest_hash((untracked_entry(),)),
            lambda: untracked_manifest_hash((untracked_entry("notes-2.txt"),)),
            UNTRACKED_HASH,
            "a6686fc07e3ba6952b3b914efa41c0986bddc7e69abd56c4a51ac7b78a35b7f0",
        ),
        (
            "state",
            lambda: state(DIRTY_HASH),
            lambda: state("fb63b67a19a9f71b18cf0fabe97a9d41d97f3061bb7fd6c0f8c9647c945ac46d"),
            STATE_HASH,
            "977af9c3409e2a7d872e374706e6d751801d6a22086136f89523bdcb36aa2de7",
        ),
        (
            "snapshot",
            lambda: snapshot("11111111-1111-4111-8111-111111111111"),
            lambda: snapshot("22222222-2222-4222-8222-222222222222"),
            SNAPSHOT_HASH,
            "f69bb81b6c65844437735404188a507c6531e0d4bb3bbe34ca73319eec87d37c",
        ),
        (
            "bundle-entry",
            lambda: bundle_entry("100644"),
            lambda: bundle_entry("100755"),
            "b54af3f2dbf1cc74860579e003bab15cbf3eb1f2709cd6c35b94d0ca1e3e03ae",
            "ea61ccb353b0662aea0ca44c0ae917e7d979698017e8f1ff9ae7498b1de5ad1e",
        ),
        (
            "bundle-entries",
            lambda: repository_inspection_bundle_entries_manifest_hash(
                bundle_items("f6b16094e2f7d40ff072ad2fdd6a3987cc30461857bc0aced35600004cea4e18")
            ),
            lambda: repository_inspection_bundle_entries_manifest_hash(
                bundle_items("f3548551019ad27cbc694736db06d2c0a8a70e9b068600c1dcff3a249204698f")
            ),
            BUNDLE_ENTRIES_HASH,
            "f7547041857693baa2c6ed37ddad30386a772a4d5c4619b7d56f08a93c90b5a8",
        ),
        (
            "bundle",
            lambda: bundle("wp1-a-git-observation:v1"),
            lambda: bundle("wp1-a-git-observation:v2"),
            "5af2d5a55f9c76677a94bb18dd2fcc6524524ed42f6d18ec5da681601db5a702",
            "c57ed760eb2352cb2b978a0f972f88c3e1ce2bf87ac9d7967807e6c27282f168",
        ),
        (
            "evidence",
            lambda: evidence(10),
            lambda: evidence(11),
            EVIDENCE_HASH,
            "c417437863d189fff1f88986552d0a371ac04d5decc703f6b0722adf56280144",
        ),
        (
            "evidence-entries",
            lambda: repository_evidence_manifest_entries_hash((EVIDENCE_HASH,)),
            lambda: repository_evidence_manifest_entries_hash((EVIDENCE_HASH, "f" * 64)),
            EVIDENCE_ENTRIES_HASH,
            "ddbc6328b21b53794c7b3ec8ff88a273b5ed57dc0c206102cbb45232e7fc7670",
        ),
        (
            "evidence-manifest",
            lambda: evidence_manifest("9" * 64),
            lambda: evidence_manifest("8" * 64),
            "6d5b500cc15135b38e8a9ac84d7e499723d6dcfed242d64bdc3b6f8189f16af5",
            "5e6842427e78b4daad8665545b52c48844bb66e697ff9f84fb0b524b2b7a4961",
        ),
        (
            "registry",
            lambda: registry("v1"),
            lambda: registry("v2"),
            REGISTRY_HASH,
            "dcc20132a782d04f4e89aca16faacf3378e479a08da18f40a0781eaa1f234e35",
        ),
        (
            "limits",
            lambda: inspection_limits_profile_identity(limits()),
            lambda: inspection_limits_profile_identity(limits(max_file_bytes=2097152)),
            LIMITS_HASH,
            "3d6441ffcbcfbfbb57a6836e54b21f4a194769d0694661807abdc00dcd4d5ff1",
        ),
        (
            "request",
            lambda: request(LIMITS_HASH),
            lambda: request("3d6441ffcbcfbfbb57a6836e54b21f4a194769d0694661807abdc00dcd4d5ff1"),
            REQUEST_HASH,
            "a51cdded8b170f93d1ae41708461021e94e11c0c47966f8cbc8f99f09f33894d",
        ),
        (
            "result",
            lambda: result(),
            lambda: result("src/eval.py"),
            "6534b765e3e9e297859b0853bb6cd5e6bc52ff356579c1c2b1412a1313ef36a0",
            "278a12ac56cb5fa97cb7642fede55034ee4ca2ff14a86073a628e557158402d4",
        ),
    ]


@pytest.mark.parametrize(
    ("name", "positive", "changed", "positive_hash", "changed_hash"),
    golden_cases(),
    ids=[case[0] for case in golden_cases()],
)
def test_all_fifteen_positive_and_changed_golden_hashes(
    name: str,
    positive: Callable[[], str],
    changed: Callable[[], str],
    positive_hash: str,
    changed_hash: str,
) -> None:
    del name
    assert positive() == positive_hash
    assert changed() == changed_hash
    assert positive_hash != changed_hash


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/a",
        "C:/a",
        "//server/a",
        "a\\b",
        "a/",
        "a//b",
        ".",
        "a/./b",
        "..",
        "a/../b",
        "a\0b",
        "a\ud800b",
    ],
)
def test_relative_path_rejects_every_forbidden_form(value: str) -> None:
    with pytest.raises((PersistedTextInvalid, RepositoryIdentityInputInvalid)):
        validate_repository_relative_path(value)


def test_text_and_path_are_returned_without_normalization() -> None:
    path = "é dir/\tname\n.py"
    assert validate_repository_relative_path(path) == path
    command = "python -m train\n\t--epochs 5"
    assert (
        validate_persisted_text(
            command,
            field="command_text",
            max_utf8_bytes=100,
            allow_lf=True,
            allow_tab=True,
            allow_cr=False,
        )
        == command
    )


@pytest.mark.parametrize(
    "value,reason", [("a\0b", "nul"), ("a\ud800b", "isolated_surrogate"), ("a\rb", "cr")]
)
def test_persisted_text_stable_reason_codes(value: str, reason: str) -> None:
    with pytest.raises(PersistedTextInvalid) as captured:
        validate_persisted_text(
            value,
            field="command_text",
            max_utf8_bytes=100,
            allow_lf=True,
            allow_tab=True,
            allow_cr=False,
        )
    assert (captured.value.field, captured.value.reason_code) == ("command_text", reason)


def test_collections_are_canonical_reject_duplicates_and_do_not_mutate_callers() -> None:
    deletion = RepositoryManifestEntryV1(
        "README.md",
        None,
        "deletion",
        "regular",
        "100644",
        OID_1,
        None,
        None,
        0,
        "not_applicable",
    )
    entries = [dirty_entry(), deletion]
    before = list(entries)
    assert dirty_worktree_manifest_hash(entries) == dirty_worktree_manifest_hash(
        tuple(reversed(entries))
    )
    assert entries == before
    with pytest.raises(RepositoryIdentityInputInvalid):
        dirty_worktree_manifest_hash(())
    with pytest.raises(RepositoryIdentityInputInvalid):
        dirty_worktree_manifest_hash((dirty_entry(), dirty_entry()))
    with pytest.raises(RepositoryIdentityInputInvalid):
        dirty_worktree_manifest_hash((untracked_entry(),))
    with pytest.raises(RepositoryIdentityInputInvalid):
        untracked_manifest_hash((dirty_entry(),))


def test_precapture_manifest_allows_unverified_but_bundle_entry_rejects_it() -> None:
    unverified = RepositoryManifestEntryV1(
        "src/train.py",
        None,
        "tracked_overlay",
        "regular",
        "100644",
        OID_1,
        OID_2,
        None,
        0,
        "unverified",
    )
    assert len(dirty_worktree_manifest_hash((unverified,))) == 64
    kwargs = dict(
        repository_path="src/train.py",
        original_repository_path=None,
        entry_kind="tracked_overlay",
        file_type="regular",
        file_mode="100644",
        head_blob_oid=OID_1,
        index_blob_oid=OID_2,
        content_hash=None,
        size_bytes=0,
    )
    with pytest.raises(RepositoryIdentityInputInvalid) as captured:
        repository_inspection_bundle_entry_identity(
            **kwargs,
            content_identity_status="unverified",  # type: ignore[arg-type]
        )
    assert captured.value.reason_code == "bundle_entry_unverified"


def test_state_snapshot_and_bundle_gitlink_cross_field_rules() -> None:
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_state_digest(
            head_commit=OID_1,
            tracked_tree_identity=OID_2,
            is_clean=True,
            dirty_worktree_manifest_hash=DIRTY_HASH,
            untracked_manifest_hash=None,
        )
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_state_digest(
            head_commit=OID_1,
            tracked_tree_identity=OID_2,
            is_clean=False,
            dirty_worktree_manifest_hash=None,
            untracked_manifest_hash=None,
        )
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_snapshot_identity(
            repository_registration_id=UUID("11111111-1111-4111-8111-111111111111"),
            repository_state_digest=STATE_HASH,
            current_branch="main",
            is_detached_head=True,
        )
    gitlink = dict(
        repository_path="vendor/lib",
        original_repository_path=None,
        entry_kind="tracked_overlay",
        file_type="submodule",
        file_mode="160000",
        head_blob_oid=OID_1,
        index_blob_oid=OID_2,
        content_hash=None,
        size_bytes=0,
        content_identity_status="not_applicable",
    )
    assert len(repository_inspection_bundle_entry_identity(**gitlink)) == 64  # type: ignore[arg-type]
    for key, value in (("file_mode", "100644"), ("content_hash", SHA_A), ("size_bytes", 1)):
        invalid = gitlink | {key: value}
        with pytest.raises(RepositoryIdentityInputInvalid):
            repository_inspection_bundle_entry_identity(**invalid)  # type: ignore[arg-type]


def test_bundle_entry_manifest_uses_path_order_and_rejects_duplicate_identity() -> None:
    first = BundleEntryManifestItemV1("a.py", "tracked_overlay", "regular", "0" * 64)
    second = BundleEntryManifestItemV1("README.md", "tracked_overlay", "regular", "f" * 64)
    caller = [first, second]
    assert repository_inspection_bundle_entries_manifest_hash(
        caller
    ) == repository_inspection_bundle_entries_manifest_hash(tuple(reversed(caller)))
    assert caller == [first, second]
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_inspection_bundle_entries_manifest_hash(
            (first, BundleEntryManifestItemV1("b.py", "tracked_overlay", "regular", "0" * 64))
        )


def test_gitlink_evidence_uses_domain_separated_metadata_hash() -> None:
    content_hash = sha256(b"gitlink\0" + OID_1.encode("ascii")).hexdigest()
    kwargs = dict(
        repository_snapshot_identity=SNAPSHOT_HASH,
        repository_path="vendor/lib",
        evidence_kind="file_presence",
        line_start=None,
        line_end=None,
        structural_locator=f"gitlink:{OID_1}",
        symbol_name=None,
        file_mode="160000",
        content_hash=content_hash,
        excerpt_hash=None,
        declared_or_inferred="declared",
        inspector_identity=InspectorIdentity("file_inventory", "v1"),
    )
    assert len(repository_evidence_identity(**kwargs)) == 64  # type: ignore[arg-type]
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_evidence_identity(**(kwargs | {"content_hash": SHA_A}))  # type: ignore[arg-type]


def test_registry_categories_order_and_duplicate_rules() -> None:
    required = list(inspectors())
    before = list(required)
    assert inspector_registry_identity(
        profile_id="wp1-a-default:v1",
        required_inspectors=required,
        optional_inspectors=(),
        registry_schema_version=1,
    ) == inspector_registry_identity(
        profile_id="wp1-a-default:v1",
        required_inspectors=tuple(reversed(required)),
        optional_inspectors=(),
        registry_schema_version=1,
    )
    assert required == before
    with pytest.raises(RepositoryIdentityInputInvalid):
        inspector_registry_identity(
            profile_id="wp1-a-default:v1",
            required_inspectors=(required[0], required[0]),
            optional_inspectors=(),
            registry_schema_version=1,
        )
    with pytest.raises(RepositoryIdentityInputInvalid):
        inspector_registry_identity(
            profile_id="wp1-a-default:v1",
            required_inspectors=(required[0],),
            optional_inspectors=(required[0],),
            registry_schema_version=1,
        )


def test_limits_profile_enforces_node_count_and_byte_budgets() -> None:
    assert inspection_limits_profile_identity(limits()) == LIMITS_HASH
    with pytest.raises(InspectionResourceLimitExceeded) as captured:
        inspection_limits_profile_identity(limits(max_unresolved_questions=10000))
    assert captured.value.reason_code == "result_node_budget"
    with pytest.raises(InspectionResourceLimitExceeded):
        inspection_limits_profile_identity(limits(max_result_canonical_bytes=8000001))


def test_all_candidate_value_types_validate_real_values() -> None:
    e = (EVIDENCE_HASH,)
    assert ConfigurationCandidateV1("pyproject.toml", "pyproject", None, e)
    assert MetricCandidateV1("src/train.py", None, "loss", "ast:Call:1", e)
    assert ArtifactCandidateV1("src/train.py", None, "out.bin", "ast:Call:2", e)
    assert DatasetReferenceCandidateV1("src/train.py", "cli_option", "--data", "ast:Call:3", e)
    assert EnvironmentCandidateV1("pyproject.toml", "python_project", None, e)
    assert DetectedCommandV1("README.md", "readme_code_block", "python train.py", "fence:1", e)
    assert UnresolvedCandidateValueV1("repository_path", "src/train.py")


def test_typed_unresolved_values_and_complete_ambiguous_rules() -> None:
    question = UnresolvedQuestionV1(
        "multiple_entrypoint_candidates",
        None,
        None,
        (
            UnresolvedCandidateValueV1("repository_path", "src/z.py"),
            UnresolvedCandidateValueV1("repository_path", "src/a.py"),
        ),
        (EVIDENCE_HASH,),
    )
    candidates = [entrypoint("src/z.py"), entrypoint("src/a.py")]
    before = list(candidates)
    assert (
        len(
            repository_inspection_result_hash(
                inspection_request_identity=REQUEST_HASH,
                result_status="ambiguous",
                candidate_entrypoints=candidates,
                configuration_candidates=(),
                metric_candidates=(),
                artifact_candidates=(),
                dataset_reference_candidates=(),
                environment_candidates=(),
                detected_commands=(),
                unresolved_questions=(question,),
                completed_inspector_identities=inspectors(),
                result_schema_version=1,
            )
        )
        == 64
    )
    assert candidates == before
    for status, questions in (("complete", (question,)), ("ambiguous", ())):
        with pytest.raises(RepositoryIdentityInputInvalid):
            repository_inspection_result_hash(
                inspection_request_identity=REQUEST_HASH,
                result_status=status,
                candidate_entrypoints=(),
                configuration_candidates=(),
                metric_candidates=(),
                artifact_candidates=(),
                dataset_reference_candidates=(),
                environment_candidates=(),
                detected_commands=(),
                unresolved_questions=questions,
                completed_inspector_identities=inspectors(),
                result_schema_version=1,
            )


def test_result_rejects_duplicate_semantic_keys_and_profile_count() -> None:
    duplicate = entrypoint()
    common = dict(
        inspection_request_identity=REQUEST_HASH,
        result_status="complete",
        configuration_candidates=(),
        metric_candidates=(),
        artifact_candidates=(),
        dataset_reference_candidates=(),
        environment_candidates=(),
        detected_commands=(),
        unresolved_questions=(),
        completed_inspector_identities=inspectors(),
        result_schema_version=1,
    )
    with pytest.raises(RepositoryIdentityInputInvalid):
        repository_inspection_result_hash(candidate_entrypoints=(duplicate, duplicate), **common)  # type: ignore[arg-type]
    too_many = tuple(entrypoint(f"src/{index:03}.py") for index in range(129))
    with pytest.raises(InspectionResourceLimitExceeded) as captured:
        repository_inspection_result_hash(candidate_entrypoints=too_many, **common)  # type: ignore[arg-type]
    assert captured.value.reason_code == "max_candidates_per_collection"


def test_excluded_mutable_fields_do_not_change_identity() -> None:
    source = {
        "canonical_root_path": "/srv/labpilot/repos/demo",
        "repository_type": "local_git",
        "project_id": "a",
        "captured_at": "first",
    }

    def identity() -> str:
        return repository_registration_identity(
            canonical_root_path=source["canonical_root_path"],
            repository_type=source["repository_type"],  # type: ignore[arg-type]
        )

    expected = identity()
    source.update(project_id="b", captured_at="second")
    assert identity() == expected
