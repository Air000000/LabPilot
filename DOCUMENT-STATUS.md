# LabPilot 文档状态

> 当前状态：R0 仍为 V1 rebaseline authority root；Slice 1 已 Accepted；Slice 2 bounded MVP 已验证并集成到 `main`，production hardening deferred；Slice 3 Result Plane implementation 已完成。Competition Core Demo Runtime/E2E 已验收并冻结。P1 Result-Aware Experiment Iteration implementation completed and accepted; deterministic two-iteration acceptance + regression/full-CI evidence closed。

| 文档 | 状态 | 用途 |
|---|---|---|
| Master Blueprint v0.2 | Historical high-level input for remaining V1 | 保留历史产品/架构上下文；不自动约束尚未实现的 V1 工作 |
| V1 Scope v0.2 | Approved / Frozen / Historical contract for accepted work only | 继续解释在其约束下已接受的实现 |
| V1 Scope v0.3 | Approved / Current | 剩余 V1 的最高产品范围与验收 authority |
| Architecture 02–07 v0.2 | Approved / Frozen / Historical for remaining V1 | 对 remaining V1 默认 historical/informative；仅在 v0.3/current Slice authority 显式引用具体 invariant 时重新进入当前 tranche |
| Architecture 08 v0.2 | Historical / selectively incorporated | v0.3 仅保留 human-input、abstention、no-progress、bounded-budget、unknown-side-effect 等显式行为语义 |
| Architecture 09 v0.1 | Historical / Deferred / non-normative for V1 Core | Evidence/Claim Closure 移出 V1 Core；文档本体不修改 |
| Architecture 10 v0.3 | Approved Architecture Rebaseline / Current | 剩余 V1 的 closed-world architecture authority |
| Architecture Index v0.2 | Historical dependency/index input | 继续服务历史冻结依赖，不决定 remaining-V1 authority |
| Master Roadmap v0.2.3 | Historical authority for accepted work only | 保留历史工作包与已接受工作的上下文；不再决定剩余 V1 执行顺序 |
| Master Roadmap v0.3 | Approved / Current | 以 R0 + Slice 1/2/3 管理剩余 V1 |
| S2→S3 Sequencing Amendment v0.3.1 | Approved / Current narrow sequencing amendment | 仅覆盖 Master Roadmap v0.3 的 S2→S3 planning gate；bounded Slice2 MVP closeout 可解锁 Slice3 JIT planning，不构成 Slice2 production acceptance |
| Slice3 Result Plane Design | Approved after focused re-review | 冻结 Slice3 Core Result Plane 的对象、边界、验证、恢复、API/SSE 与验收语义 |
| Slice3 Detailed Implementation Plan | Approved after focused plan review | 冻结 Slice3 implementation 的 task/file/interface/TDD/acceptance 执行顺序；focused review C1–C5 为后续窄 authority |
| Competition Core Demo closeout | **Accepted / Frozen** | Demo-specific Runtime/E2E closure：browser Golden Path + same immutable ExperimentVersion recovery replay + exact-authority CI；不构成 Slice 2 production acceptance |
| P1 Result-Aware Experiment Iteration Design | **Approved / Current** | focused design review 已 APPROVE；P1-RESULT-001 CLOSED；作为当前 P1 设计 authority |
| P1 Result-Aware Detailed Implementation Plan | **Approved / Current** | focused plan review APPROVE；P1-PLAN-001 CLOSED；正式授权按计划实施 P1-A/B |
| Master Roadmap v0.2.1 | Historical frozen dependency | 仅用于历史 WP0 dependency verification |
| WP0 Foundation v0.3.1 | Completed / Historical Accepted Execution Plan | WP0 已完成；不再标记 Ready for Execution |

## 1. Current authority rule

