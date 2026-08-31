# LabPilot Master Implementation Roadmap

```text
版本：v0.3
修订：R0-final
状态：Approved / Current
日期：2026-08-10
```

## 0. 目标

本路线图把剩余 V1 从旧 WP/micro-task 驱动方式改成三个 Vertical Slice，并把 release-grade 验证集中在真正的 Slice boundary。

```text
Accepted Historical Foundation
→ R0 v0.3 authority
→ Slice 1 Detailed Plan
→ Slice 1: Repository → Proposal → Approval
→ Slice 2: Trial → Real Training → Recovery
→ Slice 3: Artifact → Metric → Validation → Summary
→ V1 Core Demo
```

项目进度以后主要看用户闭环是否成立，而不是完成了多少 identity、manifest、worker 或 micro-task。

---

## 1. Historical baseline 与 supersession

### 1.1 Accepted Historical Foundation

```text
WP0
WP1-A v0.2 Tasks 0–10
```

状态：

```text
Completed / independently reviewed / pushed / CI passed
```

这些工作继续由其历史 contract 解释，不为 v0.3 主动回拆。

### 1.2 Superseded old execution plan

```text
WP1-A v0.2 Tasks 11–17
```

状态现为：

```text
Not executed
Superseded / Never Executed
```

旧 Task 11–17 不改名后塞回新路线图。任何未来工作都必须重新证明它直接服务当前 Slice acceptance。

### 1.3 Old roadmap authority

`01-master-implementation-roadmap-v0.2.3-frozen.md` 保留为历史设计和已接受工作的上下文；它不再拥有剩余 V1 的执行顺序 authority。

---

## 2. Milestones and authorization gates

| Milestone | Result | What it authorizes |
|---|---|---|
| R0 Rebaseline Accepted | v0.3 Scope/Architecture/Roadmap 成为 Current authority | **只授权起草 Slice 1 Detailed Implementation Plan** |
| S1 Plan Approved | 精确文件、接口、migration/UoW/test 边界冻结 | 授权 Slice 1 implementation |
| S1 Proposal Loop Ready | Repository → Proposal → Approval → immutable ExperimentVersion | 授权 Slice 2 JIT planning |
| S2 Recovery Ready | real training → restart recovery → no duplicate launch | 授权 Slice 3 JIT planning |
| S3 Result Loop Ready | Artifact → Metric → Validation → Summary | 授权 V1 Core hardening |
| V1 Core Demo | 三个 Slice 串成真实 E2E | V1 Core Release candidate |

**Roadmap 本身不直接授权实现。** 每个 Slice 在开工前只写当下 Slice 的 detailed plan，绑定 exact approved baseline commit SHA；不提前写 Slice 2/3 的代码级计划。

Slice plan / ADR 是最低层 authority：只能细化 `Scope > Architecture > Roadmap` 已批准的当前 Slice，不能扩大范围、削弱 Core invariant 或改变 Slice acceptance。

---

# 3. R0 — Rebaseline Accepted

R0 broad architecture review 与后续 finding-specific re-review 均已完成。最终 finding-specific verdict 为 `APPROVE`：B1–B5 全部 PASS，Direct regressions = NONE，Critical = 0，Important = 0。R0 不再进入新的 broad architecture review。

### 3.1 R0 targeted remediation scope

本轮只关闭：

```text
closed-world authority
v0.2 FR/NFR traceability
clean-source/materialization/dataset/environment/approval/trial invariants
Repository Fact vs Agent Inference authority
R0 → Slice 1 JIT plan gate
Idempotency Matrix
Core Audit/Event Matrix
immutable model-request semantics
append-only scientific result semantics
accepted-foundation facade permission
review debt gate
secure Runner deployment boundary
known foundation alignment items
```

不新增 Evidence Closure、generic workflow、generic Tool platform、Projection platform 或 Result Processing platform。

### 3.2 Finding-specific re-review record

```text
Verdict: APPROVE
B1–B5: PASS
Direct regressions: NONE
Critical: 0
Important: 0
Second broad architecture review: NOT WARRANTED
```

### 3.3 R0 finalization

本 finalization 固定以下状态：

