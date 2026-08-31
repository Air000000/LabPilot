# LabPilot WP1-A Task 6 Canonical Identity Contract Supplement

Document type: Implementation Contract Supplement
Version: v0.3.1
Status: Approved Implementation Contract
Date: 2026-08-02
Applies to: WP1-A Task 6 and identity consumers in Tasks 7-14
Base commit: 64ea1ac87a49a774911f4dd8e2e7444b683742c1
Approved design input: WP1 Domain and Context Overall Design v0.5
Approved implementation plan: WP1-A Implementation Plan v0.2
Reviewed candidate SHA-256: 63c54a268c862af1556ee1718d5fcf8f3fac1d9c18d55b781cdb8e5e1b904b04
Independent review archive SHA-256: 235438f1fa63c99b1ddf460b4e277c7732af2bcb914a361b77e031b8d1461c2f
Authority: subordinate to frozen architecture and approved planning inputs
Execution authorization: none until the approved-contract installation commit is independently reviewed, pushed, and confirmed by GitHub CI

## 0. v0.3.1 approval closure

v0.3.1 preserves the v0.3 design closure, incorporates the two independently reviewed mechanical corrections, and closes the four findings from the v0.2 independent review without changing the frozen State or Snapshot payloads:

1. the Inspection Limits Profile is now strictly subordinate to the accepted Canonical JSON V1 container, node, and output-byte budgets;
2. unresolved candidate alternatives use an explicit typed value object rather than a field-dependent string rule;
3. source ownership now covers `environment*.yml|yaml`, gitlinks/submodules, Git LFS pointer files, supported binary files, and resource-limit failures;
4. gitlink and LFS Evidence content identities are exact, and `complete|ambiguous` Results both map to a completed InspectionRun.

This document is approved as the WP1-A Task 6 implementation contract. It does not by itself authorize implementation or a tracked-file change; implementation remains blocked until this exact approved document is installed in the Repository, its installation commit is independently reviewed, the commit is pushed, and GitHub CI passes.

## 1. Authority, scope, and non-authorization

This document is the approved WP1-A Task 6 implementation-contract supplement. It does not authorize Task 6 implementation, Task 7 work, or any code change until the approved-contract installation gate defined in the header has completed.

This supplement does not amend the frozen architecture or the approved implementation plan. It closes exact implementation-contract details that those inputs require but do not fully spell out. If any statement here conflicts with the frozen architecture, approved overall design, or approved implementation plan, the higher document wins in that order.

The supplement covers only Canonical JSON V1 identities, the unchanged content-store boundary, and their persisted consumers in Tasks 7-14. It does not define WP1-B/C inference, runtime model output, source materialization, or public API transport schemas.

## 2. Canonical primitives

### 2.1 Hash construction

Every identity in this supplement is a 64-character lowercase hexadecimal string computed as:

```python
sha256(namespace.encode("ascii") + b"\n" + canonical_json_bytes(payload)).hexdigest()
```

`canonical_json_bytes` is the accepted Canonical JSON V1 implementation at the base commit. Object keys are sorted by their UTF-8 bytes; arrays preserve the order supplied to the encoder; strings are encoded as UTF-8; isolated Unicode surrogates, unsupported Python types, and configured resource-limit violations are rejected. A namespace is restricted ASCII and includes its `:vN` suffix.

The accepted Canonical JSON V1 hard limits at the base commit are normative upper bounds for every identity producer in this supplement:

```text
max_depth = 64
max_total_nodes = 100000
max_container_entries = 10000
max_output_bytes = 8000000
```

A domain limits profile must be stricter than these encoder limits. It must reject an observation before persistence when its canonical payload would exceed the applicable profile count, node, or byte budget; an encoder-limit exception is not a valid domain Result.

Hash functions validate and copy caller collections before sorting. They do not mutate caller lists, tuples, dictionaries, or dataclass instances.

### 2.2 Scalar validation

The following aliases are normative logical types:

```python
type Sha256Hex = str       # fullmatch r"[0-9a-f]{64}"
type GitObjectOid = str    # fullmatch r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
type FileMode = Literal["100644", "100755", "120000", "160000"]
type RepositoryType = Literal["local_git"]
type EntryKind = Literal[
    "tracked_overlay", "untracked_file", "deletion", "mode_change"
]
type FileType = Literal["regular", "symlink", "submodule"]
type ContentIdentityStatus = Literal["verified", "unverified", "not_applicable"]
```

Gitlink entries use `file_type="submodule"` and `file_mode="160000"`. Git status manifests do not contain standalone directory entries: Git status is flattened to files, symlinks, and gitlinks. An unsupported filesystem directory record is rejected instead of being coerced to a file.

UUID inputs are Python `UUID` values and are encoded with `str(value)`, yielding lowercase hyphenated canonical form. Boolean fields require exact `bool`, integers require exact `int` and must not be booleans, sizes and ordinals are non-negative, and schema versions in this document equal integer `1` unless the frozen Snapshot namespace says `v2`.

#### 2.2A Valid persisted text

Every persisted string first passes this shared logical validator, in addition to any field-specific grammar:

```python
class PersistedTextInvalid(ValueError):
    field: str
    reason_code: Literal[
        "empty", "nul", "isolated_surrogate", "utf8_limit", "lf", "tab", "cr"
    ]

def validate_persisted_text(
    value: str,
    *,
    field: str,
    max_utf8_bytes: int,
    allow_empty: bool = False,
    allow_lf: bool,
    allow_tab: bool,
    allow_cr: bool,
) -> str: ...
```

The function requires exact `str`, rejects NUL and every isolated Unicode surrogate, encodes the value with strict UTF-8, enforces the byte limit, and returns the input unchanged. It does not trim, case-fold, normalize Unicode, or rewrite line endings. A rejected condition raises `PersistedTextInvalid` with the stable reason code shown above.

The control-character policy is exhaustive:

| Persisted field family | LF | Tab | CR | Additional rule |
|---|---:|---:|---:|---|
| `canonical_root_path` | allow | allow | allow | non-empty canonical absolute path; NUL-delimited Git/filesystem acquisition preserves legal bytes |
| `relative_path`, `original_relative_path`, `repository_path`, `original_repository_path`, `artifact_path_literal` | allow | allow | allow | non-null repository paths also pass section 2.3; artifact literals are source text, not resolved paths |
| `command_text` | allow | allow | reject | exact logical command/block after the owning parser removes source line terminators |
| `structural_locator` | reject | reject | reject | non-empty, at most 500 UTF-8 bytes |
| `symbol_name`, `metric_name`, `reference_value` | reject | reject | reject | non-null values are non-empty; JSONB-only values are at most 8 KiB |
| inspector names/versions, profile IDs, branch names, enum values, question codes, capture tool version | reject | reject | reject | the narrower grammar or enum also applies |
| remote origin and default branch diagnostic values | reject | reject | reject | excluded from identities; validation still applies before persistence |
| unresolved candidate value `repository_path` | allow | allow | allow | section 2.3 path validation; at most 4096 UTF-8 bytes |
| unresolved candidate value `source_literal` | allow | allow | reject | non-empty exact source text; at most 8192 UTF-8 bytes |
| unresolved candidate value `identifier` | reject | reject | reject | non-empty exact identifier/value; at most 8192 UTF-8 bytes |

Nullable fields use JSON `null`; null is never represented by an empty string. A NUL rejection oracle is `validate_persisted_text("ab\0cd", field="symbol_name", max_utf8_bytes=8192, allow_lf=False, allow_tab=False, allow_cr=False)`, which raises `PersistedTextInvalid(reason_code="nul")`. Replacing NUL with isolated surrogate `"ab\ud800cd"` raises `reason_code="isolated_surrogate"`. A positive multiline command is `"python -m train\n\t--epochs 5"`; replacing its LF with CR raises `reason_code="cr"`.
Additional stable rejection tests are:

| Field | Input | Result |
|---|---|---|
| `relative_path` | `"src/a\0b.py"` | `PersistedTextInvalid("relative_path","nul")` |
| `command_text` | `"python train.py\0--fast"` | `PersistedTextInvalid("command_text","nul")` |
| `inspector_name` | `"file\0inventory"` | `PersistedTextInvalid("inspector_name","nul")` |
| candidate `symbol_name` | `"train\0main"` | `PersistedTextInvalid("symbol_name","nul")` |
### 2.3 Canonical repository-relative path contract

The shared logical validator is:

```python
def validate_repository_relative_path(value: str) -> str: ...
```

It returns the unchanged input only when all checks pass. It rejects:

- `""`;
- a POSIX absolute path beginning with `/`;
- a Windows drive absolute path matching `^[A-Za-z]:/`;
- a UNC form beginning with `//` or with backslashes;
- any backslash anywhere;
- a trailing slash;
- an empty component, including `a//b`;
- a component equal to `.` or `..`;
- NUL;
- an isolated Unicode surrogate;
- a value that cannot be encoded as UTF-8.

The validator does not trim, case-fold, replace separators, collapse components, apply Unicode normalization, call filesystem resolution, or consult the host operating system. A legal path is ordered by `value.encode("utf-8")`.

Every non-null `original_relative_path` or `original_repository_path` passes the same validator. It is non-null only on a normalized rename/copy destination. A normalized rename emits a deletion at the old path plus a destination entry; a normalized copy emits only the destination entry. No two normalized entries may have the same current path.

Repository-entry base order is the exact tuple:

```python
(
    relative_path.encode("utf-8"),
    entry_kind.encode("utf-8"),
    file_type.encode("utf-8"),
)
```

The second and third elements therefore use UTF-8 byte order, not an implicit enum ordinal or host collation. Current-path uniqueness makes the latter elements tie-breakers only.

### 2.4 Shared manifest entry object

```python
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
```

Its canonical object always contains all ten keys, including nullable keys:

```json
{
  "relative_path": "...",
  "original_relative_path": null,
  "entry_kind": "tracked_overlay",
  "file_type": "regular",
  "file_mode": "100644",
  "head_blob_oid": null,
  "index_blob_oid": null,
  "content_hash": null,
  "size_bytes": 0,
  "content_identity_status": "unverified"
}
```

Additional invariants are exact:

