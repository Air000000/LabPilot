# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Stop at the review/authorization gates defined below.

**Planning/status baseline:** `3a02feeb3342b99269fd47f4e0514088060e9877`

**Accepted Slice 1 implementation baseline:** `ff7909503c3b755bffb564423de3a56ce5bb9b4a`

**R0 authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**S2.0 design spec:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md`

**Status:** Draft for focused plan review. In this revision, only **Task 1 / S2.0** is execution-ready. Approval of this revision authorizes Task 1 only. Tasks 2–5 remain named, bounded future task slots and require an ADR-guided amendment of this same plan before implementation.

**Goal:** Prove a real native-Linux supervision contract first, then use that evidence to build one controlled path from an approved `ExperimentVersion` through exact source materialization, real training, restart reconciliation, and no duplicate physical launch.

**Architecture:** Slice 2 is stage-gated at the physical-execution boundary. Task 1 is an evidence-only native-Linux/systemd PoC that must select one supervision topology through a short ADR before production Batch/Trial/Attempt execution semantics are frozen. Production implementation follows only after the ADR, with PostgreSQL as durable execution ledger, the selected OS supervisor as physical observation authority, and Coordinator reconciliation as the only owner that combines durable expectation with physical evidence.

**Tech Stack:** Python 3.12, pytest, standard-library `subprocess`/`json`/`hashlib`/filesystem APIs for the PoC harness, native Linux, systemd/systemctl/systemd-run/journalctl for S2.0 evidence, existing `uv`/Ruff/mypy toolchain. PostgreSQL/SQLAlchemy/Alembic are deliberately not part of Task 1.

## Global Constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > approved Slice 2 plan / selected S2.0 ADR`.
- Roadmap §6.2 calls the Runner PoC `S2.1`; this plan uses the conversation/design label `S2.0` for the same pre-production gate. The mapping is explicit; no extra capability is introduced.
- S2.0 runs authoritative recovery evidence on **native Linux + systemd**. WSL2 is valid for ordinary development and deterministic harness tests, but not for authoritative host-reboot evidence.
- Task 1 is one formal Codex task. Its internal checkpoints and P0–P13 scenarios are not separate implementation tasks, commits, or governance closures.
- Task 1 must not create or modify production `src/labpilot/**`, migrations, PostgreSQL schema, API routes, Coordinator production recovery logic, or Batch/Trial/Attempt models.
- The PoC harness is test/evidence infrastructure and is not a production runtime dependency.
- No generic Runner backend registry, plugin API, scheduler, worker pool, remote-runner API, multi-host abstraction, SSH path, or generic supervision framework.
- Candidate A is evaluated first. A1 → A2 escalation is allowed only for durable X/H evidence retention/correlation. A3 or Candidate B implementation requires a focused plan amendment if A1/A2 evidence proves it necessary; Codex must not invent either mechanism autonomously.
- PostgreSQL remains the future durable execution ledger. Task 1 may use only a PoC durable intent store; that store is not a second domain ledger.
- SupervisorExecutionIdentity `X` is derived deterministically from an Attempt-like UUID plus a stable installation namespace; PID, process start time, and systemd InvocationID are observation facts only.
- `execution_spec_hash H` is a versioned canonical SHA-256 over non-secret frozen launch semantics. Secret values never enter X, H, ordinary supervisor metadata, evidence files, or logs.
- `NOT_FOUND` is never equivalent to `NEVER_ACCEPTED_PROVEN`.
- Once supervisor dispatch may have occurred, absence never authorizes blind recreation. Unknown launch/control outcome must be followed by observation, not retry.
- Same X + same H reconciles existing execution. Same X + different H is identity conflict and fails closed.
- Destructive stop/kill requires an `ACTIVE_MATCH` for the same X/H. `IDENTITY_CONFLICT` or `UNKNOWN` must not trigger destructive control.
- Mandatory normal recovery scenarios P3/P6/P7/P10 may not pass as `UNKNOWN` for a selected topology.
- Host reboot does not require process survival and must never cause automatic same-Attempt resurrection.
- No `Restart=always` or equivalent supervisor policy may implement experiment retry.
- Native-host commands must use structured argv and must not use `shell=True`.
- The PoC harness must never invoke `sudo`, `reboot`, `shutdown`, or edit `/etc` automatically. P11 contains an explicit human reboot gate.
- Raw host logs are bounded evidence, not a repository dump. Do not commit secrets or unrestricted journal/system logs.
- Do not push Task 1 implementation until its required review gate passes.
- Status is updated once at Slice 2 acceptance, not after S2.0.
- On WSL, do not use `git commit -m "..."`; construct a message file and use `git commit --file=<path> --cleanup=verbatim`.

