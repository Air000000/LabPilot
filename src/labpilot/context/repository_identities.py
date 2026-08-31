import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import UUID

from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalValue,
    canonical_json_bytes,
)
from labpilot.common.hashing import namespaced_sha256, namespaced_sha256_bytes

type Sha256Hex = str
type GitObjectOid = str
type FileMode = Literal["100644", "100755", "120000", "160000"]
type RepositoryType = Literal["local_git"]
type EntryKind = Literal["tracked_overlay", "untracked_file", "deletion", "mode_change"]
type FileType = Literal["regular", "symlink", "submodule"]
type ContentIdentityStatus = Literal["verified", "unverified", "not_applicable"]
type InspectorResultStatus = Literal["complete", "partial", "ambiguous", "failed"]

_SHA256 = re.compile(r"[0-9a-f]{64}")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_INSPECTOR_NAME = re.compile(r"[a-z][a-z0-9_]{0,99}")
_INSPECTOR_VERSION = re.compile(r"v[1-9][0-9]*(?:[._-][A-Za-z0-9]+)*")
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*:v[1-9][0-9]*")
_WINDOWS_ABSOLUTE = re.compile(r"[A-Za-z]:/")


class PersistedTextInvalid(ValueError):
    __slots__ = ("field", "reason_code")

    def __init__(self, field: str, reason_code: str) -> None:
        self.field = field
        self.reason_code = reason_code
        super().__init__(reason_code)


class RepositoryIdentityInputInvalid(ValueError):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class InspectionResourceLimitExceeded(ValueError):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def validate_persisted_text(
    value: str,
    *,
    field: str,
    max_utf8_bytes: int,
    allow_empty: bool = False,
    allow_lf: bool,
    allow_tab: bool,
    allow_cr: bool,
) -> str:
    if type(value) is not str:
        raise RepositoryIdentityInputInvalid(f"{field}_type")
    if not value and not allow_empty:
        raise PersistedTextInvalid(field, "empty")
    if "\0" in value:
        raise PersistedTextInvalid(field, "nul")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PersistedTextInvalid(field, "isolated_surrogate") from exc
    if len(encoded) > max_utf8_bytes:
        raise PersistedTextInvalid(field, "utf8_limit")
    if not allow_lf and "\n" in value:
        raise PersistedTextInvalid(field, "lf")
    if not allow_tab and "\t" in value:
        raise PersistedTextInvalid(field, "tab")
    if not allow_cr and "\r" in value:
        raise PersistedTextInvalid(field, "cr")
    return value


def validate_repository_relative_path(value: str) -> str:
    value = validate_persisted_text(
        value,
        field="relative_path",
        max_utf8_bytes=1_000_000,
        allow_lf=True,
        allow_tab=True,
        allow_cr=True,
    )
    if value.startswith("/") or _WINDOWS_ABSOLUTE.match(value):
        raise RepositoryIdentityInputInvalid("absolute_path")
    if "\\" in value:
        raise RepositoryIdentityInputInvalid("backslash")
    if value.endswith("/"):
        raise RepositoryIdentityInputInvalid("trailing_slash")
    if any(component in {"", ".", ".."} for component in value.split("/")):
        raise RepositoryIdentityInputInvalid("unsafe_path_component")
    return value


def _persisted(
    value: str,
    field: str,
    limit: int,
    *,
    lf: bool = False,
    tab: bool = False,
    cr: bool = False,
) -> str:
    return validate_persisted_text(
        value,
        field=field,
        max_utf8_bytes=limit,
        allow_lf=lf,
        allow_tab=tab,
        allow_cr=cr,
    )


def _manifest_relative(value: str, field: str = "repository_path") -> str:
    try:
        return validate_repository_relative_path(value)
    except PersistedTextInvalid as exc:
        if exc.field == "relative_path" and field != "relative_path":
            raise PersistedTextInvalid(field, exc.reason_code) from exc
        raise


def _relative(value: str, field: str = "repository_path") -> str:
    validated = _manifest_relative(value, field)
    if len(validated.encode("utf-8")) > 4096:
        raise PersistedTextInvalid(field, "utf8_limit")
    return validated


def _sha(value: str, reason: str = "invalid_sha256") -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise RepositoryIdentityInputInvalid(reason)
    return value


def _optional_sha(value: str | None) -> str | None:
    return None if value is None else _sha(value)


def _oid(value: str) -> str:
    if type(value) is not str or _OID.fullmatch(value) is None:
        raise RepositoryIdentityInputInvalid("invalid_git_oid")
    return value


def _optional_oid(value: str | None) -> str | None:
    return None if value is None else _oid(value)