- `untracked_file`: `original_relative_path`, `head_blob_oid`, and `index_blob_oid` are null.
- `deletion`: `original_relative_path` and `content_hash` are null, `content_identity_status="not_applicable"`, `size_bytes=0`, and at least one Git OID is non-null.
- `mode_change`: `original_relative_path` and `content_hash` are null, `content_identity_status="not_applicable"`, `size_bytes=0`, and both Git OIDs are non-null and equal.
- `regular` requires mode `100644` or `100755`; `symlink` requires `120000`; `submodule` requires `160000`.
- `verified` requires a non-null content hash for regular or symlink content and an exact captured byte count in `size_bytes`.
- `unverified` requires a null content hash and is representable because the frozen architecture names that state. Task 10 must fail without persisting a Snapshot when any required entry remains unverified under the approved complete-capture protocol.
- `not_applicable` requires a null content hash and is limited to deletion, pure mode change, or gitlink metadata without captured file bytes.
- Symlink `content_hash` is SHA-256 over the captured normalized link-target bytes, never over target-file bytes.

`content_ref`, stable ordinal, stable stat token, timestamps, and capture diagnostics are not keys in this object.

### 2.5 Collection classifications

| Collection | Classification | Canonical order | Duplicate rule | Empty encoding |
|---|---|---|---|---|
| dirty manifest entries | unordered set | repository-entry base order | reject duplicate current path | empty input rejected; absent category is `null` at State level |
| untracked manifest entries | unordered set | repository-entry base order | reject duplicate current path | empty input rejected; absent category is `null` at State level |
| Bundle entry manifest items | unordered set | repository path UTF-8, entry kind UTF-8, file type UTF-8 | reject duplicate current path or identity | empty input rejected |
| Evidence identities | unordered set | lowercase hash ASCII bytes | reject duplicate identity | `[]` allowed |
| Registry required inspectors | unordered set | inspector name UTF-8, then version UTF-8 | reject duplicate identity | required set must be non-empty |
| Registry optional inspectors | unordered set | inspector name UTF-8, then version UTF-8 | reject duplicate identity | `[]` |
| Candidate Evidence identities | unordered set | lowercase hash ASCII bytes | reject duplicate identity | candidate requires at least one |
| Candidate collections | unordered set | collection-specific keys in section 4.2 | reject duplicate semantic key | `[]` |
| Unresolved candidate values | unordered set | value kind UTF-8, then validated value UTF-8 | reject duplicate `(value_kind, value)` | `[]` allowed subject to question-code rules |
| Completed inspectors | unordered set | inspector name UTF-8, then version UTF-8 | reject duplicate identity | `[]` is structurally valid; Task 14 completeness rules normally reject it |

No identity collection in this supplement is an unordered multiset or a caller-ordered semantic sequence. Fixed-key JSON objects are records, not open keyed maps. Nullable collections are forbidden; an existing empty collection is `[]`.

## 3. Design closure decisions

### 3.1 Registration/root identity

`labpilot-repository-registration:v1` is exactly Task 7 `root_path_identity`; it is not the identity of a particular Registration row. Its payload contains only `canonical_root_path` and `repository_type`.

`project_id` is not duplicated in the hash because PostgreSQL binds it with `UNIQUE(project_id, root_path_identity) WHERE status <> 'revoked'`. `repository_registration_id` is excluded so a live root can be semantically reused under a new command key and the same root can be registered again after revoke. Remote origin, default branch, status, and timestamps are excluded because they are diagnostic or mutable. A different canonical root produces a different identity, so relocation creates a new Registration and is never auto-merged.

Rejected alternatives are a Project-bound hash, which duplicates the database composite binding and prevents cross-Project comparison, and a random Registration-bound hash, which defeats live-root reuse.

### 3.2 Bundle entries manifest

This approved supplement selects option B and establishes one additive namespace:

```text
labpilot-repository-inspection-bundle-entries:v1
```

It is a single-purpose identity for the canonical set of Bundle Entry identities persisted as Task 9 `entries_manifest_hash`. Its function input carries each entry's repository path, entry kind, file type, and Entry identity. The canonical order and Task 9 `stable_ordinal` are the same zero-based order by repository-path UTF-8 bytes, then entry-kind UTF-8 bytes, then file-type UTF-8 bytes. It is an approved additive contract in this supplement and is not represented as though it already appeared in the approved plan.

Option A is rejected because overloading the full Bundle namespace with an `identity_kind` discriminator would assign two meanings to a namespace already named for the full Bundle. Option C is rejected because an Entry identity proves one entry and the full Bundle identity also binds Snapshot, base commit, bundle schema, and capture tool; neither proves only the entry set.

### 3.3 State and Snapshot priority resolution

The frozen payloads are copied exactly in sections 5.4 and 5.5. They do not receive a schema-version key. Their versions are expressed by `labpilot-repository-state:v1` and `labpilot-repository-snapshot:v2`. Persisted canonicalization/version columns describe how to recompute data; they do not automatically become identity inputs.

The State digest is Registration-independent and supports code-state comparison across Registrations. Snapshot identity adds `repository_registration_id`, branch, and detached state, so the same state under a different Registration remains a distinct LabPilot Snapshot. `captured_at` is observation metadata and never changes either identity.

### 3.4 Evidence and deterministic candidates

Evidence binds the persisted Snapshot semantic identity, source position/locator, source content, evidence kind, deterministic inspector, and schema. It excludes row IDs, timestamps, and storage refs. Candidate objects contain syntax-level observations and Evidence identities; they do not assert training/evaluation roles, confidence, rationale, or user confirmation. Those semantics belong to the frozen Planning Agent stage outside WP1-A.

## 4. Shared inspector and result types

### 4.1 Inspector identity

```python
@dataclass(frozen=True, slots=True)
class InspectorIdentity:
    inspector_name: str
    inspector_version: str
```

Its exact encoding is an object:

```json
{"inspector_name":"file_inventory","inspector_version":"v1"}
```

`inspector_name` must match `^[a-z][a-z0-9_]{0,99}$`. `inspector_version` must match `^v[1-9][0-9]*(?:[._-][A-Za-z0-9]+)*$` and occupy at most 100 ASCII bytes. Empty strings, whitespace, Unicode lookalikes, and strings outside those grammars are rejected.

### 4.2 Candidate objects

Every object below is a frozen value object. All keys shown are always present. Nullable values encode as `null`; collections encode as arrays.

```python
type EntrypointKind = Literal["python_main_guard", "python_callable"]
type ConfigurationKind = Literal[
    "pyproject", "requirements", "conda_environment", "setup_py",
    "setup_cfg", "dockerfile", "compose", "yaml", "json", "toml"
]
type EnvironmentKind = Literal[
    "python_project", "python_requirements", "conda_environment",
    "container_image", "compose_services"
]
type CommandSource = Literal[
    "pyproject_script", "packaging_entry_point", "dockerfile_instruction",
    "compose_command", "readme_code_block", "ci_workflow", "shell_script",
    "makefile", "taskfile"
]
type DatasetReferenceKind = Literal["cli_option", "manifest_value"]
type QuestionCode = Literal[
    "manifest_parse_error", "entrypoint_role_ambiguous",
    "multiple_entrypoint_candidates", "configuration_source_ambiguous",
    "metric_name_dynamic", "artifact_path_dynamic",
    "dataset_reference_dynamic", "environment_source_ambiguous",
    "command_parse_ambiguous", "unsupported_static_construct",
    "unsupported_source_dependency"
]
type UnresolvedCandidateValueKind = Literal[
    "repository_path", "source_literal", "identifier"
]

@dataclass(frozen=True, slots=True)
class CandidateEntrypointV1:
    repository_path: str
    entrypoint_kind: EntrypointKind
    symbol_name: str | None
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class ConfigurationCandidateV1:
    repository_path: str
    configuration_kind: ConfigurationKind
    structural_locator: str | None
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class MetricCandidateV1:
    repository_path: str
    symbol_name: str | None
    metric_name: str | None
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class ArtifactCandidateV1:
    repository_path: str
    symbol_name: str | None
    artifact_path_literal: str | None
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class DatasetReferenceCandidateV1:
    repository_path: str
    reference_kind: DatasetReferenceKind
    reference_value: str
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class EnvironmentCandidateV1:
    repository_path: str
    environment_kind: EnvironmentKind
    structural_locator: str | None
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class DetectedCommandV1:
    repository_path: str
    command_source: CommandSource
    command_text: str
    structural_locator: str
    evidence_identities: tuple[Sha256Hex, ...]

@dataclass(frozen=True, slots=True)
class UnresolvedCandidateValueV1:
    value_kind: UnresolvedCandidateValueKind
    value: str

@dataclass(frozen=True, slots=True)
class UnresolvedQuestionV1:
    question_code: QuestionCode
    repository_path: str | None
    structural_locator: str | None
    candidate_values: tuple[UnresolvedCandidateValueV1, ...]
    evidence_identities: tuple[Sha256Hex, ...]
```

`UnresolvedCandidateValueV1` has the exact canonical object:

```json
{"value_kind":"repository_path","value":"src/train.py"}
```

Its validation is determined only by `value_kind`, never by an implicit guess from the question text:

- `repository_path`: section 2.3, non-empty, at most 4096 UTF-8 bytes;
- `source_literal`: non-empty exact source text, at most 8192 UTF-8 bytes, LF and Tab allowed, CR/NUL/surrogates rejected;
- `identifier`: non-empty exact value, at most 8192 UTF-8 bytes, LF/Tab/CR/NUL/surrogates rejected.

The closed question/value-kind relation is:

| Question code | Allowed value kinds | Cardinality rule |
|---|---|---|
| `manifest_parse_error` | `source_literal` | zero or more bounded parser tokens/messages; no stack trace |
| `entrypoint_role_ambiguous` | `repository_path`, `identifier` | one or more |
| `multiple_entrypoint_candidates` | `repository_path` | at least two |
| `configuration_source_ambiguous` | `repository_path` | at least two |
| `metric_name_dynamic` | `source_literal`, `identifier` | zero or more |
| `artifact_path_dynamic` | `source_literal` | zero or more |
| `dataset_reference_dynamic` | `source_literal` | zero or more |
| `environment_source_ambiguous` | `repository_path` | at least two |
| `command_parse_ambiguous` | `source_literal` | zero or more |
| `unsupported_static_construct` | `source_literal`, `identifier` | zero or more |
| `unsupported_source_dependency` | `repository_path`, `source_literal` | at least one, including the unsupported path |

Task 12 determines fields as follows:

- `file_inventory:v1` emits file-presence Evidence and deterministic unsupported-source questions but no semantic-role candidate.
- `project_manifests:v1` derives configuration/environment kinds from the exact parsed file class; commands from exact manifest keys, container instructions, workflow/script declarations, or bounded code blocks; dataset references only from explicit keys or values.
- `python_static_signals:v1` derives `python_main_guard` from the exact AST main-guard form and `python_callable` from a function definition; it records literal metric names, artifact paths, and dataset option/value strings when statically present, otherwise uses the corresponding closed question code.
- A `structural_locator` is the exact non-empty UTF-8 locator emitted by the inspector, at most 500 bytes, with no trimming or Unicode normalization. Line ranges remain authoritative Evidence fields.
- `command_text`, reference values, symbols, metric names, and artifact literals are exact bounded source values. They are never executed by identity code.

All repository paths use section 2.3. Inspection candidate/evidence repository paths are additionally limited to 4096 UTF-8 bytes. Non-null JSONB-only strings are bounded to 8192 UTF-8 bytes unless this document gives a smaller limit. Every candidate has at least one Evidence identity from the same Snapshot. Each candidate and unresolved question has at most the active profile's `max_evidence_identities_per_candidate`; each unresolved question has at most `max_candidate_values_per_question` typed values.

The canonical uniqueness and sort keys are exact. `N(value)` is `(0, b"")` for null and `(1, value.encode("utf-8"))` otherwise; `E` is the tuple of sorted Evidence-identity ASCII bytes; `V` is the tuple of typed candidate values sorted by `(value_kind.encode("utf-8"), value.encode("utf-8"))`.

| Collection | Semantic uniqueness key | Canonical sort key |
|---|---|---|
| candidate_entrypoints | `(path, entrypoint_kind, N(symbol_name))` | uniqueness key encoded as UTF-8 tuples, then `E` |
| configuration_candidates | `(path, configuration_kind, N(structural_locator))` | uniqueness key, then `E` |
| metric_candidates | `(path, N(symbol_name), N(metric_name), structural_locator)` | uniqueness key, then `E` |
| artifact_candidates | `(path, N(symbol_name), N(artifact_path_literal), structural_locator)` | uniqueness key, then `E` |
| dataset_reference_candidates | `(path, reference_kind, reference_value, structural_locator)` | uniqueness key, then `E` |
| environment_candidates | `(path, environment_kind, N(structural_locator))` | uniqueness key, then `E` |
| detected_commands | `(path, command_source, command_text, structural_locator)` | uniqueness key, then `E` |
| unresolved_questions | `(question_code, N(path), N(structural_locator), V)` | uniqueness key, then `E` |

Each textual element in those tuples is converted to UTF-8 bytes. A duplicate semantic uniqueness key is rejected; identity code does not merge Evidence. Producers must aggregate all supporting Evidence into one candidate before hashing. Caller order has no meaning.

### 4.3 Task 12 source/Inspector ownership

The following matrix is an approved Task 12 plan clarification supplied by this contract supplement. `file_inventory:v1` owns path/type/mode/content inventory and deterministic unsupported-source classification. It may read a bounded file prefix only to recognize the exact Git LFS pointer grammar; it does not infer a semantic repository role. Exactly one semantic content parser owns every other supported source class.

| Source class | Initial owner | Candidate/question output | Evidence kinds and content identity | Bounded deterministic extraction |
|---|---|---|---|---|
| every supported regular file or symlink | `file_inventory:v1` metadata owner | no semantic candidate | `file_presence`; SHA-256 of captured file bytes or normalized symlink-target bytes | repository path, type, mode, and verified Snapshot content identity only |
| gitlink/submodule (`mode=160000`) | `file_inventory:v1` | `unsupported_source_dependency` question with the gitlink path and `gitlink:<oid>` source literal | `file_presence`; `sha256(b"gitlink\0" + object_oid.encode("ascii"))`; locator `gitlink:<oid>` | record metadata and target commit OID; never open or recurse into a submodule |
| Git LFS pointer file | `file_inventory:v1` pointer classifier | `unsupported_source_dependency` question with the pointer path and declared LFS OID/size source literals | `file_presence`; SHA-256 of the exact captured pointer-file bytes, not the remote LFS object | recognize only the bounded canonical LFS pointer grammar; never fetch the object; semantic parsers skip the classified pointer file |
| supported binary file within per-file and total byte limits | `file_inventory:v1` | `unsupported_static_construct` question when semantic parsing is unavailable | `file_presence`; SHA-256 of captured bytes | no decoding or semantic parser invocation |
| file or total scan exceeding the active limits profile | inspection orchestrator | no persisted question or Result | no new Evidence | raise `InspectionResourceLimitExceeded` before persistence; the atomic inspection leaves no Run/Result/Evidence/Manifest |
| `pyproject.toml` | `project_manifests:v1` | configuration `pyproject`; environment `python_project`; command `pyproject_script`; dataset `manifest_value` | `manifest_declaration`, `config_key`, `command_declaration` | TOML keys, tables, literal values, and declared scripts only |
| `requirements*.txt` | `project_manifests:v1` | configuration `requirements`; environment `python_requirements` | `manifest_declaration` | requirement declarations and literal include paths; no package resolution |
| `environment*.yml` / `environment*.yaml` | `project_manifests:v1` | configuration `conda_environment`; environment `conda_environment`; dataset `manifest_value` | `manifest_declaration`, `config_key` | YAML mapping/list scalars under the recognized environment schema |
| `Dockerfile` and named Dockerfiles | `project_manifests:v1` | configuration `dockerfile`; environment `container_image`; command `dockerfile_instruction` | `manifest_declaration`, `command_declaration` | instruction lexer; literal `FROM`, `RUN`, `CMD`, and `ENTRYPOINT`; no build or shell execution |
| Compose YAML | `project_manifests:v1` | configuration `compose`; environment `compose_services`; command `compose_command` | `manifest_declaration`, `config_key`, `command_declaration` | recognized Compose keys and literal command arrays/strings |
| `setup.py` | `project_manifests:v1` | configuration `setup_py`; environment `python_project`; command `packaging_entry_point` | `manifest_declaration`, `config_key`, `command_declaration` | restricted Python AST for literal `setup(...)` keywords and literal entry-point mappings; no execution; excluded from `python_static_signals:v1` |
| `setup.cfg` | `project_manifests:v1` | configuration `setup_cfg`; environment `python_project`; command `packaging_entry_point` | `manifest_declaration`, `config_key`, `command_declaration` | INI sections/keys and literal entry-point declarations |
| `README*` Markdown/reStructuredText | `project_manifests:v1` | command `readme_code_block` | `readme_example`, `command_declaration` | bounded fenced/literal code blocks explicitly tagged or syntactically recognized as shell; prose is not a command |
| `.github/workflows/*.yml` and `*.yaml` | `project_manifests:v1` | command `ci_workflow`; configuration `yaml` | `config_key`, `command_declaration` | workflow jobs/steps and literal `run`/recognized action command values; no workflow execution |
| Other YAML | `project_manifests:v1` | configuration `yaml`; environment/dataset only for a closed recognized key | `manifest_declaration`, `config_key` | mapping/list/scalar syntax; parse failure yields `manifest_parse_error` |
| Other JSON | `project_manifests:v1` | configuration `json`; environment/dataset only for a closed recognized key | `manifest_declaration`, `config_key` | object/array/scalar syntax; parse failure yields `manifest_parse_error` |
| Other TOML | `project_manifests:v1` | configuration `toml`; environment/dataset only for a closed recognized key | `manifest_declaration`, `config_key` | table/key/literal syntax; parse failure yields `manifest_parse_error` |
| `*.sh`, `*.bash` | `project_manifests:v1` | command `shell_script` | `command_declaration` | bounded POSIX-shell lexer records complete simple commands; expansions/substitutions yield `command_parse_ambiguous`; no execution |
| `Makefile`, `GNUmakefile`, `*.mk` | `project_manifests:v1` | command `makefile` | `command_declaration` | target and recipe declarations; dynamic expansion is unresolved; no make execution |
| `Taskfile.yml`, `Taskfile.yaml` | `project_manifests:v1` | command `taskfile`; configuration `yaml` | `config_key`, `command_declaration` | recognized Taskfile task/command keys; no task execution |
| ordinary `*.py`, excluding `setup.py` | `python_static_signals:v1` | entrypoint `python_main_guard`/`python_callable`; metric; artifact; dataset `cli_option` | `main_guard`, `function_definition`, `cli_argument`, `metric_logging`, `artifact_write` | Python AST only; literal syntax and closed recognized calls; no import or execution |

For gitlinks, the domain-separated raw SHA-256 rule above is a metadata content identity for the Git tree entry; it is not a hash of materialized submodule contents. The corresponding Task 10 Bundle Entry continues to use `content_hash=null` and `content_identity_status="not_applicable"`. For Git LFS pointers, the Evidence content hash always proves the pointer file bytes and never claims the remote object is present. Both cases produce `unsupported_source_dependency`, which later compatibility/freeze checks must treat as blocking when the proposed experiment requires that source.

`command_declaration` is added to the closed EvidenceKind enum in section 5.8. No semantic content source is scanned by both `project_manifests:v1` and `python_static_signals:v1`. Future source classes or parser ownership changes require a new inspector/profile version and are not silently absorbed by this matrix.

## 5. Namespace contracts

### 5.1 `labpilot-repository-registration:v1`

**Purpose.** Produce Task 7 `root_path_identity` for one canonical local-Git root.

**Exact typed signature.**

```python
def repository_registration_identity(
    *, canonical_root_path: str, repository_type: RepositoryType
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{"canonical_root_path":"<absolute canonical root>","repository_type":"local_git"}
```

`canonical_root_path` is non-null, absolute, valid UTF-8, already produced by the approved secure realpath boundary, and is encoded unchanged. It is not governed by the relative-path validator. There is no collection and caller order is inapplicable. There is no payload version key; namespace `:v1` is the version.

Excluded: `project_id`, `repository_registration_id`, remote origin, default branch, status, access checks, timestamps, row version, and primary-pointer state.

Persisted field: `RepositoryRegistrationRow.root_path_identity`; recomputation uses persisted `canonical_root_path` and `repository_type`. Consumers: Tasks 7 and 8.

Independent oracle: SHA-256 over ASCII `labpilot-repository-registration:v1`, LF, and the exact Canonical JSON bytes below.