---

## 1. Stage-gated plan policy and final Slice 2 task granularity

The final Slice 2 delivery shape remains five formal tasks:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

This revision fully specifies **Task 1 only**. That is intentional. Task 1 exists to discover the real supervisor/evidence contract. Writing exact production interfaces for Tasks 2–5 before that ADR would force Codex to implement assumptions that S2.0 is explicitly supposed to test.

After Task 1 is accepted, amend this same plan with exact Task 2–5 files/interfaces/tests while preserving the five-task outer shape. An amendment is not permission to split each design subsection into a new formal task.

### Reserved future task boundaries — non-executable in this revision

These are scope boundaries, not implementation instructions:

- **Task 2:** approved `ExperimentVersion` → Batch/Trial identity → non-launchable Attempt preparation → exact materialized source → verified manifest → versioned `FrozenExecutionSpec` → launch-ready boundary. No physical process.
- **Task 3:** committed durable launch intent → selected supervision contract → launch/observe/stop/kill → terminal physical observation. This is the first production external-side-effect boundary and must inherit X/H and acknowledgement-loss semantics from Task 1.
- **Task 4:** durable DB expectation + selected physical observation → deterministic reconciliation, restart recovery, cancel/timeout composition, and thin API composition. No second execution truth source.
- **Task 5:** real fault injection and Slice-level acceptance: API/Coordinator death/restart, process exits while controller is down, restart during reconciliation, unknown physical identity, no duplicate launch, full CI, fresh PostgreSQL, deep review.

**Do not implement these four future slots from this plan revision.**

---

# Task 1: S2.0 Runner Supervision PoC + ADR

**Risk:** `Safety / Recovery Critical`

**Deliverable:** A deterministic local test harness plus real native-Linux evidence that either selects Candidate A1/A2 through a short ADR or stops at a precisely evidenced escalation gate. No production runtime code.

## Task 1 file / ownership map

**Create — PoC infrastructure only:**

- `tests/poc/__init__.py`
- `tests/poc/s2_runner/__init__.py`
- `tests/poc/s2_runner/contract.py`
  - closed PoC semantic types;
  - X projection;
  - versioned canonical H;
  - local request validation.
- `tests/poc/s2_runner/durable_state.py`
  - fsync-backed test durable intent/evidence state;
  - first-dispatch → dispatch-may-have-occurred transition;
  - no production Attempt semantics.
- `tests/poc/s2_runner/probe.py`
  - real supervised target process;
  - long/short exit, known exit code, optional child, structured start/terminal messages, independent-root marker.
- `tests/poc/s2_runner/systemd_cli.py`
  - direct Candidate A1/A2 systemd CLI harness only;
  - structured subprocess argv, never shell;
  - launch/query/stop/kill and optional journal evidence;
  - no generic backend interface.
- `tests/poc/s2_runner/evidence.py`
  - bounded JSONL evidence writer/reader;
  - fsync before injected crash when evidence is claimed durable;
  - Markdown summary input.
- `tests/poc/s2_runner/scenario_runner.py`
  - CLI/orchestrator for P0–P13;
  - fresh child controller processes for crash/restart scenarios;
  - explicit two-phase P11 reboot workflow;
  - mechanical A1/A2 evaluation.
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
  - bounded evidence summary; no raw secret-bearing host dump.
- `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`
  - short subordinate ADR; final disposition only when the required contract is proven.

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

If Task 1 appears to require any of those files, stop and classify that need before editing. Do not fold production changes into the PoC.

---

## 2. Exact PoC contract

### 2.1 `contract.py`

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
    credential_slots: tuple[str, ...] = ()


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

- `canonical_execution_spec()` serializes a JSON object with sorted keys, UTF-8, separators `(",", ":")`, and the explicit `schema_version`.
- `environment` and `credential_slots` are canonicalized in sorted order. Duplicate environment keys are rejected before hashing.
- `schema_version` must equal `EXECUTION_SPEC_SCHEMA_VERSION` in Task 1.
- `execution_spec_hash()` is lowercase hexadecimal SHA-256 over the canonical bytes.
- No field named or typed as a secret value exists in `FrozenLaunchSpec`. The PoC uses only non-secret environment values. Credential slot names may be present; credential values may not.
- `supervisor_execution_identity()` must include the full lowercase hex UUID and a full SHA-256 of the explicit installation namespace. Do not truncate either source identity.
- Use this exact PoC projection shape:

```text
labpilot-poc-<sha256(installation_namespace UTF-8)>-<attempt_uuid_hex>
```

- `systemd_unit_name(X)` returns `X + ".service"` and rejects anything that is not an X produced by the projector.
- `validate_request_hash()` raises `ValueError("execution_spec_hash_mismatch")` before any supervisor call when the asserted H differs.

The projection is a PoC mapping only. S2.0 ADR may later freeze the production spelling, but it must preserve deterministic/collision-safe/no-loss properties.

### 2.2 `durable_state.py`

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

1. State root is explicit; the native CLI has no default `/tmp` or `/run` state path.
2. Persist JSON to a temporary file in the same directory.
3. `flush()` and `os.fsync()` the temporary file.
4. `os.replace()` onto the final path.
5. Open/fsync the containing directory after replacement.
6. `mark_dispatch_may_have_occurred()` must durably complete **before** `systemd_cli.launch()` is invoked.
7. There is no transition back to `FIRST_DISPATCH_AVAILABLE`.
8. The store never infers `NEVER_ACCEPTED_PROVEN` from missing supervisor state.

This store exists only to make injected controller-death/reboot tests valid under R2. It is not a production Attempt store.

### 2.3 `probe.py`

`python -m tests.poc.s2_runner.probe` accepts:

```text
--execution-identity X
--execution-spec-hash H
--start-marker PATH
--sleep-seconds FLOAT
--exit-code INT
--child-sleep-seconds FLOAT   # optional; default 0
--print-env KEY               # repeatable
```

Behavior:

1. Append one JSON line to `start-marker` containing `event=probe_started`, X, H, own PID, and timestamp; flush/fsync the file.
2. Print one JSON line with the same `probe_started` X/H record to stdout and flush.
3. Print requested non-secret environment key/value observations only.
4. If `child-sleep-seconds > 0`, spawn exactly one child whose lifetime is bounded by that duration; the child is a descendant, not an independent root marker.
5. Sleep for `sleep-seconds`.
6. Before normal exit, print `probe_terminal` with X, H, requested exit code, and timestamp; flush stdout.
7. Return exactly the requested exit code.

No signal handler may manufacture a `probe_terminal` record when the process is force-killed. Physical evidence must reflect what actually happened.

### 2.4 `systemd_cli.py`

This file is a narrow PoC adapter, not a production `Runner` abstraction.

Expose:

```python
from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


class SystemdCli:
    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        python_executable: str,
        use_journal_evidence: bool,
    ) -> None: ...

    def launch(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
        start_marker: Path,
        sleep_seconds: float,
        exit_code: int,
        child_sleep_seconds: float = 0.0,
        collect: bool = False,
    ) -> ControlOutcome: ...

    def observe(self, *, execution_identity: str, execution_spec_hash: str) -> PhysicalObservation: ...

    def stop(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        force: bool,
    ) -> ControlOutcome: ...
```

Required command rules:

- Invoke `systemd-run`, `systemctl`, and `journalctl` through argv arrays only; `shell=True` is forbidden.
- Unit name is derived only through `systemd_unit_name(X)`.
- Every unit has `Restart=no`.
- Unit control must cover the whole target cgroup/process tree; do not target a PID directly.
- The probe receives X and H as non-secret metadata and emits them to the journal/stdout path.
- For P7, `collect=True` may use systemd's transient-unit collection mechanism only when host characterization proves the command supports it. If unsupported, P7 cannot be claimed PASS.
- `launch()` calls `validate_request_hash()` before command execution.
- `observe()` first queries current unit properties; the implementation must capture at least `LoadState`, `ActiveState`, `SubState`, `Result`, `MainPID`, `InvocationID`, and exit-result properties available on the target host.
- A matching live unit is `ACTIVE_MATCH` only when evidence binds both X and H.
- A current unit/evidence with a different H is `IDENTITY_CONFLICT`.
- A missing current unit is never by itself `NEVER_ACCEPTED_PROVEN`.
- When `use_journal_evidence=False`, missing/insufficient current-unit evidence becomes `UNKNOWN`.
- When `use_journal_evidence=True`, query bounded journal records for the exact unit and parse only structured PoC records containing exact X/H. Do not infer H by fuzzy command-line matching.
- `TERMINAL_MATCH` requires a structured terminal record for exact X/H plus terminal physical evidence; do not classify terminal solely from a unit name.
- Journal parsing must be bounded by the exact unit and a bounded line/entry count; do not read the full host journal.
- `stop()` calls `observe(X,H)` first. It may issue systemd stop/kill only for `ACTIVE_MATCH`. `TERMINAL_MATCH` returns without destructive control. `IDENTITY_CONFLICT` and `UNKNOWN` fail closed without stop/kill.
- Generic command timeout/transport errors map to `CONTROL_UNKNOWN`, not `DEFINITE_NON_ACCEPTANCE`.
- Deterministic local validation/permission rejection that is proven to happen before dispatch may map to `CONTROL_REJECTED` or `DEFINITE_NON_ACCEPTANCE`; ordinary `systemd-run` nonzero status is not automatically proof of non-acceptance.