def _exact_int(value: int, reason: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RepositoryIdentityInputInvalid(reason)
    return value


def _version(value: int, reason: str) -> int:
    if type(value) is not int or value != 1:
        raise RepositoryIdentityInputInvalid(reason)
    return value


def _enum(value: str, allowed: set[str], reason: str) -> str:
    if type(value) is not str or value not in allowed:
        raise RepositoryIdentityInputInvalid(reason)
    return value


def _hash(namespace: str, payload: object) -> str:
    return namespaced_sha256(namespace, cast(CanonicalValue, payload))


def _mode_type(file_mode: str, file_type: str) -> None:
    allowed = {
        "regular": {"100644", "100755"},
        "symlink": {"120000"},
        "submodule": {"160000"},
    }
    if file_type not in allowed or file_mode not in allowed[file_type]:
        raise RepositoryIdentityInputInvalid("file_mode_type_mismatch")


@dataclass(frozen=True, slots=True)
class RepositoryManifestEntryV1:
    relative_path: str
    original_relative_path: str | None
    entry_kind: EntryKind
    file_type: FileType
    file_mode: FileMode
    head_blob_oid: GitObjectOid | None
    index_blob_oid: GitObjectOid | None
    content_hash: Sha256Hex | None
    size_bytes: int
    content_identity_status: ContentIdentityStatus

    def __post_init__(self) -> None:
        _validate_entry(
            self.relative_path,
            self.original_relative_path,
            self.entry_kind,
            self.file_type,
            self.file_mode,
            self.head_blob_oid,
            self.index_blob_oid,
            self.content_hash,
            self.size_bytes,
            self.content_identity_status,
            bundle=False,
        )


def _validate_entry(
    path: str,
    original_path: str | None,
    kind: str,
    file_type: str,
    mode: str,
    head_oid: str | None,
    index_oid: str | None,
    content_hash: str | None,
    size: int,
    status: str,
    *,
    bundle: bool,
) -> None:
    _manifest_relative(path, "repository_path" if bundle else "relative_path")
    if original_path is not None:
        _manifest_relative(
            original_path,
            "original_repository_path" if bundle else "original_relative_path",
        )
    _enum(kind, {"tracked_overlay", "untracked_file", "deletion", "mode_change"}, "entry_kind")
    _enum(file_type, {"regular", "symlink", "submodule"}, "file_type")
    _mode_type(mode, file_type)
    _optional_oid(head_oid)
    _optional_oid(index_oid)
    _optional_sha(content_hash)
    _exact_int(size, "size_bytes")
    _enum(status, {"verified", "unverified", "not_applicable"}, "content_identity_status")
    if original_path is not None and kind != "tracked_overlay":
        raise RepositoryIdentityInputInvalid("original_path_kind")
    if kind == "untracked_file" and any(
        value is not None for value in (original_path, head_oid, index_oid)
    ):
        raise RepositoryIdentityInputInvalid("untracked_metadata")
    if kind == "deletion" and (
        original_path is not None
        or content_hash is not None
        or status != "not_applicable"
        or size != 0
        or (head_oid is None and index_oid is None)
    ):
        raise RepositoryIdentityInputInvalid("deletion_fields")
    if kind == "mode_change" and (
        original_path is not None
        or file_type != "regular"
        or content_hash is not None
        or status != "not_applicable"
        or size != 0
        or head_oid is None
        or head_oid != index_oid
    ):
        raise RepositoryIdentityInputInvalid("mode_change_fields")
    if file_type == "submodule" and (
        kind not in {"tracked_overlay", "deletion"}
        or content_hash is not None
        or status != "not_applicable"
        or size != 0
        or (head_oid is None and index_oid is None)
    ):
        raise RepositoryIdentityInputInvalid("submodule_fields")
    if status == "verified" and (file_type not in {"regular", "symlink"} or content_hash is None):
        raise RepositoryIdentityInputInvalid("verified_content")
    if status == "unverified" and content_hash is not None:
        raise RepositoryIdentityInputInvalid("unverified_content_hash")
    if status == "not_applicable" and content_hash is not None:
        raise RepositoryIdentityInputInvalid("not_applicable_content_hash")
    if bundle and status == "unverified":
        raise RepositoryIdentityInputInvalid("bundle_entry_unverified")
    if kind in {"tracked_overlay", "untracked_file"} and file_type != "submodule":
        if status == "not_applicable":
            raise RepositoryIdentityInputInvalid("captured_content_required")
        if bundle and status != "verified":
            raise RepositoryIdentityInputInvalid("bundle_entry_unverified")


def _entry_object(entry: RepositoryManifestEntryV1) -> dict[str, object]:
    return {
        "relative_path": entry.relative_path,
        "original_relative_path": entry.original_relative_path,
        "entry_kind": entry.entry_kind,
        "file_type": entry.file_type,
        "file_mode": entry.file_mode,
        "head_blob_oid": entry.head_blob_oid,
        "index_blob_oid": entry.index_blob_oid,
        "content_hash": entry.content_hash,
        "size_bytes": entry.size_bytes,
        "content_identity_status": entry.content_identity_status,
    }


@dataclass(frozen=True, slots=True)
class InspectorIdentity:
    inspector_name: str
    inspector_version: str

    def __post_init__(self) -> None:
        name = _persisted(self.inspector_name, "inspector_name", 100)
        version = _persisted(self.inspector_version, "inspector_version", 100)
        if _INSPECTOR_NAME.fullmatch(name) is None:
            raise RepositoryIdentityInputInvalid("inspector_name")
        if _INSPECTOR_VERSION.fullmatch(version) is None:
            raise RepositoryIdentityInputInvalid("inspector_version")


def _inspector_object(value: InspectorIdentity) -> dict[str, str]:
    if not isinstance(value, InspectorIdentity):
        raise RepositoryIdentityInputInvalid("inspector_identity_type")
    return {
        "inspector_name": value.inspector_name,
        "inspector_version": value.inspector_version,
    }


def _evidence_set(values: Sequence[str], *, required: bool = True) -> list[str]:
    copied = [_sha(value, "evidence_identity") for value in values]
    if required and not copied:
        raise RepositoryIdentityInputInvalid("evidence_identities_empty")
    if len(copied) > 16:
        raise InspectionResourceLimitExceeded("max_evidence_identities_per_candidate")
    if len(set(copied)) != len(copied):
        raise RepositoryIdentityInputInvalid("duplicate_evidence_identity")
    return sorted(copied, key=str.encode)


type EntrypointKind = Literal["python_main_guard", "python_callable"]
type ConfigurationKind = Literal[
    "pyproject",
    "requirements",
    "conda_environment",
    "setup_py",
    "setup_cfg",
    "dockerfile",
    "compose",
    "yaml",
    "json",
    "toml",
]
type EnvironmentKind = Literal[
    "python_project",
    "python_requirements",
    "conda_environment",
    "container_image",
    "compose_services",
]
type CommandSource = Literal[
    "pyproject_script",
    "packaging_entry_point",
    "dockerfile_instruction",
    "compose_command",
    "readme_code_block",
    "ci_workflow",
    "shell_script",
    "makefile",
    "taskfile",
]
type DatasetReferenceKind = Literal["cli_option", "manifest_value"]
type UnresolvedCandidateValueKind = Literal["repository_path", "source_literal", "identifier"]


@dataclass(frozen=True, slots=True)
class CandidateEntrypointV1:
    repository_path: str
    entrypoint_kind: EntrypointKind
    symbol_name: str | None
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _enum(self.entrypoint_kind, {"python_main_guard", "python_callable"}, "entrypoint_kind")
        if self.symbol_name is not None:
            _persisted(self.symbol_name, "symbol_name", 8192)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class ConfigurationCandidateV1:
    repository_path: str
    configuration_kind: ConfigurationKind
    structural_locator: str | None
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _enum(
            self.configuration_kind,
            {
                "pyproject",
                "requirements",
                "conda_environment",
                "setup_py",
                "setup_cfg",
                "dockerfile",
                "compose",
                "yaml",
                "json",
                "toml",
            },
            "configuration_kind",
        )
        _optional_locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class MetricCandidateV1:
    repository_path: str
    symbol_name: str | None
    metric_name: str | None
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _optional_plain(self.symbol_name, "symbol_name")
        _optional_plain(self.metric_name, "metric_name")
        _locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class ArtifactCandidateV1:
    repository_path: str
    symbol_name: str | None
    artifact_path_literal: str | None
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _optional_plain(self.symbol_name, "symbol_name")
        if self.artifact_path_literal is not None:
            _persisted(
                self.artifact_path_literal,
                "artifact_path_literal",
                8192,
                lf=True,
                tab=True,
                cr=True,
            )
        _locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class DatasetReferenceCandidateV1:
    repository_path: str
    reference_kind: DatasetReferenceKind
    reference_value: str
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _enum(self.reference_kind, {"cli_option", "manifest_value"}, "reference_kind")
        _persisted(self.reference_value, "reference_value", 8192)
        _locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class EnvironmentCandidateV1:
    repository_path: str
    environment_kind: EnvironmentKind
    structural_locator: str | None
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _enum(
            self.environment_kind,
            {
                "python_project",
                "python_requirements",
                "conda_environment",
                "container_image",
                "compose_services",
            },
            "environment_kind",
        )
        _optional_locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class DetectedCommandV1:
    repository_path: str
    command_source: CommandSource
    command_text: str
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _relative(self.repository_path)
        _enum(
            self.command_source,
            {
                "pyproject_script",
                "packaging_entry_point",
                "dockerfile_instruction",
                "compose_command",
                "readme_code_block",
                "ci_workflow",
                "shell_script",
                "makefile",
                "taskfile",
            },
            "command_source",
        )
        _persisted(self.command_text, "command_text", 8192, lf=True, tab=True)
        _locator(self.structural_locator)
        _evidence_set(self.evidence_identities)


def _optional_plain(value: str | None, field: str) -> None:
    if value is not None:
        _persisted(value, field, 8192)


def _locator(value: str) -> str:
    return _persisted(value, "structural_locator", 500)


def _optional_locator(value: str | None) -> None:
    if value is not None:
        _locator(value)


type QuestionCode = Literal[
    "manifest_parse_error",
    "entrypoint_role_ambiguous",
    "multiple_entrypoint_candidates",
    "configuration_source_ambiguous",
    "metric_name_dynamic",
    "artifact_path_dynamic",
    "dataset_reference_dynamic",
    "environment_source_ambiguous",
    "command_parse_ambiguous",
    "unsupported_static_construct",
    "unsupported_source_dependency",
]


@dataclass(frozen=True, slots=True)
class UnresolvedCandidateValueV1:
    value_kind: UnresolvedCandidateValueKind
    value: str

    def __post_init__(self) -> None:
        _enum(self.value_kind, {"repository_path", "source_literal", "identifier"}, "value_kind")
        if self.value_kind == "repository_path":
            _relative(self.value, "value")
        elif self.value_kind == "source_literal":
            _persisted(self.value, "value", 8192, lf=True, tab=True)
        else:
            _persisted(self.value, "value", 8192)


_QUESTION_KINDS: dict[str, tuple[set[str], int]] = {
    "manifest_parse_error": ({"source_literal"}, 0),
    "entrypoint_role_ambiguous": ({"repository_path", "identifier"}, 1),
    "multiple_entrypoint_candidates": ({"repository_path"}, 2),
    "configuration_source_ambiguous": ({"repository_path"}, 2),
    "metric_name_dynamic": ({"source_literal", "identifier"}, 0),
    "artifact_path_dynamic": ({"source_literal"}, 0),
    "dataset_reference_dynamic": ({"source_literal"}, 0),
    "environment_source_ambiguous": ({"repository_path"}, 2),
    "command_parse_ambiguous": ({"source_literal"}, 0),
    "unsupported_static_construct": ({"source_literal", "identifier"}, 0),
    "unsupported_source_dependency": ({"repository_path", "source_literal"}, 1),
}


@dataclass(frozen=True, slots=True)
class UnresolvedQuestionV1:
    question_code: QuestionCode
    repository_path: str | None
    structural_locator: str | None
    candidate_values: tuple[UnresolvedCandidateValueV1, ...]
    evidence_identities: tuple[Sha256Hex, ...]

    def __post_init__(self) -> None:
        _enum(self.question_code, set(_QUESTION_KINDS), "question_code")
        if self.repository_path is not None:
            _relative(self.repository_path)
        _optional_locator(self.structural_locator)
        if len(self.candidate_values) > 16:
            raise InspectionResourceLimitExceeded("max_candidate_values_per_question")
        allowed, minimum = _QUESTION_KINDS[self.question_code]
        if len(self.candidate_values) < minimum:
            raise RepositoryIdentityInputInvalid("candidate_value_cardinality")
        keys = [(value.value_kind, value.value) for value in self.candidate_values]
        if any(key[0] not in allowed for key in keys):
            raise RepositoryIdentityInputInvalid("candidate_value_kind")
        if len(set(keys)) != len(keys):
            raise RepositoryIdentityInputInvalid("duplicate_candidate_value")
        if self.question_code == "unsupported_source_dependency" and (
            self.repository_path is None or ("repository_path", self.repository_path) not in keys
        ):
            raise RepositoryIdentityInputInvalid("unsupported_source_dependency_path")
        _evidence_set(self.evidence_identities)


@dataclass(frozen=True, slots=True)
class BundleEntryManifestItemV1:
    repository_path: str
    entry_kind: EntryKind
    file_type: FileType
    entry_identity: Sha256Hex

    def __post_init__(self) -> None:
        _manifest_relative(self.repository_path)
        _enum(
            self.entry_kind,
            {"tracked_overlay", "untracked_file", "deletion", "mode_change"},
            "entry_kind",
        )
        _enum(self.file_type, {"regular", "symlink", "submodule"}, "file_type")
        _sha(self.entry_identity, "entry_identity")


@dataclass(frozen=True, slots=True)
class InspectionLimitsProfileV1:
    limits_profile_id: str
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_ast_nodes_per_file: int
    max_total_ast_nodes: int
    max_structural_depth: int
    max_candidates_per_collection: int
    max_total_candidates: int
    max_evidence_identities_per_candidate: int
    max_evidence_entries: int
    max_manifest_entries: int
    max_unresolved_questions: int
    max_candidate_values_per_question: int
    max_result_canonical_bytes: int
    profile_schema_version: Literal[1] = 1

    def __post_init__(self) -> None:
        _profile(self.limits_profile_id, "limits_profile_id")
        for field in fields(self):
            if field.name != "limits_profile_id":
                _exact_int(getattr(self, field.name), field.name, minimum=1)
        _version(self.profile_schema_version, "profile_schema_version")
        checks = (
            self.max_file_bytes <= self.max_total_bytes,
            self.max_ast_nodes_per_file <= self.max_total_ast_nodes,
            self.max_files <= self.max_evidence_entries <= self.max_manifest_entries <= 10000,
            self.max_candidates_per_collection <= 10000,
            self.max_total_candidates <= 8 * self.max_candidates_per_collection,
            self.max_evidence_identities_per_candidate <= 10000,
            self.max_unresolved_questions <= 10000,
            self.max_candidate_values_per_question <= 10000,
        )
        if not all(checks):
            raise InspectionResourceLimitExceeded("profile_static_invariant")
        if self.max_result_canonical_bytes > 8_000_000:
            raise InspectionResourceLimitExceeded("result_byte_budget")
        if self.result_node_upper_bound > 90_000:
            raise InspectionResourceLimitExceeded("result_node_budget")

    @property
    def result_node_upper_bound(self) -> int:
        return (
            32
            + self.max_total_candidates * (8 + self.max_evidence_identities_per_candidate)
            + self.max_unresolved_questions
            * (
                8
                + 3 * self.max_candidate_values_per_question
                + self.max_evidence_identities_per_candidate
            )
        )


def _profile(value: str, field: str) -> str:
    value = _persisted(value, field, 100)
    if _PROFILE_ID.fullmatch(value) is None:
        raise RepositoryIdentityInputInvalid(field)
    return value


_DEFAULT_LIMITS = InspectionLimitsProfileV1(
    "wp1-a-default-limits:v1",
    2048,
    1048576,
    67108864,
    100000,
    500000,
    64,
    128,
    512,
    16,
    4096,
    4096,
    128,
    16,
    4000000,
)


def repository_registration_identity(
    *, canonical_root_path: str, repository_type: RepositoryType
) -> Sha256Hex:
    root = _persisted(
        canonical_root_path, "canonical_root_path", 1_000_000, lf=True, tab=True, cr=True
    )
    if not root.startswith("/"):
        raise RepositoryIdentityInputInvalid("canonical_root_path_absolute")
    _enum(repository_type, {"local_git"}, "repository_type")
    return _hash(
        "labpilot-repository-registration:v1",
        {"canonical_root_path": root, "repository_type": repository_type},
    )


def _manifest_payload(
    entries: Sequence[RepositoryManifestEntryV1], *, dirty: bool, version: int
) -> dict[str, object]:
    _version(version, "manifest_schema_version")
    copied = list(entries)
    if not copied:
        raise RepositoryIdentityInputInvalid("manifest_empty")
    if any(not isinstance(entry, RepositoryManifestEntryV1) for entry in copied):
        raise RepositoryIdentityInputInvalid("manifest_entry_type")
    allowed = {"tracked_overlay", "deletion", "mode_change"} if dirty else {"untracked_file"}
    if any(entry.entry_kind not in allowed for entry in copied):
        raise RepositoryIdentityInputInvalid("manifest_entry_kind")
    if not dirty and any(entry.file_type == "submodule" for entry in copied):
        raise RepositoryIdentityInputInvalid("untracked_submodule")
    paths = [entry.relative_path for entry in copied]
    if len(set(paths)) != len(paths):
        raise RepositoryIdentityInputInvalid("duplicate_repository_path")
    ordered = sorted(
        copied,
        key=lambda entry: (
            entry.relative_path.encode(),
            entry.entry_kind.encode(),
            entry.file_type.encode(),
        ),
    )
    return {
        "entries": [_entry_object(entry) for entry in ordered],
        "manifest_schema_version": version,
    }


def _manifest_hash(
    entries: Sequence[RepositoryManifestEntryV1], *, dirty: bool, version: int
) -> str:
    namespace = "labpilot-dirty-worktree-manifest:v1" if dirty else "labpilot-untracked-manifest:v1"
    return _hash(namespace, _manifest_payload(entries, dirty=dirty, version=version))


def repository_manifest_canonical_bytes(
    entries: Sequence[RepositoryManifestEntryV1],
    *,
    kind: Literal["dirty", "untracked"],
    manifest_schema_version: Literal[1] = 1,
) -> bytes:
    return canonical_json_bytes(
        cast(
            CanonicalValue,
            _manifest_payload(
                entries,
                dirty=kind == "dirty",
                version=manifest_schema_version,
            ),
        )
    )


def dirty_worktree_manifest_hash(
    entries: Sequence[RepositoryManifestEntryV1], *, manifest_schema_version: Literal[1] = 1
) -> Sha256Hex:
    return _manifest_hash(entries, dirty=True, version=manifest_schema_version)


def untracked_manifest_hash(
    entries: Sequence[RepositoryManifestEntryV1], *, manifest_schema_version: Literal[1] = 1
) -> Sha256Hex:
    return _manifest_hash(entries, dirty=False, version=manifest_schema_version)


def repository_state_digest(
    *,
    head_commit: GitObjectOid,
    tracked_tree_identity: GitObjectOid,
    is_clean: bool,
    dirty_worktree_manifest_hash: Sha256Hex | None,
    untracked_manifest_hash: Sha256Hex | None,
) -> Sha256Hex:
    _oid(head_commit)
    _oid(tracked_tree_identity)
    if type(is_clean) is not bool:
        raise RepositoryIdentityInputInvalid("is_clean")
    _optional_sha(dirty_worktree_manifest_hash)
    _optional_sha(untracked_manifest_hash)
    if is_clean != (dirty_worktree_manifest_hash is None and untracked_manifest_hash is None):
        raise RepositoryIdentityInputInvalid("clean_manifest_consistency")
    return _hash(
        "labpilot-repository-state:v1",
        {
            "head_commit": head_commit,
            "tracked_tree_identity": tracked_tree_identity,
            "is_clean": is_clean,
            "dirty_worktree_manifest_hash": dirty_worktree_manifest_hash,
            "untracked_manifest_hash": untracked_manifest_hash,
        },
    )


def repository_snapshot_identity(
    *,
    repository_registration_id: UUID,
    repository_state_digest: Sha256Hex,
    current_branch: str | None,
    is_detached_head: bool,
) -> Sha256Hex:
    if type(repository_registration_id) is not UUID:
        raise RepositoryIdentityInputInvalid("repository_registration_id")
    _sha(repository_state_digest)
    if type(is_detached_head) is not bool:
        raise RepositoryIdentityInputInvalid("is_detached_head")
    if current_branch is not None:
        _persisted(current_branch, "current_branch", 300)
    if is_detached_head != (current_branch is None):
        raise RepositoryIdentityInputInvalid("detached_branch_consistency")
    return _hash(
        "labpilot-repository-snapshot:v2",
        {
            "repository_registration_id": str(repository_registration_id),
            "repository_state_digest": repository_state_digest,
            "current_branch": current_branch,
            "is_detached_head": is_detached_head,
        },
    )


def repository_inspection_bundle_entry_identity(
    *,
    repository_path: str,
    original_repository_path: str | None,
    entry_kind: EntryKind,
    file_type: FileType,
    file_mode: FileMode,
    head_blob_oid: GitObjectOid | None,
    index_blob_oid: GitObjectOid | None,
    content_hash: Sha256Hex | None,
    size_bytes: int,
    content_identity_status: Literal["verified", "not_applicable"],
    entry_schema_version: Literal[1] = 1,
) -> Sha256Hex:
    _version(entry_schema_version, "entry_schema_version")
    _validate_entry(
        repository_path,
        original_repository_path,
        entry_kind,
        file_type,
        file_mode,
        head_blob_oid,
        index_blob_oid,
        content_hash,
        size_bytes,
        content_identity_status,
        bundle=True,
    )
    return _hash(
        "labpilot-repository-inspection-bundle-entry:v1",
        {
            "repository_path": repository_path,
            "original_repository_path": original_repository_path,
            "entry_kind": entry_kind,
            "file_type": file_type,
            "file_mode": file_mode,
            "head_blob_oid": head_blob_oid,
            "index_blob_oid": index_blob_oid,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "content_identity_status": content_identity_status,
            "entry_schema_version": entry_schema_version,
        },
    )


def repository_inspection_bundle_entries_manifest_hash(
    entries: Sequence[BundleEntryManifestItemV1],
) -> Sha256Hex:
    copied = list(entries)
    if not copied:
        raise RepositoryIdentityInputInvalid("bundle_entries_empty")
    if any(not isinstance(item, BundleEntryManifestItemV1) for item in copied):
        raise RepositoryIdentityInputInvalid("bundle_entry_manifest_item_type")
    if len({item.repository_path for item in copied}) != len(copied):
        raise RepositoryIdentityInputInvalid("duplicate_repository_path")
    if len({item.entry_identity for item in copied}) != len(copied):
        raise RepositoryIdentityInputInvalid("duplicate_entry_identity")
    ordered = sorted(
        copied,
        key=lambda item: (
            item.repository_path.encode(),
            item.entry_kind.encode(),
            item.file_type.encode(),
        ),
    )
    return _hash(
        "labpilot-repository-inspection-bundle-entries:v1",
        {"ordered_entry_identities": [item.entry_identity for item in ordered]},
    )


def repository_inspection_bundle_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    base_commit_sha: GitObjectOid,
    entries_manifest_hash: Sha256Hex,
    bundle_schema_version: int,
    capture_tool_version: str,
) -> Sha256Hex:
    _sha(repository_snapshot_identity)
    _oid(base_commit_sha)
    _sha(entries_manifest_hash)
    _version(bundle_schema_version, "bundle_schema_version")
    capture_tool_version = _persisted(capture_tool_version, "capture_tool_version", 100)
    try:
        capture_tool_version.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RepositoryIdentityInputInvalid("capture_tool_version_ascii") from exc
    return _hash(
        "labpilot-repository-inspection-bundle:v1",
        {
            "repository_snapshot_identity": repository_snapshot_identity,
            "base_commit_sha": base_commit_sha,
            "entries_manifest_hash": entries_manifest_hash,
            "bundle_schema_version": bundle_schema_version,
            "capture_tool_version": capture_tool_version,
        },
    )