```text
Positive JSON: {"canonical_root_path":"/srv/labpilot/repos/demo","repository_type":"local_git"}
Positive hash: ebccef3f53f569c02fd3d955dc456154297295e851e89ba3d3a28c3789a78ac1
Change: canonical_root_path -> /srv/labpilot/repos/demo-moved
Changed hash: adeb8097b98b1a2d559cde9276677a425bbe5ba13ddb293d0258842ff583eea1
Exclusion: change project_id, registration UUID, remote, branch, status, or timestamp in the source record; reconstructed payload and hash remain the positive values.
```

### 5.2 `labpilot-dirty-worktree-manifest:v1`

**Purpose.** Prove the complete normalized tracked dirty overlay, excluding untracked files.

**Exact typed signature.**

```python
def dirty_worktree_manifest_hash(
    entries: Sequence[RepositoryManifestEntryV1],
    *,
    manifest_schema_version: Literal[1] = 1,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{"entries":[RepositoryManifestEntryV1...],"manifest_schema_version":1}
```

Allowed entry kinds are `tracked_overlay`, `deletion`, and `mode_change`; `untracked_file` is rejected. All ten entry keys from section 2.4 are present. Nullable rules and relative-path validation are those in sections 2.3-2.4. The input is an unordered set sorted by the repository-entry base order; duplicate current paths are rejected; empty input is rejected. `manifest_schema_version` is explicit and equals `1`.

Excluded: content refs, ordinals, stat tokens, capture time, root path, Registration ID, error text, and observation duration.

Persisted fields: `RepositorySnapshotRow.dirty_worktree_manifest_hash` and its content-store `dirty_worktree_manifest_ref`. The hash is recomputed from the canonical manifest blob; Task 10 Bundle Entry rows independently retain the entry metadata and content hashes. Consumers: Tasks 9, 10, and 11.

Independent oracle: SHA-256 over ASCII `labpilot-dirty-worktree-manifest:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"entries":[{"content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","content_identity_status":"verified","entry_kind":"tracked_overlay","file_mode":"100644","file_type":"regular","head_blob_oid":"1111111111111111111111111111111111111111","index_blob_oid":"2222222222222222222222222222222222222222","original_relative_path":null,"relative_path":"src/train.py","size_bytes":12}],"manifest_schema_version":1}
Positive hash: 3aef9eed6519a4ef138a7b12415460b83452fc66923c9a38c3e765c62b22b099
Change: content_hash a...a -> b...b
Changed hash: fb63b67a19a9f71b18cf0fabe97a9d41d97f3061bb7fd6c0f8c9647c945ac46d
Exclusion: change content_ref, stable_ordinal, stable stat token, or captured_at outside the payload; hash remains the positive value.
```

### 5.3 `labpilot-untracked-manifest:v1`

**Purpose.** Prove the complete normalized set of untracked files required by Snapshot capture.

**Exact typed signature.**

```python
def untracked_manifest_hash(
    entries: Sequence[RepositoryManifestEntryV1],
    *,
    manifest_schema_version: Literal[1] = 1,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{"entries":[RepositoryManifestEntryV1...],"manifest_schema_version":1}
```

Every entry has `entry_kind="untracked_file"`; `original_relative_path`, `head_blob_oid`, and `index_blob_oid` are null. Regular and symlink entries require verified content for a successful Task 10 capture. Submodules and standalone directories are rejected as untracked file types. The input is an unordered set sorted by repository-entry base order; duplicate current paths and empty input are rejected. `manifest_schema_version=1` is explicit.

Excluded: content refs, ordinals, stat tokens, capture time, root path, Registration ID, ignore diagnostics, and scan duration.

Persisted fields: `RepositorySnapshotRow.untracked_manifest_hash` and `untracked_manifest_ref`; recomputation uses the canonical manifest blob. Consumers: Tasks 9, 10, and 11.

Independent oracle: SHA-256 over ASCII `labpilot-untracked-manifest:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"entries":[{"content_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","content_identity_status":"verified","entry_kind":"untracked_file","file_mode":"100644","file_type":"regular","head_blob_oid":null,"index_blob_oid":null,"original_relative_path":null,"relative_path":"notes.txt","size_bytes":5}],"manifest_schema_version":1}
Positive hash: 7979e0f46411717ba96992784c335b1366eb5f6aa8874e56c0283cb10f6a490b
Change: relative_path notes.txt -> notes-2.txt
Changed hash: a6686fc07e3ba6952b3b914efa41c0986bddc7e69abd56c4a51ac7b78a35b7f0
Exclusion: change content_ref, stable_ordinal, stable stat token, or captured_at outside the payload; hash remains the positive value.
```

### 5.4 `labpilot-repository-state:v1`

**Purpose.** Prove Git and working-tree content independently of a Registration.

**Exact typed signature.**

```python
def repository_state_digest(
    *,
    head_commit: GitObjectOid,
    tracked_tree_identity: GitObjectOid,
    is_clean: bool,
    dirty_worktree_manifest_hash: Sha256Hex | None,
    untracked_manifest_hash: Sha256Hex | None,
) -> Sha256Hex: ...
```

**Exact frozen payload; no additional key is permitted.**

```json
{
  "head_commit":"...",
  "tracked_tree_identity":"...",
  "is_clean":false,
  "dirty_worktree_manifest_hash":"...",
  "untracked_manifest_hash":"..."
}
```

`is_clean=true` requires both manifest hashes null. `is_clean=false` requires at least one non-null manifest hash. A category with no entries is null; an empty-manifest hash is never substituted. There is no collection, relative path, or payload version key. Namespace `:v1` carries the frozen version.

Excluded by frozen contract: Registration ID, Snapshot ID, branch, detached state, captured time, sequence, root path, submodule/LFS flags, compatibility version, inspector data, and errors.

Persisted field: `RepositorySnapshotRow.repository_state_digest`; recomputation uses `head_commit`, `tracked_tree_identity`, `is_clean`, and the two nullable manifest hashes. Consumers: Tasks 9, 10, and 11.

Independent oracle: SHA-256 over ASCII `labpilot-repository-state:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"dirty_worktree_manifest_hash":"3aef9eed6519a4ef138a7b12415460b83452fc66923c9a38c3e765c62b22b099","head_commit":"1111111111111111111111111111111111111111","is_clean":false,"tracked_tree_identity":"2222222222222222222222222222222222222222","untracked_manifest_hash":"7979e0f46411717ba96992784c335b1366eb5f6aa8874e56c0283cb10f6a490b"}
Positive hash: 655b62a092165453675ed5182c67a8854f162884a2a5ce4bece09a099ec68df8
Change: dirty_worktree_manifest_hash -> fb63b67a19a9f71b18cf0fabe97a9d41d97f3061bb7fd6c0f8c9647c945ac46d
Changed hash: 977af9c3409e2a7d872e374706e6d751801d6a22086136f89523bdcb36aa2de7
Exclusion: change captured_at, canonical_root_path, Snapshot sequence, or Registration ID outside the payload; hash remains the positive value.
```

### 5.5 `labpilot-repository-snapshot:v2`

**Purpose.** Bind one repository state to a Registration and exact branch/detached observation.

**Exact typed signature.**

```python
def repository_snapshot_identity(
    *,
    repository_registration_id: UUID,
    repository_state_digest: Sha256Hex,
    current_branch: str | None,
    is_detached_head: bool,
) -> Sha256Hex: ...
```

**Exact frozen payload; no additional key is permitted.**

```json
{
  "repository_registration_id":"<canonical UUID>",
  "repository_state_digest":"...",
  "current_branch":"main",
  "is_detached_head":false
}
```

`is_detached_head=true` requires `current_branch=null`; false requires a non-empty branch of at most 300 UTF-8 bytes. There is no collection, relative path, or payload schema key. Namespace `:v2` carries the frozen version.

Excluded by frozen contract: `captured_at`, sequence, canonical root, head/tree duplicated fields, dirty/untracked refs, submodule/LFS flags, inspector compatibility, Bundle identity, freshness, and error text.

Persisted field: `RepositorySnapshotRow.repository_snapshot_identity`; recomputation uses the Registration UUID, persisted state digest, branch, and detached flag. Consumers: Tasks 9-14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-snapshot:v2`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"current_branch":"main","is_detached_head":false,"repository_registration_id":"11111111-1111-4111-8111-111111111111","repository_state_digest":"655b62a092165453675ed5182c67a8854f162884a2a5ce4bece09a099ec68df8"}
Positive hash: d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12
Change: repository_registration_id -> 22222222-2222-4222-8222-222222222222
Changed hash: f69bb81b6c65844437735404188a507c6531e0d4bb3bbe34ca73319eec87d37c
Exclusion: change captured_at, snapshot_sequence, canonical_root_path, or compatibility flags outside the payload; hash remains the positive value.
```

### 5.6 `labpilot-repository-inspection-bundle-entry:v1`

**Purpose.** Prove one normalized, capture-complete dirty overlay entry.

**Exact typed signature.**

```python
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
) -> Sha256Hex: ...
```

The input type deliberately excludes `"unverified"`. Runtime calls that bypass static typing and supply `content_identity_status="unverified"` are rejected with `RepositoryIdentityInputInvalid(reason_code="bundle_entry_unverified")`.

**Exact payload.**

```json
{
  "repository_path":"...",
  "original_repository_path":null,
  "entry_kind":"tracked_overlay",
  "file_type":"regular",
  "file_mode":"100644",
  "head_blob_oid":null,
  "index_blob_oid":null,
  "content_hash":"...",
  "size_bytes":0,
  "content_identity_status":"verified",
  "entry_schema_version":1
}
```

Both path fields use section 2.3, and all strings use section 2.2A. There is no collection or caller-order meaning. `entry_schema_version=1` is explicit. The complete cross-field decision table is:

| Entry form | Required values | Rejected combinations |
|---|---|---|
| captured regular or symlink `tracked_overlay` | status `verified`; non-null `content_hash`; exact captured byte count `size_bytes >= 0`; regular mode `100644`/`100755` or symlink mode `120000` | null hash, `not_applicable`, submodule mode/type |
| captured regular or symlink `untracked_file` | same verified content rules; `original_repository_path=null`; both Git OIDs null | any Git OID, original path, `not_applicable`, submodule |
| `deletion` | status `not_applicable`; `content_hash=null`; `size_bytes=0`; original path null; at least one Git OID non-null; type/mode pair valid | `verified`, content hash, nonzero size, both OIDs null |
| pure regular-file `mode_change` | status `not_applicable`; regular type; mode `100644` or `100755`; `content_hash=null`; `size_bytes=0`; both Git OIDs non-null and equal; original path null | symlink/submodule, unequal/null OIDs, content bytes |
| gitlink/submodule `tracked_overlay` | `file_type="submodule"`; `file_mode="160000"`; `content_hash=null`; status `not_applicable`; `size_bytes=0`; at least one Git OID non-null | `verified`, `unverified`, any content hash, nonzero size, untracked kind |

