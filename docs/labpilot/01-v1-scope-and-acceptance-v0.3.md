# LabPilot V1 Scope and Acceptance

```text
版本：v0.3
修订：R0-final
状态：Approved / Current
日期：2026-08-10
```

## 0. 文档目的、权威性与 rebaseline 边界

LabPilot V1 定位为：

> **一个可控、可恢复、可验证的 Agent-assisted Experiment Runner，而不是缩小版 Research Agent OS。**

本版本是一次有意的 planning rebaseline。它不否定已经接受的 WP0 与 WP1-A Task 0–10，也不要求为了架构整洁回拆已完成代码；它重新定义尚未实现的 V1 工作，并切断旧平台化设计对未来实现的自动传播。

### 0.1 Closed-world authority

对所有**尚未实现的 V1 工作**，规范性规划输入只包括：

```text
1. docs/labpilot/01-v1-scope-and-acceptance-v0.3.md
2. docs/labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md
3. docs/labpilot/implementation/01-master-implementation-roadmap-v0.3.md
4. 当前 Slice 已批准的 detailed implementation plan / ADR（若存在）
```

更早的 v0.2 architecture、roadmap、implementation plan，以及 Architecture 08/09，对剩余 V1 默认只具有 historical / informative 价值。只有当 v0.3 或后续已批准 Slice plan **明确按名称和具体不变量引用**时，旧条款才重新成为该 Slice 的规范输入。