_EVIDENCE_KINDS = {
    "file_presence",
    "manifest_declaration",
    "cli_argument",
    "function_definition",
    "main_guard",
    "config_key",
    "command_declaration",
    "metric_logging",
    "artifact_write",
    "readme_example",
    "comment_text",
}


def repository_evidence_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    repository_path: str,
    evidence_kind: str,
    line_start: int | None,
    line_end: int | None,
    structural_locator: str | None,
    symbol_name: str | None,
    file_mode: FileMode,
    content_hash: Sha256Hex,
    excerpt_hash: Sha256Hex | None,
    declared_or_inferred: Literal["declared", "deterministic_inference"],
    inspector_identity: InspectorIdentity,
    evidence_schema_version: Literal[1] = 1,
) -> Sha256Hex:
    _sha(repository_snapshot_identity)
    _relative(repository_path)
    _enum(evidence_kind, _EVIDENCE_KINDS, "evidence_kind")
    if (line_start is None) != (line_end is None):
        raise RepositoryIdentityInputInvalid("line_range")
    if line_start is not None:
        _exact_int(line_start, "line_start", minimum=1)
        assert line_end is not None
        _exact_int(line_end, "line_end", minimum=line_start)
    _optional_locator(structural_locator)
    _optional_plain(symbol_name, "symbol_name")
    _mode_type(
        file_mode,
        "submodule" if file_mode == "160000" else "symlink" if file_mode == "120000" else "regular",
    )
    _sha(content_hash)
    _optional_sha(excerpt_hash)
    _enum(declared_or_inferred, {"declared", "deterministic_inference"}, "declared_or_inferred")
    _inspector_object(inspector_identity)
    _version(evidence_schema_version, "evidence_schema_version")
    if evidence_kind != "file_presence" and line_start is None and structural_locator is None:
        raise RepositoryIdentityInputInvalid("evidence_location")
    if file_mode == "160000":
        prefix = "gitlink:"
        if (
            evidence_kind != "file_presence"
            or structural_locator is None
            or not structural_locator.startswith(prefix)
        ):
            raise RepositoryIdentityInputInvalid("gitlink_locator")
        oid = structural_locator.removeprefix(prefix)
        _oid(oid)
        expected = sha256(b"gitlink\0" + oid.encode("ascii")).hexdigest()
        if content_hash != expected:
            raise RepositoryIdentityInputInvalid("gitlink_content_hash")
    return _hash(
        "labpilot-repository-evidence:v1",
        {
            "repository_snapshot_identity": repository_snapshot_identity,
            "repository_path": repository_path,
            "evidence_kind": evidence_kind,
            "line_start": line_start,
            "line_end": line_end,
            "structural_locator": structural_locator,
            "symbol_name": symbol_name,
            "file_mode": file_mode,
            "content_hash": content_hash,
            "excerpt_hash": excerpt_hash,
            "declared_or_inferred": declared_or_inferred,
            "inspector_identity": _inspector_object(inspector_identity),
            "evidence_schema_version": evidence_schema_version,
        },
    )


