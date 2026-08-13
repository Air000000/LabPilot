# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Stop at the review/authorization gates defined below.

**Planning/design baseline:** `9b9cc7c9d29a72cf8b568316febc7b0cc6d741ab`

**Accepted Slice 1 implementation baseline:** `ff7909503c3b755bffb564423de3a56ce5bb9b4a`

**R0 authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**S2.0 design spec:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md`

**Status:** Draft r2 for final mechanical re-review. In this revision, only **Task 1 / S2.0** is specified at execution precision. Task 1 remains **NOT AUTHORIZED** until the r2 finding-specific recheck passes. Tasks 2–5 are named, bounded future task slots and require an ADR-guided amendment of this same plan before implementation.

**Goal:** Prove a real native-Linux supervision contract first, then use that evidence to build one controlled path from an approved `ExperimentVersion` through exact source materialization, real training, restart reconciliation, and no duplicate physical launch.

**Architecture:** Slice 2 is stage-gated at the physical-execution boundary. Task 1 is an evidence-only native-Linux/systemd PoC that must select one supervision topology through a short ADR before production Batch/Trial/Attempt execution semantics are frozen. PostgreSQL remains the future durable execution ledger, the selected OS supervisor is the physical observation authority, and Coordinator reconciliation is the only owner that combines durable expectation with physical evidence.

**Tech Stack for Task 1:** Python 3.12, pytest, standard-library `subprocess`/`json`/`hashlib`/filesystem APIs, native Linux, systemd/systemctl/systemd-run/journalctl, existing `uv`/Ruff/mypy toolchain. PostgreSQL/SQLAlchemy/Alembic are deliberately not part of Task 1.

## Review corrections incorporated

This r2 preserves the detailed Task 1 execution plan and closes the focused findings accumulated during r1 review:

```text
F1. H covers every caller-controlled behavior-changing physical launch semantic.
F2. A1 remains a required baseline experiment but is not a final selectable disposition.
F3. The systemd PoC freezes the manager/service launch profile instead of letting Codex choose it.
F4. Positive creation authority is mechanically present on the creation-capable API.
F5. P11 is a scenario-level reboot result; no fifth PhysicalObservation value is invented.
I1. spec.runtime_path is the sole runtime executable authority; SystemdCli has no second executable input.
I2. Creation has an exact preflight: PRESENT / CONFIRMED_ABSENT / LOOKUP_UNKNOWN, and only CONFIRMED_ABSENT + positive creation evidence may create.
I3. A1 live X/H binding is exact: unit name binds X and systemd Description binds H.
I4. P0 characterizes the entire frozen systemd feature/authorization profile before native scenarios; missing required capability is HOST_CAPABILITY_GAP, never silent downgrade.
P1. Task 1 remains one formal task, with three internal execution batches rather than task proliferation.
```

---

# 0. Global constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > approved Slice 2 plan / selected S2.0 ADR`.
- Roadmap §6.2 calls the Runner PoC `S2.1`; this plan uses the already-approved design label `S2.0` for the same pre-production gate. This is naming only; no extra capability is introduced.
- S2.0 authoritative recovery/reboot evidence runs on **native Linux + systemd**. WSL2 is valid for ordinary development and deterministic harness tests, but not authoritative for host reboot semantics.
- Task 1 is one formal Codex task. Internal batches/checkpoints and P0–P13 scenarios are not separate tasks, commits, or governance closures unless execution exposes a real new scope boundary.
- Task 1 must not create or modify production `src/labpilot/**`, migrations, PostgreSQL schema, API routes, Coordinator production recovery logic, or Batch/Trial/Attempt models.
- The PoC harness is test/evidence infrastructure. S2.1/S2.2 production runtime must not depend on it by default.
- No generic Runner backend registry, plugin API, scheduler, worker pool, remote-runner API, multi-host abstraction, SSH path, or generic supervision framework.
- Candidate A is evaluated first. A1 is baseline-only. A2 is the first selectable Candidate-A disposition. A3 or Candidate B implementation requires a focused plan amendment if A2 evidence proves it necessary; Codex must not invent either mechanism autonomously.
- PostgreSQL remains the future durable execution ledger. Task 1 may use only a PoC durable intent store; that store is not a second domain ledger.
- SupervisorExecutionIdentity `X` is derived deterministically from an Attempt-like UUID plus a stable installation namespace. PID, process start time, and systemd InvocationID are observation facts only.
- `execution_spec_hash H` is a versioned canonical SHA-256 over non-secret frozen launch semantics.
- Every caller-controlled value that can alter target process behavior or caller-selectable supervisor launch behavior must be inside `FrozenLaunchSpec` and therefore H. Behavior-changing launch arguments outside H are forbidden.
- `FrozenLaunchSpec.runtime_path` is the sole runtime executable authority. No constructor/config value outside the frozen spec may select another executable for the same X/H.
- Secret values never enter X, H, ordinary supervisor metadata, evidence files, or logs.
- `NOT_FOUND` is never equivalent to `NEVER_ACCEPTED_PROVEN`.
- Once supervisor dispatch may have occurred, absence never authorizes blind recreation. Unknown launch/control outcome must be followed by observation, not retry.
- A creation-capable call performs an exact raw preflight before `systemd-run`: `PRESENT`, `CONFIRMED_ABSENT`, or `LOOKUP_UNKNOWN`. Only `CONFIRMED_ABSENT + positive creation evidence` may cross the creation boundary. `LOOKUP_UNKNOWN` returns fail-closed without creating.
- Same X + same H reconciles existing execution. Same X + different H is identity conflict and fails closed.
- A1 live binding uses exact metadata: systemd unit name binds X and systemd unit `Description` binds H. PID or command-line similarity is not an X/H binding mechanism.
- Destructive stop/kill requires `ACTIVE_MATCH` for exact X/H. `IDENTITY_CONFLICT` or `UNKNOWN` must not trigger destructive control.
- Mandatory normal recovery scenarios P3/P6/P7/P10 may not pass as `UNKNOWN` for a selected topology.
- Host reboot does not require process survival and must never cause automatic same-Attempt resurrection.
- No `Restart=always` or equivalent supervisor policy may implement experiment retry.
- Native-host commands use structured argv and never `shell=True`.
- Task 1 uses the system manager boundary; it must not silently switch to `systemd --user` to bypass permission failures.
- Native control paths are noninteractive. `systemd-run` and destructive `systemctl` operations must disable interactive password prompting where the installed version supports the required option; P0 must prove the concrete noninteractive behavior before scenarios. An interactive authorization prompt is a capability/permission failure, not a test step.
- The PoC harness must never invoke `sudo`, `reboot`, `shutdown`, edit `/etc`, enable lingering, or otherwise mutate host authorization/lifecycle policy automatically. P11 contains an explicit human reboot gate.
- P0 validates the full frozen systemd profile before P1–P13. Missing required manager/CLI/property behavior yields `HOST_CAPABILITY_GAP`; Codex must not drop flags, change manager scope, or invent an alternate launch mechanism to continue.
- Raw host logs are bounded evidence, not a repository dump. Do not commit secrets or unrestricted journal/system logs.
- Do not push Task 1 implementation until the required review says APPROVE.
- `DOCUMENT-STATUS.md` is updated once at Slice 2 acceptance, not after S2.0.
- On WSL, do not use `git commit -m "..."`; construct a message file and use `git commit --file=<path> --cleanup=verbatim`.