上述 normative inputs 的层级固定为：

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> current Slice detailed implementation plan / ADR
```

下位文档只能细化当前 Slice，不能扩大 Scope、削弱 Architecture/Core invariant、改变上位 Roadmap 的 Slice acceptance，或以 ADR/implementation plan 静默覆盖上位 authority。需要改变上位 contract 时，必须先显式 rebaseline 对应上位文档。

历史 contract 继续对在其约束下已经验收的工作保持规范性：

```text
WP0 + WP1-A Tasks 0–10
→ 由其历史 v0.2 / task-specific contracts 解释和验收
```

因此不再采用以下开放世界解释：

```text
v0.3 没有逐条否定旧要求
→ 旧要求自动继续约束未来实现
```

### 0.2 Machine-verifiable authority root

R0 finalization 完成后，以**首次同时包含 Approved/Current Scope、Architecture、Roadmap 与对应 `DOCUMENT-STATUS.md` 状态的 Git commit SHA** 作为 v0.3 规划基线根。该 SHA 由 Git commit 本身提供；`DOCUMENT-STATUS.md` **不得尝试在同一个 commit 内写入该 commit 自身 SHA**，避免不可实现的 self-reference。

未来 Slice-specific plan 必须记录：

```text
Planning baseline: <exact R0 authority commit SHA>
```

旧 `*.dependencies.sha256` 继续用于验证其历史实施 tranche，不因 v0.3 自动扩展成新的级联 hash 链。R0 finalization 必须更新任何仍把 v0.2 future roadmap 描述为“当前剩余 V1 authority”的 README / status / CI 提示；如果现有工具只校验历史文件完整性而不决定语义 authority，则无需为了形式统一修改旧 manifest。

---

## 1. 用户、部署与规模假设

### 1.1 用户

V1 首要用户仍为单个计算科研人员：

- 已有研究问题、Baseline、Idea 或科研代码仓库；
- 能理解 Python、Shell、参数、实验指标与基本 Git 概念；
- 接受关键实验设计和高风险副作用由本人确认；
- 目标是本地科研工作流、个人生产力和求职展示，不是多租户 SaaS。

### 1.2 技术与部署边界

V1 Core 继续采用：

```text
应用：模块化单体
API：FastAPI
数据库：PostgreSQL
协调：单 Coordinator 起步
执行：Linux host-native execution boundary
Artifact：本地受控目录 + provenance metadata
前端：单用户 Web UI
交互：REST + SSE
```

正式训练必须独立于 API / Coordinator 生命周期。V1 不要求容器成为训练执行权威，也不得为了让容器直接控制宿主机训练而向 API / Coordinator 容器暴露 Docker socket 或宿主机 systemd 高权限控制接口。

Slice 2 会通过短 PoC/ADR 在两个安全拓扑中选择实际实现：

```text
A. host-native API + Coordinator + Runner，PostgreSQL 可容器化（preferred candidate）
B. containerized control plane + narrow host Runner daemon
```

SSH、跨主机对象存储和分布式调度都不是 Core 发布前置条件。

### 1.3 规模

V1 按以下规模设计和验证：

- 1 个活跃用户；
- 1–5 个 Project；
- 每个 Batch 1–20 个 Trial；
- 默认并发 Attempt 不超过 4；
- 单个 Attempt 可运行数秒到数小时；
- 一个 Coordinator 起步；
- 一个 Linux 本地执行环境；
- 一个 PostgreSQL 实例；
- 一个本地 Artifact 根目录。

超过该规模时允许排队、拒绝或降级，不要求自动横向扩展。

---

## 2. V1 Core North Star

V1 Core 的成功标准不是对象数量、协议数量或文档完整度，而是一条真实可演示的闭环：

```text
Real Repository
→ bounded Agent Proposal
→ deterministic verification
→ Human Approval
→ immutable ExperimentVersion
→ Batch / Trial / Attempt
→ immutable source materialization
→ real training process
→ API / Coordinator restart
→ recover original Attempt without duplicate launch
→ Artifact / Metric provenance
→ deterministic Validation
→ Batch Summary / Trace
```

最终 Demo 必须使用真实 Repository 和真实训练进程，而不是只证明数据库状态机可以推进。

### 2.1 四项价值标准

```text
受控 Agent 决策
+ 真实实验执行
+ 跨重启可靠恢复
+ 可验证且可追溯
```

### 2.2 发布优先级

```text
领域正确性 > 平台完整性
可靠 Local 闭环 > feature breadth
确定性验证 > LLM 自评
可恢复性 > 高并发
真实 failure-case evidence > 形式化协议数量
```

---

## 3. V1 Core 必须保持的业务不变量

### 3.1 Repository：dirty 可分析，formal execution 必须 clean

V1 必须能够：

- 注册真实 Git Repository；
- 创建可追踪的 RepositorySnapshot；
- 允许 dirty Snapshot 用于分析、Proposal drafting 和受限只读检查；
- **Human Approval / ExperimentVersion freeze 只能绑定 clean RepositorySnapshot**；
- Proposal 后 Repository 发生变化时，能够识别 source 已不再满足批准条件；
- 正式 Attempt 不得静默回退到当前可变主工作树。

已实现的 Task 0–10 Repository 内部 identity、Bundle 和 Content Store 继续存在，但不自动成为未来模块必须消费的 contract。

### 3.2 Repository Fact 与 Agent Inference 分离

Agent inference 不是权威事实。

任何满足下列任一条件的 repository-derived fact：

```text
- 被 deterministic Verifier 使用；或
- 被冻结进 ExperimentVersion；或
- 影响 Approval / materialization
```

必须满足至少一种：

1. 来自确定性 repository fact/check；或
2. 在 verifier 中针对绑定的 RepositorySnapshot 独立重查。

Source-backed fact 应保留有界 source locator：

```text
path
+ optional line/range/symbol where applicable
```

Agent prose 或 confidence score 不能单独满足正式 verifier requirement。

### 3.3 Source materialization

正式执行必须满足：

```text
approved ExperimentVersion
→ materialize exact approved clean source
→ controlled Attempt source directory
→ verify exact source identity / manifest
→ Runner executes only that materialized source
```

Version 冻结后，主 Repository 的修改、分支切换或工作树删除不得改变已经物化的 Attempt source。

Source 无法物化或 identity/manifest 校验失败时，必须 fail closed；不得执行当前工作树作为降级方案。

P0 不要求 Agent 修改主仓库。Editable Worktree 仍属于 P1。

### 3.4 Dataset 与 Environment binding

V1 不建设通用 Dataset Registry 或 Environment Platform，但正式 ExperimentVersion 必须绑定最小 value-object 语义：

```text
DatasetReference
- reference_type / path_or_uri
- stable identity when available
- otherwise explicit mutable_reference
- reproducibility_level: strict | best_effort