def repository_evidence_manifest_entries_hash(
    evidence_identities: Sequence[Sha256Hex], *, entries_schema_version: Literal[1] = 1
) -> Sha256Hex:
    _version(entries_schema_version, "entries_schema_version")
    copied = [_sha(value, "evidence_identity") for value in evidence_identities]
    if len(set(copied)) != len(copied):
        raise RepositoryIdentityInputInvalid("duplicate_evidence_identity")
    return _hash(
        "labpilot-repository-evidence-manifest-entries:v1",
        {
            "ordered_evidence_identities": sorted(copied, key=str.encode),
            "entries_schema_version": entries_schema_version,
        },
    )


def repository_evidence_manifest_hash(
    *,
    repository_snapshot_identity: Sha256Hex,
    inspection_result_hash: Sha256Hex,
    entries_manifest_hash: Sha256Hex,
    manifest_schema_version: int,
) -> Sha256Hex:
    _sha(repository_snapshot_identity)
    _sha(inspection_result_hash)
    _sha(entries_manifest_hash)
    _version(manifest_schema_version, "manifest_schema_version")
    return _hash(
        "labpilot-repository-evidence-manifest:v1",
        {
            "repository_snapshot_identity": repository_snapshot_identity,
            "inspection_result_hash": inspection_result_hash,
            "entries_manifest_hash": entries_manifest_hash,
            "manifest_schema_version": manifest_schema_version,
        },
    )