No other `not_applicable` case is legal. Symlink `content_hash` is SHA-256 over captured normalized link-target bytes, never target-file bytes. The pre-capture dirty and untracked manifest object in section 2.4 may still express `unverified`; Task 10 must resolve every such regular/symlink entry to `verified`, and every legal metadata-only case to `not_applicable`, before calculating an Entry identity or persisting a Bundle/Snapshot.

Included: repository/original paths, kind, type, mode, Git OIDs, content hash, size, derived content-identity status, and entry schema. Excluded: `content_ref`, `stable_ordinal`, `StableStatToken`, Bundle/Snapshot database IDs, created time, and errors. A ref is a storage locator and cannot replace `content_hash`; ordinal is derived after sorting; stat tokens only detect concurrent mutation.

Persisted field: `RepositorySnapshotInspectionBundleEntryRow.entry_identity`; recomputation uses its persisted semantic columns plus the derived closed status and v1 constant. Consumers: Tasks 9, 10, and 12.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-bundle-entry:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","content_identity_status":"verified","entry_kind":"tracked_overlay","entry_schema_version":1,"file_mode":"100644","file_type":"regular","head_blob_oid":"1111111111111111111111111111111111111111","index_blob_oid":"2222222222222222222222222222222222222222","original_repository_path":null,"repository_path":"src/train.py","size_bytes":12}
Positive hash: b54af3f2dbf1cc74860579e003bab15cbf3eb1f2709cd6c35b94d0ca1e3e03ae
Change: file_mode 100644 -> 100755
Changed hash: ea61ccb353b0662aea0ca44c0ae917e7d979698017e8f1ff9ae7498b1de5ad1e
Exclusion: change content_ref, stable_ordinal, stable stat token, or entry database UUID outside the payload; hash remains the positive value.
Validation: status unverified is rejected; a gitlink is accepted only with mode 160000, null content_hash, not_applicable, and size 0.
```

#### 5.6A Approved additive contract: `labpilot-repository-inspection-bundle-entries:v1`

**Purpose.** Prove exactly the canonical Bundle Entry identity set stored in Task 9 `entries_manifest_hash`.

**Exact typed signature.**

```python
@dataclass(frozen=True, slots=True)
class BundleEntryManifestItemV1:
    repository_path: str
    entry_kind: EntryKind
    file_type: FileType
    entry_identity: Sha256Hex