---

# 1. Stage-gated plan policy and final Slice 2 task granularity

The final Slice 2 delivery shape remains five formal tasks:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

This revision fully specifies **Task 1 only**. Task 1 exists to discover the real supervisor/evidence contract. Writing exact production interfaces for Tasks 2–5 before that ADR would force implementation to depend on assumptions that S2.0 is explicitly meant to test.

After Task 1 is accepted, amend this same plan with exact Task 2–5 files/interfaces/tests while preserving the five-task outer shape. An amendment is not permission to split each design subsection into a separate formal task.

## 1.1 Reserved future task boundaries — non-executable in this revision

- **Task 2:** approved `ExperimentVersion` → Batch/Trial identity → non-launchable Attempt preparation → exact materialized source → verified manifest → versioned `FrozenExecutionSpec` → launch-ready boundary. No physical process.
- **Task 3:** committed durable launch intent → selected supervision contract → launch/observe/stop/kill → terminal physical observation. This is the first production external-side-effect boundary and must inherit X/H and acknowledgement-loss semantics from Task 1.
- **Task 4:** durable DB expectation + selected physical observation → deterministic reconciliation, restart recovery, cancel/timeout composition, and thin API composition. No second execution truth source.
- **Task 5:** real fault injection and Slice-level acceptance: API/Coordinator death/restart, process exits while controller is down, restart during reconciliation, unknown physical identity, no duplicate launch, full CI, fresh PostgreSQL, deep review.

**Do not implement Tasks 2–5 from this revision.**

---

# 2. Task 1 — S2.0 Runner Supervision PoC + ADR

**Risk:** `Safety / Recovery Critical`

**Deliverable:** A deterministic local harness plus real native-Linux evidence that either selects Candidate A2 through a short ADR or stops at a precisely evidenced escalation/blocking gate. No production runtime code.

Task 1 is executed in three internal batches:

```text
Batch A — deterministic contract foundation
Batch B — physical evidence/scenario infrastructure
Batch C — native-Linux evidence + ADR
```

These batches control implementation precision. They do **not** create additional formal tasks, governance closures, or mandatory review cycles.

## 2.1 Task 1 file / ownership map

**Create — PoC infrastructure only:**

- `tests/poc/__init__.py`
- `tests/poc/s2_runner/__init__.py`
- `tests/poc/s2_runner/contract.py`
  - closed PoC semantic types;
  - X projection;
  - versioned canonical H;
  - local request validation.
- `tests/poc/s2_runner/durable_state.py`
  - fsync-backed test durable intent state;
  - first-dispatch → dispatch-may-have-occurred transition;
  - no production Attempt semantics.
- `tests/poc/s2_runner/probe.py`
  - real supervised target process;
  - long/short exit, known exit code, optional child, structured start/terminal messages, independent-root marker.
- `tests/poc/s2_runner/systemd_cli.py`
  - direct Candidate-A system-manager harness only;
  - exact X/H live binding;
  - exact creation preflight;
  - structured subprocess argv, never shell;
  - launch/query/stop/kill and optional journal evidence;
  - no generic backend interface.
- `tests/poc/s2_runner/evidence.py`
  - bounded JSONL evidence writer/reader;
  - fsync before injected crash when evidence is claimed durable.
- `tests/poc/s2_runner/scenario_runner.py`
  - CLI/orchestrator for P0–P13;
  - full host capability characterization;
  - fresh child controller processes for crash/restart scenarios;
  - explicit two-phase P11 reboot workflow;
  - mechanical A1 baseline/A2 evaluation.
- `tests/poc/s2_runner/README.md`
  - exact native-host runbook and human reboot checkpoint.

**Create — deterministic unit tests:**

- `tests/unit/s2_runner_poc/__init__.py`
- `tests/unit/s2_runner_poc/test_contract.py`
- `tests/unit/s2_runner_poc/test_durable_state.py`
- `tests/unit/s2_runner_poc/test_systemd_cli.py`
- `tests/unit/s2_runner_poc/test_scenario_runner.py`

**Create only after native evidence exists:**