### 2.5 `evidence.py`

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

Each append is JSONL, flushed/fsynced before an injected failure when later recovery relies on the record. Evidence must be bounded and secret-free. The writer rejects keys named `secret`, `token`, `password`, `api_key`, or `authorization` case-insensitively anywhere in `detail`; this is a PoC safety guard, not a general DLP system.

### 2.6 `scenario_runner.py`

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

`characterize` records, at minimum:

```text
kernel release
systemd version
current boot_id
effective user/uid
systemd-run/systemctl/journalctl executable paths
whether systemd-run supports the collection option used by P7
state/evidence root mount target/filesystem type
whether /var/log/journal exists
bounded journald storage evidence
current boot list sufficient to assess persistence
```

It must also record that neither state root nor evidence root resolves under `/tmp` or `/run` for reboot-dependent evidence. If either is volatile for P11, P11 is invalid until the operator supplies a persistent root.

---

## 3. Task 1 TDD / implementation steps

### Checkpoint A — deterministic harness contract

- [ ] **Step 1: Verify the exact baseline before editing**

Run:

```bash
git status --short
git rev-parse HEAD
git merge-base --is-ancestor ff7909503c3b755bffb564423de3a56ce5bb9b4a HEAD
git diff --name-only 3a02feeb3342b99269fd47f4e0514088060e9877...HEAD
```

Expected before Task 1 implementation:

```text
clean worktree
Slice 1 acceptance commit is an ancestor
no unreviewed production diff
```

If HEAD contains only later approved planning docs, record the exact HEAD and continue. If production/test implementation has changed since the approved baseline, stop and report the drift before coding.

- [ ] **Step 2: Write failing contract tests**

Create `tests/unit/s2_runner_poc/test_contract.py` covering exactly:

```python
def test_same_namespace_and_attempt_produce_same_full_execution_identity(): ...
def test_different_namespace_changes_execution_identity(): ...
def test_different_attempt_changes_execution_identity(): ...
def test_execution_identity_contains_full_uuid_without_truncation(): ...
def test_semantically_same_spec_hashes_identically_after_env_reordering(): ...
def test_schema_version_changes_hash(): ...
def test_hash_mismatch_is_rejected_locally(): ...
def test_duplicate_environment_keys_are_rejected(): ...
```

Use fixed UUIDs and sentinel non-secret values. Do not use random test inputs for the canonical examples.

- [ ] **Step 3: Run the contract tests and verify RED**

Run:

```bash
uv run pytest tests/unit/s2_runner_poc/test_contract.py -q
```

Expected: collection/import failure because `tests.poc.s2_runner.contract` does not exist yet.

- [ ] **Step 4: Implement the minimal contract module**

Create package initializers and `tests/poc/s2_runner/contract.py` with the exact types/functions in §2.1. Use standard-library `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)` and UTF-8 encoding. Hash with `hashlib.sha256(...).hexdigest()`.

- [ ] **Step 5: Run contract tests GREEN**

Run:

```bash
uv run pytest tests/unit/s2_runner_poc/test_contract.py -q
```

Expected: all PASS.

### Checkpoint B — durable PoC intent before dispatch

- [ ] **Step 6: Write failing durable-state tests**

Create `tests/unit/s2_runner_poc/test_durable_state.py` covering:

```python
def test_create_round_trips_first_dispatch_intent(tmp_path): ...
def test_mark_dispatch_may_have_occurred_is_monotonic(tmp_path): ...
def test_missing_state_does_not_create_non_acceptance_evidence(tmp_path): ...
def test_replace_failure_does_not_report_new_phase_as_committed(tmp_path, monkeypatch): ...
```

The monotonic test must prove there is no API that transitions back to `FIRST_DISPATCH_AVAILABLE`.

- [ ] **Step 7: Run durable-state tests RED, then implement and run GREEN**

Run before implementation:

```bash
uv run pytest tests/unit/s2_runner_poc/test_durable_state.py -q
```

Expected: FAIL because module/classes are absent.