def repository_inspection_bundle_entries_manifest_hash(
    entries: Sequence[BundleEntryManifestItemV1],
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{"ordered_entry_identities":["<64 lowercase hex>"]}
```

Input is an unordered set. Each path passes section 2.3. The function copies, validates, and sorts items by exactly:

```python
(
    item.repository_path.encode("utf-8"),
    item.entry_kind.encode("utf-8"),
    item.file_type.encode("utf-8"),
)
```

It does not sort by `entry_identity`. Duplicate repository paths or duplicate Entry identities are rejected; empty input is rejected; caller order has no meaning and is not mutated. The Task 9 `stable_ordinal` is the zero-based index in this same sorted list. The calling service verifies that each item's three sorting fields equal the corresponding persisted/captured Bundle Entry before hashing. The payload contains only the Entry identities in that path-derived order. It has no extra version key because this additive namespace fixes entries schema v1.

Excluded: Bundle ID, Snapshot ID, ordinals, entry row IDs, content refs, created time, base commit, capture tool, and Bundle schema.

Persisted field: `RepositorySnapshotInspectionBundleRow.entries_manifest_hash`; recomputation reads Task 9 Entry rows, validates/recomputes their `entry_identity`, constructs the four-field items, and applies the same path-derived order. Consumers: Tasks 9, 10, and 12.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-bundle-entries:v1`, LF, and the positive Canonical JSON bytes below. The identity for `README.md` begins `f6` and the identity for `a.py` begins `02`; identity sorting would reverse them, so the vector detects the prohibited hash order.

```text
Positive items in canonical order:
  ("README.md","tracked_overlay","regular","f6b16094e2f7d40ff072ad2fdd6a3987cc30461857bc0aced35600004cea4e18")
  ("a.py","tracked_overlay","regular","0215a8af8592b5ee90f77eedd61596d5d6a5fca9912fd5fa5ee92a77d95dd7c2")
Positive JSON: {"ordered_entry_identities":["f6b16094e2f7d40ff072ad2fdd6a3987cc30461857bc0aced35600004cea4e18","0215a8af8592b5ee90f77eedd61596d5d6a5fca9912fd5fa5ee92a77d95dd7c2"]}
Positive hash: 77b1cf5c0ab3590461dc8b01639aa1e225a80b65b70efa10053cebbb6d9b295f
Change: README.md Entry identity -> f3548551019ad27cbc694736db06d2c0a8a70e9b068600c1dcff3a249204698f
Changed JSON: {"ordered_entry_identities":["f3548551019ad27cbc694736db06d2c0a8a70e9b068600c1dcff3a249204698f","0215a8af8592b5ee90f77eedd61596d5d6a5fca9912fd5fa5ee92a77d95dd7c2"]}
Changed hash: f7547041857693baa2c6ed37ddad30386a772a4d5c4619b7d56f08a93c90b5a8
Exclusion: change row UUIDs, content refs, created_at, or caller input order; path order, ordinals, payload, and hash remain the positive values.
```
### 5.7 `labpilot-repository-inspection-bundle:v1`

**Purpose.** Bind the exact dirty entry set and capture profile to the exact Repository Snapshot.

**Exact typed signature.**

```python
def repository_inspection_bundle_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    base_commit_sha: GitObjectOid,
    entries_manifest_hash: Sha256Hex,
    bundle_schema_version: int,
    capture_tool_version: str,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{
  "repository_snapshot_identity":"...",
  "base_commit_sha":"...",
  "entries_manifest_hash":"...",
  "bundle_schema_version":1,
  "capture_tool_version":"wp1-a-git-observation:v1"
}
```

All fields are non-null. `bundle_schema_version` is positive and equals `1` for this contract; `capture_tool_version` is non-empty ASCII, at most 100 bytes. No collection or relative path appears directly. The Snapshot identity is included; `repository_state_digest` is excluded because the Snapshot already binds it and direct duplication creates two consistency sources.

Excluded: Bundle/Snapshot database IDs, state digest as a separate key, canonical root, content refs, entry ordinals, created time, and errors.

Persisted field: `RepositorySnapshotInspectionBundleRow.inspection_bundle_identity`; recomputation uses its `base_commit_sha`, `entries_manifest_hash`, `bundle_schema_version`, `capture_tool_version`, and the parent Snapshot's persisted identity. Consumers: Tasks 9, 10, and 12.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-bundle:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"base_commit_sha":"1111111111111111111111111111111111111111","bundle_schema_version":1,"capture_tool_version":"wp1-a-git-observation:v1","entries_manifest_hash":"77b1cf5c0ab3590461dc8b01639aa1e225a80b65b70efa10053cebbb6d9b295f","repository_snapshot_identity":"d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12"}
Positive hash: 5af2d5a55f9c76677a94bb18dd2fcc6524524ed42f6d18ec5da681601db5a702
Change: capture_tool_version v1 -> v2
Changed hash: c57ed760eb2352cb2b978a0f972f88c3e1ce2bf87ac9d7967807e6c27282f168
Exclusion: change created_at, Bundle UUID, entry ordinals, or direct state-digest metadata outside the payload; hash remains the positive value.
```

### 5.8 `labpilot-repository-evidence:v1`

**Purpose.** Prove one deterministic source observation in one exact Snapshot content view.

**Exact typed signature.**

```python
def repository_evidence_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    repository_path: str,
    evidence_kind: Literal[
        "file_presence", "manifest_declaration", "cli_argument",
        "function_definition", "main_guard", "config_key", "command_declaration",
        "metric_logging", "artifact_write", "readme_example", "comment_text"
    ],
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
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{
  "repository_snapshot_identity":"...",
  "repository_path":"...",
  "evidence_kind":"main_guard",
  "line_start":10,
  "line_end":10,
  "structural_locator":"python-ast:If:10:0",
  "symbol_name":null,
  "file_mode":"100644",
  "content_hash":"...",
  "excerpt_hash":"...",
  "declared_or_inferred":"deterministic_inference",
  "inspector_identity":{"inspector_name":"python_static_signals","inspector_version":"v1"},
  "evidence_schema_version":1
}
```

`repository_path` uses section 2.3. Every string uses section 2.2A; enum/name/locator fields reject NUL, isolated surrogates, LF, Tab, and CR. Line fields are both null or both positive with `line_end >= line_start`. `structural_locator` and `symbol_name` are null or non-empty valid UTF-8 bounded to 500 bytes. `file_presence` may have no line range and no locator; every other kind requires at least one of them. `content_hash` is always non-null. For regular files it is SHA-256 of captured file bytes; for symlinks it is SHA-256 of normalized link-target bytes. For a gitlink `file_presence` Evidence it is exactly `sha256(b"gitlink\0" + object_oid.encode("ascii")).hexdigest()` and the structural locator is `gitlink:<object_oid>`; this proves Git metadata, not submodule contents. For a Git LFS pointer it is SHA-256 of the captured pointer-file bytes and does not prove the remote LFS object. `excerpt_hash` is null exactly when no excerpt blob is captured. `evidence_schema_version=1` is explicit. There is no collection or caller-order meaning inside the Evidence object.

The Snapshot semantic identity, not the Snapshot database UUID, is included. This prevents reuse across different Snapshot semantics while preserving recomputation from persisted authority. `excerpt_ref` is excluded because storage location cannot replace excerpt content identity.

Excluded: `repository_evidence_id`, Snapshot database ID, `excerpt_ref`, captured time, row/manifest ordinals, Result/Run IDs, diagnostics, confidence, rationale, and Agent IDs.

Persisted field: `RepositoryEvidenceRow.repository_evidence_identity`; recomputation uses the parent Snapshot identity and all persisted Evidence fields except `excerpt_ref`/`captured_at`, with inspector name/version encoded as the nested object. Consumers: Tasks 13 and 14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-evidence:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"content_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","declared_or_inferred":"deterministic_inference","evidence_kind":"main_guard","evidence_schema_version":1,"excerpt_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","file_mode":"100644","inspector_identity":{"inspector_name":"python_static_signals","inspector_version":"v1"},"line_end":10,"line_start":10,"repository_path":"src/train.py","repository_snapshot_identity":"d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12","structural_locator":"python-ast:If:10:0","symbol_name":null}
Positive hash: d7f05cbeac16a6202123f775be3e2e29f8c08394ec4ffc1d1aebb235d080b5b8
Change: line range and structural locator move from line 10 to line 11
Changed hash: c417437863d189fff1f88986552d0a371ac04d5decc703f6b0722adf56280144
Exclusion: change Evidence UUID, Snapshot database UUID, excerpt_ref, captured_at, or manifest ordinal outside the payload; hash remains the positive value.
```

### 5.9 `labpilot-repository-evidence-manifest-entries:v1`

**Purpose.** Prove only the canonical Evidence identity set.

**Exact typed signature.**

```python
def repository_evidence_manifest_entries_hash(
    evidence_identities: Sequence[Sha256Hex],
    *,
    entries_schema_version: Literal[1] = 1,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{"ordered_evidence_identities":["..."],"entries_schema_version":1}
```

Input is an unordered set sorted by lowercase identity ASCII bytes; duplicates are rejected; caller order has no meaning; `[]` is allowed and distinct from null. Relative paths are bound inside each Evidence identity. `entries_schema_version=1` is explicit.

Excluded: Snapshot, Result, Manifest/Evidence database IDs, ordinals, timestamps, and Evidence row order.

Persisted field: `RepositoryEvidenceManifestRow.entries_manifest_hash`; Task 14 also persists the exact ordered identities in join-table `entry_identity` with ordinals derived from this sorted list. Consumers: Tasks 13 and 14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-evidence-manifest-entries:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"entries_schema_version":1,"ordered_evidence_identities":["d7f05cbeac16a6202123f775be3e2e29f8c08394ec4ffc1d1aebb235d080b5b8"]}
Positive hash: 9dcf8c6647b3d8367e78e5f434d6e450959e221fc7f30cb9cf8d83699fdb9d93
Change: add ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
Changed hash: ddbc6328b21b53794c7b3ec8ff88a273b5ed57dc0c206102cbb45232e7fc7670
Exclusion: change stable ordinals, Evidence database UUIDs, captured_at, or join-row insertion order; hash remains the positive value.
```

### 5.10 `labpilot-repository-evidence-manifest:v1`

**Purpose.** Bind one Evidence identity set to one exact Snapshot and Inspection Result.

**Exact typed signature.**

```python
def repository_evidence_manifest_hash(
    *,
    repository_snapshot_identity: Sha256Hex,
    inspection_result_hash: Sha256Hex,
    entries_manifest_hash: Sha256Hex,
    manifest_schema_version: int,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{
  "repository_snapshot_identity":"...",
  "inspection_result_hash":"...",
  "entries_manifest_hash":"...",
  "manifest_schema_version":1
}
```

All fields are non-null; `manifest_schema_version` is positive and equals `1` here. There is no direct collection or path. The entries hash and full manifest hash are different authoritative hashes and are never aliases.

Excluded: Manifest, Result, Snapshot, Evidence, Run, and join-row database IDs; ordinals; created/captured times; and Event IDs.

Persisted field: `RepositoryEvidenceManifestRow.manifest_hash`; recomputation uses the persisted parent Snapshot identity, `inspection_result_hash`, `entries_manifest_hash`, and `manifest_schema_version`. Consumers: Tasks 13 and 14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-evidence-manifest:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"entries_manifest_hash":"9dcf8c6647b3d8367e78e5f434d6e450959e221fc7f30cb9cf8d83699fdb9d93","inspection_result_hash":"9999999999999999999999999999999999999999999999999999999999999999","manifest_schema_version":1,"repository_snapshot_identity":"d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12"}
Positive hash: 6d5b500cc15135b38e8a9ac84d7e499723d6dcfed242d64bdc3b6f8189f16af5
Change: inspection_result_hash 9...9 -> 8...8
Changed hash: 5e6842427e78b4daad8665545b52c48844bb66e697ff9f84fb0b524b2b7a4961
Exclusion: change Manifest/Result/Snapshot row UUIDs, evidence ordinals, or created_at outside the payload; hash remains the positive value.
```

### 5.11 `labpilot-inspector-registry:v1`

**Purpose.** Prove the exact required and optional deterministic inspector profile.

**Exact typed signature.**

```python
def inspector_registry_identity(
    *,
    profile_id: str,
    required_inspectors: Sequence[InspectorIdentity],
    optional_inspectors: Sequence[InspectorIdentity],
    registry_schema_version: int,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{
  "profile_id":"wp1-a-default:v1",
  "required_inspectors":[{"inspector_name":"...","inspector_version":"..."}],
  "optional_inspectors":[],
  "registry_schema_version":1
}
```

`profile_id` matches `^[a-z0-9][a-z0-9._-]*:v[1-9][0-9]*$` and is at most 100 ASCII bytes. Inspector values use section 4.1. Required and optional inputs are two separate unordered sets, each sorted by `(inspector_name UTF-8 bytes, inspector_version UTF-8 bytes)`. Duplicates within a category are rejected. The same InspectorIdentity in both categories is rejected. Required is non-empty; optional empty is `[]`; categories are never merged. `registry_schema_version=1` is explicit.

Initial profile `wp1-a-default:v1` contains exactly required `file_inventory:v1`, `project_manifests:v1`, and `python_static_signals:v1`, with optional `[]`.

Excluded: registry object IDs, code import order, registration order, timestamps, inspector instances, configuration objects, and execution state.

Persisted field: `RepositoryInspectionRunRow.inspector_registry_identity`; recomputation uses `inspector_profile_id`, `inspector_registry_schema_version`, persisted required identities, and the versioned registry definition. The v1 default optional set is exactly empty. A future profile with non-empty optional inspectors requires an explicit persisted optional-set column before it can be an authoritative run profile. Consumers: Tasks 12-14.

Independent oracle: SHA-256 over ASCII `labpilot-inspector-registry:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"optional_inspectors":[],"profile_id":"wp1-a-default:v1","registry_schema_version":1,"required_inspectors":[{"inspector_name":"file_inventory","inspector_version":"v1"},{"inspector_name":"project_manifests","inspector_version":"v1"},{"inspector_name":"python_static_signals","inspector_version":"v1"}]}
Positive hash: b79266a491a075c01bf4311b6ac743ab0362314c2bb906eb7d572f2bc7467eff
Change: python_static_signals inspector_version v1 -> v2
Changed hash: dcc20132a782d04f4e89aca16faacf3378e479a08da18f40a0781eaa1f234e35
Exclusion: permute caller input, registry construction order, or timestamps; canonical arrays and hash remain the positive values.
```

#### 5.11A Approved additive contract: `labpilot-repository-inspection-limits-profile:v1`

**Purpose.** Prove the exact deterministic resource limits that bound a Task 12 inspection and guarantee that every authorized canonical Result and Evidence-identity manifest remains below the accepted Canonical JSON V1 limits.

**Exact typed profile and signature.**

```python
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

def inspection_limits_profile_identity(
    profile: InspectionLimitsProfileV1,
) -> Sha256Hex: ...
```

**Exact payload.**

```json
{
  "limits_profile_id":"wp1-a-default-limits:v1",
  "max_files":2048,
  "max_file_bytes":1048576,
  "max_total_bytes":67108864,
  "max_ast_nodes_per_file":100000,
  "max_total_ast_nodes":500000,
  "max_structural_depth":64,
  "max_candidates_per_collection":128,
  "max_total_candidates":512,
  "max_evidence_identities_per_candidate":16,
  "max_evidence_entries":4096,
  "max_manifest_entries":4096,
  "max_unresolved_questions":128,
  "max_candidate_values_per_question":16,
  "max_result_canonical_bytes":4000000,
  "profile_schema_version":1
}
```

This object is the initial `wp1-a-default-limits:v1` profile; every displayed number is normative. The profile ID uses section 5.11 grammar and section 2.2A control policy. Every numeric field requires exact `int`, rejects `bool`, and is greater than zero.

The profile validator enforces all of these static invariants:

```text
max_file_bytes <= max_total_bytes
max_ast_nodes_per_file <= max_total_ast_nodes
max_files <= max_evidence_entries <= max_manifest_entries <= 10000
max_candidates_per_collection <= 10000
max_total_candidates <= 8 * max_candidates_per_collection
max_evidence_identities_per_candidate <= 10000
max_unresolved_questions <= 10000
max_candidate_values_per_question <= 10000
max_result_canonical_bytes <= 8000000
```

It also computes this conservative Result node upper bound:

```text
32
+ max_total_candidates * (8 + max_evidence_identities_per_candidate)
+ max_unresolved_questions * (
    8
    + 3 * max_candidate_values_per_question
    + max_evidence_identities_per_candidate
  )
```

The profile is invalid unless that bound is at most `90000`, leaving at least 10,000 Canonical JSON nodes of headroom below the accepted `max_total_nodes=100000`. For the initial profile the bound is `21536`.

The inspection producer enforces, before persistence:

- no source scan exceeds `max_files`, `max_file_bytes`, or `max_total_bytes`;
- no Python file or complete inspection exceeds the per-file or total AST-node budgets;
- no collection exceeds `max_candidates_per_collection`, and the sum of all eight candidate collections does not exceed `max_total_candidates`;
- no candidate or unresolved question exceeds `max_evidence_identities_per_candidate`;
- no question exceeds `max_candidate_values_per_question`;
- total Evidence rows and the ordered Evidence manifest do not exceed `max_evidence_entries` and `max_manifest_entries`;
- the final canonical Result payload is encoded once and its byte length must be at most `max_result_canonical_bytes` before hashing or persistence.

The final byte check is part of domain validation, not merely a fallback to the encoder's 8 MB exception. Any count, node, AST, byte, or manifest limit violation raises `InspectionResourceLimitExceeded`; the Task 14 atomic workflow persists no completed Run, Result, Evidence, Manifest, Event, Outbox, or Receipt.

There is no collection, nullable field, relative path, duplicate policy, or caller-order meaning inside the profile. All keys are identity inputs; `profile_schema_version=1` is explicit.

Excluded: operational cancellation tokens, wall-clock deadlines, worker count, process IDs, host capacity, transient retry counts, error text, timestamps, and database IDs.

Persisted fields: Task 13 must persist both `RepositoryInspectionRunRow.inspection_limits_profile_identity` and an immutable `inspection_limits_profile_json JSONB` containing exactly this payload. Recomputation hashes that JSONB after exact typed validation; the Request references the recomputed identity. This one-column JSONB addition is an approved plan clarification, not an implementation in this document. Consumers: Tasks 12-14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-limits-profile:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"limits_profile_id":"wp1-a-default-limits:v1","max_ast_nodes_per_file":100000,"max_candidate_values_per_question":16,"max_candidates_per_collection":128,"max_evidence_entries":4096,"max_evidence_identities_per_candidate":16,"max_file_bytes":1048576,"max_files":2048,"max_manifest_entries":4096,"max_result_canonical_bytes":4000000,"max_structural_depth":64,"max_total_ast_nodes":500000,"max_total_bytes":67108864,"max_total_candidates":512,"max_unresolved_questions":128,"profile_schema_version":1}
Positive hash: 8e26df5c1ec65da59ee4e6b923ee3a87cd9e35721f58c78c8b41533f194ad28b
Change: max_file_bytes 1048576 -> 2097152
Changed hash: 3d6441ffcbcfbfbb57a6836e54b21f4a194769d0694661807abdc00dcd4d5ff1
Exclusion: change deadline, worker count, host capacity, retry count, row UUID, or timestamp outside the payload; hash remains the positive value.
Validation: inserting NUL into limits_profile_id raises PersistedTextInvalid(reason_code="nul") before hashing; a profile whose Result node upper bound exceeds 90000 is rejected before hashing.
```

### 5.12 `labpilot-repository-inspection-request:v1`

**Purpose.** Prove the exact immutable Snapshot, inspector profile/registry, limits profile, and request schema selected by the server.

**Exact typed signature.**

```python
def repository_inspection_request_identity(
    *,
    repository_snapshot_identity: Sha256Hex,
    inspector_profile_id: str,
    inspector_registry_identity: Sha256Hex,
    inspection_limits_profile_identity: Sha256Hex,
    request_schema_version: int,
) -> Sha256Hex: ...
```

**Exact payload keys.**

```json
{
  "repository_snapshot_identity":"...",
  "inspector_profile_id":"wp1-a-default:v1",
  "inspector_registry_identity":"...",
  "inspection_limits_profile_identity":"...",
  "request_schema_version":1
}
```

All fields are non-null. Profile grammar is section 5.11; hashes use section 2.2; `request_schema_version=1` is explicit. There is no collection, relative path, or caller-order meaning. The limits-profile identity is server-computed from the exact additive profile in section 5.11A, independently recomputed from the persisted immutable profile JSON, and persisted with the Request inputs.

The caller supplies a Snapshot database ID and allowed profile selection through the Task 14 command. The caller never supplies or overrides the final request hash, Snapshot identity, registry identity, limits identity, or version.

Excluded: Snapshot/Run database IDs, request/result row IDs, start/completion time, actor, idempotency key, status, candidates, and final hash supplied by a client.

Persisted field: `RepositoryInspectionRunRow.inspection_request_identity`; every payload field is persisted on the Run or resolved through its Snapshot, registry definition, and immutable `inspection_limits_profile_json`. Consumers: Tasks 13 and 14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-request:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"inspection_limits_profile_identity":"8e26df5c1ec65da59ee4e6b923ee3a87cd9e35721f58c78c8b41533f194ad28b","inspector_profile_id":"wp1-a-default:v1","inspector_registry_identity":"b79266a491a075c01bf4311b6ac743ab0362314c2bb906eb7d572f2bc7467eff","repository_snapshot_identity":"d826d6595eb4fa1bdd76465cf5602da9acb6b74d010d687ae9972fa025d48f12","request_schema_version":1}
Positive hash: 4ae56685557d956e8755ef91d56c93d12f2b9576ddb1be57a1182f3f0a45dc3f
Change: inspection_limits_profile_identity -> 3d6441ffcbcfbfbb57a6836e54b21f4a194769d0694661807abdc00dcd4d5ff1
Changed hash: a51cdded8b170f93d1ae41708461021e94e11c0c47966f8cbc8f99f09f33894d
Exclusion: change Run UUID, started_at, idempotency key, or a client-supplied hash field; reconstructed payload and hash remain the positive values.
```

