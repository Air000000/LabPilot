# LabPilot V1 Core Architecture Rebaseline

```text
版本：v0.3
修订：R0-final
状态：Approved Architecture Rebaseline / Current
日期：2026-08-10
```

## 0. Purpose and closed-world precedence

本文是剩余 V1 的 architecture precedence layer。它不 consolidation rewrite 旧 `02–09`，也不要求删除已完成实现；它规定从 R0 以后哪些 architecture obligations 仍然属于 V1 Core。

### 0.1 Remaining-V1 normative inputs

对所有尚未实现的 V1 工作，规范性规划输入只包括：

```text
V1 Scope v0.3
Architecture Rebaseline v0.3（本文）
Master Roadmap v0.3
当前 Slice 已批准的 detailed implementation plan / ADR
```

早期 v0.2 architecture、roadmap、implementation plans 和 Architecture 08/09 对剩余 V1 默认是 historical / informative。只有 v0.3 或后续批准的 Slice plan **显式引用某个具体 invariant** 时，该旧 invariant 才重新进入当前实施 tranche。

剩余 V1 的 precedence 固定为：

```text
V1 Scope v0.3
> Architecture Rebaseline v0.3
> Master Roadmap v0.3
> current Slice detailed implementation plan / ADR
```

Slice plan / ADR 只能做 JIT specialization，不得扩大 Scope、削弱本 Architecture 的 Core invariant、改变 Roadmap acceptance，或以“implementation detail”为名重新引入被 v0.3 移出的平台义务。

历史 contract 继续规范已经在其约束下验收的实现：

```text
WP0 + WP1-A Tasks 0–10
```

因此 v0.3 是 closed-world rebaseline，而不是“只在冲突时覆盖旧条款”的 additive layer。

### 0.2 Current authority root

R0 finalization 完成后，以**首次同时包含 Approved/Current Scope、Architecture、Roadmap 与对应 `DOCUMENT-STATUS.md` 状态的 Git commit SHA** 固定：

```text
Scope v0.3
Architecture 10 v0.3
Roadmap v0.3
DOCUMENT-STATUS.md
```

该 authority SHA 由 Git commit identity 提供；同一 commit 内的 `DOCUMENT-STATUS.md` 不写入自身 commit SHA。Slice-specific plan 必须记录这个 exact SHA。旧 `*.dependencies.sha256` 仍可验证历史 tranche，但不自动决定剩余 V1 的语义 authority。

---

## 1. Accepted Foundation / Compatibility Layer

以下已验收实现定义为：

```text
Accepted Foundation / Compatibility Layer
- WP0
- WP1-A Tasks 0–10
```

为了 architecture purity，不主动：

- 回拆已接受 migrations；
- 删除 CommandReceipt；
- 删除 RuntimeEvent / Outbox；
- 重做 Task 6 identity contract；
- 重写 RepositorySnapshot / Bundle；
- 替换已经工作的 ContentAddressedStore；
- 为了让 schema 更对称而重做 Task 0–10。

只有真实 defect、security/correctness issue 或 Core Demo blocker 才能重新打开这些区域。

### 1.1 Non-destructive bridge explicitly allowed

Preserve Accepted Code 不等于冻结所有历史内部结构。

允许在不破坏既有接受 contract 的前提下新增或调整：

```text
facade
adapter
read model
query service
owner-local value object
non-destructive schema bridge
```

目的只能是：

> **阻止历史内部复杂度继续向未来模块传播。**

这类 bridge 不得成为新的平台 family。

---

## 2. Downstream Narrow-Contract Principle

内部实现复杂度不得自动成为下游 contract surface。

Repository 对剩余 V1 默认只暴露完成业务闭环所需的粗粒度能力：

```text
repository_snapshot_id
repository_snapshot_identity
source cleanliness/currentness required by the use case
bounded tree/path/content access
bounded deterministic repository facts
```

不得仅因为内部已有，就要求下游绑定：

```text
Bundle identity
entry-set identity
internal manifest identities
content-store implementation identity
inspector registry identity
other cache/replay-only identities
```

原则：

> **Internal implementation richness does not automatically become downstream contract surface.**

如果某个内部 identity 需要泄漏到下游，必须指出它直接保护的当前 Core invariant。

---

## 3. Complexity Admission Rules

### 3.1 Persisted Object Admission Rule

新概念至少满足一项，才默认允许成为独立 aggregate/table：

- independent lifecycle；
- independent query need；
- independent update；
- independent authorization；
- independent concurrency ownership；
- independent immutable-history value。