1. Scope v0.3 → `Approved / Current`；
2. Architecture 10 → `Approved Architecture Rebaseline / Current`；
3. Roadmap v0.3 → `Approved / Current`；
4. Master Roadmap v0.2.3 → `Historical authority for accepted work only`；
5. Architecture 09 → status map 中标记 `Historical / Deferred / non-normative for V1 Core`，不修改 09 本体；
6. WP1-A v0.2 Tasks 11–17 → `Superseded / Never Executed`；
7. 完成 finalization 的 commit 本身作为 R0 authority root；`DOCUMENT-STATUS.md` 不写入该 commit 自身 SHA；
8. README / CI / authority tooling 中任何“v0.2 roadmap 仍是 future current authority”的提示同步修正；
9. 旧 dependency hash manifests 继续作为历史 tranche integrity evidence，不建立新的级联 architecture hash graph；
10. 不启动 Task 11，不直接实现 Slice 1。

### 3.4 Machine-verifiable baseline

Slice 1 Detailed Plan 必须写：

```text
Planning baseline: <exact final R0 authority commit SHA>
```

其中 `final R0 authority commit SHA` 指完成 R0 状态切换后、首次同时包含 Approved/Current Scope/Architecture/Roadmap/Status 的 Git commit；它不需要也不能被同一 commit 内的 status 文件自引用。实施分支必须包含该 commit 作为 ancestor。若未来工具需要验证规划输入，从该 Git tree 读取 v0.3 authority；不要自动把所有历史 frozen docs 重新加入新 dependency graph。

---

# 4. Slice 1 planning gate

R0 Accepted 后，第一件事不是编码，而是只写一份：

```text
Slice 1 Detailed Implementation Plan
```

它必须绑定 final R0 authority SHA，并明确：

- exact files；
- exact public/internal interfaces；
- migration ownership；
- transaction/UoW boundaries；
- per-unit acceptance；
- exact tests/commands；
- risk tier；
-哪些 existing foundation interfaces 复用、哪些用 facade 隔离。

计划只需一次 focused plan review。批准后才进入 Slice 1 implementation。

Slice 2/3 detailed plan 不在此时创建。

---

# 5. Slice 1 — Repository → Proposal → Approval

## 5.1 Slice outcome

```text
Real Repository
→ existing RepositorySnapshot
→ bounded repository facts/source access
→ bounded AgentRun
→ ProposalRevision
→ deterministic verification
→ Human Approval
→ immutable ExperimentVersion
```

完成标准是得到真实、可追踪、已批准的 `ExperimentVersion`，不是完成 Inspector/Evidence platform。

## 5.2 S1.0 — Foundation alignment gate

在新的 feature implementation 前，做一组很小的基础对齐，不重构 WP0：

### Runtime/dependency alignment

- 默认本地 API exposure 绑定 loopback（Compose 目标为 `127.0.0.1:${LABPILOT_API_PORT:-8000}:8000`）或明确受控接口，不默认发布到所有 host interfaces；
- Core default runtime 不要求启动 Projection Worker；已有 projection code 可以保留为 optional/historical capability；
- CI 在完整 project metadata 可用时执行 `uv lock --check`，同步使用 `uv sync --locked`（或等价 locked semantics）；`--frozen` 仅用于明确跳过 freshness check 的分层构建阶段；
- 在真正实现 SSE endpoint 前，将 FastAPI 刷新到经过测试的 `>=0.135.0` baseline（或后续兼容版本），使用官方 SSE 能力而不是为历史 pin 维护自有冗余 plumbing；
- dependency refresh 必须单独跑 API/Pydantic/integration regression，不和大 feature diff 混在一起。

### Safety alignment

- secret 不能进入 Prompt/RuntimeEvent/普通 trace；
- Project/installation 必须能表达 `local_only` 或 `remote_provider_allowed` 的最小 data-egress policy；
- 结构化 logs 的 correlation fields 和 redaction rule 在 S1 plan 中固定。

风险：`Routine` 或窄 `Boundary-sensitive`，按实际 diff 分类。

## 5.3 S1.1 — Minimal Repository Fact Surface

### 目标

在 Task 0–10 foundation 上建立窄 facade：

```text
snapshot id/identity
clean/current source state required by use case
bounded tree/path/content access
minimal deterministic repository facts
bounded source locator
```

### 必须保持

- dirty Snapshot 可用于分析；
-正式 Version 只绑定 clean Snapshot；
- verifier/Version 使用的 repository-derived fact 必须 deterministic check 或 source-traceable；
- Agent inference 不成为 authority。