def inspector_registry_identity(
    *,
    profile_id: str,
    required_inspectors: Sequence[InspectorIdentity],
    optional_inspectors: Sequence[InspectorIdentity],
    registry_schema_version: int,
) -> Sha256Hex:
    _profile(profile_id, "profile_id")
    _version(registry_schema_version, "registry_schema_version")
    required = list(required_inspectors)
    optional = list(optional_inspectors)
    if not required:
        raise RepositoryIdentityInputInvalid("required_inspectors_empty")
    for value in (*required, *optional):
        _inspector_object(value)
    required_keys = [(value.inspector_name, value.inspector_version) for value in required]
    optional_keys = [(value.inspector_name, value.inspector_version) for value in optional]
    if len(set(required_keys)) != len(required_keys) or len(set(optional_keys)) != len(
        optional_keys
    ):
        raise RepositoryIdentityInputInvalid("duplicate_inspector_identity")
    if set(required_keys) & set(optional_keys):
        raise RepositoryIdentityInputInvalid("inspector_category_overlap")

    def key(value: InspectorIdentity) -> tuple[bytes, bytes]:
        return (value.inspector_name.encode(), value.inspector_version.encode())

    return _hash(
        "labpilot-inspector-registry:v1",
        {
            "profile_id": profile_id,
            "required_inspectors": [
                _inspector_object(value) for value in sorted(required, key=key)
            ],
            "optional_inspectors": [
                _inspector_object(value) for value in sorted(optional, key=key)
            ],
            "registry_schema_version": registry_schema_version,
        },
    )