否则优先：

```text
typed value object
versioned JSON
child record
owner snapshot
```

### 3.2 Identity Admission Rule

新 identity/hash 必须直接服务至少一项：

```text
deduplication
idempotency
replay
immutable semantic comparison
```

`future cache might need it`、模型对称性或“其他对象都有 identity”不是充分理由。

### 3.3 Worker / Projection Admission Rule

新增独立 Worker 前必须证明现有 control owner 无法安全负责该工作。

新增 Projection 前至少出现一项：

- 代表性数据下 direct query 发生被测量的性能问题；
- 第二个真实、独立、需要异步消费的 consumer。

没有 trigger 时，Projection Worker 不属于 Core 默认运行拓扑。

### 3.4 Protocol Admission Rule

新增 manifest / freshness layer / DSL / generic retry protocol 前必须回答：

```text
如果没有这一层，哪个当前批准的 V1 Core invariant 会被破坏？
```

无法回答时不进入当前 Slice。

---

## 4. Repository, Proposal and Experiment authority boundary

### 4.1 Clean vs dirty

```text
Dirty RepositorySnapshot
→ 可用于 analysis / Proposal drafting / bounded read-only inspection

Clean RepositorySnapshot
→ 才能进入 Design Approval / ExperimentVersion freeze
```

正式执行永远不得把当前可变 worktree 当成已批准 source 的替代品。

### 4.2 Repository Fact vs Agent Inference

Agent inference 永远是 non-authoritative。

任何影响 verifier、Approval 或 ExperimentVersion 的 repository-derived fact 必须：

```text
A. 来自 deterministic repository fact/check
OR
B. 被 deterministic verifier 针对 bound Snapshot 独立重查
```

Source-backed fact 保留 bounded source locator：

```text
path
+ optional line/range/symbol
```

不恢复 EvidenceObligationSet / Claim graph 来表达这一点。

### 4.3 Minimal Dataset and Environment semantics

不引入通用 Dataset / Environment platform。正式 Version 使用 owner 内 value objects：

```text
DatasetReference
- reference_type / path_or_uri
- stable identity when available
- mutable_reference when not
- reproducibility_level

EnvironmentExpectation
- runtime/Python profile
- framework constraints
- device/CUDA constraints where relevant
- other execution-critical expectations
```

Attempt 保存必要 observed environment，Validation 对重要 expectation 做确定性检查。

### 4.4 Design Approval

Design Approval 必须精确绑定：

```text
ProposalRevision / canonical payload hash
Verifier result / result hash or identity
clean RepositorySnapshot
DatasetReference
EnvironmentExpectation
actor
time
```

任一语义输入变化，旧 Approval 不授权新的 Version。

### 4.5 Immutable ExperimentVersion

ExperimentVersion 是正式实验语义封装，至少绑定：

```text
approved Proposal semantics
clean source identity
DatasetReference
EnvironmentExpectation
Validation protocol semantics
```

语义变化必须创建新 Version，不原地更新。

### 4.6 Minimal Trial identity

```text
trial_identity = hash(
  experiment_version_identity,
  resolved_parameters,
  seed,
)
```

同 Batch 内唯一。

code/dataset/environment/validation 已由 immutable Version 封装，不在 Trial identity 中重复展开。

---

## 5. Source materialization and execution source authority

正式 Attempt 只执行 Version 绑定的 frozen source：

```text
ExperimentVersion
→ exact clean source identity
→ immutable/controlled source materialization
→ source manifest verification
→ Attempt execution directory
→ Runner launch
```

要求：

- materialized source 与 Version source identity 一致；
- Attempt workdir 位于 controlled execution root；
- 主 Repository 后续修改/切换/删除不影响已物化 Attempt；
- materialization 或 verification 失败时 fail closed；
- **禁止 fallback 到当前 worktree**。

P0 不需要 editable worktree。代码修改工作流仍是 P1。

---

## 6. Idempotency policy

现有 `CommandReceipt` 保留，但不再全局强制所有未来 public write。

### 6.1 V1 Idempotency Matrix

| Command class | Policy | Typical examples | Required proof |
|---|---|---|---|
| Unsafe-to-repeat external side effect | **Required** | Attempt launch、SSH/external submit、side-effecting Tool execution、external publish | durable idempotency / existing CommandReceipt or equivalent; duplicate request cannot create a second active side effect |
| Important create/materialize command likely to be retried by caller | **Recommended** | expensive Version/Batch materialization、bounded generation with non-trivial replay cost | prefer existing CommandReceipt; may use domain identity + unique constraint when replay semantics are equally explicit |
| Pure DB mutation with provably safe CAS/unique semantics | **Not required** | metadata update、archive、simple approve/reject state transition with expected version | transaction + constraint/CAS must make retry outcome unambiguous |