### 明确不做

```text
generic Inspector Registry
Inspection Limits/Request/Result identity platform
Evidence persistence
Evidence Closure
Claim model
```

风险：`Boundary-sensitive`。

## 5.4 S1.2 — Minimal Agent + Tool Path

### Agent Core

实现完成 Proposal 所需最小 loop：

```text
AgentRun
AgentStep
AgentDecision semantics
ToolCall
bounded context assembly
static ToolDefinition registry
repository-read tools
REQUEST_HUMAN_INPUT
proposal-producing action
```

### Model call audit

每次 model call 至少记录：

```text
provider/model
canonical_request_hash
semantic provider parameters
bound tool/schema identity where applicable
outcome/usage/timestamps
```

不要求独立 ModelRequestSnapshot table。

### Tool Safety Core

```text
schema-validated canonical args
risk classification
approval where required
path containment
non-shell structured argv
secret isolation
readonly repository
side-effect/result/error audit
```

不实现 dynamic ToolManifest/Capability/PolicyManifest platform。

风险：`Boundary-sensitive`；path/secret/approval-bypass 部分按 `Safety Critical` 验证。

## 5.5 S1.3 — Proposal → Verifier → Approval → Version

### Proposal minimum semantics

至少包含：

```text
goal / experiment type
train/eval entrypoint
resolved workdir semantics
parameter template/matrix
seeds
DatasetReference
EnvironmentExpectation
Metric declarations
Artifact declarations
Validation rules
runtime/retry budget relevant to execution
```

### Deterministic verifier

至少检查：

```text
schema
entrypoint
parameter definitions
trial matrix
seed semantics
DatasetReference / EnvironmentExpectation
Metric / Artifact declarations
clean repository source binding
repository-derived approval facts
experiment-type rules
Validation coverage
```

### Approval / Version

Approval 精确绑定：

```text
ProposalRevision/hash
Verifier result
clean RepositorySnapshot
DatasetReference
EnvironmentExpectation
actor/time
```

ExperimentVersion immutable；semantic change → new Version。

普通纯 DB approve/reject 若 transaction/CAS 足够，可不强制 CommandReceipt；expensive materialization 依据 Idempotency Matrix 选择 Recommended/Required。

风险：`Boundary-sensitive`。

## 5.6 S1.4 — Slice 1 Acceptance

使用真实 Repository 完成：

```text
Repository
→ Agent
→ Proposal
→ deterministic verifier
→ Human Approval
→ immutable ExperimentVersion
```

必须证明：

- dirty source 无法冻结 Version；
- Version 绑定 clean source、DatasetReference、EnvironmentExpectation、Validation semantics；
- 每个影响 approval/materialization 的 repository-derived fact 都被 deterministic check 验证或可追踪到 bound Snapshot；
- Agent prose 单独不能让 verifier 通过；
- Design Approval exact binding 可查询；
- egress policy/secret boundary 生效。

Slice 1 boundary 才运行 full CI/fresh PostgreSQL 及 Slice-level acceptance review。

通过后更新一次 status；不为每个 S1.x 创建 governance closure commit。

---

# 6. Slice 2 — Trial → Real Training → Recovery

Slice 2 是 V1 最高风险、最值得投入验证预算的部分。

## 6.1 JIT Slice 2 plan

S1 Accepted 后才写 Slice 2 Detailed Plan，绑定 exact S1 acceptance baseline SHA。

首先完成 host Runner PoC + short ADR，不提前写 generic supervision framework。

## 6.2 S2.1 — Runner deployment/supervision PoC

验证：

```text
deterministic execution/unit identity
launch/query/stop/kill
PID/start/exit observation
stdout/stderr
Conda/venv/CUDA/env propagation
Coordinator death + rediscovery
host reboot semantics
permissions
```

候选：

```text
A. host-native API + Coordinator + dedicated Runner user + systemd
B. containerized control plane + narrow host runner daemon + systemd
C. existing minimal runner abstraction only if same recovery invariant is proven
```

禁止 direct Docker/systemd privileged socket 暴露给 API/Coordinator container。

PoC 后用一份短 ADR 冻结实际方案。

风险：`Safety / Recovery Critical`。

## 6.3 S2.2 — Source Materialization + Trial/Attempt

必须实现：

```text
ExperimentVersion
→ clean source identity
→ exact materialized Attempt source
→ verified source manifest
→ Trial identity
→ Attempt intent
```