Implement `tests/poc/s2_runner/durable_state.py` exactly as §2.2, including file fsync and parent-directory fsync after `os.replace()`.

Run again:

```bash
uv run pytest tests/unit/s2_runner_poc/test_durable_state.py -q
```

Expected: PASS.

### Checkpoint C — physical adapter semantics without a real host

- [ ] **Step 8: Write failing systemd adapter tests with an injected command runner**

Create `tests/unit/s2_runner_poc/test_systemd_cli.py`. The fake runner must record every argv vector and return scripted `CompletedProcess` values. Cover at least:

```python
def test_hash_mismatch_performs_zero_commands(): ...
def test_launch_uses_structured_argv_and_restart_no(): ...
def test_active_matching_x_h_becomes_active_match(): ...
def test_active_different_h_becomes_identity_conflict(): ...
def test_not_found_without_journal_is_unknown(): ...
def test_journal_exact_x_h_terminal_record_can_be_terminal_match(): ...
def test_journal_different_h_is_identity_conflict(): ...
def test_generic_launch_error_is_control_unknown_not_non_acceptance(): ...
def test_stop_active_match_issues_control(): ...
def test_stop_terminal_match_is_noop(): ...
def test_stop_identity_conflict_never_issues_destructive_control(): ...
def test_stop_unknown_never_issues_destructive_control(): ...
```

The structured-argv assertion must verify the fake runner receives a sequence and never a shell command string.

- [ ] **Step 9: Run adapter tests RED, implement `systemd_cli.py`, run GREEN**

Run:

```bash
uv run pytest tests/unit/s2_runner_poc/test_systemd_cli.py -q
```

Expected before implementation: FAIL.

Implement the narrow adapter from §2.4. Do not create a `RunnerBackend` Protocol/base class/registry. Keep command parsing private to this file.

Run again:

```bash
uv run pytest tests/unit/s2_runner_poc/test_systemd_cli.py -q
```

Expected: PASS.

### Checkpoint D — probe, crash orchestration, and mechanical scenarios

- [ ] **Step 10: Write failing probe/scenario contract tests**

Create `tests/unit/s2_runner_poc/test_scenario_runner.py` covering pure orchestration with fakes:

```python
def test_probe_start_marker_is_one_independent_root_record(tmp_path): ...
def test_first_dispatch_phase_is_persisted_before_launch_call(tmp_path): ...
def test_p3_recovery_observes_before_any_possible_replay(tmp_path): ...
def test_p4_concurrent_same_x_h_never_generates_fallback_identity(tmp_path): ...
def test_p5_same_x_different_h_never_stops_or_relaunches(tmp_path): ...
def test_p8_pre_dispatch_validation_can_prove_never_accepted(tmp_path): ...
def test_p8_not_found_after_possible_dispatch_is_unknown(tmp_path): ...
def test_p9_does_not_claim_cancel_caused_exit_without_evidence(tmp_path): ...
def test_reboot_verify_never_calls_launch(tmp_path): ...
def test_evidence_writer_rejects_secret_shaped_keys(tmp_path): ...
```

- [ ] **Step 11: Implement `probe.py`, `evidence.py`, and `scenario_runner.py` minimally**

P3 implementation must use a fresh child controller process and this ordering:

```text
persist FIRST_DISPATCH_PROVEN intent
→ durably mark DISPATCH_MAY_HAVE_OCCURRED
→ child dispatches systemd launch
→ probe start marker proves target root started
→ child exits with os._exit(73) before durable success acknowledgement
→ fresh recovery process observes X/H
→ same logical launch path must reconcile, not create
```

P4 launches two fresh child controllers for the same X/H and verifies both the authoritative unit count and independent start-marker count remain one.

P5 runs X/H1 then attempts X/H2 and verifies conflict with no stop/replacement/relaunch.

P6 uses a very short successful/known-exit probe so recovery begins after the physical process is already terminal.

P7 uses collection/unit-GC behavior proven available by `characterize`; after current unit state disappears, recovery must still classify exact X/H as terminal for a selected evidence stack.

P8 has two cases: deterministic local/pre-dispatch rejection with supervisor command count zero → `NEVER_ACCEPTED_PROVEN`; and possible-dispatch + current NOT_FOUND → `UNKNOWN`.

P9 executes stop-first, natural-exit-first, and near-concurrent variants. Evidence records `stop_requested` and terminal observation separately; it does not invent causal winner.

P10 launches a bounded process, terminates the controller/orchestrator observation loop while the process continues, waits for the process to exit, then starts a fresh recovery process and requires `TERMINAL_MATCH`.