- `docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`
- `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

**Must not modify in Task 1:**

```text
src/labpilot/**
migrations/**
compose.yaml
Dockerfile
pyproject.toml
uv.lock
DOCUMENT-STATUS.md
```

If Task 1 appears to require one of those files, stop and classify the need before editing. Do not fold production changes into the PoC.

---

# 3. Exact PoC contract

## 3.1 `contract.py`

Expose these names exactly:

```python
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

EXECUTION_SPEC_SCHEMA_VERSION = "s2-poc-v1"


class PhysicalObservationKind(StrEnum):
    ACTIVE_MATCH = "active_match"
    TERMINAL_MATCH = "terminal_match"
    IDENTITY_CONFLICT = "identity_conflict"
    UNKNOWN = "unknown"


class PositiveCreationEvidence(StrEnum):
    FIRST_DISPATCH_PROVEN = "first_dispatch_proven"
    NEVER_ACCEPTED_PROVEN = "never_accepted_proven"


class ControlOutcomeKind(StrEnum):
    CREATED = "created"
    EXISTING_MATCH = "existing_match"
    TERMINAL_MATCH = "terminal_match"
    CONTROL_ACCEPTED = "control_accepted"
    IDENTITY_CONFLICT = "identity_conflict"
    DEFINITE_NON_ACCEPTANCE = "definite_non_acceptance"
    CONTROL_REJECTED = "control_rejected"
    CONTROL_UNKNOWN = "control_unknown"


@dataclass(frozen=True, slots=True)
class FrozenLaunchSpec:
    schema_version: str
    runtime_path: str
    argv: tuple[str, ...]
    working_directory: str
    environment: tuple[tuple[str, str], ...]
    credential_slots: tuple[str, ...]
    execution_user: str
    start_marker_path: str
    sleep_seconds: float
    exit_code: int
    child_sleep_seconds: float
    print_env_keys: tuple[str, ...]
    collect_after_exit: bool


@dataclass(frozen=True, slots=True)
class PhysicalObservation:
    kind: PhysicalObservationKind
    execution_identity: str
    expected_spec_hash: str
    observed_spec_hash: str | None = None
    pid: int | None = None
    invocation_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    result: str | None = None
    evidence_source: str | None = None


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    kind: ControlOutcomeKind
    execution_identity: str
    expected_spec_hash: str
    detail_code: str


def canonical_execution_spec(spec: FrozenLaunchSpec) -> bytes: ...
def execution_spec_hash(spec: FrozenLaunchSpec) -> str: ...
def supervisor_execution_identity(installation_namespace: str, attempt_id: UUID) -> str: ...
def systemd_unit_name(execution_identity: str) -> str: ...
def validate_request_hash(spec: FrozenLaunchSpec, asserted_hash: str) -> None: ...
```

Implementation rules:

- `canonical_execution_spec()` rejects any `schema_version` other than `EXECUTION_SPEC_SCHEMA_VERSION` before serializing/hashing.
- Canonical JSON uses sorted object keys, UTF-8, `ensure_ascii=False`, and separators `(",", ":")`.
- `environment`, `credential_slots`, and `print_env_keys` are canonicalized in sorted order. Duplicate environment keys are rejected before hashing.
- `execution_spec_hash()` is lowercase hexadecimal SHA-256 over the canonical bytes.
- Every caller-controlled probe/launch behavior in Task 1 is represented in `FrozenLaunchSpec`. `SystemdCli.launch()` must not take separate caller-controlled runtime/sleep/exit/child/marker/collect/user parameters.
- `runtime_path` is absolute/non-empty and is the only executable selected for the physical target command. There is no second executable authority in `SystemdCli` construction or scenario configuration.
- No field named or typed as a secret value exists in `FrozenLaunchSpec`. Task 1 uses only non-secret environment values. Credential slot names may be present; credential values may not.
- `supervisor_execution_identity()` includes the full lowercase UUID hex and the full SHA-256 of the explicit installation namespace. Do not truncate either source identity.
- Use this exact PoC projection shape:

```text
labpilot-poc-<sha256(installation_namespace UTF-8)>-<attempt_uuid_hex>
```

- `systemd_unit_name(X)` returns `X + ".service"` and rejects values that are not valid outputs of the projector.
- `validate_request_hash()` raises `ValueError("execution_spec_hash_mismatch")` before any supervisor call when asserted H differs.

The projection spelling is PoC-only. The later ADR may freeze a production spelling, but it must preserve deterministic/collision-safe/no-loss properties.

## 3.2 `durable_state.py`

Expose:

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID


class DispatchPhase(StrEnum):
    FIRST_DISPATCH_AVAILABLE = "first_dispatch_available"
    DISPATCH_MAY_HAVE_OCCURRED = "dispatch_may_have_occurred"


@dataclass(frozen=True, slots=True)
class DurableIntent:
    attempt_id: UUID
    installation_namespace: str
    execution_identity: str
    execution_spec_hash: str
    dispatch_phase: DispatchPhase


class DurableIntentStore:
    def __init__(self, root: Path) -> None: ...
    def create(self, intent: DurableIntent) -> None: ...
    def load(self, attempt_id: UUID) -> DurableIntent: ...
    def mark_dispatch_may_have_occurred(self, attempt_id: UUID) -> DurableIntent: ...
```

Persistence rules:

1. State root is explicit; native CLI has no default `/tmp` or `/run` state path.
2. Persist JSON to a temporary file in the same directory.
3. `flush()` and `os.fsync()` the temporary file.
4. `os.replace()` onto the final path.
5. Open/fsync the containing directory after replacement.
6. `mark_dispatch_may_have_occurred()` must durably complete **before** the creation-capable systemd call.
7. There is no transition back to `FIRST_DISPATCH_AVAILABLE`.
8. The store never infers `NEVER_ACCEPTED_PROVEN` from missing supervisor state.

This store exists only to make injected controller-death/reboot tests valid under R2. It is not a production Attempt store.

## 3.3 `probe.py`

`python -m tests.poc.s2_runner.probe` accepts the physical arguments derived from one validated `FrozenLaunchSpec`, including:

```text
--execution-identity X
--execution-spec-hash H
--start-marker PATH
--sleep-seconds FLOAT
--exit-code INT
--child-sleep-seconds FLOAT
--print-env KEY       # repeatable
```

Behavior:

1. Append one JSON line to `start-marker` containing `event=probe_started`, X, H, own PID, and timestamp; flush/fsync the file.
2. Print one JSON `probe_started` record with exact X/H to stdout and flush.
3. Print only requested non-secret environment observations.
4. If `child_sleep_seconds > 0`, spawn exactly one bounded child. The child is a descendant and must not write an independent-root marker.
5. Sleep for the frozen `sleep_seconds`.
6. Before normal exit, print `probe_terminal` with exact X/H, requested exit code, and timestamp; flush stdout.
7. Return exactly the frozen exit code.

No signal handler may manufacture a `probe_terminal` record when force-killed. Physical evidence must reflect what actually happened.

## 3.4 `systemd_cli.py`

This file is a narrow PoC adapter, not a production `Runner` abstraction.

Expose:

```python
from collections.abc import Callable, Sequence
from enum import StrEnum
import subprocess

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


class RawUnitLookupKind(StrEnum):
    PRESENT = "present"
    CONFIRMED_ABSENT = "confirmed_absent"
    LOOKUP_UNKNOWN = "lookup_unknown"


class SystemdCli:
    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        use_journal_evidence: bool,
    ) -> None: ...

    def launch(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
        creation_evidence: PositiveCreationEvidence,
    ) -> ControlOutcome: ...

    def observe(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
    ) -> PhysicalObservation: ...

    def stop(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        force: bool,
    ) -> ControlOutcome: ...
```

`RawUnitLookupKind` is adapter-private supervision evidence, not a domain/recovery enum. It exists only to prevent `PhysicalObservation.UNKNOWN` from being overloaded as creation authority.

`SystemdCli` deliberately has **no `python_executable`, runtime executable, or second runtime selector**. The target executable used in the physical argv is exactly `spec.runtime_path`.

`launch()` is creation-capable and therefore requires explicit `PositiveCreationEvidence`. The orchestration layer remains responsible for proving that evidence from durable state; the adapter must not offer a second creation-capable entry point without the gate.

### Frozen systemd launch profile

Task 1 evaluates Candidate A using the **system manager**. Do not silently switch to `--user`.

The launch argv must make the following semantics explicit when supported by the characterized host:

```text
--system
--unit=<systemd_unit_name(X)>
--description=labpilot-poc:<H>
--service-type=exec
--expand-environment=no
--property=Restart=no
--property=StandardOutput=journal
--property=StandardError=journal
--property=KillMode=control-group
--uid=<spec.execution_user>
--working-directory=<spec.working_directory>
noninteractive authorization / no password prompt
```

Use repeated `--setenv=NAME=VALUE` only for the non-secret environment already covered by H. Do not import arbitrary controller environment values by name.

`spec.runtime_path`, `spec.argv`, X/H probe metadata, and the remaining frozen probe values are converted to structured argv after all systemd options. No shell command string is ever constructed.

### Exact X/H live binding

For A1 live observation:

```text
unit name == systemd_unit_name(X)
AND
Description == "labpilot-poc:" + H
```

is the exact X/H binding mechanism. `observe()` must request `Description` together with the other unit properties and compare it exactly. A live unit with the expected X but a different Description/H is `IDENTITY_CONFLICT`. PID, InvocationID, executable path, or fuzzy command-line matching cannot substitute for this binding.

### Exact creation preflight

Before any `systemd-run` call, `launch()` performs a raw exact unit lookup for X and classifies it privately as:

```text
PRESENT
CONFIRMED_ABSENT
LOOKUP_UNKNOWN
```

Rules:

```text
PRESENT
→ call observe(X,H)
→ ACTIVE_MATCH      => EXISTING_MATCH, no create
→ TERMINAL_MATCH    => TERMINAL_MATCH, no create
→ IDENTITY_CONFLICT => IDENTITY_CONFLICT, no create
→ UNKNOWN           => CONTROL_UNKNOWN, no create

CONFIRMED_ABSENT + valid PositiveCreationEvidence
→ creation may be attempted

CONFIRMED_ABSENT without valid PositiveCreationEvidence
→ local CONTROL_REJECTED, no create

LOOKUP_UNKNOWN
→ CONTROL_UNKNOWN, no create
```

`CONFIRMED_ABSENT` is only the adapter's current preflight fact. It is **not** `NEVER_ACCEPTED_PROVEN` and cannot manufacture replay authority after a prior uncertain dispatch. Positive creation evidence remains independently required.

If the actual `systemd-run` dispatch returns timeout, transport failure, or an ordinary nonzero result whose non-acceptance semantics were not positively proven, `launch()` must immediately `observe(X,H)` before returning:

```text
ACTIVE_MATCH      → EXISTING_MATCH
TERMINAL_MATCH    → TERMINAL_MATCH
IDENTITY_CONFLICT → IDENTITY_CONFLICT
UNKNOWN           → CONTROL_UNKNOWN
```

It must never create a fallback unit, generate another X, or blindly retry the same physical creation. This is the P4 concurrent-race loser behavior as well as the ack-lost behavior.

### Command rules

- Invoke `systemd-run`, `systemctl`, and `journalctl` through argv arrays only; `shell=True` is forbidden.
- Unit name is derived only through `systemd_unit_name(X)`.
- Creation and destructive control must be noninteractive; if the required no-prompt behavior is unavailable or authorization would prompt, report capability/permission failure rather than wait for input.
- Unit control covers the whole target cgroup/process tree; never target a PID as the durable control identity.
- The probe receives exact X/H as non-secret metadata and emits them to journal/stdout.
- `collect_after_exit=True` may map to `--collect` only when P0 host characterization proves the target systemd supports it. Unsupported collection is `HOST_CAPABILITY_GAP`; it is not silently ignored.
- `launch()` calls `validate_request_hash()` before any raw lookup/command execution.
- `launch()` rejects an unsupported/invalid creation-evidence value before command execution.
- `observe()` queries current unit properties including at least `LoadState`, `ActiveState`, `SubState`, `Description`, `Result`, `MainPID`, `InvocationID`, and exit-result properties available on the host.
- A matching live unit is `ACTIVE_MATCH` only when exact unit/X and Description/H both match.
- A current unit/evidence with a different H is `IDENTITY_CONFLICT`.
- A missing current unit is never by itself `NEVER_ACCEPTED_PROVEN`.
- With `use_journal_evidence=False`, missing/insufficient current-unit evidence becomes `UNKNOWN`.
- With `use_journal_evidence=True`, query bounded journal records for the exact unit and parse only structured PoC records containing exact X/H. Do not infer H through fuzzy command-line matching.
- `TERMINAL_MATCH` requires exact X/H binding plus terminal physical evidence. Unit name alone is insufficient.
- Journal parsing is bounded by exact unit plus a bounded entry count. Do not read the full host journal.
- `stop()` calls `observe(X,H)` first. It may issue systemd stop/kill only for `ACTIVE_MATCH`.
- `TERMINAL_MATCH` returns without destructive control.
- `IDENTITY_CONFLICT` and `UNKNOWN` fail closed without stop/kill.
- A successfully accepted stop/kill request returns `CONTROL_ACCEPTED`; it does not claim the Attempt is cancelled.
- Generic command timeout/transport errors map to `CONTROL_UNKNOWN`, not `DEFINITE_NON_ACCEPTANCE`.
- Deterministic local validation/permission rejection that is proven to happen before dispatch may map to `CONTROL_REJECTED` or `DEFINITE_NON_ACCEPTANCE`. Ordinary `systemd-run` nonzero status is not automatically proof of non-acceptance.

## 3.5 `evidence.py`

Expose:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    scenario_id: str
    phase: str
    execution_identity: str | None
    execution_spec_hash: str | None
    expected: str
    observed: str
    detail: dict[str, object]


class EvidenceLog:
    def __init__(self, path: Path) -> None: ...
    def append(self, record: EvidenceRecord) -> None: ...
    def read_all(self) -> tuple[EvidenceRecord, ...]: ...
```

Each append is JSONL, flushed/fsynced before an injected failure when later recovery relies on the record. Evidence must be bounded and secret-free. The writer rejects keys named `secret`, `token`, `password`, `api_key`, or `authorization` case-insensitively anywhere in `detail`; this is a PoC guard, not a general DLP system.

## 3.6 `scenario_runner.py`

CLI syntax:

```text
python -m tests.poc.s2_runner.scenario_runner \
  --state-root <persistent-path> \
  --evidence-root <persistent-path> \
  --installation-namespace <non-secret-string> \
  --evidence-stack systemd|systemd+journal \
  <command>
```

Commands:

```text
characterize
p1
p2
p3
p4
p5
p6
p7
p8
p9
p10
p12
p13
reboot-prepare
reboot-verify
summarize
```

There is deliberately no `reboot` command. The harness must never reboot the host itself.

### P0 full host capability characterization

`characterize` records at minimum:

```text
kernel release
systemd version
current boot_id
effective controller user/uid
intended training execution user/uid resolution
systemd-run/systemctl/journalctl executable paths
system manager reachability
noninteractive system-manager authorization result
support/behavior for service Type=exec semantics
support for environment expansion disablement used by the fixed profile
support for execution user selection
support for explicit working directory
support for exact non-secret environment injection
support for unit Description metadata used for H binding
support for Restart=no / KillMode=control-group / journal stdout+stderr properties
support for --collect behavior required by P7
state/evidence root mount target/filesystem type
whether /var/log/journal exists
bounded effective journald storage evidence
current/previous boot list sufficient to assess persistence
```

P0 must verify the concrete behavior needed by the frozen profile, not merely grep `--help`. A harmless disposable characterization unit may be used when needed, but it must itself use a bounded, non-secret command and be cleaned up without broad host mutation.

If any required fixed-profile capability is missing or cannot be exercised noninteractively by the intended control identity, record:

```text
HOST_CAPABILITY_GAP: <exact missing capability or authorization>
```

and stop before P1–P13. Codex must not delete a required option, switch to a user manager, run as root, edit authorization policy, or substitute another launch mechanism to continue.

For reboot-dependent evidence, neither state root nor evidence root may resolve under `/tmp` or `/run`. If either is volatile, P11 is invalid until the operator supplies a persistent root.

The harness records permission failures. It does not run `sudo`, modify polkit/sudoers, enable linger, or switch to a user manager to force a PASS.

---

# 4. Batch A — deterministic contract foundation

## Step A1 — verify exact baseline before editing

Run:

```bash
git status --short
git rev-parse HEAD
git merge-base --is-ancestor ff7909503c3b755bffb564423de3a56ce5bb9b4a HEAD
git diff --name-only 9b9cc7c9d29a72cf8b568316febc7b0cc6d741ab...HEAD
```

Expected before implementation:

```text
clean worktree
Slice 1 acceptance commit is an ancestor
only approved planning/spec docs exist after the design baseline
```

If production/test implementation changed unexpectedly, stop and report drift before coding.

## Step A2 — write failing contract tests

Create `tests/unit/s2_runner_poc/test_contract.py` covering at least:

```python
def test_same_namespace_and_attempt_produce_same_full_execution_identity(): ...
def test_different_namespace_changes_execution_identity(): ...
def test_different_attempt_changes_execution_identity(): ...
def test_execution_identity_contains_full_uuid_without_truncation(): ...
def test_semantically_same_spec_hashes_identically_after_set_order_normalization(): ...
def test_behavior_change_changes_hash(): ...
def test_runtime_path_change_changes_hash(): ...
def test_unsupported_schema_version_is_rejected(): ...
def test_hash_mismatch_is_rejected_locally(): ...
def test_duplicate_environment_keys_are_rejected(): ...
def test_secret_value_field_is_not_part_of_frozen_spec_surface(): ...
```

`test_behavior_change_changes_hash()` varies representative fields that were previously outside H: sleep/exit behavior, execution user, marker path, and collect flag. `test_runtime_path_change_changes_hash()` proves the runtime executable itself is part of H.

Use fixed UUIDs and non-secret sentinel values. Do not use random canonical examples.

Run RED:

```bash
uv run pytest tests/unit/s2_runner_poc/test_contract.py -q
```

Expected: module/import/API absence failure before implementation.

Implement package initializers and `contract.py` minimally, then run GREEN:

```bash
uv run pytest tests/unit/s2_runner_poc/test_contract.py -q
```

## Step A3 — write and implement durable-state tests

Create `tests/unit/s2_runner_poc/test_durable_state.py` covering:

```python
def test_create_round_trips_first_dispatch_intent(tmp_path): ...
def test_mark_dispatch_may_have_occurred_is_monotonic(tmp_path): ...
def test_missing_state_does_not_create_non_acceptance_evidence(tmp_path): ...
def test_replace_failure_does_not_report_new_phase_as_committed(tmp_path, monkeypatch): ...
```

Run RED, implement the fsync/replace rules in §3.2, run GREEN:

```bash
uv run pytest tests/unit/s2_runner_poc/test_durable_state.py -q
```

## Step A4 — systemd adapter tests with injected command runner

Create `tests/unit/s2_runner_poc/test_systemd_cli.py`. The fake runner records every argv vector and returns scripted `CompletedProcess` values.

Cover at least:

```python
def test_hash_mismatch_performs_zero_commands(): ...
def test_launch_requires_positive_creation_evidence(): ...
def test_launch_uses_only_hashed_runtime_path(): ...
def test_launch_uses_system_manager_exec_profile_and_structured_argv(): ...
def test_launch_does_not_use_user_manager_or_shell(): ...
def test_launch_uses_only_non_secret_frozen_environment(): ...
def test_launch_sets_exact_h_description_metadata(): ...
def test_active_matching_x_and_description_h_becomes_active_match(): ...
def test_same_x_different_description_h_becomes_identity_conflict(): ...
def test_preflight_present_observes_and_never_creates(): ...
def test_preflight_confirmed_absent_with_positive_evidence_may_create(): ...
def test_preflight_confirmed_absent_without_positive_evidence_never_creates(): ...
def test_preflight_lookup_unknown_never_creates(): ...
def test_not_found_without_journal_is_unknown(): ...
def test_dispatch_nonzero_observes_before_returning(): ...
def test_concurrent_race_loser_reconciles_existing_x_without_fallback_unit(): ...
def test_journal_exact_x_h_terminal_record_can_be_terminal_match(): ...
def test_journal_different_h_is_identity_conflict(): ...
def test_generic_launch_error_is_control_unknown_not_non_acceptance(): ...
def test_stop_active_match_issues_control_and_returns_control_accepted(): ...
def test_stop_terminal_match_is_noop(): ...
def test_stop_identity_conflict_never_issues_destructive_control(): ...
def test_stop_unknown_never_issues_destructive_control(): ...
```

The structured-argv assertion verifies the fake runner receives a sequence and never a shell command string. `test_launch_uses_only_hashed_runtime_path()` must prove there is no constructor/config executable that can override `spec.runtime_path`.

Run RED:

```bash
uv run pytest tests/unit/s2_runner_poc/test_systemd_cli.py -q
```

Implement `systemd_cli.py` minimally. Do not create a `RunnerBackend` Protocol/base class/registry.

Run GREEN:

```bash
uv run pytest tests/unit/s2_runner_poc/test_systemd_cli.py -q
```

## Batch A verification

Run:

```bash
uv run pytest \
  tests/unit/s2_runner_poc/test_contract.py \
  tests/unit/s2_runner_poc/test_durable_state.py \
  tests/unit/s2_runner_poc/test_systemd_cli.py -q

uv run ruff check tests/poc/s2_runner tests/unit/s2_runner_poc
uv run mypy tests/poc/s2_runner
```

Expected: all PASS before continuing to Batch B.

---

# 5. Batch B — evidence and scenario infrastructure

## Step B1 — write failing orchestration/evidence tests

Create `tests/unit/s2_runner_poc/test_scenario_runner.py` covering pure orchestration with fakes:

```python
def test_probe_start_marker_is_one_independent_root_record(tmp_path): ...
def test_creation_path_has_no_call_without_positive_evidence(tmp_path): ...
def test_dispatch_phase_is_persisted_before_launch_call(tmp_path): ...
def test_p3_recovery_observes_before_any_possible_replay(tmp_path): ...
def test_p4_concurrent_same_x_h_never_generates_fallback_identity(tmp_path): ...
def test_p5_same_x_different_h_never_stops_or_relaunches(tmp_path): ...
def test_p8_pre_dispatch_validation_can_prove_never_accepted(tmp_path): ...
def test_p8_not_found_after_possible_dispatch_is_unknown(tmp_path): ...
def test_p9_does_not_claim_cancel_caused_exit_without_evidence(tmp_path): ...
def test_reboot_verify_never_calls_launch(tmp_path): ...
def test_reboot_verify_does_not_invent_lost_observation_kind(tmp_path): ...
def test_characterize_checks_entire_required_systemd_profile(tmp_path): ...
def test_characterize_capability_gap_stops_before_scenarios(tmp_path): ...
def test_evidence_writer_rejects_secret_shaped_keys(tmp_path): ...
```

## Step B2 — implement `probe.py` and `evidence.py`

Implement only the behavior in §3.3/§3.5.

Verify focused tests before adding native-host orchestration.

## Step B3 — implement scenario orchestration minimally

P3 ordering is fixed:

```text
persist FIRST_DISPATCH_AVAILABLE intent
→ durably mark DISPATCH_MAY_HAVE_OCCURRED
→ retain the already-proven FIRST_DISPATCH_PROVEN permit for this dispatch attempt
→ child calls launch(X,H,spec,permit)
→ launch performs exact raw preflight
→ confirmed absence allows the one creation attempt
→ probe start marker proves target root started
→ child exits with os._exit(73) before durable success acknowledgement
→ fresh recovery process observes X/H
→ same logical request reconciles; it does not create again
```

The durable transition happens before the supervisor call. The permit represents proof about the pre-dispatch state for that one dispatch attempt; it is not regenerated after uncertainty.

Other scenarios:

```text
P4  two fresh child controllers race the same X/H; both use exact preflight; any dispatch loser observes/reconciles the original X and never generates a fallback identity.
P5  run X/H1 then present X/H2; exact Description/H conflict, no stop/replacement/relaunch.
P6  very-short known-exit probe; recovery begins only after physical exit.
P7  use collect/unit-GC behavior only when P0 proves support; after current unit state disappears, exact X/H terminal evidence must still be recoverable by a selectable stack.
P8  deterministic local/pre-dispatch rejection with supervisor command count zero → NEVER_ACCEPTED_PROVEN; possible-dispatch + NOT_FOUND → UNKNOWN.
P9  stop-first, natural-exit-first, and near-concurrent variants; retain facts without fabricating a causal winner.
P10 controller observation loop is gone while process continues/exits; fresh controller recovers TERMINAL_MATCH.
P11 split into reboot-prepare/reboot-verify; verify path contains no launch call.
```

## Step B4 — deterministic Batch B verification

Run:

```bash
uv run pytest tests/unit/s2_runner_poc -q
uv run ruff check tests/poc/s2_runner tests/unit/s2_runner_poc
uv run mypy tests/poc/s2_runner
uv run pytest tests/unit tests/architecture -q
```

Task 1 does not justify changing production code to fix an unrelated pre-existing failure. Investigate before any remediation.

## Step B5 — local harness commit, no push

Use a message file:

```bash
printf '%s\n' 'test: add slice two runner supervision poc' > /tmp/labpilot-s2-task1-message.txt
git add tests/poc tests/unit/s2_runner_poc
git commit --file=/tmp/labpilot-s2-task1-message.txt --cleanup=verbatim
```

Record the commit SHA. Do not update status docs and do not push.

---

# 6. Batch C — native Linux evidence and ADR

Choose persistent non-secret roots explicitly, for example:

```bash
STATE_ROOT="$HOME/.local/state/labpilot-s2-poc/state"
EVIDENCE_ROOT="$HOME/.local/state/labpilot-s2-poc/evidence"
INSTALLATION_NAMESPACE="labpilot-s2-poc-native"
mkdir -p "$STATE_ROOT" "$EVIDENCE_ROOT"
```

The harness records the actual mount/filesystem characteristics. The example path is not authority.

## Step C0 — P0 capability gate

Run first:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack systemd characterize
```

P0 must PASS every required frozen-profile capability in §3.6 before any physical scenario. If it returns `HOST_CAPABILITY_GAP`, stop Task 1 and report the exact missing capability/authorization. No silent downgrade is authorized.

## Step C1 — P1/P2/P12/P13 baseline characterization

After P0 PASS, run A1 baseline evidence:

```bash
for scenario in p1 p2 p12 p13; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd "$scenario"
done
```

Required:

```text
P1 stable/collision-safe X PASS
P2 ACTIVE_MATCH using exact unit-name/X + Description/H binding PASS
P2 exact argv/workdir/non-secret env/log observation PASS
P12 intended system-manager permission boundary recorded honestly
P13 representative environment/runtime/stdout/stderr/exit propagation PASS
```

P12 rule:

```text
If the intended unprivileged control identity cannot exercise the required system-manager operations noninteractively,
DO NOT rerun as root and call it PASS.
DO NOT switch to systemd --user.
DO NOT edit host policy automatically.
Record the exact denied operation and stop for topology/authorization evaluation.
```

If a narrow host policy change is potentially sufficient, request explicit operator configuration/review before retesting.

## Step C2 — P3/P4/P5/P8 duplicate/ack-loss/conflict safety

Run each with `--evidence-stack systemd`.

Required:

```text
P3 ACTIVE_MATCH or TERMINAL_MATCH; independent-root starts = 1
P4 authoritative execution units <= 1; independent-root starts = 1; dispatch loser reconciles exact X
P5 IDENTITY_CONFLICT from exact X/H binding; no stop/replacement/relaunch
P8 proven pre-dispatch rejection distinguishable from ambiguous NOT_FOUND
```

Any second independent target root for one X is an immediate S2.0 FAIL. Do not add retry/lease/lock mechanisms to hide it.

## Step C3 — A1 baseline terminal-retention experiment

Run P6/P7/P10 with current-unit-only evidence:

```bash
for scenario in p6 p7 p10; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd "$scenario"
done
```

A1 is **not selectable**. Its purpose is to establish whether live identity/dedupe/control semantics are sound and whether the remaining gap is exactly durable X/H terminal retention/correlation.

P7 must actively demonstrate the post-GC condition; a unit that happens to remain loaded does not prove P7.

## Step C4 — A2 persistent-journal evaluation

Proceed to A2 only when A1 shows:

```text
live launch identity PASS
duplicate safety PASS
controller independence PASS
permission/control semantics PASS
remaining gap limited to durable X/H terminal retention/correlation
```

First verify that the target deployment actually has the persistent journal behavior the ADR would depend on. Do not infer persistence from one successful `journalctl` query.

Then run P6/P7/P10 using `systemd+journal`:

```bash
for scenario in p6 p7 p10; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd+journal "$scenario"
done
```

Required for SELECT_A2:

```text
P6 TERMINAL_MATCH with exact X/H
P7 TERMINAL_MATCH after current unit GC/unload with exact X/H
P10 TERMINAL_MATCH after controller-down exit with exact X/H
```

If A2 cannot satisfy these, stop before implementing A3 or a daemon. Classify the blocker:

```text
bounded physical witness retention/correlation only
→ request A3-focused plan amendment

host-control privilege / serialization / topology unsuitable
→ request Candidate-B-focused plan amendment
```

## Step C5 — P9 stop/natural-exit convergence

Run P9 on the currently eligible A2 evidence stack.

Required:

```text
one physical execution
one converged physical terminal result
no duplicate launch
no fabricated claim that cancel caused exit when ordering is unprovable
```

## Step C6 — P11 human reboot gate

Run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack systemd+journal reboot-prepare
```

The command ends after persisting and printing the pre-reboot evidence location. **Codex must not issue `reboot`, `shutdown`, `systemctl reboot`, or equivalent.**

The human operator manually reboots the native Linux host.

After login:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack systemd+journal reboot-verify
```

P11 is a scenario-level safety result, not a fifth `PhysicalObservationKind`.

Required:

```text
boot_id changed
original process survival is not required
same Attempt/X is not automatically relaunched
independent-root start count does not increase
no new activation is treated as the old execution
reboot-verify performs no launch
```

`PhysicalObservation` may remain `UNKNOWN / needs_attention` if exact terminal evidence cannot be proven after reboot. Do not create `LOST`, `REBOOTED`, or another physical observation enum to make the scenario look cleaner.

If A2 depends on persistent journal evidence, P11 must prove the relevant pre-reboot X/H evidence is actually queryable after reboot; configuration text alone is not enough.

---

# 7. Evidence report and ADR gate

## Step D1 — bounded evidence summary

Run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack systemd+journal summarize
```

Create only from actual evidence:

`docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

The committed report includes a bounded P0–P13 table with expected classification/result, observed classification/result, evidence stack, duplicate-root count where applicable, and PASS/FAIL. It explicitly records:

```text
F1 stable execution identity
F2 create/query/stop control
F3 controller-death survival
F4 fresh-controller rediscovery
F5 same-X replay / ack-loss / different-H conflict safety
F6 terminal X/H retention across controller restart + unit GC
F7 stop/natural-exit convergence
F8 host-reboot fail-closed behavior
permission boundary
environment propagation
```

Do not copy unbounded `journalctl` output into Git. Quote only bounded fields needed to prove classifications.

## Step D2 — subordinate ADR only when disposition is justified

Create:

`docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

Required front matter:

```text
Status: Proposed for review
Planning baseline: <Task 1 deterministic harness commit SHA>
Design authority: docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md
Disposition: SELECT_A2 | SELECT_A3 | SELECT_B | BLOCKED
```

Under this plan revision, Codex may write a final implemented disposition only for:

```text
SELECT_A2
BLOCKED
```

`SELECT_A3` or `SELECT_B` requires a focused plan amendment that defines and then tests the actual witness/daemon mechanism and its own crash ordering. Do not write either disposition based on an unimplemented idea.

For `SELECT_A2`, the ADR records:

```text
Selected topology: A
Selected physical evidence stack: systemd + persistent journald
SupervisorExecutionIdentity projection/namespace rule
exact unit-name/X + Description/H live binding mechanism
exact creation preflight semantics
frozen system-manager/service profile and P0 capability evidence
ack-lost behavior
meaning of NOT_FOUND
terminal retention contract
P3/P6/P7/P10 required classifications
finite allowed-UNKNOWN set
P11 reboot behavior
minimum demonstrated privilege boundary
why A1 is baseline-only
explicitly deferred production concerns
```

No selected topology may list P3/P6/P7/P10 in the allowed-UNKNOWN set.

---

# 8. Final Task 1 verification and review handoff

## Step E1 — deterministic verification

Run fresh:

```bash
uv run pytest tests/unit/s2_runner_poc -q
uv run pytest tests/unit tests/architecture -q
uv run ruff check tests/poc/s2_runner tests/unit/s2_runner_poc
uv run mypy tests/poc/s2_runner
```

## Step E2 — scope verification

Run:

```bash
git status --short
git diff --name-only 9b9cc7c9d29a72cf8b568316febc7b0cc6d741ab...HEAD
git diff --check
```

Task 1 implementation/evidence scope is limited to:

```text
tests/poc/**
tests/unit/s2_runner_poc/**
docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
```

plus already-approved plan/spec history. Any `src/`, migration, dependency, compose, Dockerfile, or status diff is out of scope.

## Step E3 — local evidence/ADR commit, no push

If a final ADR is justified, use a message file:

```bash
printf '%s\n' 'docs: record slice two runner supervision decision' > /tmp/labpilot-s2-task1-adr-message.txt
git add \
  docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md \
  docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
git commit --file=/tmp/labpilot-s2-task1-adr-message.txt --cleanup=verbatim
```

If Task 1 stops at A3/B escalation, do not manufacture a final ADR disposition. Preserve the harness/evidence and return for a focused plan amendment.

## Step E4 — review handoff

Provide the reviewer:

```text
Task 1 harness commit SHA
Task 1 ADR/evidence commit SHA if present
exact changed-file list
native host characterization including P0 full profile
P0–P13 evidence table
F1–F8 disposition
R1–R4 disposition
permission result
selected evidence stack
finite allowed UNKNOWN set
proof independent target-root count never exceeded 1
all fresh deterministic verification outputs
```

Task 1 is recovery-critical. Do not push until the required review says APPROVE.

---

# 9. Task 1 acceptance checklist

Task 1 is accepted only when every applicable item has fresh evidence:

```text
[ ] Native Linux, not WSL, is the authoritative supervision/reboot host.
[ ] X projection is deterministic, stable, collision-safe, and not lossily truncated.
[ ] H is versioned canonical SHA-256 over every caller-controlled behavior-changing FrozenLaunchSpec field.
[ ] spec.runtime_path is the sole target executable authority; no second executable selector exists.
[ ] Unsupported spec schema version is rejected before hashing/dispatch.
[ ] H mismatch causes zero supervisor interaction.
[ ] PID/start time/InvocationID never serve as LabPilot durable identity.
[ ] Durable PoC intent is fsync-backed for the injected failure class.
[ ] DISPATCH_MAY_HAVE_OCCURRED is durably persisted before supervisor dispatch.
[ ] Creation-capable API mechanically requires positive creation evidence.
[ ] Exact preflight distinguishes PRESENT / CONFIRMED_ABSENT / LOOKUP_UNKNOWN.
[ ] LOOKUP_UNKNOWN performs no physical create.
[ ] Dispatch nonzero/timeout observes exact X/H before returning and never blind-retries/falls back.
[ ] NOT_FOUND alone never becomes NEVER_ACCEPTED_PROVEN.
[ ] A1 live X/H binding is exact unit-name/X + Description/H.
[ ] P0 proves the complete required systemd profile and noninteractive authorization before scenarios.
[ ] Missing required host capability produces HOST_CAPABILITY_GAP and no silent downgrade.
[ ] System manager + Type=exec + no environment expansion + Restart=no profile is explicit.
[ ] Controller and training Unix identities are recorded separately.
[ ] P3 accepted-before-ack recovers ACTIVE_MATCH or TERMINAL_MATCH.
[ ] P4 concurrent same-X/same-H creates at most one authoritative execution/root and loser reconciles exact X.
[ ] P5 same-X/different-H fails closed and does not kill/replace the original.
[ ] P6 very-short process recovers TERMINAL_MATCH.
[ ] P7 after actual unit GC/unload recovers TERMINAL_MATCH.
[ ] P8 distinguishes positive pre-dispatch non-acceptance from ambiguous absence.
[ ] P9 converges without fabricated causal certainty.
[ ] P10 process exit while controller down recovers TERMINAL_MATCH.
[ ] P11 proves reboot safety without inventing a fifth PhysicalObservation kind.
[ ] P12 minimum acceptable permission boundary is demonstrated; no root/user-manager shortcut.
[ ] P13 representative runtime/env/stdout/stderr/exit observation is demonstrated.
[ ] stop/kill is issued only after ACTIVE_MATCH for exact X/H.
[ ] P3/P6/P7/P10 are not in allowed UNKNOWN cases.
[ ] F1–F8 PASS for SELECT_A2.
[ ] Evidence report is bounded and secret-free.
[ ] PoC code remains outside production src/migrations.
[ ] No A3 witness or Candidate B daemon was invented without a plan amendment.
```

## 9.1 Immediate stop semantics

Stop rather than patch around evidence when any of these occurs:

```text
- HOST_CAPABILITY_GAP in a required frozen-profile feature or noninteractive authorization;
- duplicate independent target root for one X;
- same-X/different-H execution is replaced or killed;
- P3/P6/P7/P10 can only become UNKNOWN under the candidate stack;
- Candidate A requires overbroad host privilege;
- the intended system-manager boundary cannot be made acceptably narrow without a topology decision;
- current supervisor/evidence contract cannot bind exact X/H;
- A2 cannot retain/recover exact X/H terminal evidence;
- fixing the problem would require a persistent host witness/control daemon;
- any proposal to change production Attempt/Coordinator schema before the ADR.
```

Classify the blocker:

```text
bounded physical witness retention/correlation only
→ request A3-focused plan amendment

host-control privilege / serialization / topology unsuitable
→ request Candidate-B-focused plan amendment

required host capability absent but architecture not yet disproven
→ report HOST_CAPABILITY_GAP and request a narrow environment/profile decision; no implementation workaround

fundamental invariant cannot be proven even with the admitted topology family
→ ADR BLOCKED; S2.1 production work remains unauthorized
```

---

# 10. Final r2 mechanical re-review checklist

Before approving this r2, verify it against the approved S2.0 design and the four final findings:

```text
[I1] SystemdCli constructor has no python_executable/runtime selector.
[I1] Actual physical executable is exactly spec.runtime_path and runtime changes change H.
[I2] launch mechanically requires positive creation evidence.
[I2] exact raw preflight separates PRESENT / CONFIRMED_ABSENT / LOOKUP_UNKNOWN.
[I2] LOOKUP_UNKNOWN cannot create; dispatch uncertainty triggers observe and never blind retry/fallback identity.
[I3] A1 ACTIVE_MATCH binds X through exact unit name and H through exact Description metadata.
[I3] same X + different Description/H is IDENTITY_CONFLICT.
[I4] P0 checks the entire frozen systemd launch/control/authorization profile, not only --collect.
[I4] missing required capability becomes HOST_CAPABILITY_GAP and stops without silent downgrade.
[ ] Unsupported schema version is rejected, not treated as another valid H version in Task 1.
[ ] FIRST_DISPATCH_PROVEN and NEVER_ACCEPTED_PROVEN are separate from physical observation.
[ ] NOT_FOUND never authorizes replay.
[ ] X/H binding is required for ACTIVE/TERMINAL/destructive control.
[ ] systemd --user is not an automatic permission workaround.
[ ] R2 durable PoC state is explicit.
[ ] R3 stop/natural-exit race does not fabricate causality.
[ ] R4 F5 covers concurrent replay, ack-lost replay, and different-H conflict.
[ ] A1 is baseline-only; final dispositions do not include SELECT_A1.
[ ] P3/P6/P7/P10 cannot pass as UNKNOWN.
[ ] A1→A2 is retention/correlation-only.
[ ] A3 is not silently implemented as a daemon.
[ ] Candidate B would record a separate physical evidence stack if later admitted.
[ ] P11 remains scenario-level and does not add LOST/REBOOTED to PhysicalObservationKind.
[ ] PoC harness is not a production dependency.
[ ] No production schema/interface has been invented before the ADR.
[ ] Formal Slice 2 task count remains five.
[ ] Task 1 internal Batch A/B/C does not create task/review/governance proliferation.
```

---

# 11. Execution authorization after plan approval

If and only if this r2 receives the final mechanical approval, the sole authorized implementation instruction is:

```text
Execute Task 1 — S2.0 Runner Supervision PoC + ADR.

Do not start Task 2.
Do not modify production runtime/schema.
Do not implement A3 witness or Candidate B daemon without a focused plan amendment.
Stop at HOST_CAPABILITY_GAP or the A3/B escalation gate if A2 cannot satisfy the frozen contract.
```

After Task 1/ADR acceptance, return to planning and amend this same document with exact files/interfaces/tests for Tasks 2–5. Do not expand the formal task count merely because those future tasks contain multiple design sections or test scenarios.