对所有尚未实现的 V1 工作：

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> S2→S3 Sequencing Amendment v0.3.1 where applicable
> approved Slice3 Result Plane design
> approved Slice3 Detailed Implementation Plan
> focused plan review clarifications C1-C5
```

下位 Slice plan / review 只能细化当前 Slice，不能扩大 Scope、削弱 Core invariant 或改变上位 Roadmap acceptance。Sequencing Amendment v0.3.1 只覆盖 S2→S3 planning gate，不改变其他 Roadmap requirement。

更早的 v0.2 architecture、roadmap、implementation plans 与 Architecture 08/09 对 remaining V1 默认只具有 historical / informative 价值。只有当前 v0.3 authority 或已批准 Slice authority **显式按名称引用具体 invariant** 时，该 invariant 才重新进入当前实施 tranche。

历史 contract 继续对在其约束下已经验收的 WP0 与 WP1-A Task 0–10 保持规范性。

## 2. R0 review and finalization state

```text
Broad architecture review: COMPLETED
Disposition: APPROVED AFTER TARGETED REMEDIATION
Targeted remediation: COMPLETED
Finding-specific re-review: APPROVE
B1–B5: PASS
Direct regressions: NONE
Critical: 0
Important: 0
Second broad architecture review: NOT WARRANTED
R0 status: APPROVED / CURRENT
```

完成本状态转换的 Git commit 本身是 **R0 authority root**。本文件不写入同一 commit 的自身 SHA；Git commit identity 提供机器可验证基线。

Slice-specific detailed plans must record their exact planning baseline commit and remain subordinate to the authority rule above.

旧 `*.dependencies.sha256` / `FINAL-DOCUMENTS.sha256` 继续作为其历史 tranche / release bundle 的完整性证据，不自动成为 remaining-V1 的语义 authority，也不建立新的级联 architecture hash graph。

## 3. WP1 / V1 execution state

- WP1-A Task 0–10: Completed / independently reviewed / pushed / CI passed
- WP1-A v0.2 Task 11–17: **Superseded / Never Executed**
- Task 11: Not executed; superseded by V1 v0.3
- Slice 1 Detailed Implementation Plan: Approved / Executed
- Slice 1: Accepted
- Slice 1 acceptance commit: `ff7909503c3b755bffb564423de3a56ce5bb9b4a`
- Slice 2 Detailed Plan: Approved
- Slice 2 bounded MVP implementation path: Validated / Integrated
- Slice 2 integration commit: `3e3cebf32877cfe8d65f76a393eefb20ac41d798`
- Slice 2 production acceptance: **NO**
- Slice 2 production hardening: Deferred
- S2→S3 Sequencing Amendment: Approved / Current for the planning gate only
- Slice 3 Result Plane Design: **Approved after focused re-review**
- Slice 3 focused design re-review: `docs/superpowers/specs/2026-09-01-labpilot-v1-slice3-result-plane-focused-rereview.md`
- Slice 3 Detailed Implementation Plan: **Approved after focused plan review**
- Slice 3 plan: `docs/superpowers/plans/2026-09-01-labpilot-v1-slice3-result-plane.md`
- Slice 3 focused plan review: `docs/superpowers/plans/2026-09-01-labpilot-v1-slice3-result-plane-plan-review.md`
- Slice 3 Result Plane implementation: **COMPLETED**
- Slice 3 canonical CI: **PASS**
- Slice 3 real native validation: bounded / partial but meaningful
- Slice 3 Product defects discovered by native testing: fixed
- Slice 3 final full native acceptance: not fully closed because the B6 acceptance supervisor failed before child start
- Slice 3 additional native rerun: not required for integration
- Slice 3 deferred issue: acceptance-supervisor durable-marker reliability; this affects acceptance-evidence reliability, not established Product behavior
- Slice 3 migration rollback: `DB_DOWNGRADE_AFTER_DUPLICATE_IDENTITY=PROHIBITED`; after duplicate semantic identities exist under `0012_version_identity_nonunique`, restoring the former global UNIQUE constraint is not generally valid. Application rollback may leave the DB at `0012`.
- Task 8 accepted implementation commit: `ab3e773ee957b02f257df3f60f3ea3492626bcfa`
- Task 9 accepted implementation commit: `949d22a676141cc279086775135610726b518c31`
- Task 10 accepted implementation commit: `6d85b88c238714ebbd1fb1d7b90b0f93308f5006`
- Task 10 governance/status closure commit: `b0c4e89dc2f4c9d680b0a491bdd0815eff869a2d`
- Competition Core Demo Runtime/E2E: **ACCEPTED / FROZEN**
- Competition Core Demo product evidence baseline: `c92bf45b63053b1ae00209660ed351cf309f91c8`
- Competition fresh browser Golden Path: **PASS**
- Competition same immutable ExperimentVersion Coordinator recovery replay: **PASS**
- Competition exact-authority full CI: **PASS** (`264 passed`, `1 skipped` opt-in external OpenCode Go smoke, `5 deselected`; dependency guard PASS)
- Competition recovery invariant: Coordinator restart reconciled the same running Attempt / execution identity / systemd InvocationID with physical start count remaining `1`; no duplicate physical launch observed
- Competition claim boundary: browser Golden Path Batch itself did **not** undergo the Coordinator restart; accepted wording is `Fresh browser Golden Path PASS + same immutable ExperimentVersion recovery replay PASS.`
- Competition closure does **not** establish global exactly-once execution, Slice 2 production acceptance, Slice 2 production hardening completion, or an autonomous research-agent claim
- Competition closeout evidence: `docs/superpowers/evidence/2026-09-19-labpilot-competition-core-demo-closeout.md`
- Competition console integration into `main`: `26217975103b3bb53de768e223c64781ce27753f`
- Parallel Core Demo acceptance-contract convergence: `bf26f9ffcfed02542f4944e160226f3437355706`
- P1 Result-Aware Iteration Design: **APPROVED / CURRENT**
- P1 Result-Aware Iteration design doc: `docs/superpowers/specs/2026-09-20-labpilot-p1-result-aware-iteration-design.md`
- P1 Result-Aware Iteration focused design review: **APPROVE**; P1-RESULT-001 CLOSED; direct regressions NONE
- P1 Result-Aware Iteration detailed implementation plan: **APPROVED / CURRENT**
- P1 Result-Aware Iteration plan doc: `docs/superpowers/plans/2026-09-20-labpilot-p1-result-aware-iteration.md`
- P1 Result-Aware Iteration focused plan review: **APPROVE**; P1-PLAN-001 CLOSED; direct regressions NONE
- P1 Result-Aware Iteration implementation: **COMPLETED / ACCEPTED**
- P1 last production-code commit: `ae079be252fd62eff415ef040af4228ab45e2d61`
- P1 tranche acceptance/test baseline: `e8caaf668fed2b5e5878393fa0c47c001ba218e9`
- P1 deterministic two-iteration acceptance: **PASS**
- P1 Task 8 focused regression: **PASS**
- P1 Task 8 GitHub CI #312: **PASS**

Under Sequencing Amendment v0.3.1, the bounded Slice 2 MVP closeout is sufficient to unlock Slice 3. It does not retroactively complete the deferred Slice 2 production-hardening campaign or establish Slice 2 production acceptance.

The Git commit containing this status transition is the exact Slice3 implementation authority baseline. This file does not self-reference that commit SHA; Git history provides it. An implementation worktree must start from or contain that commit as an ancestor.

## 4. Next authorized action

The competition workstream is closed. Its Runtime/E2E evidence and UI are frozen unless a concrete regression or demo-critical defect is demonstrated.

P1 Result-Aware Experiment Iteration implementation is accepted. Repository integration is completed through the normal reviewed PR process; any further remaining-V1 tranche requires its own planning/authority gate.

```text
implementation accepted
→ reviewed PR integration
→ separate planning/authority gate for any further tranche
```

Competition closure still does not complete deferred Slice 2 production hardening or establish Slice 2 production acceptance.

## 5. Historical Slice 1 foundation alignment

以下 alignment 项是 Slice 1 implementation 前/期间的有限对齐要求，现保留为已完成 Slice 1 的历史 planning context，不构成新的 global architecture work：

- 默认本地 API exposure 绑定 loopback（目标 Compose publish 为 `127.0.0.1:${LABPILOT_API_PORT:-8000}:8000`）或明确受控接口；
- Projection Worker 移出 Core default runtime topology；
- dependency CI 验证 `uv.lock` 与 `pyproject.toml` 一致；
- 实现 SSE 前刷新到经过测试、支持目标 SSE 路径的 FastAPI baseline；
- secret 与 Agent context/trace 分离；
- 最小 data-egress policy 生效。