Trial identity：

```text
hash(experiment_version_identity, resolved_parameters, seed)
```

同 Batch 唯一。

Infrastructure retry → new Attempt；semantic change → new Trial/Version。

风险：`Boundary-sensitive` / source integrity 部分 `Safety Critical`。

## 6.4 S2.3 — Local Attempt Runner

实现：

```text
durable launch intent
→ controlled physical launch
→ running observation
→ cancel/timeout
→ terminal observation
```

Attempt launch 属于 Idempotency Matrix 的 **Required** 类。

PostgreSQL 是 durable execution ledger；OS supervisor 是 physical observation source；Coordinator RAM 不是 truth。

风险：`Safety / Recovery Critical`。

## 6.5 S2.4 — Coordinator + Reconciliation

恢复算法：

```text
DB expected state
+ physical supervisor observation
→ deterministic reconciliation
```

必须：

- 原 process 存在时 reattach/reconcile；
- identity 不确定时 fail closed / needs attention；
- 不因为失去内存状态再次 launch；
- 只有确证旧 launch 未跨越副作用边界时，才可安全恢复同一 launch request；
-真正 retry 创建新 Attempt/new launch generation。

locking/lease/fencing 只有真实 ownership race 证明需要时引入。

风险：`Safety / Recovery Critical`。

## 6.6 S2.5 — Recovery Acceptance

真实 fault injection：

```text
start training
kill API
training remains when expected
restart API
kill Coordinator
training remains
restart Coordinator
reconcile original execution
physical training process count = 1
```

还要覆盖有限、明确的 failure set：

```text
failed launch
timeout
process exits while Coordinator is down
restart during reconciliation
unknown physical identity
```

不要求穷举所有理论 interleaving。

Gate：host E2E + PostgreSQL integration + fault injection + full CI + one deep independent review。

---

# 7. Slice 3 — Artifact → Metric → Validation → Summary

## 7.1 JIT Slice 3 plan

S2 Accepted 后才写 Slice 3 Detailed Plan，绑定 exact S2 acceptance baseline SHA。

## 7.2 S3.1 — Artifact Collection + Provenance

Artifact 至少回答：

```text
来自哪个 Attempt
logical role/path
content/integrity identity
availability
```

正式内容身份不可静默覆盖。

stdout/stderr 如果用于 Metric extraction，先注册为 log Artifact。

不建设 full Artifact CAS/object-store platform。

风险：`Boundary-sensitive`。

## 7.3 S3.2 — Metric + deterministic Validation

```text
Artifact
→ Metric(extractor semantic version)
→ ValidationReport(input identity + validator/rule version + immutable verdict)
→ Trial eligibility
```

必须区分：

```text
process/extractor execution error
vs
scientific validation invalid
```

semantic interpretation 改变 → new immutable result；可选 `supersedes`，不覆盖历史。

### ProcessingJob gate

只有：

```text
result processing must independently resume across restart
AND deterministic rerun is insufficient
```

才允许极简 ProcessingJob。

风险：`Boundary-sensitive`。

## 7.4 S3.3 — BatchSummary + Trace

BatchSummary 只消费 eligible results，并保存：

```text
included result references
excluded reasons
summary protocol version
immutable summary content
```

Timeline 默认使用：

```text
authoritative tables
+ Core Audit/Event Matrix
+ SQL query/view/service
+ SSE
```

不建设 Projection Worker 作为 Core dependency。

## 7.5 S3.4 — V1 Core Demo Acceptance

完整真实链：

```text
Repository
→ Agent Proposal
→ Verifier
→ Approval
→ ExperimentVersion
→ Trial / Attempt
→ exact source materialization
→ Real Training
→ API/Coordinator restart
→ original execution recovered, no duplicate
→ Artifact
→ Metric
→ deterministic Validation
→ BatchSummary
```

必须包含负例：

```text
process exit code = 0
but scientific output violates rule
→ execution success
→ Validation invalid
→ Trial ineligible
→ excluded from BatchSummary
```

Core gate：fresh PostgreSQL + full CI + host E2E + fault injection + key security/provenance regression + data-egress review。

---

# 8. P1 / P2 freeze

Core Demo 之前不允许以下能力阻塞：

### P1 candidates