def _limits_object(profile: InspectionLimitsProfileV1) -> dict[str, object]:
    return {field.name: getattr(profile, field.name) for field in fields(profile)}


def inspection_limits_profile_identity(profile: InspectionLimitsProfileV1) -> Sha256Hex:
    if not isinstance(profile, InspectionLimitsProfileV1):
        raise RepositoryIdentityInputInvalid("limits_profile_type")
    return _hash("labpilot-repository-inspection-limits-profile:v1", _limits_object(profile))


def repository_inspection_request_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    inspector_profile_id: str,
    inspector_registry_identity: Sha256Hex,
    inspection_limits_profile_identity: Sha256Hex,
    request_schema_version: int,
) -> Sha256Hex:
    _sha(repository_snapshot_identity)
    _profile(inspector_profile_id, "inspector_profile_id")
    _sha(inspector_registry_identity)
    _sha(inspection_limits_profile_identity)
    _version(request_schema_version, "request_schema_version")
    return _hash(
        "labpilot-repository-inspection-request:v1",
        {
            "repository_snapshot_identity": repository_snapshot_identity,
            "inspector_profile_id": inspector_profile_id,
            "inspector_registry_identity": inspector_registry_identity,
            "inspection_limits_profile_identity": inspection_limits_profile_identity,
            "request_schema_version": request_schema_version,
        },
    )