EnvironmentExpectation
- Python/runtime profile
- important framework expectations
- device/CUDA expectations where relevant
- other execution-critical constraints
```

如果 Dataset 无法获得稳定 identity，可以使用 `mutable_reference + best_effort`，但 Validation 和 Summary 必须显式显示复现性警告。

Attempt 还必须记录足以验证关键 EnvironmentExpectation 的 observed environment；不要求建立完整环境快照平台。

### 3.5 Bounded Agent 与 model-request identity

V1 必须支持有界 Agent 任务，使模型能够：

- 读取允许的上下文和 Repository facts；
- 调用受约束 Tool；
- 请求必要的人类输入；
- 生成结构化实验 Proposal；
- 在预算或无进展条件下停止。

Agent 不能直接创建权威 ExperimentVersion、不能自行批准实验设计、不能绕过 Tool/Approval boundary。

V1 不要求独立 `ModelRequestSnapshot` aggregate，但每次正式 model call 至少必须有不可混淆的请求语义：

```text
provider / model
canonical request hash（或受保留策略约束的 immutable canonical request）
provider parameters that affect semantics
bound tool/schema identity when applicable
started/completed time
outcome / usage where available
```

同一逻辑 retry 若宣称复用同一输入，必须能证明 request hash 一致；request semantics 改变时不得伪装成同一次 retry。

### 3.6 Data egress 与 Secret boundary

在向远程 Model / Tool / Provider 发送 Repository 或 Dataset 内容前，V1 必须有明确的 deployment/project egress policy。P0 可以是安装级配置或 owner 内 value object，不要求企业 DLP/RBAC 平台。

至少区分：

```text
local_only
remote_provider_allowed
```

并满足：

- secret 不进入 Prompt、ToolResult、RuntimeEvent、普通 trace 或 Artifact manifest；
- 未经允许的敏感 source/data 不外发；
- Provider API key、SSH key、cloud credential 只通过执行边界引用/注入；
- Core Release 前必须对 Demo Repository / Dataset 完成一次数据分类与外发检查。

### 3.7 Deterministic Proposal Verification

正式 Proposal 必须经过确定性验证。Core 至少验证：

- schema 和必填字段；
- train/eval entrypoint；
- 参数定义和允许范围；
- Trial matrix / seed semantics；
- DatasetReference / EnvironmentExpectation 的必要字段；
- Metric / Artifact 声明；
- Repository source binding；
- experiment-type specific rules；
- Validation 规则覆盖必要输出。

V1 Core 不负责证明研究创新性、文献完整性或一般化 Claim/Evidence closure。

### 3.8 Human Approval 与 immutable ExperimentVersion

正式 Design Approval 必须绑定精确的：

```text
ProposalRevision / canonical payload hash
Verifier result / result hash or identity
clean RepositorySnapshot
DatasetReference
EnvironmentExpectation
approving actor
approval time
```

Proposal、Verifier input、RepositorySnapshot、DatasetReference 或 EnvironmentExpectation 的语义变化使旧 Approval 不再授权新的 Version materialization。

`ExperimentVersion` 是不可变实验语义边界，至少封装：

```text
approved Proposal semantics
clean source identity
DatasetReference
EnvironmentExpectation
Validation protocol semantics
```

任何影响上述语义的修改必须创建新的 ExperimentVersion。

### 3.9 Trial / Attempt 与最小 Trial identity

Trial 与 Attempt 保持语义分离：

- Trial 表示逻辑实验条件；
- Attempt 表示一次物理执行；
- 相同 Trial 的基础设施 retry 创建新 Attempt；
- 参数、seed 或 ExperimentVersion 改变创建新 Trial。

最小 Trial identity：

```text
trial_identity = hash(
  experiment_version_identity,
  resolved_parameters,
  seed,
)
```

因为 code、dataset、environment 和 validation semantics 已被 immutable ExperimentVersion 封装，不在 Trial hash 中重复展开。

同一 Batch 内 `trial_identity` 必须唯一。

### 3.10 跨重启恢复且不重复启动

V1 Core 必须证明：

```text
Attempt execution intent persisted
→ exact source materialized
→ real process launched
→ API / Coordinator terminates
→ training continues when appropriate
→ control plane restarts
→ original execution is rediscovered/reconciled
→ no second physical training process is launched
```

这里要求的是可证明的 at-most-one-active launch generation 与 fail-closed reconciliation；不宣称在无法观测的操作系统崩溃窗口实现数学意义上的 process exactly-once。

### 3.11 Result provenance 与 append-only scientific history

Core Result Plane 固定为：

```text
Attempt
→ Artifact
→ Metric
→ ValidationReport
→ BatchSummary
```

必须满足：

- formal Artifact 绑定来源 Attempt 和内容/integrity metadata；
- stdout/stderr 如果作为 Metric 输入，先登记为 log Artifact；
- Metric 绑定来源 Artifact 与 extractor semantic version；
- process success 与 scientific validity 分离；
- Validation 为确定性程序，不调用 LLM 作最终 eligibility 判定；
- ValidationReport 记录 input identity、validator/rule semantic version 和 immutable verdict；
- BatchSummary 记录 included eligible result references 与 summary protocol version；
- 新 extractor / validator / rule / summary protocol 导致新 semantic interpretation 时创建新 immutable result，不原地覆盖历史；可使用可选 `supersedes` 关系；
- Summary 只消费 eligible / valid Trial result；invalid / inconclusive / execution error 不得静默作为有效结果。

后台 worker retry 本身不自动产生新的科研语义版本。

### 3.12 最小 Trace 与 Web Surface

V1 必须让用户回答：

```text
为什么生成这个 Proposal？
批准了哪份精确设计？
哪个 Attempt 真正运行？
重启后如何恢复？
Artifact / Metric 来自哪里？
为什么 Validation 通过或失败？
Summary 排除了哪些 Trial？
```

Core UI 状态优先使用可理解的产品语义，例如：

```text
Proposal: Draft / Needs Approval / Approved / Rejected
Attempt: Queued / Running / Recovering / Failed / Completed
Validation: Valid / Invalid / Inconclusive / Error
Batch: Running / Complete / Complete-with-invalid-results
```

不得把 ManifestFreshness、LogicalRun、ProcessingRevision 等内部架构对象作为用户必须理解的主概念。

---

## 4. V1 Core 明确不做的事情

以下能力不属于 V1 Core 发布要求：

- 通用 Observation / Evidence / Claim 治理；
- Evidence Closure、Claim graph、literature coverage、Acceptance Rule DSL；
- 通用 Research Agent OS；
- 通用多 Agent 协作平台；
- 通用 Workflow DSL / 任意 DAG；
- 动态第三方 Tool 插件生态；
- Projection Worker / 通用 Ops Projection 平台；
- 通用 Supervision Backend family；
- 多主机或分布式调度；
- GPU/CPU 集群资源调度；
- 完整 Artifact CAS、跨项目全局去重与复杂 retention；
- 多租户、组织 RBAC、计费、企业 HA、跨区域容灾；
- 自动证明科研创新性或自动完成论文治理；
- 企业级 DLP / 合规治理平台。

这些能力只有在明确 trigger 出现后才能重新进入规划。

---

## 5. Architecture 08 / 09 的 V1 处置

### 5.1 Architecture 08

V1 Core 只保留以下行为要求：

- `REQUEST_HUMAN_INPUT`；
- abstention / unable-to-proceed；
- no-progress termination；
- bounded budget；
- unknown external side effect 不自动 retry。

这些行为优先由已有 AgentRun / AgentStep / ToolCall 及 owner 内 value objects 表达。除非真实 failure mode 证明有独立生命周期需求，否则不要求新增 authoritative persistence family。

### 5.2 Architecture 09

`09-evidence-obligation-and-closure-v0.1.md` 在 v0.3 下定义为：

```text
Historical / Deferred Planning Input
Not normative for V1 Core
Reconsider at P2 only when the product scope
actually enters external evidence / claim governance
```

Architecture 09 不删除，也不用于否定历史 review；但 WP1-C / WP2 后续工作不得自动按其中的 EvidenceObligationSet、Claim、Closure、Acceptance Rule DSL 等对象继续实现。

---

## 6. P1 / P2 边界

### 6.1 P1 candidates

Core Demo 完成后再排序：

- SSH Executor；
- editable Worktree workflow；
- LLM semantic verifier；
- richer Agent UX / follow-up proposal；
- result interpretation；
- enhanced Timeline / usage statistics。

### 6.2 P2 / trigger-based deferred

- Evidence Closure / Claim governance；
- literature evidence platform；
- dynamic plugin ecosystem；
- Projection Worker / advanced Ops projections；
- generic supervision backend；
- distributed scheduling；
- full Artifact CAS。

具体重新引入 trigger 由 Architecture 10 冻结。

---

## 7. V1 Core Release Acceptance

### 7.1 Slice 1 — Repository → Proposal → Approval

必须真实证明：

```text
Real Repository
→ RepositorySnapshot
→ bounded repository facts/source access
→ bounded Agent
→ ProposalRevision
→ deterministic verification
→ Human Approval
→ immutable ExperimentVersion
```

并满足：

- dirty Repository 可以用于分析，但不能冻结正式 Version；
- 每个影响 Approval/materialization 的 repository-derived fact 都被 deterministic check 验证或可追踪到绑定 Snapshot；
- Agent prose 单独不能满足 verifier requirement；
- ExperimentVersion 精确绑定 source、DatasetReference、EnvironmentExpectation 和 Validation semantics。

### 7.2 Slice 2 — Trial → Real Training → Recovery

必须真实证明：

```text
ExperimentVersion
→ Batch / Trial / Attempt
→ exact source materialization
→ real training
→ kill API / Coordinator
→ training remains when appropriate
→ restart control plane
→ reconcile original execution
→ physical launch generation remains unique
→ terminal Attempt
```

### 7.3 Slice 3 — Artifact → Metric → Validation → Summary

必须真实证明：

```text
terminal Attempt
→ Artifact
→ Metric
→ deterministic ValidationReport
→ Trial eligibility
→ BatchSummary
```

必须包含负例：

```text
process exit code = 0
but required scientific result is invalid
→ Attempt execution succeeds
→ Validation invalid
→ Trial ineligible
→ excluded from BatchSummary
```

### 7.4 Core Release safety/data gate

Core Release 前至少确认：

- Demo Repository / Dataset 的外发分类与 provider policy 已明确；
- secret 不出现在 Prompt、ToolResult、RuntimeEvent、Artifact manifest 或普通 logs；
- API 默认仅对预期本机/受控网络暴露；
- Runner 不依赖给应用容器暴露宿主机高权限控制 socket；
- no-duplicate-launch、approval bypass、path/argv/secret、scientific provenance corruption 均有针对性测试。

---

## 8. v0.2 P0 Requirement Traceability

本 Appendix 用于证明 v0.3 的精简是**有意识处置**而不是遗漏。Disposition 只使用：

```text
KEEP
SIMPLIFY
SUPERSEDED
DEFER_P1
DEFER_P2
ALREADY_SATISFIED
```

`SIMPLIFY` 表示保留原 requirement 的业务不变量，但移除不必要的平台生命周期、独立 aggregate 或全局传播机制。

### 8.1 Functional Requirements

| Old requirement | Disposition | v0.3 owner | Notes |
|---|---|---|---|
| V1-FR-001 Project | KEEP | Accepted Foundation / S1 | 复用现有 Project；Slice 1 只补 Core 真正缺失字段，不扩展项目治理平台。 |
| V1-FR-002 Repository Registration | KEEP | Accepted Foundation | Task 0–10 基础继续使用。 |
| V1-FR-003 Repository Snapshot 与 Inspection Result | SIMPLIFY | Foundation + S1 | Snapshot 保留；不要求通用 InspectionResult hierarchy，正式 facts 走 deterministic check/source locator。 |
| V1-FR-004 Repository Inspection Tools | KEEP | S1 | 保留受控只读 source access、path containment 与确定性检查。 |
| V1-FR-005 Agent Run | KEEP | S1 | 保留 bounded authoritative AgentRun。 |
| V1-FR-006 Context Compiler | SIMPLIFY | S1 | 仅组装当前目标、Project、bound Snapshot/facts、tools、budget；不要求大型 Context 平台。 |
| V1-FR-007 Bounded Loop | KEEP | S1 | step/call/time/token budget 与 termination reason 保留。 |
| V1-FR-008 Tool Registry | SIMPLIFY | S1 | static typed ToolDefinition registry；不建设动态 manifest/plugin lifecycle。 |
| V1-FR-009 Tool Policy | SIMPLIFY | S1 | 保留 risk/approval/path/argv/secret safety，删除通用 policy platform。 |
| V1-FR-010 Structured Trace | SIMPLIFY | Arch10 Audit/Event + Logs | 保留可追踪性；不要求所有状态经过 projection。 |
| V1-FR-011 Experiment Proposal | KEEP | S1 | 保留目标、入口、参数/seed、DatasetReference、EnvironmentExpectation、Metric/Artifact/Validation。 |
| V1-FR-012 Deterministic Verifier | KEEP | S1 | Core 产品不变量。 |
| V1-FR-013 Rule Verifier | SIMPLIFY | S1 | 规则合并进 versioned deterministic verifier profile，不要求独立 verifier platform family。 |
| V1-FR-014 Experiment Design Approval | KEEP | S1 | 精确绑定 ProposalRevision、verifier result、clean Snapshot、actor/time。 |
| V1-FR-015 Tool Execution Approval | KEEP | S1 | 与 Design Approval 分离；绑定 exact action semantics。 |
| V1-FR-016 Experiment 与 Experiment Version | KEEP | S1 | immutable Version，绑定 source/dataset/environment/validation semantics。 |
| V1-FR-017 Run Batch 与状态所有权 | SIMPLIFY | S2/S3 | 保留 Batch/Trial/Attempt ownership；不要求通用 Workflow projection/platform。 |
| V1-FR-018 Trial Identity | SIMPLIFY | S2 | 简化为 hash(ExperimentVersion identity, resolved parameters, seed)；Batch 内唯一。 |
| V1-FR-019 Attempt | KEEP | S2 | 物理执行事实与 Trial 分离。 |
| V1-FR-020 Validation Report | KEEP | S3 | 保留独立验证状态，并强化 immutable semantic versioning。 |
| V1-FR-021 P0 Workspace | SIMPLIFY | S2 | 保留 controlled Attempt directory 与 immutable source materialization；不建设通用 Workspace platform。 |
| V1-FR-022 Snapshot | SIMPLIFY | S2 | execution/source/dataset/environment/command binding 可作为 owner 内 immutable launch manifest/value object。 |
| V1-FR-023 Host-native Local Runner 与 Local Executor | KEEP | S2 | 保留 host-native execution；具体 supervision 经 PoC/ADR 冻结。 |
| V1-FR-024 固定状态机 | SIMPLIFY | S2 | 保留固定 Coordinator flow，不建设通用 Workflow DSL。 |
| V1-FR-025 Persistent Workflow State | SIMPLIFY | S2 | 只持久化恢复所需最小状态；优先落在 Batch/Attempt/窄 Job owner。 |
| V1-FR-026 Worker Claim 与 Lease | SIMPLIFY | S2 | 只有真实 competing ownership 需要时使用 claim/lease/fencing；不建设多 Worker 平台。 |
| V1-FR-027 Idempotency 与提交协议 | KEEP | S2 | Attempt launch 为 Required durable idempotency boundary。 |
| V1-FR-028 Retry、Timeout 与 Cancel | KEEP | S2 | 保留 fail classification、new Attempt retry、幂等取消。 |
| V1-FR-029 Reconciliation | KEEP | S2 | Core recovery 差异化，不削减。 |
| V1-FR-030 Logs | KEEP | S2/S3 | stdout/stderr 持久化；作为 Metric source 时登记为 log Artifact。 |
| V1-FR-031 Artifact Metadata | SIMPLIFY | S3 | 保留 provenance/integrity/availability；不建设完整 CAS/lifecycle platform。 |
| V1-FR-032 Structured Metric | KEEP | S3 | 绑定 source Artifact 与 extractor semantic version。 |
| V1-FR-033 Validation Engine | KEEP | S3 | deterministic eligibility boundary。 |
| V1-FR-034 Batch Summary | KEEP | S3 | valid-only deterministic summary + immutable protocol version。 |
| V1-FR-035 Transactional Runtime Event Enqueue | SUPERSEDED | Arch10 Core Audit/Event Matrix | 不再要求每个 authoritative mutation Event+Outbox；仅矩阵中 critical audit/跨进程通知。 |
| V1-FR-036 三条 Timeline | SIMPLIFY | S3 / Web | authoritative tables + selected audit events + SQL query/view + SSE，无 Core Projection Worker。 |
| V1-FR-037 最小 Web Surface | KEEP | Slice acceptance | 继续围绕 Project/Proposal/Attempt/Validation/Trace，不暴露内部平台对象。 |
| V1-FR-038 SSE | KEEP | Web / Slice acceptance | REST 为命令/查询，SSE 为单向状态/log 更新；断线可 REST 恢复。 |

### 8.2 Non-functional Requirements

| Old requirement | Disposition | v0.3 owner | Notes |
|---|---|---|---|
| V1-NFR-001 模块化单体 | KEEP | Global | 技术栈不重写；模块边界继续保持。 |
| V1-NFR-002 PostgreSQL First | KEEP | Global | 核心并发/恢复测试继续使用真实 PostgreSQL。 |
| V1-NFR-003 Migration | KEEP | Global | 数据库变更继续 Alembic + fresh DB migration。 |
| V1-NFR-004 Tests | SIMPLIFY | Roadmap risk gates | 不变量测试不减；从 every-change release-grade 改为 risk-based + Slice gate。 |
| V1-NFR-005 CI | KEEP | Global / foundation hardening | 保留 lint/type/unit/PostgreSQL/migration；lockfile 必须做一致性检查。 |
| V1-NFR-006 Deployment | SIMPLIFY | Arch10 + S2 ADR | 保留 Linux host-native execution；不再强制 containerized control plane 直接管理 host process。 |
| V1-NFR-007 Secret Handling | KEEP | Global Safety Core | secret 不进入 Agent context/trace；增加最小 data-egress policy，不建设企业 secrets platform。 |

---

## 9. Scope change rule

v0.3 之后，`frozen` 的含义是：

> **当前实施 tranche 的批准输入不可偷偷改变。**

它不表示尚未发布的产品必须永久兼容所有历史设计。

任何新 aggregate、identity、worker、projection、DSL 或 platform family 都必须先说明：

```text
没有它，会破坏哪个当前 V1 Core invariant？
```

无法明确回答时，不进入当前 Slice。