各 Slice 不得重新发明第四种 retry model。若命令无法归类，先按副作用和 caller retry 行为归入以上三类。

CommandReceipt 的存在不替代 domain semantic identity。

---

## 7. RuntimeEvent, Outbox, Audit and Timeline

现有 RuntimeEvent / Outbox 实现保留。

取消：

```text
every authoritative state change
→ RuntimeEvent + Outbox
```

### 7.1 Core Audit/Event Matrix

| Domain occurrence | Durable audit record | Outbox by default | Notes |
|---|---|---|---|
| Proposal approved/rejected | **Yes** | No | 可直接查询领域表；保存 actor、exact revision/result binding |
| ExperimentVersion created | **Yes** | No | immutable domain fact |
| High-risk Tool approved/executed | **Yes** | Conditional | 只有跨进程可靠执行/通知需要时 Outbox |
| Attempt launch intent committed | **Yes** | Conditional/Yes | Runner 若依赖跨进程 delivery，则 Outbox 必须可靠 |
| Physical training started | **Yes** | Conditional | systemd/Runner observation + DB reconciliation；按协调方式决定通知 |
| Attempt reconciled after restart | **Yes** | No | 需要可解释恢复链 |
| Attempt terminal | **Yes** | Conditional | 若触发异步 result processing，则使用 Outbox |
| Validation finalized | **Yes** | No | immutable scientific result 可直接查询 |
| BatchSummary published | **Yes** | No | immutable scientific result 可直接查询 |

表中 `Durable audit record = Yes` 不意味着必须为每项创建新的 Event aggregate；可以是权威领域记录 + selected Runtime/AuditEvent，只要历史决策可查询且不可静默丢失。

### 7.2 Timeline default

Core Timeline：

```text
authoritative domain tables
+ selected Runtime/Audit events
+ SQL query/view or query service
+ SSE for UI updates
```

Projection Worker 不是 Core 默认依赖。现有 projection code 可以保留为 historical/optional capability，但默认 Core runtime topology 不应要求它启动。

### 7.3 Structured operational logs

Timeline 与 debug/ops logs 分离。

统一 structured logs 至少在适用时携带：

```text
request_id
project_id
agent_run_id
batch_id
trial_id
attempt_id
execution_identity
tool_call_id
```

不得记录：

```text
provider secret
SSH/cloud secret
full sensitive environment
未经 egress policy 允许的 source/data content
```

---

## 8. Agent Core and provider boundary

V1 Core 保留：

```text
AgentRun
AgentStep
AgentDecision semantics
ToolCall
Proposal generation
Human Input
bounded execution
```

核心不变量：

- 模型不能直接创建 ExperimentVersion；
- 模型不能自行批准；
- 模型不能绕过 Tool Policy / Approval；
- 模型输出必须结构化并通过 deterministic checks 后才进入权威领域写入；
- 已跨越外部副作用边界的动作不能因 model retry 重复执行。

### 8.1 Immutable model-request semantics without a platform family

不要求独立 `ModelRequestSnapshot` table，但每个实际 model call 必须可审计：

```text
provider/model
canonical_request_hash
semantic provider parameters
bound tool/schema identity when applicable
started/completed timestamps
outcome/usage
```

如按保留策略保存 canonical request body，必须先做 secret handling/redaction。

同一逻辑 retry 必须复用相同 request semantics；request hash 改变意味着新的逻辑 model call/step，而不是“同一次 retry”。

### 8.2 Third-party harness

第三方 provider/harness 可以作为 Adapter 使用，只要：

```text
Provider owns inference/session mechanics
LabPilot owns Tool policy, Approval, Proposal,
ExperimentVersion, Attempt and Validation authority
```

Provider thread/session 不得成为 LabPilot 领域恢复或执行 truth。

---

## 9. Tool Safety Core

保留的安全语义：

```text
tool/version identity
schema-validated canonical arguments
risk classification
human approval where required
exact execution-plan snapshot where side effects matter
path containment
non-shell structured argv
secret isolation
readonly Repository by default
side-effect/result/error audit
```

未来 V1 概念目标约为：

```text
static ToolDefinition
ToolCall
ToolApproval when required
ToolResult
```

Execution plan / policy snapshot 优先作为 ToolCall/Approval owner 内 immutable value object，除非 admission rule 证明独立生命周期。