```text
SSH Executor
editable Worktree workflow
LLM semantic verifier
richer Agent UX/follow-up
result interpretation
enhanced Timeline/usage statistics
```

### P2 / trigger-based

```text
Evidence Closure / Claim governance
literature evidence platform
dynamic plugin ecosystem
Projection Worker / advanced Ops
generic supervision backend
distributed scheduling
full Artifact CAS
enterprise compliance platform
```

遇到好想法记录 Deferred Finding / P1-P2 candidate 后继续当前 Slice。

---

# 9. Risk-based implementation governance

## 9.1 Routine

典型：

```text
DTO
CRUD
simple query
UI
pure deterministic transform
small config alignment
```

要求：focused tests + one implementation review；full CI 在 Slice candidate。

## 9.2 Boundary-sensitive

典型：

```text
DB transaction
migration
identity
filesystem
state machine
concurrency
external side effect boundary
```

要求：focused + relevant integration + one deep independent review；blocking finding 只做 finding-specific re-review。

## 9.3 Safety / Recovery Critical

典型：

```text
Runner recovery
duplicate launch prevention
source materialization integrity
path/argv/secret
approval bypass
Artifact provenance
```

要求：integration + fault injection + deep independent review + Slice E2E + final full CI。

Review depth 由**本次 diff 风险**决定，不因为“整个 LabPilot 很重要”把所有 change 升成 release-grade。

---

# 10. Review finding and debt policy

### 10.1 First review

可以完整审本次批准的 diff scope。

### 10.2 Remediation review

只检查：

```text
previous blocking findings
+ direct regressions caused by remediation
```

新 unrelated issue 只有属于以下情况才可直接阻塞当前 candidate：

```text
security boundary violation
data loss/corruption
duplicate external side effect
approval bypass
scientific provenance corruption
approved Core invariant violation
high-probability regression with material rework
```

其他 finding 进入 Deferred Finding，不自动开启第 3/4/5 轮 broad review。

### 10.3 Deferred Finding minimum record

每项至少有：

```text
severity
owner
target milestone
affected invariant
adjudication rationale
status
```

### 10.4 Debt gate

- Slice acceptance 时不得存在**未 adjudicate** 的 Critical/Important finding；
- Critical 或直接违反当前 Slice/Core invariant 的 finding 不能通过“Deferred”标签绕过；
- Important 可以显式 defer，前提是它不破坏当前 acceptance，并有 owner/target milestone；
- V1 Core Release 前，所有影响 Core safety/recovery/provenance 的 Important finding 必须关闭或由产品范围明确移出 Core。

### 10.5 Two-remediation stop rule

Safety/Recovery Critical finding 连续两次 targeted remediation 仍未关闭：

```text
STOP
→ architecture adjudication / simplify boundary
```

不是创建第 4/5/6 个 candidate。

---

# 11. CI strategy

### Per implementation unit

```text
relevant unit tests
relevant integration tests
Ruff/mypy where relevant
lock consistency check when dependency metadata changes
```

### Migration unit

```text
fresh migration suite
destructive migration only when relevant
```

### Slice acceptance

```text
full CI
fresh PostgreSQL integration
host/E2E/fault injection appropriate to slice
key security/provenance gates
```

局部 remediation 不再无条件重复 release-grade 全套 gate。

---

# 12. Complexity budget gate

### New aggregate/table

必须证明至少一项：

```text
independent lifecycle/query/update/authorization/concurrency/history
```

### New identity/hash

必须用于：

```text
dedup/idempotency/replay/immutable semantic comparison
```

### New worker/projection

必须有实际 ownership/performance/consumer trigger。

### New protocol layer

必须指出缺少它会破坏哪个当前 Slice/Core invariant。

无法证明就不加。

---

# 13. Definition of Done

以后只问三个业务问题：

### Slice 1

> 能否从真实 Repository 得到经过 deterministic verification 和人工批准、绑定 clean source/dataset/environment/validation semantics 的 immutable ExperimentVersion？

### Slice 2

> 能否物化批准源码，启动真实训练，杀掉控制面后恢复同一物理执行且不重复 launch？

### Slice 3

> 能否从 Attempt 得到有 provenance 和 semantic version 的 Artifact/Metric，确定性判断 validity，并生成只使用 eligible Trial 的 immutable Summary？

三项为 YES，并通过 Core safety/data gate：

```text
V1 Core Demo complete
```

然后才重新排序 P1。