P11 is split into `reboot-prepare` and `reboot-verify`. `reboot-prepare` persists X/H, original boot ID, invocation observation if available, and start-marker count, then exits with an instruction for the human operator. `reboot-verify` may only observe and classify; it must contain no launch path.

- [ ] **Step 12: Run the focused deterministic Task 1 suite**

Run:

```bash
uv run pytest tests/unit/s2_runner_poc -q
uv run ruff check tests/poc/s2_runner tests/unit/s2_runner_poc
uv run mypy tests/poc/s2_runner
```

Expected: all PASS.

Then run existing non-PostgreSQL regressions:

```bash
uv run pytest tests/unit tests/architecture -q
```

Expected: all PASS. Task 1 does not justify changing production code to fix an unrelated pre-existing failure; if a new failure appears, investigate it before any remediation.

- [ ] **Step 13: Commit the deterministic harness locally; do not push**

Use a message file, not `git commit -m`:

```bash
printf '%s\n' 'test: add slice two runner supervision poc' > /tmp/labpilot-s2-task1-message.txt
git add tests/poc tests/unit/s2_runner_poc
git commit --file=/tmp/labpilot-s2-task1-message.txt --cleanup=verbatim
```

Record the commit SHA. Do not modify status docs.

---

## 4. Native Linux evidence execution

The following runs on the selected native Linux host. Do not claim S2.0 acceptance from WSL evidence.

Choose persistent, non-secret roots explicitly, for example under the operator's persistent home state directory. The harness must record the actual filesystem/mount information; the example path is not an authority requirement.

```bash
STATE_ROOT="$HOME/.local/state/labpilot-s2-poc/state"
EVIDENCE_ROOT="$HOME/.local/state/labpilot-s2-poc/evidence"
INSTALLATION_NAMESPACE="labpilot-s2-poc-native"
mkdir -p "$STATE_ROOT" "$EVIDENCE_ROOT"
```

### Checkpoint E — host characterization and normal execution

- [ ] **Step 14: Run P0/P1/P2/P12/P13 with A1 evidence**

Run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack systemd characterize

for scenario in p1 p2 p12 p13; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd "$scenario"
done
```

Expected:

```text
P1 stable/collision-safe X PASS
P2 ACTIVE_MATCH + argv/workdir/env/log observation PASS
P12 no harness use of sudo/Docker socket/unconstrained host shell; actual supervisor permission result recorded
P13 representative environment propagation PASS
```

If the intended unprivileged user cannot exercise the required supervisor operations, do not rerun the harness as root and call that a permission PASS. Record the exact denied operations. If proving a narrow authorization boundary requires host policy changes, stop and request explicit operator configuration/review; the harness must not edit `/etc`.

### Checkpoint F — duplicate/ack-loss/conflict safety

- [ ] **Step 15: Run P3/P4/P5/P8**

Run each with `--evidence-stack systemd`.

Required:

```text
P3 ACTIVE_MATCH or TERMINAL_MATCH; independent root starts = 1
P4 authoritative execution units <= 1; independent root starts = 1
P5 IDENTITY_CONFLICT; no stop/replacement/relaunch
P8 proven pre-dispatch rejection distinguishable from ambiguous NOT_FOUND
```

Any second independent target root for the same X is an immediate S2.0 FAIL. Do not continue by adding retries/locks/leases to hide the result.

### Checkpoint G — terminal retention and A1→A2 escalation

- [ ] **Step 16: Run P6/P7/P10 with A1**

Run:

```bash
for scenario in p6 p7 p10; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd "$scenario"
done
```

If P6/P7/P10 all produce `TERMINAL_MATCH`, A1 remains eligible.

If and only if live launch/identity/dedupe/permissions are sound but exact X/H terminal retention/correlation is insufficient, evaluate A2:

```bash
for scenario in p6 p7 p10; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$STATE_ROOT" \
    --evidence-root "$EVIDENCE_ROOT" \
    --installation-namespace "$INSTALLATION_NAMESPACE" \
    --evidence-stack systemd+journal "$scenario"