不再默认实现：

```text
dynamic Tool Availability lifecycle
ToolManifest hierarchy
ManifestFreshness
generic Capability persistence
PolicyManifest family
generic plugin marketplace/runtime lifecycle
```

Tool approval 与 Experiment Design Approval 是独立 authority boundary。

---

## 10. Data egress and secret policy

V1 不建设企业合规平台，但必须有最小 source/data 外发边界。

在远程 provider 首次读取某 Project source/data 前，必须存在明确 policy：

```text
local_only
remote_provider_allowed
```

如果未来需要更细粒度分类，通过 admission rule 增量引入；R0 不设计通用 classification taxonomy。

Secret 必须：

- 通过 provider/execution boundary 引用或注入；
- 不进入 Prompt、ToolResult、RuntimeEvent、Artifact manifest 和普通 trace；
- 不因 debug mode 自动放宽。

Core release acceptance 必须包含一次 Demo Repository/Dataset egress review。

---

## 11. Runner deployment, supervision and recovery boundary

Runner/Recovery 是 V1 允许承担最高 essential complexity 的区域。

### 11.1 Authority split

```text
PostgreSQL
= durable execution intent / authoritative workflow ledger

OS supervisor (systemd preferred candidate)
= current physical process supervision / observation

Coordinator/Reconciler
= compare DB expectation with physical observation and converge
```

systemd transient unit 不是永久 workflow database，也不能代替 PostgreSQL ledger。

### 11.2 Allowed Core deployment boundary

优先候选：

```text
Host:
  LabPilot API
  Coordinator/Reconciler
  dedicated Runner execution user
  systemd Attempt units
  repository/artifact roots

Docker:
  PostgreSQL (and optional dev/test services)
```

如果保留 containerized API/Coordinator，必须通过**narrow host Runner daemon / explicit protocol** 控制宿主机执行。

明确禁止为了省一个 daemon 而：

```text
mount Docker socket into API/Coordinator container
mount unrestricted host systemd control socket into API/Coordinator container
run training as an accidental child of API/Coordinator
```

Runner 使用专用 Unix execution identity；API/Coordinator 不获得超出其业务所需的宿主机权限。

### 11.3 systemd decision timing

`systemd transient service` 是 Linux P0 preferred candidate，但 R0 不冻结具体 unit schema。

Slice 2 开始前先做短 host PoC：

```text
deterministic unit identity
launch/query/stop/kill
PID/start/exit observation
Coordinator death + rediscovery
stdout/stderr capture
Conda/venv/CUDA/env propagation
permissions
host reboot semantics
```

PoC 后做一个短 ADR，选择：

```text
A. direct host-native Coordinator/Runner + systemd
B. narrow host runner daemon + systemd
C. existing minimal runner abstraction only if it proves the same recovery invariants
```

ADR 后立即实现，不扩展成 generic supervision framework。

### 11.4 Recovery invariant

必须证明：

```text
execution intent persisted
→ exact approved source materialized
→ physical launch occurs
→ control plane crashes
→ existing physical execution remains authoritative when observed
→ restart reconciles same launch generation
→ duplicate physical launch is prevented
```

未知进程身份 fail closed / needs attention，不通过“再启动一次”猜测恢复。

---

## 12. Result Plane reset with immutable semantic history

Core Result Plane：

```text
Attempt
→ Artifact
→ Metric
→ ValidationReport
→ BatchSummary
```

### 12.1 Artifact

Formal Artifact 至少绑定：

```text
source Attempt
logical role/path
integrity/content identity metadata
availability
```

正式内容身份不可静默覆盖；availability 可以是可变状态。

stdout/stderr 如作为 Metric source，先注册为 log Artifact，从而统一 Metric provenance。

### 12.2 Metric

Metric 至少绑定：

```text
source Artifact
extractor name
extractor semantic version
value/split/step semantics as needed
```

新 extractor semantics 产生新 immutable Metric result，不覆盖旧结果。

### 12.3 ValidationReport

至少绑定：

```text
input identity
validator name/version
rule/protocol version
immutable verdict
reason codes
```

新的 input/validator/rule semantics 产生新 immutable report；可选 `supersedes` 指向旧报告。

### 12.4 BatchSummary

至少绑定：

```text
included eligible Trial result references
excluded reasons
summary protocol version
immutable summary content
```

新 summary protocol 或 selected result set 产生新的 summary；不原地重写科研历史。

### 12.5 ProcessingJob admission

后台 retry 不是新的科研事实。