### 5.13 `labpilot-repository-inspection-result:v1`

**Purpose.** Prove one immutable deterministic Inspector result, including every persisted candidate collection, unresolved question, and completed Inspector identity.

**Exact typed signature.**

```python
def repository_inspection_result_hash(
    *,
    inspection_request_identity: Sha256Hex,
    result_status: Literal["complete", "partial", "ambiguous", "failed"],
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
) -> Sha256Hex: ...
```

**Exact payload keys.**

```json
{
  "inspection_request_identity":"...",
  "result_status":"complete",
  "candidate_entrypoints":[],
  "configuration_candidates":[],
  "metric_candidates":[],
  "artifact_candidates":[],
  "dataset_reference_candidates":[],
  "environment_candidates":[],
  "detected_commands":[],
  "unresolved_questions":[],
  "completed_inspector_identities":[],
  "result_schema_version":1
}
```

A non-empty unresolved question encodes each alternative as the exact two-key object from section 4.2, for example `{"value_kind":"repository_path","value":"src/train.py"}`. Raw candidate-value strings are invalid under result schema v1 as closed by this supplement.

Candidate object keys and field types are exactly section 4.2. Each collection is an unordered set normalized with the collection-specific sort key in section 4.2; duplicate semantic keys are rejected; all eight empty collections are `[]`, never null. Nested Evidence sets and typed unresolved candidate-value sets follow section 2.5. Completed inspectors form a separate unordered set sorted by InspectorIdentity encoding; duplicates are rejected. Caller order has no meaning, and caller collections are not mutated. Every path uses section 2.3 and every persisted string uses section 2.2A; a NUL in any candidate, typed question value, inspector, or status string is rejected before hashing. The active Limits Profile bounds every collection, nested Evidence set, typed value set, total candidate count, total Evidence count, Result node upper bound, and final canonical Result byte length.

`result_schema_version=1` is explicit. Hash decoding recognizes the approved persisted enum `complete|partial|ambiguous|failed`. The initial WP1-A producer has exactly two authorized emissions: `complete` requires the completed Inspector set to equal the referenced registry's required set and `unresolved_questions=[]`; `ambiguous` requires the same complete required set and a non-empty unresolved-question collection. Both authorized Results map to `RepositoryInspectionRunRow.status="completed"`; domain ambiguity never maps the Run to `partial`. `partial` and `failed` remain decodable values reserved by the approved database constraint but have no authorized Task 12-14 creation transition in the initial profile. An inspector exception, resource-limit exception, corruption, cancellation, or incomplete required scan rolls back and leaves no Run/Result/Evidence/Manifest rows; it is never converted to `partial` or `failed`. A future producer for either reserved value requires an approved result-schema/profile revision before first persistence.

Excluded: Inspection Result/Run/Snapshot database IDs, result sequence, base/supersedes IDs, created/started/completed times, row version, Event IDs, Evidence database IDs, stable ordinals, confidence, rationale, alternative free text, Agent IDs, user confirmation, and WP1-B/C inference fields.

Persisted field: `RepositoryInspectionResultRow.result_hash`. Recompute from `inspection_request_identity` on the Run; exact JSONB arrays on the Result; completed Inspector JSON on the Run; and `result_schema_version`. Consumers: Tasks 13 and 14.

Independent oracle: SHA-256 over ASCII `labpilot-repository-inspection-result:v1`, LF, and the positive Canonical JSON bytes below.

```text
Positive JSON: {"artifact_candidates":[],"candidate_entrypoints":[{"entrypoint_kind":"python_main_guard","evidence_identities":["d7f05cbeac16a6202123f775be3e2e29f8c08394ec4ffc1d1aebb235d080b5b8"],"repository_path":"src/train.py","symbol_name":null}],"completed_inspector_identities":[{"inspector_name":"file_inventory","inspector_version":"v1"},{"inspector_name":"project_manifests","inspector_version":"v1"},{"inspector_name":"python_static_signals","inspector_version":"v1"}],"configuration_candidates":[],"dataset_reference_candidates":[],"detected_commands":[],"environment_candidates":[],"inspection_request_identity":"4ae56685557d956e8755ef91d56c93d12f2b9576ddb1be57a1182f3f0a45dc3f","metric_candidates":[],"result_schema_version":1,"result_status":"complete","unresolved_questions":[]}
Positive hash: 6534b765e3e9e297859b0853bb6cd5e6bc52ff356579c1c2b1412a1313ef36a0
Change: candidate_entrypoints[0].repository_path src/train.py -> src/eval.py
Changed hash: 278a12ac56cb5fa97cb7642fede55034ee4ca2ff14a86073a628e557158402d4
Exclusion: change Result/Run/Snapshot row UUIDs, created_at, result_sequence, Evidence row UUIDs, or ordinals outside the payload; hash remains the positive value.
```

## 6. Content Store boundary: unchanged

This supplement proposes no Content Store API, ref, layout, or durability design change; it makes validation and verification equality explicit.

Public API:

```python
async def put_bytes(self, value: bytes) -> str: ...
async def read_bytes(self, ref: str) -> bytes: ...
async def verify(self, ref: str, expected_hash: str) -> None: ...
```

`ref` fullmatches `sha256:[0-9a-f]{64}`. `expected_hash` fullmatches `[0-9a-f]{64}`; uppercase, prefixes, whitespace, short/long strings, NUL, and non-hex characters are malformed. The storage path remains:

```text
<root>/sha256/<first-two-hex>/<remaining-hex>
```

The required equality is exact:

```text
digest embedded in ref == expected_hash == sha256(actual stored bytes).hexdigest()
```