def _nullable_key(value: str | None) -> tuple[int, bytes]:
    return (0, b"") if value is None else (1, value.encode())


def _candidate_object(value: Any) -> dict[str, object]:
    return {
        field.name: _evidence_set(getattr(value, field.name))
        if field.name == "evidence_identities"
        else getattr(value, field.name)
        for field in fields(value)
    }


def _normalize(
    values: Sequence[Any],
    expected: type[Any],
    semantic: Callable[[Any], object],
) -> list[dict[str, object]]:
    copied = list(values)
    if len(copied) > _DEFAULT_LIMITS.max_candidates_per_collection:
        raise InspectionResourceLimitExceeded("max_candidates_per_collection")
    if any(not isinstance(value, expected) for value in copied):
        raise RepositoryIdentityInputInvalid("candidate_type")
    keys = [semantic(value) for value in copied]
    if len(set(keys)) != len(keys):
        raise RepositoryIdentityInputInvalid("duplicate_candidate_semantic_key")
    ordered = sorted(
        copied,
        key=lambda value: (
            semantic(value),
            tuple(item.encode() for item in _evidence_set(value.evidence_identities)),
        ),
    )
    return [_candidate_object(value) for value in ordered]


def _value_object(value: UnresolvedCandidateValueV1) -> dict[str, str]:
    return {"value_kind": value.value_kind, "value": value.value}


def _question_object(value: UnresolvedQuestionV1) -> dict[str, object]:
    ordered_values = sorted(
        value.candidate_values, key=lambda item: (item.value_kind.encode(), item.value.encode())
    )
    return {
        "question_code": value.question_code,
        "repository_path": value.repository_path,
        "structural_locator": value.structural_locator,
        "candidate_values": [_value_object(item) for item in ordered_values],
        "evidence_identities": _evidence_set(value.evidence_identities),
    }