只有 result processing 必须跨 restart 独立恢复，且 simple deterministic rerun 不足时，才允许一个窄 `ProcessingJob`：

```text
pending
running
retryable_failure
failed
completed
retry_count
```

不为 Artifact/Metric/Validation/Summary 各自复制 LogicalRun/ExecutionAttempt/Revision 平台。

---

## 13. Deterministic Proposal Verifier

Core verifier 检查：

```text
schema
entrypoint
parameters
trial matrix
seed semantics
DatasetReference / EnvironmentExpectation
Metric / Artifact declarations
repository source binding
experiment-type rules
Validation coverage
```

并执行 §4 的 repository fact authority rule。

明确不承担：

```text
general evidence completeness
Claim closure
literature coverage
research novelty proof
Evidence DSL
```

LLM semantic review 只能是 P1 辅助，不得成为正式 approval eligibility authority。

---

## 14. Core technical baseline alignment

基础技术栈不重写：

```text
Python
FastAPI
Pydantic
SQLAlchemy async
PostgreSQL
Alembic
uv
React/TypeScript/Vite
REST + SSE
Linux host execution
```

但现有 accepted foundation 中的具体 dependency/runtime 配置不是永久产品 contract。后续可以用小型非破坏改动修正与 v0.3 不一致的 baseline。

在 Slice 1 implementation 前必须关闭以下已知 alignment items：

1. **SSE dependency:** 使用经过测试的 FastAPI `>=0.135.0`（或后续兼容版本）来实现正式 SSE；不因历史 pin 维护自有冗余 SSE plumbing。
2. **Lock consistency:** CI / build 在完整 project metadata 可用时必须执行等价于 `uv lock --check` / `uv sync --locked` 的一致性验证；`--frozen` 只用于明确不做 freshness check 的阶段。
3. **Local API exposure:** 默认本地 V1 API 绑定 loopback（Compose 语义等价于 `127.0.0.1:${LABPILOT_API_PORT:-8000}:8000`）或显式受控接口，不默认公开到所有 host network interfaces。
4. **Projection topology:** Projection Worker 不作为 Core default compose/runtime dependency。

这些是 foundation alignment，不是重新架构 WP0。

---

## 15. Deferred capability triggers

延期能力只有 trigger 成立才可重新规划：

| Capability | Re-entry trigger |
|---|---|
| Projection Worker | 代表性数据下 direct query 不达目标，或出现第二个真实独立 event consumer |
| Multi-worker fencing platform | 同一 job type 实际部署 >1 competing worker，且现有 DB ownership 不能安全解决 |
| Full Artifact CAS/object store | 可测量存储重复问题，或真实 cross-host object storage requirement |
| Dynamic Tool Manifest/plugin lifecycle | 实际支持 runtime-installed / third-party Tool ecosystem |
| Evidence Closure / Claim governance | 产品真实进入外部 evidence/claim governance |
| Generic supervision backend | 实际需要支持非 systemd Core execution environment |
| Distributed scheduling | single PostgreSQL + Coordinator 被测量为实际瓶颈 |
| Enterprise compliance platform | 真实出现受监管数据、组织级隔离或审计要求 |

Trigger 未出现时，不以“未来扩展性”提前实现。

---

## 16. Rebaseline governance

### 16.1 Frozen 的新含义

`Frozen` 表示：

> 当前实施 tranche 的批准输入不可偷偷改变。

它不表示产品尚未完成一次 Core Demo 时，所有历史设计都永久拥有兼容权。

### 16.2 R0 finalization

R0 finding-specific re-review 已以 `APPROVE` 结束。finalization 固定以下状态：

- 将 Scope / Architecture / Roadmap 状态改为 Approved / Current；
- 更新 `DOCUMENT-STATUS.md`；
- finalization commit 本身成为 R0 authority root；`DOCUMENT-STATUS.md` 不写入同一 commit 的自身 SHA；
- 更新 README 或自动化中的“当前 future authority”文字，使其不再指向 v0.2 roadmap；
- 保留旧 hash manifests 为历史 tranche integrity evidence，除非现有脚本确实因 authority 语义而需要一个最小适配；
- 不启动旧 Task 11；
- **只授权起草 Slice 1 Detailed Implementation Plan，不直接授权 implementation。**

### 16.3 No renewed global architecture loop

R0 finding-specific re-review 已关闭 B1–B5，Direct regressions = NONE，Critical = 0，Important = 0；不再重新 broad review 整个 V1 architecture。

后续新问题按 Slice admission / risk rules 处理；不是通过继续扩大 global architecture 文档解决。