`read_bytes` validates the ref, reads the complete blob, recomputes SHA-256 on the returned bytes every time, compares it with the ref digest, and returns only verified bytes. `verify` validates both inputs, first requires ref digest equal to `expected_hash`, then requires the actual-byte digest equal to both. `put_bytes` derives the ref from actual bytes and verifies an existing same-ref blob before reuse.

Stable exception mapping is:

| Condition | Exception and stable reason code |
|---|---|
| malformed ref | `ContentReferenceInvalid(reason_code="invalid_ref")` |
| malformed expected hash | `ContentHashInvalid(reason_code="invalid_expected_hash")` |
| valid ref, blob absent | `ContentBlobMissing(reason_code="blob_missing")` |
| ref digest differs from expected hash | `ContentStoreCorruption(reason_code="reference_expected_hash_mismatch")` |
| actual-byte digest differs from ref/expected | `ContentStoreCorruption(reason_code="blob_digest_mismatch")` |

The durability sequence is unchanged:

```text
same-directory temp
-> complete write
-> flush
-> fsync(temp)
-> close temp
-> os.replace
-> open parent directory
-> fsync(parent)
-> close parent
-> verify published bytes
-> return ref
```

A content ref is a content-identity reference, not a mutable filesystem path. An existing blob is verified before reuse. A damaged existing blob is not silently overwritten. A partial temporary file never becomes the final blob. Database rollback may leave an unreferenced valid blob for later garbage collection, but a persisted ref must resolve and verify. Required stable errors therefore include `ContentReferenceInvalid`, `ContentHashInvalid`, `ContentBlobMissing`, and `ContentStoreCorruption`.
## 7. Compatibility and downstream matrix

| Contract | Task 6 function | Task 7-14 consumer | Persisted columns | Recomputation source | Migration compatibility | Excluded mutable fields |
|---|---|---|---|---|---|---|
| Valid persisted text | `validate_persisted_text` | 7-14 | every persisted string named below | stored exact string + field policy | validation tightening rejects new invalid writes; no identity payload grows | trimming, host locale, Unicode rewriting |
| Registration/root | `repository_registration_identity` | 7, 8 | Registration `root_path_identity` | canonical root path + repository type | namespace v1; DB composite binds Project | status, remote, branch, timestamps, row UUID |
| Dirty manifest | `dirty_worktree_manifest_hash` | 9-11 | Snapshot dirty hash/ref | verified canonical manifest blob, including pre-capture status | payload schema 1; new shape requires new namespace | ref, ordinals, stat tokens, time |
| Untracked manifest | `untracked_manifest_hash` | 9-11 | Snapshot untracked hash/ref | verified canonical manifest blob, including pre-capture status | payload schema 1; new shape requires new namespace | ref, ordinals, stat tokens, time |
| State | `repository_state_digest` | 9-11 | Snapshot state digest | exact five persisted frozen fields | frozen namespace v1; payload cannot grow | Registration, branch, time, flags |
| Snapshot | `repository_snapshot_identity` | 9-14 | Snapshot identity | exact four persisted frozen fields | frozen namespace v2; payload cannot grow | time, sequence, root, flags, Bundle |
| Bundle Entry | `repository_inspection_bundle_entry_identity` | 9, 10, 12 | Entry `entry_identity` | persisted metadata + closed derived verified/not-applicable status + v1 constant | new entry shape/status rule requires new namespace | ref, ordinal, stat token, row UUID |
| Bundle entry set | `repository_inspection_bundle_entries_manifest_hash` | 9, 10, 12 | Bundle `entries_manifest_hash` | persisted Entry sorting fields + recomputed identities, path/kind/type order | approved additive namespace; implementation remains subject to the approved-contract installation gate | ordinals, row IDs, refs, time |
| Bundle | `repository_inspection_bundle_identity` | 9, 10, 12 | Bundle identity | Bundle row + parent Snapshot identity | bundle schema and tool version persisted | row IDs, state duplicate, time |
| Evidence | `repository_evidence_identity` | 13, 14 | Evidence identity | Evidence row + parent Snapshot identity | EvidenceKind adds `command_declaration` as Task 12 clarification; schema remains v1 only if approved before first write | row IDs, refs, time, confidence |
| Evidence entry set | `repository_evidence_manifest_entries_hash` | 13, 14 | Manifest entries hash + join entry identities | sorted join `entry_identity` values + entries schema | entries schema version persisted | ordinals, row IDs, insertion order |
| Evidence full manifest | `repository_evidence_manifest_hash` | 13, 14 | Manifest `manifest_hash` | Snapshot identity + Result hash + entries hash + schema | manifest schema version persisted | all database IDs and timestamps |
| Inspector registry | `inspector_registry_identity` | 12-14 | Run registry identity/schema/profile/required JSON | persisted Run + versioned registry; v1 optional set empty | non-empty optional set requires persisted optional JSON | import order, instances, time |
| Inspection limits profile | `inspection_limits_profile_identity` | 12-14 | Run limits identity + approved immutable `inspection_limits_profile_json` | exact persisted profile JSON + Canonical JSON V1 budget validation | approved additive namespace/JSONB clarification; profile change changes identity | deadline, host, workers, retries, IDs, time |
| Inspection request | `repository_inspection_request_identity` | 13, 14 | Run request identity and five input values | Run + parent Snapshot + registry + limits JSON | request schema version persisted | row IDs, actor, idempotency key, time |
| Inspection result | `repository_inspection_result_hash` | 13, 14 | Result hash/schema/eight JSONB arrays + Run request/completed JSON | persisted canonical arrays, typed unresolved values, active limits profile, and Run values | result schema version persisted; shape changes require new namespace/version | row IDs, sequence, time, Agent inference |

Task-specific checks:

- Task 7 `root_path_identity` is exactly section 5.1.
- Task 9 `entries_manifest_hash` is the additive section 5.6A, ordered by Entry repository path/kind/type, not by identity and not by an unnamespaced digest.
- Task 9 Bundle Entry `entry_identity` is section 5.6 and cannot represent `unverified`.
- Task 10 `CapturedBundleEntry.entry_identity` is recomputed before persistence; its `stable_ordinal` is the zero-based section 5.6A order; `content_ref` and `stable_stat_token` remain outside identity.
- Task 12 `InspectorIdentity` is a two-key object, not a colon-joined string; section 4.3 owns every initial deterministic parser and section 5.8 supplies `command_declaration`.
- Task 12-14 limits are frozen by section 5.11A; Task 13 needs the approved immutable profile JSON to make that identity independently recomputable, and every legal Result must satisfy the profile node/count/byte invariants before hashing.
- Task 12 gitlink and LFS pointer Evidence follows sections 4.3 and 5.8; gitlink metadata identity never claims materialized submodule bytes, and LFS Evidence hashes pointer bytes only.
- Task 13 Evidence fields reconstruct section 5.8; structural locator is included and excerpt ref is excluded.
- Task 13 Result JSONB fields persist the exact eight canonical arrays in section 5.13.
- Task 14 Result, Evidence entries, and full Manifest formulas are three different hashes with three different namespaces.

## 8. Approved additive contracts and plan clarifications

### 8.1 Approved additive namespaces

```text
labpilot-repository-inspection-bundle-entries:v1
labpilot-repository-inspection-limits-profile:v1
```

These are exactly two approved additive contracts. Neither is represented as present in the approved plan; their authority comes from this approved supplement. They may be implemented only after the approved-contract installation gate completes.

### 8.2 Approved downstream plan clarifications

1. Task 9 must name section 5.6A as the source of `entries_manifest_hash`, carry repository path/kind/type with each input identity, and derive `stable_ordinal` from that same canonical order.
2. Task 12 must adopt the section 4.3 ownership matrix, `command_declaration`, `unsupported_source_dependency`, gitlink/LFS content-identity rules, wildcard `environment*.yml|yaml` ownership, and typed unresolved candidate values. This resolves initial ownership without overlapping semantic content parsers.
3. Task 13 must add immutable `inspection_limits_profile_json JSONB` to the Run contract, containing exactly section 5.11A's payload, so its already-planned identity column is independently recomputable; its Result JSONB contract must persist typed unresolved candidate-value objects.
4. Task 13 does not persist an optional Inspector identity set. This is exact for `wp1-a-default:v1` because its optional set is empty. A future non-empty optional set requires an immutable optional-inspector JSON column and a new registry profile before use.

The approved plan does not yet name the two additive namespaces, the expanded source-owner matrix, `command_declaration`, `unsupported_source_dependency`, typed unresolved values, gitlink/LFS content-identity rules, or the limits-profile JSONB. Those are explicit authority gaps closed by this approved supplement, not unresolved internal identity formulas. No contradiction remains between this supplement's State/Snapshot payloads and the frozen architecture. This document changes no code by itself; Task 6 implementation authorization activates only after the approved-contract installation gate completes.

## 9. Review assertions

- The thirteen plan-defined namespaces and two clearly marked approved additive namespaces appear with exact typed signatures, payloads, ordering or no-collection statement, duplicate policy, nullable policy, version treatment, exclusions, persisted consumers, and positive/change/exclusion vectors.
- The total contract count is fifteen: thirteen plan-defined plus two approved additive contracts.
- The State payload has exactly five keys and no schema-version key.
- The Snapshot payload has exactly four keys and no schema-version key.
- Bundle Entry identity rejects `unverified`; pre-capture dirty/untracked manifests retain it.
- Bundle entry ordering and `stable_ordinal` use repository path, entry kind, and file type UTF-8 bytes.
- Evidence entries hash and full Evidence Manifest hash are distinct.
- Every persisted string rejects NUL and isolated surrogates; LF/Tab/CR policy is field-specific in section 2.2A.
- Content Store verification requires ref digest, expected hash, and actual-byte digest equality.
- The initial Limits Profile is strictly below Canonical JSON V1 container/node/output limits; the conservative Result node bound is 21536 and the final Result byte cap is 4000000.
- Unresolved candidate values are typed objects; no field-dependent validation remains.
- Gitlinks, LFS pointers, supported binaries, and resource-limit failures have explicit ownership and evidence/question behavior.
- `complete` and `ambiguous` Results both persist `InspectionRun.status=completed`.
- All golden hashes in sections 5.1-5.13 and both additive sections were independently recomputed with Python standard-library JSON/SHA-256 code that imports no LabPilot module.
- This v0.3.1 document is an Approved Implementation Contract. It grants no immediate execution authorization; Task 6 remains blocked until the approved-contract installation gate completes.