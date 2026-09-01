# LabPilot Master Roadmap v0.3 — S2→S3 Sequencing Amendment

```text
Version: v0.3.1
Status: Approved / Current narrow sequencing amendment
Date: 2026-09-01
Applies to: Master Implementation Roadmap v0.3 §2 and §7.1 only
Planning baseline: 3e3cebf32877cfe8d65f76a393eefb20ac41d798
```

## 1. Purpose

This amendment resolves one sequencing mismatch between the original v0.3 Roadmap wording and the bounded MVP-first execution strategy used after Slice 2.

It does not change Slice 2 recovery invariants, does not claim Slice 2 production acceptance, and does not expand Slice 3 scope.

## 2. Verified Slice 2 MVP closeout used by this amendment

The bounded Slice 2 MVP path has been validated and integrated into `main` at:

```text
3e3cebf32877cfe8d65f76a393eefb20ac41d798
```

The bounded closeout established the MVP capabilities needed by Slice 3 planning:

```text
real API + Coordinator + PostgreSQL + bounded Runner topology
real supervised execution
Coordinator restart/recovery of the original execution
no duplicate physical launch for the validated execution identity
product cancellation with no orphan/relaunch in the validated MVP path
```

This evidence is sufficient only for the downstream planning dependency described below.

## 3. Narrow override of the S2→S3 planning gate

For current V1 MVP-first sequencing, interpret the Roadmap milestone gate as:

```text
Slice 2 bounded MVP closeout validated and integrated
→ Slice 3 JIT design/planning authorized
```

Accordingly, the original unconditional wording in Master Roadmap v0.3 §7.1:

```text
S2 Accepted 后才写 Slice 3 Detailed Plan
```

is superseded for the remaining V1 planning sequence by:

```text
在当前 MVP-first sequencing 下，Slice 2 bounded MVP closeout
验证并集成后即可进入 Slice 3 JIT design / Detailed Implementation Plan drafting，
并绑定该 closeout/integration baseline。

Slice 2 production hardening 可继续 deferred；
该 sequencing 不构成 Slice 2 production acceptance。
```

The corresponding §2 milestone interpretation is therefore:

```text
S2 Recovery Ready for Slice3 planning
= bounded MVP recovery/no-duplicate/cancel path validated and integrated
```

It is not equivalent to full production-hardening acceptance.

## 4. What remains deferred

Slice 2 production-hardening work remains deferred, including the previously deferred extended native/formal acceptance campaign and other production-hardening checks not required to establish the bounded MVP path.

Those items are not prerequisites for the Slice 3 MVP unless a concrete dependency or failure mode discovered during Slice 3 requires one of them.

Nothing in this amendment marks those deferred items complete.

## 5. Authorization state after this amendment

```text
Slice 2 bounded MVP implementation path: VALIDATED / INTEGRATED
Slice 2 production acceptance: NO
Slice 2 production hardening: DEFERRED
Slice 3 JIT design/planning: AUTHORIZED
Slice 3 production implementation: NOT AUTHORIZED YET
```

Slice 3 implementation still requires:

```text
focused design re-review passes
→ Slice 3 Detailed Implementation Plan is written
→ plan is reviewed and approved
→ implementation is then explicitly authorized
```

## 6. Authority and non-expansion rule

For the sequencing question covered here, current authority is read as:

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> this v0.3.1 narrow sequencing amendment
> current approved Slice-specific plan / ADR
```

All Master Roadmap v0.3 requirements not explicitly changed by this amendment remain unchanged.

In particular, this amendment does not:

- reopen historical Architecture 07 as a generic processing-platform requirement;
- weaken Slice 2 recovery or no-duplicate-launch invariants;
- claim Slice 2 production acceptance;
- authorize Slice 3 production code by itself;
- add a new hash/identity graph;
- require ProcessingJob, CAS, Projection Worker, or generic result-platform infrastructure.
