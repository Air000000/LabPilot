# LabPilot 文档状态

> 当前状态：V1 v0.3 R0 Rebaseline 已通过 broad architecture review、targeted remediation 与 finding-specific re-review，现为剩余 V1 的 Current planning authority。

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
| Master Roadmap v0.2.1 | Historical frozen dependency | 仅用于历史 WP0 dependency verification |
| WP0 Foundation v0.3.1 | Completed / Historical Accepted Execution Plan | WP0 已完成；不再标记 Ready for Execution |

## 1. Current authority rule

对所有尚未实现的 V1 工作：

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> current approved Slice-specific detailed plan / ADR
```

下位 Slice plan / ADR 只能细化当前 Slice，不能扩大 Scope、削弱 Core invariant 或改变上位 Roadmap acceptance。

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

后续 Slice 1 Detailed Implementation Plan 必须记录：

```text
Planning baseline: <exact R0 authority commit SHA>
```

旧 `*.dependencies.sha256` / `FINAL-DOCUMENTS.sha256` 继续作为其历史 tranche / release bundle 的完整性证据，不自动成为 remaining-V1 的语义 authority，也不建立新的级联 architecture hash graph。

## 3. WP1 / V1 execution state

- WP1-A Task 0–10: Completed / independently reviewed / pushed / CI passed
- WP1-A v0.2 Task 11–17: **Superseded / Never Executed**
- Task 11: Not executed; superseded by V1 v0.3
- Slice 1 Detailed Implementation Plan: Not started; **now authorized for drafting**
- Slice 1 implementation: Not started; **not authorized until the Slice 1 Detailed Implementation Plan is approved**
- Slice 2: Not started; JIT planning only after Slice 1 acceptance
- Slice 3: Not started; JIT planning only after Slice 2 acceptance
- Current branch: `feature/wp1-domain-context`
- Task 8 accepted implementation commit: `ab3e773ee957b02f257df3f60f3ea3492626bcfa`
- Task 9 accepted implementation commit: `949d22a676141cc279086775135610726b518c31`
- Task 10 accepted implementation commit: `6d85b88c238714ebbd1fb1d7b90b0f93308f5006`
- Task 10 governance/status closure commit: `b0c4e89dc2f4c9d680b0a491bdd0815eff869a2d`

## 4. Next authorized action

R0 finalization **does not authorize implementation**。

唯一下一步是起草一份绑定 exact R0 authority commit SHA 的：

```text
Slice 1 Detailed Implementation Plan
```

该 plan 必须定义 exact files、interfaces、migration ownership、transaction/UoW boundaries、tests、per-unit acceptance、risk tier 与 Accepted Foundation/facade usage。plan 通过 focused review 后才允许开始 Slice 1 implementation。

不得恢复旧 Task 11–17 执行路径，也不得提前创建 Slice 2/3 detailed implementation plans。

## 5. Foundation alignment to include in Slice 1 Detailed Plan

以下是进入 Slice 1 implementation 前的有限 alignment 项，不是新的 global architecture work：

- 默认本地 API 绑定 loopback（目标 Compose publish 为 `127.0.0.1:${LABPILOT_API_PORT:-8000}:8000`）或明确受控接口；
- Projection Worker 移出 Core default runtime topology；
- dependency CI 验证 `uv.lock` 与 `pyproject.toml` 一致；
- 实现 SSE 前刷新到经过测试、支持目标 SSE 路径的 FastAPI baseline；
- secret 与 Agent context/trace 分离；
- 最小 data-egress policy 生效。