def repository_inspection_result_hash(
    *,
    inspection_request_identity: Sha256Hex,
    result_status: InspectorResultStatus,
    candidate_entrypoints: Sequence[CandidateEntrypointV1],
    configuration_candidates: Sequence[ConfigurationCandidateV1],
    metric_candidates: Sequence[MetricCandidateV1],
    artifact_candidates: Sequence[ArtifactCandidateV1],
    dataset_reference_candidates: Sequence[DatasetReferenceCandidateV1],
    environment_candidates: Sequence[EnvironmentCandidateV1],
    detected_commands: Sequence[DetectedCommandV1],
    unresolved_questions: Sequence[UnresolvedQuestionV1],
    completed_inspector_identities: Sequence[InspectorIdentity],
    result_schema_version: int,
) -> Sha256Hex:
    _sha(inspection_request_identity)
    _enum(result_status, {"complete", "partial", "ambiguous", "failed"}, "result_status")
    _version(result_schema_version, "result_schema_version")
    unresolved = list(unresolved_questions)
    if len(unresolved) > _DEFAULT_LIMITS.max_unresolved_questions:
        raise InspectionResourceLimitExceeded("max_unresolved_questions")
    if any(not isinstance(value, UnresolvedQuestionV1) for value in unresolved):
        raise RepositoryIdentityInputInvalid("unresolved_question_type")

    def value_key(item: UnresolvedQuestionV1) -> Any:
        return tuple(
            sorted(
                (value.value_kind.encode(), value.value.encode()) for value in item.candidate_values
            )
        )

    def question_key(item: UnresolvedQuestionV1) -> Any:
        return (
            item.question_code.encode(),
            _nullable_key(item.repository_path),
            _nullable_key(item.structural_locator),
            value_key(item),
        )

    question_keys = [question_key(item) for item in unresolved]
    if len(set(question_keys)) != len(question_keys):
        raise RepositoryIdentityInputInvalid("duplicate_unresolved_question")
    if result_status == "complete" and unresolved:
        raise RepositoryIdentityInputInvalid("complete_has_unresolved_questions")
    if result_status == "ambiguous" and not unresolved:
        raise RepositoryIdentityInputInvalid("ambiguous_without_unresolved_questions")
    c1 = _normalize(
        candidate_entrypoints,
        CandidateEntrypointV1,
        lambda x: (
            x.repository_path.encode(),
            x.entrypoint_kind.encode(),
            _nullable_key(x.symbol_name),
        ),
    )
    c2 = _normalize(
        configuration_candidates,
        ConfigurationCandidateV1,
        lambda x: (
            x.repository_path.encode(),
            x.configuration_kind.encode(),
            _nullable_key(x.structural_locator),
        ),
    )
    c3 = _normalize(
        metric_candidates,
        MetricCandidateV1,
        lambda x: (
            x.repository_path.encode(),
            _nullable_key(x.symbol_name),
            _nullable_key(x.metric_name),
            x.structural_locator.encode(),
        ),
    )
    c4 = _normalize(
        artifact_candidates,
        ArtifactCandidateV1,
        lambda x: (
            x.repository_path.encode(),
            _nullable_key(x.symbol_name),
            _nullable_key(x.artifact_path_literal),
            x.structural_locator.encode(),
        ),
    )
    c5 = _normalize(
        dataset_reference_candidates,
        DatasetReferenceCandidateV1,
        lambda x: (
            x.repository_path.encode(),
            x.reference_kind.encode(),
            x.reference_value.encode(),
            x.structural_locator.encode(),
        ),
    )
    c6 = _normalize(
        environment_candidates,
        EnvironmentCandidateV1,
        lambda x: (
            x.repository_path.encode(),
            x.environment_kind.encode(),
            _nullable_key(x.structural_locator),
        ),
    )
    c7 = _normalize(
        detected_commands,
        DetectedCommandV1,
        lambda x: (
            x.repository_path.encode(),
            x.command_source.encode(),
            x.command_text.encode(),
            x.structural_locator.encode(),
        ),
    )
    if (
        sum(map(len, (c1, c2, c3, c4, c5, c6, c7))) + len(unresolved)
        > _DEFAULT_LIMITS.max_total_candidates
    ):
        raise InspectionResourceLimitExceeded("max_total_candidates")
    completed = list(completed_inspector_identities)
    for value in completed:
        _inspector_object(value)
    completed_keys = [(value.inspector_name, value.inspector_version) for value in completed]
    if len(set(completed_keys)) != len(completed_keys):
        raise RepositoryIdentityInputInvalid("duplicate_completed_inspector")

    def inspector_key(value: InspectorIdentity) -> tuple[bytes, bytes]:
        return (value.inspector_name.encode(), value.inspector_version.encode())

    ordered_questions = sorted(
        unresolved,
        key=lambda item: (
            question_key(item),
            tuple(value.encode() for value in _evidence_set(item.evidence_identities)),
        ),
    )
    payload = {
        "inspection_request_identity": inspection_request_identity,
        "result_status": result_status,
        "candidate_entrypoints": c1,
        "configuration_candidates": c2,
        "metric_candidates": c3,
        "artifact_candidates": c4,
        "dataset_reference_candidates": c5,
        "environment_candidates": c6,
        "detected_commands": c7,
        "unresolved_questions": [_question_object(value) for value in ordered_questions],
        "completed_inspector_identities": [
            _inspector_object(value) for value in sorted(completed, key=inspector_key)
        ],
        "result_schema_version": result_schema_version,
    }
    try:
        encoded = canonical_json_bytes(cast(CanonicalValue, payload))
    except CanonicalizationError as exc:
        if str(exc) != "canonical output exceeds max_output_bytes":
            raise
        raise InspectionResourceLimitExceeded("max_result_canonical_bytes") from exc
    if len(encoded) > _DEFAULT_LIMITS.max_result_canonical_bytes:
        raise InspectionResourceLimitExceeded("max_result_canonical_bytes")
    return namespaced_sha256_bytes("labpilot-repository-inspection-result:v1", encoded)