done
```

A2 is eligible only if host characterization proves the journal configuration/retention used by the ADR. A journal record found by accident is not enough.

If A2 still cannot deterministically return `TERMINAL_MATCH` for P6/P7/P10, **STOP Task 1 before writing A3 or a daemon**. Report whether the failure is bounded witness retention/correlation (candidate A3 amendment) or host-control topology/privilege/serialization (candidate B amendment). This revision does not authorize implementing A3/B.

### Checkpoint H — stop/natural-exit convergence

- [ ] **Step 17: Run P9 on the currently eligible evidence stack**

Required:

```text
one physical execution
one converged physical terminal result
no duplicate launch
no fabricated claim that cancel caused exit when ordering is unprovable
```

### Checkpoint I — P11 human reboot gate

- [ ] **Step 18: Prepare P11, then STOP for the human reboot**

Run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack <selected-a1-or-a2-stack> reboot-prepare
```

The command must end after printing the persisted pre-reboot evidence location. **Codex must not issue `reboot`, `shutdown`, `systemctl reboot`, or equivalent.**

Human operator manually reboots the native Linux host.

After login, run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack <selected-a1-or-a2-stack> reboot-verify
```

Required:

```text
original process survival is not required
same Attempt/X is not automatically relaunched
independent start-marker count does not increase
no new activation is treated as the old execution
classification is terminal/lost or an ADR-explicit allowed UNKNOWN
```

If A2 depends on persistent journal evidence, P11 must also prove the pre-reboot X/H evidence is actually queryable after reboot; do not infer persistence from configuration text alone.

---

## 5. Evidence report and ADR gate

- [ ] **Step 19: Generate and review the bounded evidence summary**

Run:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$STATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" \
  --installation-namespace "$INSTALLATION_NAMESPACE" \
  --evidence-stack <selected-a1-or-a2-stack> summarize
```

Use the summary to create:

`docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

The committed report must include a table with P0–P13, expected classification, observed classification, evidence stack, duplicate-root count where applicable, and PASS/FAIL. It must explicitly record:

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

Do not copy unbounded `journalctl` output into Git. Quote only the bounded fields needed to prove the classification.

- [ ] **Step 20: Write the short subordinate ADR only if a final disposition is justified**

Create:

`docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

Required front matter:

```text
Status: Proposed for review
Planning baseline: <Task 1 deterministic harness commit SHA>
Design authority: docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md
Disposition: SELECT_A1 | SELECT_A2 | SELECT_A3 | SELECT_B | BLOCKED
```

Under this plan revision, Codex may write a final ADR only for:

```text
SELECT_A1
SELECT_A2
BLOCKED
```

`SELECT_A3` or `SELECT_B` requires a focused plan amendment that defines the actual witness/daemon mechanism and its own crash ordering before implementation. Do not write `SELECT_A3`/`SELECT_B` based on an unimplemented idea.

For SELECT_A1/A2 the ADR must record:

```text
Selected topology: A
Selected physical evidence stack: systemd | systemd + persistent journald
SupervisorExecutionIdentity projection/namespace rule
exact X/H binding evidence
ack-lost behavior
meaning of NOT_FOUND
terminal retention contract
P3/P6/P7/P10 required classifications
finite allowed-UNKNOWN set
host reboot behavior
minimum demonstrated privilege boundary
why lighter/failed candidate was rejected or not needed
explicitly deferred production concerns
```

No selected topology may list P3/P6/P7/P10 in the allowed-UNKNOWN set.

- [ ] **Step 21: Run final Task 1 verification**

Run deterministic verification again:

```bash
uv run pytest tests/unit/s2_runner_poc -q
uv run pytest tests/unit tests/architecture -q
uv run ruff check tests/poc/s2_runner tests/unit/s2_runner_poc
uv run mypy tests/poc/s2_runner
```

Then inspect scope:

```bash
git status --short
git diff --name-only 3a02feeb3342b99269fd47f4e0514088060e9877...HEAD
git diff --check
```

Task 1 implementation/evidence scope must be limited to:

```text
tests/poc/**
tests/unit/s2_runner_poc/**
docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
```

plus this already-approved plan/spec history. Any `src/`, migration, dependency, compose, Dockerfile, or status diff is out of scope.

- [ ] **Step 22: Commit the evidence/ADR locally; do not push**

Use a message file:

```bash
printf '%s\n' 'docs: record slice two runner supervision decision' > /tmp/labpilot-s2-task1-adr-message.txt
git add \
  docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md \
  docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
git commit --file=/tmp/labpilot-s2-task1-adr-message.txt --cleanup=verbatim
```

If Task 1 stopped before a final ADR because A3/B needs a plan amendment, do not create a fake final ADR commit. Commit only the harness/evidence that supports the blocker after review instructions are agreed.

- [ ] **Step 23: Task 1 review handoff**

Provide the reviewer:

```text
Task 1 harness commit SHA
Task 1 ADR/evidence commit SHA if present
exact changed-file list
native host characterization
P0–P13 table
F1–F8 disposition
R1–R4 disposition
permission result
selected evidence stack
allowed UNKNOWN set
proof independent target-root count never exceeded 1
all deterministic verification command results
```

Review is finding-specific/deep because S2.0 is recovery-critical. Do not push until the required review says APPROVE.

---

## 6. Task 1 acceptance checklist

Task 1 is accepted only when all applicable items below are proven with fresh evidence:

```text
[ ] Native Linux, not WSL, is the authoritative supervision/reboot host.
[ ] X projection deterministic, stable, collision-safe, no lossy truncation.
[ ] H is versioned canonical SHA-256 of supplied non-secret FrozenLaunchSpec.
[ ] H mismatch causes zero supervisor interaction.
[ ] PID/start time/InvocationID never serve as LabPilot durable identity.
[ ] Durable PoC intent is fsync-backed before injected controller death/reboot.
[ ] Dispatch-may-have-occurred is persisted before supervisor dispatch.
[ ] NOT_FOUND alone never becomes NEVER_ACCEPTED_PROVEN.
[ ] P3 accepted-before-ack recovers ACTIVE_MATCH or TERMINAL_MATCH.
[ ] P4 concurrent same-X/same-H creates at most one authoritative execution/root.
[ ] P5 same-X/different-H fails closed and does not kill/replace the original.
[ ] P6 very-short process recovers TERMINAL_MATCH.
[ ] P7 terminal after unit GC recovers TERMINAL_MATCH.
[ ] P8 distinguishes positive pre-dispatch non-acceptance from ambiguous absence.
[ ] P9 converges without fabricated causal certainty.
[ ] P10 process exit while controller down recovers TERMINAL_MATCH.
[ ] P11 reboot never silently resurrects/relaunches the same Attempt/X.
[ ] P12 minimum privilege boundary is demonstrated; no broad socket/root shortcut.
[ ] P13 representative runtime/env/stdout/stderr/exit observation is demonstrated.
[ ] stop/kill is issued only after ACTIVE_MATCH for exact X/H.
[ ] P3/P6/P7/P10 are not in allowed UNKNOWN cases.
[ ] F1–F8 PASS for SELECT_A1/SELECT_A2.
[ ] Evidence report is bounded and secret-free.
[ ] PoC code remains outside production src/migrations.
[ ] No A3 witness or Candidate B daemon was invented without a plan amendment.
```

### Task 1 fail/stop semantics

Immediately stop rather than patch around evidence when any of these occurs:

```text
- duplicate independent target root for one X;
- same-X/different-H execution is replaced or killed;
- P3/P6/P7/P10 can only become UNKNOWN under the candidate stack;
- Candidate A requires overbroad host privilege;
- current supervisor contract cannot bind exact X/H;
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

fundamental invariant cannot be proven even with the admitted topology family
→ ADR BLOCKED; S2.1 production work remains unauthorized
```

---

## 7. Plan self-review checklist

Before asking for plan approval, verify this document against the approved S2.0 design:

```text
[ ] Sections 1–5 of the design spec map to explicit Task 1 requirements.
[ ] FIRST_DISPATCH_PROVEN and NEVER_ACCEPTED_PROVEN are separate from physical observation.
[ ] NOT_FOUND never authorizes replay.
[ ] X/H binding is required for ACTIVE/TERMINAL/destructive control.
[ ] R2 durable PoC state is explicit.
[ ] R3 stop/natural-exit race does not fabricate causality.
[ ] R4 F5 covers concurrent replay, ack-lost replay, and different-H conflict.
[ ] P3/P6/P7/P10 cannot pass as UNKNOWN.
[ ] A1→A2 is retention/correlation-only.
[ ] A3 is not silently implemented as a daemon.
[ ] B would have to record a separate physical evidence stack if later admitted.
[ ] PoC harness is not a production dependency.
[ ] No production schema/interface has been invented before the ADR.
[ ] Formal task count remains five; Task 1 internal steps are not task proliferation.
```

---

## 8. Execution authorization after this plan revision

If this plan revision receives focused approval, the only authorized implementation instruction is:

```text
Execute Task 1 — S2.0 Runner Supervision PoC + ADR.
Do not start Task 2.
Do not modify production runtime/schema.
Stop at the A3/B escalation gate if A1/A2 cannot satisfy the frozen contract.
```

After Task 1/ADR acceptance, return to planning and amend this same document with exact files/interfaces/tests for Task 2–5. Do not expand the formal task count merely because those future tasks contain multiple design sections or test scenarios.
