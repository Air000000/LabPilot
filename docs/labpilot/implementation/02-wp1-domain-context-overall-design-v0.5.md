# LabPilot WP1 Domain and Context Overall Design

```text
Document type: Work Package Overall Design Specification
Version: v0.5
Status: Approved Design Input
Date: 2026-07-28
Target branch: feature/wp1-domain-context
Baseline: main@769e8fd
Applies to: WP1-A, WP1-B, WP1-C
Consumes:
  - 01-v1-scope-and-acceptance-v0.2-frozen
  - 02-experiment-domain-v0.2-frozen
  - 04-context-and-repository-v0.2-frozen
  - 05-tools-policy-and-workspace-v0.2-frozen
  - 06-workflow-and-executors-v0.2-frozen
  - 07-results-validation-and-agentops-v0.2-frozen
  - 08-agent-abstention-and-progress-control-v0.2
  - 01-master-implementation-roadmap-v0.2.3-frozen
Supersedes for planning:
  - LabPilot-WP1-domain-context-overall-design-v0.4
Does not reopen:
  - WP0
  - WP0 Manifest or acceptance evidence
  - v0.1.0-foundation
```

---

# 0. 文档目的与约束层级

## 0.1 目的

本文将 WP1 的冻结需求、WP0 已实现基础设施和规划阶段确认的范围决议合并为一份可审查的总体设计。本文解决：

1. WP1-A、WP1-B、WP1-C 的对象所有权和依赖顺序；
2. Repository、Policy、Context、Proposal、Freeze、Version、Batch、Trial 的权威状态边界；
3. `ContextCompilation` 与 WP2 Runtime `ContextSnapshot` 的分离；
4. `context → experiments` 单向依赖下的跨模块 Freeze；
5. Canonical identity、事务、事件、错误、并发和幂等原则；
6. 三个子阶段的独立验收门槛和 WP1 最终端到端门槛。

本文不是逐 Task implementation plan，不分配具体文件修改顺序，不授权开始编码。

## 0.2 约束优先级与 WP1 implementation profile

发生冲突时按以下顺序处理：

```text
已冻结的 V1 Scope / Architecture contracts
→ Approved Planning Input 08
→ 本文记录的显式 Scope Resolution
→ Master Roadmap 概括性条目
→ 后续 implementation plan
```

冻结架构继续拥有领域语义、权威对象、证据、身份和下游不变量。本文的 WP1 Scope Resolution 只允许进行以下实施映射：

```text
1. 将尚不具备权威依赖的对象推迟到后续 Work Package；
2. 将逻辑生命周期阶段映射为事务内部的瞬时阶段；
3. 为尚不能实例化的 Runtime 对象定义语义独立的 pre-runtime 对象；
4. 在不削弱权威、证据、identity 和下游约束的前提下收敛 WP1 持久化边界。
```

Scope Resolution 不得静默删除或弱化冻结契约。发生映射时必须在本文中明确记录 source contract、WP1 representation、deferred representation 和保持不变的 invariant。

### 0.2.1 冻结契约到 WP1 的显式映射

```text
Frozen logical state: verifying
→ WP1 representation: SubmitProposalForVerification 事务内部执行阶段
→ persistence: 不作为可被其他命令观察的稳定状态
→ preserved invariant: Revision 只有在完整 VerifierResult 形成后才可提交

Frozen logical state: awaiting_approval
→ WP1 representation: ProposalRevision.status = verified
→ persistence: verified 直接表示等待人工决定
→ preserved invariant: 只有完整 passing VerifierResult 才可批准

Frozen runtime object: ContextSnapshot
→ WP1 representation: 无同名替代对象；推迟到 WP2
→ WP1 pre-runtime object: ContextCompilation
→ preserved invariant: Runtime ContextSnapshot 仍须绑定 AgentRun、Tool Manifest、runtime policy 和 run-scoped visibility

Frozen Source Snapshot contract
→ WP1 representation: 完整 Git tree SourceReferenceManifest + SourceSnapshot
→ Proposal-specific source selection: 独立 ProposalSourceSelectionManifest
→ preserved invariant: Runner 可验证完整 materialized source 与冻结 Git tree 一致
```

本文不修改冻结文档，也不使用假 ID、nullable 占位或语义降级绕过未到阶段的权威对象。

## 0.3 当前 WP0 基础

WP1 必须复用以下真实接口：

- `Database.session()` 提供 `AsyncSession` 生命周期；
- `UnitOfWork` 显式开启事务，命令显式 `commit()`，未提交退出自动 rollback；
- `append_runtime_event(uow, envelope)` 在同一事务中分配 Aggregate sequence、写 `RuntimeEvent` 和 `OutboxRecord`；
- Event payload 必须通过版本化 registry、Canonical JSON 和 payload limits；
- `namespaced_sha256()` 与 Canonical JSON V1 是 WP1 identity 的唯一基础算法；
- `create_app()` 通过依赖注入和 lifespan 管理服务，不允许 Router 自行创建全局 Session；
- Migration 链从 `0002_runtime_events_outbox` 继续；
- 模块依赖固定为 `context → experiments`，禁止 `experiments → context`。

WP1 不建立第二套事务、事件、Outbox、Canonicalization 或 hashing 基础设施。

---

## 0.4 v0.5 修订摘要

v0.5 是封闭范围修订，只解决最终一致性复审确认的对象所有权、命令和基数缺口，不扩展新的 WP1 功能：

1. 增加 `ClearActiveProjectPolicyBinding`，闭合 active Policy revoke 的双指针清除路径；
2. 固定 `ContextSourceReference` 为内部维护的 current projection，并引入 `ContextSourceRegistryService` 与 `SynchronizeProjectContextSources` 生命周期契约；
3. 删除 `RepositorySnapshot → InspectionBundle` 反向字段，固定 `RepositorySnapshotInspectionBundle.repository_snapshot_id` 的单向一对零/一所有权；
4. 在 `ExperimentVersion` 中固定 Version-owned `ExecutionConstraintManifest` 与 `BudgetConstraintManifest`，作为 formal Batch concurrency / budget 校验的唯一权威来源；
5. 将 Dataset 顶层基数收敛为每个 ProposalRevision 恰好一个规范化 `DatasetSpecification`，成功 Freeze 恰好一个 `DatasetSnapshot`；
6. `DesignDecisionBinding` 增加 `proposal_revision_identity`，完整绑定 Revision schema 与 lineage；
7. 同步更新模块对象、Application Service、内部命令、Migration、数据库/应用不变量、A/B/C acceptance、Planning Resolution Index 与 Traceability Matrix；
8. 本版已通过最终一致性复审并升级为 `Approved Design Input`；后续只进入 WP1-A detailed implementation plan。

# 1. WP1 范围与规划决议

## 1.1 WP1-A

WP1-A 实现：

```text
Project core schema
RepositoryRegistration
RepositorySnapshot
RepositorySnapshotFreshness
RepositoryInspectionRun
RepositoryInspectionResult
RepositoryEvidence
RepositoryEvidenceManifest
RepositorySnapshotInspectionBundle / Entry
CommandReceipt technical foundation
```

WP1-A 只实现确定性 Repository inspection，不实现 Planning Agent inference。`Project` 在 WP1-A 只创建本阶段拥有且能够建立真实约束的核心列；Research Context 与 Policy 当前指针在 WP1-B 对应表存在后追加。

## 1.2 WP1-B

WP1-B 实现：

```text
ProjectResearchContextRevision
ProjectPolicyRoot
ProjectPolicyRevision
ContextSourceReference current projection
ContextSourceRegistryService / SynchronizeProjectContextSources lifecycle
ContextCompilation
ContextCompilationSourceBinding
ContextCompilationSegment
ContextCompilationSegmentSourceBinding
ContextCompilationFact
ContextCompilationPrecondition
ContextCompilationFreshness
CheckContextCompilationFreshness command
typed fact manifest hash
execution precondition manifest hash
```

WP1-B 不实现 AgentRun-bound `ContextSnapshot`。WP1-B 通过 experiments-owned 内部命令更新 Project 的 active Research Context / Policy 指针，不直接写 experiments ORM Row。

## 1.3 WP1-C

WP1-C 实现：

```text
Experiment
ExperimentProposal mutable draft
ProposalRequirementManifest
ProposalSemanticBindingManifest
ProposalSourceSelectionManifest / ProposalSourceSelectionEntry
ProposalRevision
VerifierResult
DesignApproval
DesignRejection
FreezePreconditionAssessment
SourceReferenceManifest
SourceReferenceManifestEntry
SourceSnapshot
DatasetSnapshot
EnvironmentExpectation
ExecutionConstraintManifest / BudgetConstraintManifest（ExperimentVersion-owned value objects）
ExperimentVersion
RunBatch
Trial
Trial condition identity
Trial initial projections
Attempt minimal persistence foundation
one-active-Attempt-per-Trial database constraint
```

`SourceReferenceManifest`、`SourceSnapshot`、`DatasetSnapshot` 与 `EnvironmentExpectation` 是 Freeze 的 experiments-owned 内部产物，不建立独立公开创建命令。

## 1.4 明确推迟

### WP2

```text
AgentRun-bound ContextSnapshot
available / exposed Tool Manifest binding
runtime policy manifest binding
Agent Runtime
HumanInputRequest
ProgressAssessment
RuntimePolicyDecision
abstention / no-progress / termination control
```

### WP3

```text
CreateTrialAttempt
Attempt lifecycle commands
Coordinator
Runner
lease acquisition and renewal
heartbeat
retry policy evaluation
reconciliation
external process launch
result collection
```

### P1

```text
TrialRerunAuthorization
AuthorizeTrialRerun
SelectTrialAttemptResult
eligible Trial manual rerun
rerun authorization creation, revocation and consumption
```

## 1.5 Trial rerun authorization 冲突决议

Master Roadmap WP1-C 中的 `rerun authorization` 解释为后续兼容提醒，不是 WP1 交付功能。WP1 建立 Trial 和最小 Attempt 数据库基础，但不建立空的 `TrialRerunAuthorization` 表或无调用者的授权流程。

V1 Core 中：

```text
Trial 尚未 eligible
→ WP3 根据 retry policy 决定是否允许新 Attempt

Trial 已 eligible
→ 没有 P1 one-time authorization 时，一律禁止新 Attempt
```

## 1.6 `ContextCompilation` 与 `ContextSnapshot`

```text
ContextCompilation ≠ ContextSnapshot
```

- `ContextCompilation` 是 WP1 创建的、Agent 无关、确定性、可复用的不可变编译产物；
- `ContextSnapshot` 是 WP2 创建的、绑定具体 AgentRun、Tool Manifest、runtime policy 和 run-scoped visibility 的不可变运行时证据。

WP1 不使用假 `agent_run_id`、空 Tool Manifest、nullable runtime binding 或固定占位 hash 来实例化冻结的 Runtime `ContextSnapshot`。

---

# 2. 模块边界与总体组件

## 2.1 模块所有权

```text
experiments
├── Project
├── Experiment
├── ExperimentProposal / ProposalRevision
├── ProposalRequirementManifest
├── ProposalSemanticBindingManifest
├── ProposalSourceSelectionManifest / ProposalSourceSelectionEntry
├── VerifierResult
├── DesignApproval / DesignRejection
├── SourceReferenceManifest / SourceReferenceManifestEntry / SourceSnapshot
├── DatasetSnapshot / EnvironmentExpectation
├── ExperimentVersion-owned ExecutionConstraintManifest / BudgetConstraintManifest
├── ExperimentVersion
├── RunBatch / Trial
└── Attempt persistence foundation

context
├── RepositoryRegistration / Snapshot / Freshness
├── RepositorySnapshotInspectionBundle / Entry
├── Inspection / Evidence / EvidenceManifest
├── ProjectResearchContextRevision
├── ProjectPolicyRoot / Revision
├── ContextSourceReference
├── ContextCompilation and compilation children
├── ContextCompilationFreshness
└── FreezePreconditionAssessment

db technical foundation
└── CommandReceipt
```

`FreezePreconditionAssessment` 由 `context` 持有，因为它证明 Repository、Policy、Research Context、Compilation 和 Evidence 等 Context 前提；`ExperimentVersion` 只消费一个经过验证的 experiments-owned Freeze command DTO，不反向 import Context entity。

`ProposalSourceSelectionManifest` 表示 Proposal 声明的 entrypoint、source role 和相关源码选择；`SourceReferenceManifest` 表示 frozen commit 的完整 Git tree。两者不可合并，否则无法同时证明 Proposal 选择语义和完整源码物化一致性。

`CommandReceipt` 是领域无关的技术性幂等记录。它不保存业务判断，不以 `project_id` 作为通用唯一作用域，不对 Project 建立外键，也不能替代任何领域 identity。

## 2.2 依赖方向

```text
context → experiments
experiments -X→ context
api → context, experiments
```

`experiments` 不读取 Context 表，不 import Context repository，不查询“当前 Policy”或“最新 Snapshot”。它只接受服务端生成、版本化且属于 experiments 契约的内部命令 DTO，并验证其中属于 Experiment Domain 的绑定。

Context 模块需要修改 Experiments-owned Project pointers 时，必须调用 experiments-owned 内部 handler：

```text
SetPrimaryRepositoryBinding
ClearPrimaryRepositoryBinding
SetActiveResearchContextBinding
SetActiveProjectPolicyBinding
ClearActiveProjectPolicyBinding
```

这些 handler：

- 由 `experiments` 定义并拥有；
- 接受调用方传入的同一个 UoW；
- 不开启嵌套事务；
- 只修改允许的 Project pointer；
- 验证 expected `row_version`、Project identity、target Project binding 和 pointer transition；
- 不读取 Context-owned Revision 状态，也不自行判断其 `approved` / `active` 语义。

Context Application Service 负责在调用 handler 前验证目标 Context-owned 对象属于同一 Project、状态和 identity 满足要求；数据库外键负责证明目标记录存在。

Freeze 时，Context Orchestrator 根据已持久化 Assessment 的结果映射为 experiments-owned command：

```text
passed
→ FreezeExperimentVersionCommand

failed + invalidate_context_binding
→ InvalidateProposalRevisionFromFreezeCommand

failed + preserve_approved
→ 不调用 Experiments 状态变更命令
```

Experiments 不接收或 import `FreezePreconditionAssessment` ORM/entity 类型。

## 2.3 Application Services

### experiments

```text
ProjectApplicationService
ExperimentApplicationService
ProposalApplicationService
ProposalDecisionService
ExperimentVersionService
BatchApplicationService
```

### context

```text
RepositoryApplicationService
RepositoryInspectionService
ResearchContextApplicationService
ProjectPolicyApplicationService
ContextSourceRegistryService
ContextCompilationService
ContextFreezeOrchestrator
```

### api

Router 只负责：

```text
解析认证后的 actor
解析并验证请求 DTO
调用一个公开 Application Service / Orchestrator
映射结构化错误和响应
```

Router 不负责领域判断，不组合多个查询后自行决定 Freeze，不直接访问 ORM Row。

## 2.4 UnitOfWork 所有权

每个公开命令只有一个 UoW owner。

普通单模块命令：

```text
Application Service
└── UnitOfWork
    ├── repositories
    ├── domain mutation
    ├── RuntimeEvent
    ├── Outbox
    └── CommandReceipt
```

跨模块 Project pointer 更新：

```text
Context Application Service
└── UnitOfWork
    ├── context repositories
    ├── experiments-owned primary/context/policy pointer command handler
    ├── RuntimeEvents / Outbox
    └── CommandReceipt
```

`RegisterLocalRepository` 创建 Registration 与设置 primary pointer 必须同事务提交；撤销当前 primary Registration 时，撤销与清除或替换 Project pointer 也必须同事务提交。

跨模块 Freeze：

```text
ContextFreezeOrchestrator
└── UnitOfWork
    ├── context repositories
    ├── FreezePreconditionAssessment
    ├── experiments-owned passed/invalidation command handler
    ├── Source/Dataset/Environment immutable objects（passed path）
    ├── ExperimentVersion（passed path）
    ├── ProposalRevision transition（version_created 或 context_stale）
    ├── RuntimeEvents / Outbox
    └── CommandReceipt
```

禁止 Context Service 开启 UoW 后由 Experiments Service 再开启嵌套 UoW。

# 3. 跨领域通用契约

## 3.1 ID 与 identity

所有权威对象同时区分：

```text
object_id
canonical semantic/content identity
```

- ID 指向本实例中的具体权威记录；
- identity/hash 证明该记录的规范化内容；
- current/latest 指针不能替代 immutable binding。

所有 semantic identity 使用：

```text
namespaced SHA-256
+
Canonical JSON V1
+
显式 schema / algorithm version
```

时间戳、数据库自增序号、display metadata 不进入 semantic identity，除非对应冻结契约明确要求。

## 3.2 服务端权威计算

客户端不得提交并要求服务端信任：

```text
payload_hash
manifest_hash
snapshot_identity
compilation_identity
verification passed/failed
approval_hash
assessment passed/failed
version_identity
batch_identity
trial_condition_hash
```

客户端只提交业务输入、目标 ID、expected version 和必需的 `Idempotency-Key`。所有 identity、状态、时钟和 authority binding 由服务端读取权威数据后计算。

## 3.3 幂等模型

WP1 的首要幂等依据是领域 identity 和数据库唯一约束，不依赖内存缓存：

```text
RepositorySnapshot identity
ContextCompilation identity
Revision materialization identity
DesignApproval / DesignRejection uniqueness
Freeze assessment identity
one Version per Revision
Batch identity
Trial condition identity within Batch
```

所有公开写命令接受 `Idempotency-Key`。WP1 在 `db` foundation 中建立领域无关的技术性 `CommandReceipt`：

```text
command_receipt_id
idempotency_scope_identity
command_name
command_version
idempotency_key
normalized_request_hash
outcome_kind
primary_result_type
primary_result_id
result_envelope_schema_version
result_envelope_json
result_envelope_hash
created_at
```

`result_envelope_json` 是有界、版本化、Canonical JSON V1 的命令结果摘要，不是完整 HTTP Response，也不复制大型 Manifest、完整 Trial 列表、文件内容或可变对象的全部字段。至少保存：

```text
outcome_kind
primary_result_type
primary_result_id
primary_result_identity nullable
related_result_refs[]
committed_state
```

`result_envelope_hash` 使用 `labpilot-command-result-envelope:v1` namespace 与 Canonical JSON V1 计算。Receipt 顶层的 `outcome_kind`、`primary_result_type`、`primary_result_id` 必须与 Envelope 一致。Transport 可以在重放响应外层增加 `replayed = true`，但该标记不属于已提交 Result Envelope。

数据库保证：

```text
UNIQUE(
  idempotency_scope_identity,
  command_name,
  command_version,
  idempotency_key
)
```

`idempotency_scope_identity` 必须在命令产生副作用前确定，并由服务端基于认证与授权上下文计算：

```text
CreateProject
→ tenant / installation + authenticated actor scope

Project 内命令
→ tenant / installation + authenticated actor + Project authority scope

对象命令
→ tenant / installation + authenticated actor + 对象所属 Project authority scope
```

底层 `db` 模块只存储 opaque scope identity，不识别 Project，不对 Project 建立外键。`project_id` 可作为上层查询元数据，但不参与通用唯一作用域。

`normalized_request_hash` 至少覆盖：

```text
command_name
command_version
target object IDs
expected versions
normalized request body
authority / actor scope identity
```

不得包含：

```text
Idempotency-Key
随机生成的结果 ID
请求时间
非语义 transport metadata
```

命令在同一 UoW 中创建领域结果和 Receipt：

- 相同 scope、command、version、key 与相同 `normalized_request_hash` 返回已提交的相同 Result Envelope；
- 相同唯一键与不同 request hash 返回 `IdempotencyKeyConflict`；
- 命令回滚时 Receipt 一并回滚；
- precondition error、invariant violation 和 infrastructure failure 不留下成功 Receipt；
- `verification_failed` Revision 和 completed failed Freeze Assessment 是已提交领域结果，产生成功 Receipt；
- 重放不得通过重新读取可变 primary object 来重建原命令结果；对象后续被 archive、revoke 或更新，不改变原 Receipt 的 Result Envelope；
- 需要对象当前状态时，客户端使用 Query API；
- 已提交领域失败的重放仍返回相同 Envelope 与 `outcome_kind`，不得重放成命令异常。

### 3.3.1 并发相同 Idempotency-Key

WP1 固定使用 transaction rollback + winner reload 算法，不引入 reservation 状态：

```text
1. 命令开始时读取已提交 CommandReceipt；
2. 命中且 request hash 相同：直接重放 Result Envelope；
3. 命中但 request hash 不同：IdempotencyKeyConflict；
4. 未命中：执行领域命令，并在同一 UoW 插入 Receipt；
5. Receipt 插入发生唯一约束竞争：整个 loser UoW rollback；
6. 使用新的只读 Session 读取 winner Receipt；
7. winner request hash 相同：重放 winner Result Envelope；
8. winner request hash 不同：IdempotencyKeyConflict。
```

loser 事务发生 unique violation 后不得继续在同一 Session 查询 Receipt。领域写入、Event 和 Outbox 随 loser UoW 一并回滚。

### 3.3.2 数据库约束与应用不变量

数据库直接保证 Receipt 唯一键、必填字段、JSON 类型和有界字段长度。以下属于应用不变量，并通过 unit/integration tests 验证：

```text
result_envelope_hash ==
  namespaced_sha256(Canonical JSON V1(result_envelope_json))

Receipt 顶层 outcome/result refs
  == Result Envelope 中对应字段
```

除非未来明确引入 PostgreSQL canonicalization/hash 函数或 trigger，否则不得把上述关系描述成普通 SQL `CHECK` 能自动验证的数据库约束。

`CommandReceipt` 不能代替任何领域 semantic identity，也不绑定 HTTP status/body 的完整 transport 表示。

## 3.4 RuntimeEvent / Outbox

所有权威状态变化与对应 Event/Outbox 同事务提交。Event：

- 使用受注册的版本化 payload schema；
- payload 只保存摘要、reason code、对象 ID 和 identity；
- 不内嵌完整 Proposal、Policy、Compilation 或 Manifest；
- 不保存 secret、凭证、完整 Repository 文件内容或隐藏推理链；
- 同一 Aggregate sequence 由 WP0 cursor 分配。

一次公开命令使用同一 interaction correlation；跨 Freeze/Batched materialization 的相关事件使用明确 causation 关系。

## 3.5 领域失败、命令错误与执行异常

必须区分：

```text
completed domain result
command precondition error
invariant violation
infrastructure / execution failure
```

例如：

```text
VerifierResult(overall_result=failed)
≠ verifier execution failed

FreezePreconditionAssessment(overall_result=failed)
≠ assessment execution failed
```

只有完整执行全部 required checks 后形成的领域结果可以持久化为 failed。执行异常、数据损坏、错误绑定、不支持版本、取消或未完成检查必须回滚。

## 3.6 外部 Repository 与 PostgreSQL

Git 和文件系统不能与 PostgreSQL 形成原子提交。所有涉及外部 Repository 的命令采用：

```text
事务外执行有界、只读 observation
→ 得到精确 observed identity
→ 开启短 UoW
→ 锁定必要数据库对象
→ 重新验证数据库 current pointers / expected versions
→ 持久化 observation binding、领域对象、Event 和 Outbox
→ commit
```

数据库事务只证明“提交时记录了哪个 immutable observation”，不声称 commit 后工作目录永远不变。

---

# 4. WP1-A：Project 与 Repository

## 4.1 Project

`Project` 属于 `experiments`。逻辑上的完整权威契约遵循冻结 Context 规格，但数据库字段按对象所有权和 Migration 可验证性分期建立。

### WP1-A core schema

```text
project_id
name
description
status
primary_repository_registration_id nullable during initial create
artifact_root_ref
created_at
updated_at
row_version
```

WP1-A 在 RepositoryRegistration 表建立后，为 `primary_repository_registration_id` 添加真实外键和一个 active primary Repository 的约束。

### WP1-B added bindings

在 Research Context 和 Project Policy 表已存在后追加：

```text
active_research_context_revision_id
active_project_policy_revision_id
```

并建立真实外键、索引和 pointer 更新契约。

### Deferred bindings

```text
default_execution_profile_id
default_budget_policy_id
```

只有对应权威对象在某一 Work Package 正式建立时才通过 Migration 添加。WP1 不创建无目标表的 nullable 外键占位。

WP1 允许的最小状态：

```text
active
archived
```

P0 每个 Project 最多一个 active primary Repository。

公开命令：

```text
CreateProject
ArchiveProject
```

## 4.2 RepositoryRegistration

P0 只支持：

```text
repository_type = local_git
```

注册时必须：

1. 对用户输入路径执行 realpath；
2. 验证路径位于允许的 Repository roots；
3. 拒绝越界 symlink；
4. 验证为可识别 Git repository；
5. 计算稳定 registration/root identity；
6. 保存 sanitized remote origin，不保存凭证；
7. 通过 Project 行锁和唯一约束保证一个 active primary registration。

状态：

```text
active
unavailable
invalid_git_repository
revoked
```

Repository 实质迁移到不同 root 或身份变化时，新建 Registration 并撤销旧 Registration，不原地替换身份字段。临时不可访问可以在 `unavailable ↔ active` 之间恢复。

`RegisterLocalRepository` 在同一 UoW 中创建 Registration，并通过 `experiments.set_primary_repository_binding(...)` 设置 `Project.primary_repository_registration_id`。撤销当前 primary Registration 时，必须在同一 UoW 中调用 `experiments.clear_primary_repository_binding(...)`，或先以受验证的新 Registration 原子替换；禁止提交“Registration 已 revoked，但 Project 仍指向它”的状态。

公开命令：

```text
RegisterLocalRepository
CheckRepositoryAvailability
RevokeRepositoryRegistration
```

## 4.3 RepositorySnapshot 与精确 Inspection 内容

Snapshot 是创建后不可变的 Repository observation，保存冻结规格定义的：

```text
head_commit
branch / detached state
clean flag
tracked tree identity
dirty tracked manifest identity
untracked manifest identity
repository state digest
repository snapshot identity
submodule / LFS presence
capture compatibility version
```

必须满足：

```text
同一 commit + 不同 dirty/untracked content
→ 不同 RepositorySnapshot
```

### Clean Snapshot

Clean Snapshot 的精确内容来自 Git object database：

```text
head_commit + git tree/object identities
```

Inspector 必须按 Snapshot 的 commit/tree 读取 Git object，不得读取 mutable working tree。

### Dirty Snapshot

Dirty Snapshot 可以用于分析，但必须同时创建不可变、content-addressed：

```text
RepositorySnapshotInspectionBundle
RepositorySnapshotInspectionBundleEntry[]
```

Bundle 保存 base commit、tracked dirty overlay 与 untracked file 的精确捕获内容引用：

```text
inspection_bundle_id
repository_snapshot_id
base_commit_sha
bundle_schema_version
capture_tool_version
entries_manifest_hash
inspection_bundle_identity
created_at
```

所有权固定为单向：

```text
RepositorySnapshot
    1
    ↓
RepositorySnapshotInspectionBundle
```

数据库只建立：

```text
RepositorySnapshotInspectionBundle.repository_snapshot_id
UNIQUE(repository_snapshot_id)
```

`RepositorySnapshot` 不保存 `inspection_bundle_id` 或 `inspection_bundle_identity`，避免 Snapshot 与 Bundle 的循环外键和 Snapshot 创建后的回写。通过唯一反向关系查询 Bundle。

应用不变量：

```text
clean Snapshot
→ 不得存在 Inspection Bundle

dirty Snapshot
→ 必须恰好存在一个 Inspection Bundle
```

Capture handler 在同一 UoW 中创建 Snapshot 与 dirty Bundle/Entries；该跨表条件由 handler 和 PostgreSQL integration test 保证，不伪装为双向外键。

每个 Entry 至少保存：

```text
repository_path
entry_kind = tracked_overlay | untracked_file | deletion | mode_change
file_mode
content_ref nullable
content_hash nullable
size_bytes
stable_ordinal
entry_identity
```

`content_ref` 指向受管理的 content-addressed blob，不得指向可变工作树路径。Deletion 和纯 mode change 可以没有 content blob，但必须形成确定性 Entry identity。

Blob 写入在数据库事务前以 content hash 为 key 幂等完成并校验可读性；短 UoW 只绑定已经存在且 hash 匹配的 blob refs。事务回滚可能留下未引用 blob，但不得留下引用不存在 blob 的 Snapshot；未引用 blob 由独立 GC 清理。

Dirty Snapshot capture 只有在以下内容均能在服务端资源上限内完整捕获时才成功：

```text
全部 tracked dirty overlay
全部 untracked entries required by snapshot identity
symlink metadata
file modes
content hashes / content-addressed refs
```

若超过文件数、单文件大小、总字节或 unsupported file type 上限，命令返回结构化错误并且不创建语义不完整的 dirty Snapshot。不得只保存 manifest hash 后让后续 Inspector 重新读取工作树。

Inspection 读取模型固定为：

```text
Clean Snapshot
→ frozen Git objects

Dirty Snapshot
→ frozen base Git objects
+ RepositorySnapshotInspectionBundle overlay
```

因此工作树在 Snapshot 创建后变化，不会改变既有 Inspection 的输入。Dirty Snapshot 仍不允许进入正式 ProposalRevision 或 ExperimentVersion。

公开命令：

```text
CaptureRepositorySnapshot
```

并发捕获相同 Repository state 时，通过 `repository_snapshot_identity` 返回既有 Snapshot 与同一 Inspection Bundle。

## 4.4 RepositorySnapshotFreshness

`RepositorySnapshotFreshness` 是每个 immutable Snapshot 的唯一当前可变投影，不是历史 Assessment 集合，也不是仅在查询时临时计算的值。

状态：

```text
current
stale
unverifiable
invalid
```

字段：

```text
repository_snapshot_id
status
reason_code
current_probe_identity
probe_version
checked_at
row_version
```

数据库保证：

```text
UNIQUE(repository_snapshot_id)
```

每次 freshness check：

```text
事务外执行有界只读 Repository probe
→ 短 UoW 锁定 Freshness projection
→ 验证 Snapshot / Registration binding
→ 更新 current projection
→ 写 repository.snapshot.freshness_assessed RuntimeEvent / Outbox
→ commit
```

Freshness 不进入 Snapshot identity。历史变化由 RuntimeEvent 保留；Freeze 时的精确观察由不可变 `FreezePreconditionAssessment` 保存。

公开命令：

```text
CheckRepositorySnapshotFreshness
```

`SubmitProposalForVerification` 与 Freeze 可以在自己的唯一 UoW 中调用同一 experiments/context-owned freshness projection helper；它们不嵌套调用该公开命令，也不创建第二个 Receipt。

## 4.5 Deterministic Inspection

WP1 Inspection：

- 只读取已捕获 Snapshot 的不可变内容视图；
- Clean Snapshot 从 frozen Git objects 读取；
- Dirty Snapshot 从 frozen base objects 与 `RepositorySnapshotInspectionBundle` overlay 读取；
- 不调用模型；
- 不执行训练；
- 不访问网络；
- 使用版本化 inspector profile 和 inspector registry；
- 资源有界；
- 结果和 Evidence 不可变。

Inspector 禁止在运行时读取 RepositoryRegistration 的当前 working tree path 来补齐缺失内容。任何未进入 Snapshot/Bundle identity 的内容都不能成为该 Inspection 的 Evidence。

`RepositoryInspectionRun` 允许冻结规格中的执行状态，但公开命令在 WP1 可以同步完成。执行异常不伪装成 `partial` 或 `failed` 领域结果；只有 inspector registry 定义的完整终态输出才提交。

`RepositoryInspectionResult` 保存候选入口、配置、Metric、Artifact、Dataset、Environment 和 unresolved questions。Agent inference 不进入 WP1。

## 4.6 Inspection 完整性

正式完整性不只读取一个 `status=complete` 字符串，而验证：

```text
required_inspector_registry_version
required_inspector_identity_set
completed_inspector_identity_set
```

必须满足：

```text
completed required set == expected required set
```

额外 optional inspector 可以存在，但不能代替 required inspector。

## 4.7 RepositoryEvidence 与 Manifest

Evidence 绑定：

```text
repository_snapshot_id
repository_path
line/range or structural locator
symbol_name
file_mode
content_hash
excerpt hash/ref
inspector identity/version
evidence kind
```

`RepositoryEvidenceManifest` 是对某个 Snapshot 的 Evidence 集合进行规范化后的不可变清单，至少包含：

```text
manifest_schema_version
repository_snapshot_identity
inspection_result_identity
evidence entries ordered by canonical evidence identity
manifest_hash
```

Evidence 完整性按当前 Proposal 的 `ProposalRequirementManifest` 判断，不要求 Project 所有可能 Evidence 均存在。

## 4.8 WP1-A 原子边界

每个命令写入：

```text
domain object / projection
必要的 Experiments-owned Project pointer transition
RuntimeEvent
Outbox
CommandReceipt
```

Snapshot capture 和 Inspection 读取外部文件的阶段不得长时间持有数据库锁。

---

# 5. WP1-B：Research Context、Policy 与 ContextCompilation

## 5.1 ProjectResearchContextRevision

Research Context 是用户维护的研究背景和声明，不是 Agent Memory，也不是行为 Policy。

规则：

- Revision 不可变；
- 修改创建新 Revision；
- Agent 不得自动把自身推断写入 Revision；
- `user_claims` 保持 `user_claim` provenance；
- Project 通过 `active_research_context_revision_id` 指向当前 Revision；
- 激活新 Revision 时锁 Project 行并使用 `row_version`；
- 激活不会修改任何既有 ProposalRevision。

公开命令：

```text
CreateResearchContextRevision
ActivateResearchContextRevision
```

## 5.2 ProjectPolicyRoot / Revision

Repository 中发现的 Policy 文件先是 non-authoritative source candidate。只有授权用户批准精确 `policy_content_hash` 后，PolicyRevision 才拥有行为控制权。

状态遵循冻结规格：

```text
draft
awaiting_approval
approved
revoked
superseded
```

规则：

- 不可变语义内容包括 Policy content、`policy_content_hash`、revision identity 与 source binding；
- lifecycle status 与 approval/revocation/supersession metadata 可按状态机更新，但不得修改上述语义内容；
- Approval 绑定 Revision 与精确内容 hash；
- 新 Revision approved 后旧 active Revision 转为 `superseded`；
- Repository 原文件变化不修改已批准 Revision；
- revoke/supersede 不批量改写旧 ProposalRevision；
- 旧 Proposal 是否失效由后续 Freeze Assessment 证明。

Project Policy 同时存在两个权威 pointer：

```text
ProjectPolicyRoot.active_revision_id
Project.active_project_policy_revision_id
```

必须满足：

```text
Project.active_project_policy_revision_id IS NULL
且 ProjectPolicyRoot.active_revision_id IS NULL

或

Project.active_project_policy_revision_id
== ProjectPolicyRoot.active_revision_id
```

Approve、Revoke 和 Supersede 在同一 UoW 中原子更新。正式 revoke 路径固定为：

```text
Revoke 非 active Revision
→ 只更新目标 Revision lifecycle metadata
→ ProjectPolicyRoot / Project active pointers 不变

Revoke 当前 active Revision
→ ProjectPolicyRevision: approved → revoked
→ ProjectPolicyRoot.active_revision_id = null
→ experiments.clear_active_project_policy_binding(...)
→ Project.active_project_policy_revision_id = null
→ RuntimeEvents / Outbox / CommandReceipt
→ 同一 UoW commit
```

批准新 Revision 时：

```text
new Revision → approved
old active Revision → superseded（如存在）
ProjectPolicyRoot.active_revision_id → new Revision
experiments.set_active_project_policy_binding(...)
Project.active_project_policy_revision_id → new Revision
RuntimeEvents / Outbox / CommandReceipt
→ 同一 UoW commit
```

Context service 负责验证目标 Revision 属于同一 Project、状态和 exact identity 满足条件；experiments-owned set/clear pointer handlers 只验证 Project identity、expected `row_version`、target Project binding 和 pointer transition，然后更新 Project pointer。Handler 不反向读取 Context 表判断 Policy 状态。

公开命令：

```text
CreateProjectPolicyRevision
SubmitProjectPolicyForApproval
ApproveProjectPolicyRevision
RevokeProjectPolicyRevision
```

WP1 不创建通用 Policy Approval 聚合；批准元数据属于不可变 PolicyRevision 契约。

## 5.3 ContextSourceReference 生命周期

`ContextSourceReference` 是 `context` 模块内部维护的 current projection，用于将不同权威来源统一投影为 Context Compiler 可消费的来源注册表。它不是客户端可创建的事实记录，也不是编译时不可变证据。

正式所有者：

```text
ContextSourceRegistryService
```

正式内部操作：

```text
SynchronizeProjectContextSources
```

该操作不建立公开 Route。Repository Snapshot/Evidence、Research Context、Project Policy 或其他已注册权威来源发生待提交的权威变化时，由对应公开命令在同一 UoW 内调用；也允许显式内部维护流程对已提交权威对象执行幂等同步。它不能读取未提交或客户端自报的 authority/freshness 结论。

最小字段：

```text
context_source_reference_id
project_id
source_object_type
source_object_id
source_object_identity
source_object_version nullable
source_content_hash nullable
instruction_authority
factual_provenance
availability
freshness
sensitivity
source_reference_schema_version
source_reference_identity
synchronized_at
row_version
```

数据库保证：

```text
UNIQUE(project_id, source_object_type, source_object_id)
```

生命周期：

```text
RepositorySnapshot / RepositoryEvidenceManifest
ProjectResearchContextRevision
ProjectPolicyRevision
其他已注册权威来源
        ↓
ContextSourceRegistryService
        ↓
create or update current ContextSourceReference projection
        ↓
CompileProjectContext
        ↓
immutable ContextCompilationSourceBinding
```

同步规则：

1. Registry Service 从权威对象读取 ID、identity、content hash、lifecycle、availability 与 freshness；
2. `instruction_authority`、`factual_provenance`、`sensitivity` 由版本化 source-type registry 和已批准 Policy 决定，客户端不得提交或覆盖；
3. 同一 source object 的当前状态更新同一 Reference projection，并递增 `row_version`；
4. Reference 更新不修改既有 `ContextCompilationSourceBinding`；Binding 才是编译时不可变观察；
5. Source object 被 revoke、supersede、unavailable 或 stale 时，Reference 投影同步更新，但不删除历史 Compilation；
6. 同步操作与触发它的权威对象变化、RuntimeEvent、Outbox 和 CommandReceipt 在同一 UoW 中提交；
7. 如果一个公开命令会产生多个 Source Reference 更新，Registry Service 对稳定排序后的 source keys 执行确定性 upsert。

`context_source_revision_set_identity` 根据参与本次编译的精确 Reference identities 稳定排序后计算：

```text
hash(
  project_id,
  ordered source_reference_identity set,
  source_registry_schema_version,
  source_type_registry_version
)
```

Compile 前必须重新读取这些 current References，并把每个精确 Reference identity、authority、provenance、availability 与 freshness复制到 immutable `ContextCompilationSourceBinding`。Repository 文本、README、注释或外部内容不能因进入 Registry 或 Compilation 而自动获得 Policy authority。

## 5.4 ContextCompilation 数据模型

为避免提前创建 WP2 Runtime `ContextSnapshot`，WP1 使用：

```text
ContextCompilation
ContextCompilationSourceBinding
ContextCompilationSegment
ContextCompilationSegmentSourceBinding
ContextCompilationFact
ContextCompilationPrecondition
ContextCompilationFreshness
```

### ContextCompilation

至少保存：

```text
context_compilation_id
project_id
compiler_schema_version
compiler_version
selector_version
rendering_version
redaction_version
deterministic_reduction_version
tokenizer / estimator identity
context budget profile
source binding manifest ref/hash
segment manifest ref/hash
compiled context ref/hash
typed_fact_manifest_hash
execution_precondition_manifest_hash
repository_snapshot_id / identity
repository_inspection_result_id / identity
research_context_revision_id / identity
project_policy_revision_id / identity
context_source_revision_set_identity
context_compilation_identity
created_at
```

不包含：

```text
agent_run_id
available_tool_manifest_hash
exposed_tool_manifest_hash
runtime policy manifest
run-scoped visibility decision
```

### ContextCompilationSourceBinding

保存编译时看到的精确 Source identity、content hash、authority、provenance、availability、freshness、dependency strength 和 included scope。Source Reference 后续变化不覆盖 Binding。

### ContextCompilationSegment

保存顺序、类型、编译内容引用/hash、authority、provenance、token estimate、redaction/truncation status 和 lineage manifest hash。

### ContextCompilationSegmentSourceBinding

保存：

```text
primary
supporting
equivalent
conflicting
```

的多来源 lineage。它必须引用同一 `ContextCompilation` 中的 `ContextCompilationSourceBinding`，不能只引用可变 `ContextSourceReference`，也不能跨 Compilation 绑定。

### ContextCompilationFact / Precondition

Typed facts 与 execution preconditions 固定为结构化、可查询 child records。父对象的两个 Manifest Hash 由各自子记录 identity 稳定排序后计算。

### ContextCompilationFreshness

每个 Compilation 只有一条 current freshness projection：

```text
context_compilation_id
status
reason_code
observed_input_set_identity
evaluator_version
checked_at
row_version
```

数据库保证：

```text
UNIQUE(context_compilation_id)
```

WP2 创建 Runtime `ContextSnapshot` 时引用整个 immutable `ContextCompilation`，不复制相同的 Segment 和 Source Binding 表；但必须增加 run-scoped selection/visibility overlay，并生成新的 Runtime render/hash。`ContextCompilation.compiled_context_ref` 不是所有 AgentRun 都可以原样发送给模型的最终 Prompt。

## 5.5 Typed facts

Compilation 必须区分：

```text
confirmed_fact
unverified_claim
missing_fact
unverifiable_fact
```

使用正式对象：

```text
ContextCompilationFact
- context_compilation_fact_id
- context_compilation_id
- fact_identity
- fact_state
- fact_code
- subject
- value_ref_or_hash nullable
- factual_provenance
- instruction_authority
- source_binding_id nullable
- freshness_or_snapshot_reference
- visibility_scope
```

规则：

1. `fact_identity` 使用独立 namespace 和 Canonical JSON V1；
2. `source_binding_id` 如存在，必须属于同一 Compilation；
3. Missing/Unverifiable Fact 可以没有 value，但必须有 reason/provenance；
4. 子记录按 `fact_identity` 稳定排序后形成 `typed_fact_manifest_hash`；
5. Manifest Hash 证明完整集合，子记录提供查询、外键引用和 Requirement evaluation；
6. Agent 推断不能被写成 `confirmed_fact`，除非存在权威 Source/Verifier 证明。

## 5.6 Execution preconditions

Compilation 必须暴露：

```text
required_information
required_capabilities
required_authority
environment_assumptions
unresolved_questions
```

使用正式对象：

```text
ContextCompilationPrecondition
- context_compilation_precondition_id
- context_compilation_id
- precondition_identity
- requirement_type
- requirement_code
- dependency_strength
- fact_state
- source_binding_id nullable
- fact_id nullable
- visibility_scope
- reason_code nullable
```

规则：

1. `precondition_identity` 使用独立 namespace 和 Canonical JSON V1；
2. `source_binding_id`、`fact_id` 如存在，必须属于同一 Compilation；
3. hard/soft dependency 由版本化 registry 定义；
4. 子记录按 `precondition_identity` 稳定排序后形成 `execution_precondition_manifest_hash`；
5. 模型自然语言不能证明 capability、authority、environment 或 Policy 已存在；
6. 下游 Revision Requirement 与 Freeze Assessment 引用精确 Fact/Precondition identity，而不是解析编译文本。

## 5.7 CompileProjectContext

公开命令：

```text
CompileProjectContext
```

只消费已持久化的不可变输入：

```text
RepositorySnapshot
RepositoryInspectionResult
RepositoryEvidenceManifest
active ResearchContextRevision
approved ProjectPolicyRevision
由 ContextSourceRegistryService 同步的 current ContextSourceReference set
compiler profile and versions
```

Compiler 必须：

- 确定性；
- 资源有界；
- 不调用模型；
- 不访问网络；
- 不执行新的 Git 扫描；
- 不在事务中等待外部进程。

缺失事实、不可验证事实和 unresolved question 是合法输出，不等于 Compiler 失败。只有未完成注册步骤、算法异常、不支持版本或不变量破坏才回滚。

## 5.8 Compilation identity 与 freshness

```text
相同精确输入
+ 相同 compiler / selector / rendering / reduction versions
+ 相同 Fact / Precondition child manifests
→ 相同 context_compilation_identity
```

数据库保证 Compilation identity 唯一，重复编译返回既有对象。Identity payload 不包含自身 identity，也不包含可变 freshness projection。

Compilation 内容不可变。输入变化时创建新 Compilation；旧 Compilation 不原地更新。

`ContextCompilationFreshness.status` 固定为：

```text
current
stale
unverifiable
invalid
```

每个 Compilation 只有一条 current projection：

```text
事务外或无锁阶段计算 bounded observed_input_set_identity
→ 短 UoW 锁定 current projection
→ 重新验证 Compilation 与 current pointers
→ 更新 status / reason / evaluator version / row_version
→ 写 context_compilation.freshness_assessed Event / Outbox
```

历史变化由 RuntimeEvent 保存；Freeze 时的精确 observed identities 进入不可变 Assessment checks。新 Compilation 的出现不自动修改旧 ProposalRevision；旧 Revision 在 Freeze 时根据精确 semantic binding 判断是否 `context_stale`。

`SubmitProposalForVerification` 与 Freeze 不得仅依赖可能过期的 current projection。它们各自执行命令内 bounded freshness evaluation，并在同一 UoW：

```text
更新 ContextCompilationFreshness current projection
记录本次 observed_input_set_identity
继续 materialization 或 Assessment
```

若候选 observation 与锁定后的 current pointers 不一致，命令返回 conflict 或重新执行，不得使用旧 projection 继续。

公开命令：

```text
CheckContextCompilationFreshness
```

# 6. WP1-C：Experiment、Proposal、Verifier 与人工决定

## 6.1 Experiment

`Experiment` 是同一研究主题下 Version 的长期容器，最小状态：

```text
active
archived
```

公开命令：

```text
CreateExperiment
ArchiveExperiment
```

## 6.2 Mutable ExperimentProposal

`ExperimentProposal` 是可编辑工作容器：

```text
proposal_id
project_id
experiment_id
title / description
draft_payload
draft_schema_version
draft_payload_hash
draft_version
latest_revision_id
status
created_at
updated_at
row_version
```

状态：

```text
active
closed
```

修改 draft 与提交验证都必须携带 `expected_draft_version`。Draft 更新使用乐观并发控制。

Proposal closed 后：

- 禁止继续修改 draft；
- 禁止创建新 ProposalRevision；
- 不修改既有 Revision；
- 关闭前已 approved Revision 仍可 Freeze。

公开命令：

```text
CreateExperimentProposal
UpdateProposalDraft
CloseExperimentProposal
SubmitProposalForVerification
```

## 6.3 ProposalRequirementManifest

该 Manifest 表示当前 Proposal materialization 所需的精确前提集合，不等于项目所有事实必须完整。

至少包括：

```text
requirement_schema_version
proposal schema / experiment type
verifier profile
required inspector identities
required evidence kinds / subjects
required information
required capabilities
required authority
required environment assumptions
required semantic binding types
manifest_hash
```

生命周期固定为：

```text
根据 Draft + schema + verifier profile 确定性生成候选 Requirement set
→ 使用候选集合执行 strict materialization preconditions
→ 入口失败：不持久化正式 Manifest，不创建 Revision
→ 入口通过：RequirementManifest 与 SemanticBindingManifest、Revision、VerifierResult 同 UoW 持久化
```

错误响应可以返回 bounded requirement failure summary，但不能创建孤立的权威 Requirement Manifest。

正式 `ProposalRequirementManifest` 是 `ProposalRevision` 的 child record，使用 `proposal_revision_id` 作为 owner FK；Revision 保存其 immutable hash，不建立反向必需 FK，从而避免 Revision 与 Manifest 的循环插入依赖。

入口判断只评估与当前 Proposal 相关的 hard requirements：

```text
无关 missing_fact
→ 不阻止 Revision

当前 Proposal hard precondition 缺失
→ 不允许 materialize Revision
```

## 6.4 ProposalSemanticBindingManifest

该 Manifest 表示 Revision 创建时实际绑定的完整语义环境：

```text
schema_version
repository_snapshot ID + identity
research_context_revision ID + identity
project_policy_revision ID + identity
context_compilation ID + identity
context_source_revision_set_identity
proposal_source_selection_manifest ID + identity
bound fact identities[]
bound precondition identities[]
dataset_specification_identity
execution_constraint_specification_identity
budget_constraint_specification_identity
environment specification identity
additional semantic bindings[]
manifest_hash
```

每个 ProposalRevision 必须恰好绑定一个规范化 DatasetSpecification。Dataset、Execution/Budget Constraints、Environment、Source、System、Tool 或其他 Proposal 明确依赖的设计输入必须在 Revision 创建时进入完整 binding，不得到 Freeze 时临时补齐为 latest/current。

正式 SemanticBindingManifest 与 RequirementManifest、ProposalSourceSelectionManifest、ProposalRevision、VerifierResult 在同一 UoW 持久化，不产生无 Revision 所有者的孤立正式 Manifest。

正式 `ProposalSemanticBindingManifest` 是 `ProposalRevision` 的 child record，使用 `proposal_revision_id` 作为 owner FK；Revision 保存其 immutable hash，不建立反向必需 FK。

Dataset 顶层基数固定为：

```text
每个 ProposalRevision 恰好一个 DatasetSpecification
每个成功 Freeze 恰好一个 DatasetSnapshot
每个 ExperimentVersion 恰好一个 DatasetSnapshot
```

无 Dataset 时使用 `DatasetSpecification.kind = none`。未来 train/validation/test 等多数据源由单个 DatasetSpecification 内部的 named entries manifest 表达，不在 WP1 将顶层关系扩展为多个 DatasetSnapshot。

Execution/Budget constraint specification 由 Draft、绑定的 approved ProjectPolicyRevision 和 server-supported schema defaults 确定性归一化，其 identity 在 Revision materialization 时进入 SemanticBindingManifest；Verifier、DesignDecisionBinding 和 Freeze 均不得重新选择不同约束来源。

### 6.4.1 ProposalSourceSelectionManifest

`ProposalSourceSelectionManifest` 表示 Proposal 声明的源码语义选择，不表示完整 Repository tree。至少包含：

```text
proposal_source_selection_manifest_id
proposal_revision_id
proposal_id
entrypoint path / symbol
working_directory specification
selected source roles[]
required source paths/patterns[]
excluded generated paths[]
selection_schema_version
selection_algorithm_version
entries_manifest_hash
proposal_source_selection_identity
```

它由服务端根据 Draft、Proposal schema、Verifier profile 和 Evidence 确定性生成，并与 Revision 同事务持久化。它是 `ProposalRevision` 的 child record，使用 `proposal_revision_id` 作为 owner FK；Revision 只保存其 immutable hash。正式子对象 `ProposalSourceSelectionEntry` 可以包含：

```text
repository_path
source_role
requirement_code
resolved_evidence_refs[]
stable_ordinal
entry_identity
```

Selection identity：

```text
hash(
  normalized entrypoint / working directory,
  ordered selection entry identities,
  selection_schema_version,
  selection_algorithm_version
)
```

该 identity 不包含 `proposal_revision_id`，从而可比较不同 Revision 的相同 source selection；Revision semantic identity 显式包含它。Freeze 使用该对象验证 entrypoint 和 required selection 均存在于完整 `SourceReferenceManifest` 中，但不得用它替代完整 Git tree manifest。

## 6.5 Strict materialization preconditions

`SubmitProposalForVerification` 只有在以下条件全部成立时才创建 Revision：

1. Proposal 为 `active`；
2. `expected_draft_version == current draft_version`；
3. draft schema 合法；
4. RepositoryRegistration 为 `active`；
5. Snapshot `is_clean == true`；
6. 本命令新执行的 bounded Repository probe 证明 Snapshot current；
7. required inspector 集合完整；
8. 当前 Proposal 所需 Evidence 完整并绑定同一 Snapshot；
9. ResearchContextRevision 是 Project 当前 active Revision；
10. ProjectPolicyRevision 是 Project 当前 approved Revision；
11. ContextCompilation 精确绑定上述输入；
12. 本命令新执行的 bounded Compilation freshness evaluation 证明 Compilation current；
13. 当前 Proposal 所需 hard execution preconditions 均满足；
14. 所有 required semantic bindings 均由服务端精确解析；
15. ProposalSourceSelectionManifest 可确定性解析且 required entrypoint/source evidence 完整。

命令边界：

```text
事务外执行 bounded Repository probe 和 Compilation input observation
→ 打开短 UoW
→ 锁定 Proposal、Project pointers 与 freshness projections
→ 验证 candidate observations 仍绑定同一数据库 identities
→ 更新 RepositorySnapshotFreshness 与 ContextCompilationFreshness current projections
→ 物化 Requirement/Semantic/SourceSelection Manifests、Revision 与 VerifierResult
→ commit
```

入口失败时：

```text
不创建 ProposalRevision
不创建 VerifierResult
不持久化正式 Requirement/Semantic/SourceSelection Manifests
不更新 latest_revision_id
不消耗 revision_number
不修改 draft
```

返回结构化 `RevisionMaterializationPreconditionError`，而不是创建 `verification_failed` Revision。

## 6.6 Immutable ProposalRevision

Revision 至少保存：

```text
proposal_revision_id
proposal_id
revision_number
based_on_revision_id
based_on_revision_identity nullable
materialized_from_draft_version
proposal_payload
proposal_payload_hash
proposal_requirement_manifest_hash
proposal_semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
revision_schema_version
verifier_profile_version
verifier_version
verifier_check_registry_version
revision_materialization_identity
revision_identity
status
created_by
created_at
```

Materialization identity：

```text
hash(
  proposal_id,
  materialized_from_draft_version,
  proposal_payload_hash,
  proposal_requirement_manifest_hash,
  proposal_semantic_binding_manifest_hash,
  proposal_source_selection_manifest_hash,
  revision_schema_version,
  verifier_profile_version,
  verifier_version,
  verifier_check_registry_version
)
```

`requirement_schema_version` 与 Proposal schema 已进入 Requirement Manifest 时不在外层重复，但 Requirement Manifest Hash 必须进入 materialization identity。

Revision semantic identity：

```text
hash(
  proposal_payload_hash,
  proposal_requirement_manifest_hash,
  proposal_semantic_binding_manifest_hash,
  proposal_source_selection_manifest_hash,
  revision_schema_version,
  based_on_revision_identity nullable
)
```

`based_on_revision_id` 是实例引用；lineage 进入 semantic identity 时使用持久化的不可变 `based_on_revision_identity`，不得依赖后续查询重新推导。

数据库保证：

```text
UNIQUE(proposal_id, revision_number)
UNIQUE(revision_materialization_identity)
```

相同输入的重复提交返回既有 Revision 和 VerifierResult。Revision 创建后不允许修改 payload、binding、selection、identity 或 lineage。所有进入 materialization identity 的版本字段必须持久化，以便离线重算和审计。

## 6.7 Synchronous deterministic verifier

`SubmitProposalForVerification` 是一个公开命令、两个独立领域记录：

```text
物化 ProposalRevision
→ 同步运行 deterministic verifier
→ 创建 VerifierResult
```

Verifier 只读取已持久化的不可变输入，不执行：

```text
模型调用
网络访问
新的 Git 扫描
外部进程
不受界限的计算
```

`VerifierResult` 至少绑定：

```text
verifier_result_id
proposal_revision_id
proposal_revision_identity
proposal_payload_hash
proposal_requirement_manifest_hash
semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
verifier_version
check_registry_version
overall_result
checks[]
result_hash
completed_at
```

每项 check 包含：

```text
check_code
result
subject identity
observed value/status
evidence refs
failure_detail_code
```

完整领域验证结果：

```text
passed → ProposalRevision.status = verified
failed → ProposalRevision.status = verification_failed
```

两者均提交。Verifier exception、检查未完成、数据不变量破坏或基础设施异常时整个 UoW 回滚。

WP1 不持久化长期 `verifying` 状态，也不持久化 `awaiting_approval`。

## 6.8 DesignApproval

批准与拒绝共享非持久化领域值对象：

```text
DesignDecisionBinding
- proposal_revision_id
- proposal_revision_identity
- proposal_payload_hash
- proposal_requirement_manifest_hash
- proposal_semantic_binding_manifest_hash
- proposal_source_selection_manifest_hash
- verifier_result_id
- verifier_result_hash
```

该值对象只复用构造、校验和 Canonical identity 逻辑，不建立独立表、聚合或公开 API。

`verified` 表示等待人工批准。授权用户执行：

```text
ApproveProposalRevision
```

该命令锁定 Revision，验证唯一 passing VerifierResult，创建不可变 `DesignApproval` 并执行：

```text
verified → approved
```

Approval 精确绑定：

```text
proposal_revision_id
proposal_revision_identity
proposal_payload_hash
proposal_requirement_manifest_hash
semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
verifier_result_id
verifier_result_hash
approval scope
authority identity / context
approval policy version
approved_by
approved_at
approval identity
```

`DesignDecisionBinding` 的 Canonical identity 必须覆盖 `proposal_revision_identity`、Requirement、Semantic Binding、Source Selection 和 VerifierResult 的全部精确 hash。`proposal_revision_identity` 进一步绑定 revision schema 与 lineage，因此不能只依赖各 Manifest hash。客户端不得提交这些 hash 或 authority 结论。

WP1 不实现 Approval 撤销，不允许：

```text
approved → verified
approved → rejected
```

## 6.9 DesignRejection

授权用户执行：

```text
RejectProposalRevision
```

该命令锁定 Revision，创建不可变 `DesignRejection` 并执行：

```text
verified → rejected
```

Rejection 精确绑定：

```text
proposal_revision_id
proposal_revision_identity
proposal_payload_hash
proposal_requirement_manifest_hash
semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
verifier_result_id
verifier_result_hash
rejected_by
authority identity / context
decided_at
rejection_reason_code
rejection_comment
rejection identity
```

`DesignApproval` 与 `DesignRejection` 为独立持久对象，不引入通用 `DesignDecision` 聚合。它们共享内部 `DesignDecisionBinding` 值对象，该值对象覆盖 ProposalRevision identity、Requirement、Semantic Binding、Source Selection 与 VerifierResult 的完整绑定。

Approve/Reject 的跨对象互斥由 Revision 行锁和 `verified` 状态机保证，不依赖先查询两张决策表。

## 6.10 Revision 状态机

WP1 稳定持久状态：

```text
verified
verification_failed
approved
rejected
context_stale
version_created
```

主要转换：

```text
Submit + verifier passed
→ verified

Submit + verifier failed
→ verification_failed

verified + approval
→ approved

verified + rejection
→ rejected

approved + semantic binding invalidation
→ context_stale

approved + successful freeze
→ version_created
```

以下状态对同一 Revision 不可原地恢复：

```text
verification_failed
rejected
context_stale
version_created
```

恢复工作必须创建新 Revision、重新验证并重新批准。

## 6.11 Multiple approved revisions

同一个 Proposal 可以同时存在多个 `approved`、尚未冻结的 Revision。

WP1 不维护：

```text
current_approved_revision_id
active_approval_id
```

`latest_revision_id` 只表达 revision lineage，不代表唯一有效或唯一批准。新 Revision 获批不撤销旧 Approval。Freeze 必须显式指定 `proposal_revision_id`。

---

# 7. Freeze Cross-Module Validation

## 7.1 权威调用链

```text
API Router
→ ContextFreezeOrchestrator
→ 收集并验证冻结前提
→ 生成 FreezePreconditionAssessment
→ 根据 Assessment 结果选择唯一分支

passed
→ FreezeExperimentVersionCommand
→ 创建 Source/Dataset/Environment immutable objects
→ 创建 ExperimentVersion
→ ProposalRevision: approved → version_created

failed + invalidate_context_binding
→ InvalidateProposalRevisionFromFreezeCommand
→ ProposalRevision: approved → context_stale

failed + preserve_approved
→ 不修改 Experiments 状态

所有分支
→ RuntimeEvents + Outbox + CommandReceipt
→ 一个 UnitOfWork commit
```

公开请求只提交：

```text
proposal_revision_id  # path
expected_design_approval_id
expected_revision_version
idempotency_key       # header
```

`project_id` 由服务端从 Revision 权威关系解析，用于授权、绑定验证和 idempotency scope。客户端不能提交 Assessment、pass/fail claim、current flags 或 Project binding。

`FreezeExperimentVersionCommand` 由 `experiments` 定义，只表示 passed path，至少传递：

```text
project_id
proposal_revision_id
assessment_id / hash / schema / validator / registry version
proposal_payload_hash
proposal_requirement_manifest_hash
semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
verifier_result_id / hash
design_approval_id / hash
required_check_proofs[]
source/dataset/environment candidate identities and content hashes
```

它不携带通用 `overall_result`；调用该命令即表示 Orchestrator 已提交一条完整 passed Assessment，但 Experiments 仍验证 required proof 与 authority binding。

`InvalidateProposalRevisionFromFreezeCommand` 由 `experiments` 定义，只表示 completed failed Assessment 的 invalidation path，至少传递：

```text
project_id
proposal_revision_id
assessment_id / hash / schema / validator / registry version
failure_effect = invalidate_context_binding
failed_check_proofs[]
proposal_payload_hash
proposal_requirement_manifest_hash
semantic_binding_manifest_hash
proposal_source_selection_manifest_hash
expected_revision_version
```

两个内部命令均不是公开 Route，也不接收 Context ORM/entity 类型。

## 7.2 FreezePreconditionAssessment

Assessment 是不可变、版本化、可重算 hash 的完整冻结证明：

```text
assessment_id
project_id
proposal_revision_id
assessment_schema_version
validator_version
check_registry_version
checks_manifest_hash
assessment_identity
assessment_hash
overall_result
checks[]
assessed_at
```

每项 check 至少包含：

```text
check_code
check_schema_version
subject_type
subject_id
expected_identity
observed_identity
observed_status
result
failure_effect
evidence_refs
check_identity
```

`check_identity`：

```text
hash(
  check_code,
  check_schema_version,
  subject_type,
  subject_id,
  expected_identity,
  observed_identity,
  observed_status,
  result,
  failure_effect,
  canonical evidence refs
)
```

所有 required check 按 registry-defined canonical order 排序后形成：

```text
checks_manifest_hash =
hash(ordered check_identity set)
```

Required check 覆盖：

```text
RepositoryRegistration
RepositorySnapshot
RepositorySnapshotFreshness
RepositoryInspectionResult
RepositoryEvidenceManifest
ProjectResearchContextRevision
ProjectPolicyRevision
ContextCompilation
ProposalRevision payload/requirement/binding/source-selection
VerifierResult
DesignApproval
```

Assessment identity：

```text
hash(
  project_id,
  proposal_revision_id,
  checks_manifest_hash,
  assessment_schema_version,
  validator_version,
  check_registry_version
)
```

`assessment_hash` 覆盖完整规范化 Assessment record，但排除：

```text
assessment_id
assessed_at
assessment_hash itself
```

因此相同精确观察和相同 validator/registry 产生相同 identity/hash，时间戳不破坏幂等。数据库保证：

```text
UNIQUE(assessment_identity)
```

## 7.3 experiments 的验证责任

Experiments internal handlers 不查询 Context persistence，但必须验证各自 command DTO。

`FreezeExperimentVersionCommand`：

```text
command.project_id == proposal.project_id
command.proposal_revision_id == target revision
assessment proposal payload hash == persisted revision payload hash
assessment requirement manifest hash == persisted revision requirement manifest hash
assessment semantic binding hash == persisted revision binding hash
assessment source selection hash == persisted revision source selection hash
VerifierResult / DesignApproval == revision authority records
所有 required passed check_code 存在且恰好一次
schema / validator / registry version 受支持
Source/Dataset/Environment candidate identities 与 Revision binding 一致
```

`InvalidateProposalRevisionFromFreezeCommand`：

```text
command.project_id == proposal.project_id
command.proposal_revision_id == target revision
Revision 当前状态 == approved
failure_effect == invalidate_context_binding
failed check registry proof 完整且版本受支持
payload / requirement / semantic binding / source selection hashes == persisted Revision
expected_revision_version 匹配
```

错误 Project、错误 Revision、payload integrity mismatch、缺少 experiments-owned authority record 属于 command error 或 invariant violation，不能伪装成 failed Assessment。

Experiments 只消费其拥有的 command contract；不得 import `context.models`、Context repository 或 `FreezePreconditionAssessment` entity。

## 7.4 Failed Assessment persistence

完成全部 required checks 的评估，无论 passed 或 failed 都持久化。

### Valid failed domain assessment

```text
FreezePreconditionAssessment(overall_result=failed)
完整 checks[]
可选的 ProposalRevision context_stale transition
RuntimeEvent
Outbox
CommandReceipt
```

不创建 Version。`preserve_approved` 不调用 Experiments 状态变更命令；`invalidate_context_binding` 必须在同一 UoW 调用 `InvalidateProposalRevisionFromFreezeCommand`。

### Not an assessment result

以下情况回滚且不创建 Assessment：

```text
请求格式错误
不存在或无权访问的 Project / Revision
Revision 不在 Freeze 入口状态
错误的 Project/Revision binding
proposal payload integrity mismatch
不支持的版本
validator bug
Git probe 崩溃
数据库异常
检查取消或未完成
事务失败
```

## 7.5 Versioned check failure effects

每个 required `check_code` 在版本化 registry 中固定：

```text
preserve_approved
invalidate_context_binding
command_error
```

### preserve_approved

可修复的当前执行前提失败，例如：

```text
repository_not_clean
repository_freshness_unverifiable
inspection_not_current_but_rerunnable
required_evidence_missing
context_compilation_missing_for_unchanged_inputs
unsupported_source_dependency
source_manifest_limit_exceeded
dataset_reproducibility_insufficient
```

结果：

```text
Assessment = failed
Revision = approved
Version = not created
```

### invalidate_context_binding

证明批准时绑定的精确语义输入已变化，例如：

```text
bound_repository_snapshot_identity_changed
research_context_revision_changed
project_policy_revision_changed
context_source_revision_changed
context_compilation_input_identity_changed
```

结果：

```text
Assessment = failed
InvalidateProposalRevisionFromFreezeCommand
Revision: approved → context_stale
Version = not created
```

Assessment、Revision transition、`proposal.revision_context_stale` RuntimeEvent、Outbox 与 Receipt 必须原子提交。

### command_error

错误绑定、payload integrity mismatch、缺失 experiments-owned authority 或系统不变量破坏不创建 Assessment，UoW 回滚。

该分类由 registry 决定，Orchestrator 不解析错误文本临时判断。

## 7.6 Irreversible context_stale

`context_stale` 对当前 Revision 不可逆。旧 Revision、VerifierResult、Approval 和 Assessment 永久保留。恢复路径：

```text
复制或引用旧 Revision 内容
→ 更新 Proposal draft
→ 创建新 ProposalRevision
→ 新 VerifierResult
→ 新 DesignApproval
→ 新 Freeze
```

## 7.7 External observations and short transaction boundary

Freeze 前执行有界 observation，形成精确候选 identity：

```text
Repository freshness observation
ContextCompilation input freshness observation
完整 frozen commit Git tree enumeration
ProposalSourceSelectionManifest 对完整 tree 的解析验证
Dataset observation based on persisted specification
Environment expectation materialization based on persisted specification
```

短 UoW 内：

1. 锁定 ProposalRevision、Project pointers 与 freshness projections；
2. 验证候选 Repository/Compilation observations 仍绑定相同数据库 identities；
3. 更新 `RepositorySnapshotFreshness` 与 `ContextCompilationFreshness` current projections；
4. 验证 ProposalSourceSelectionManifest 中的 entrypoint/required entries 存在于完整 Git tree manifest；
5. 创建包含本次 probe identity 的 Assessment checks；
6. passed 时创建 Source/Dataset/Environment immutable objects 与 Version。

命令内新鲜 probe 是本次判断依据；不得只读取可能很旧的 `current` projection。Projection 更新用于后续查询，Assessment check 保存本次精确 observed identity。

Version 冻结 immutable Snapshot 和 Freeze 时 observation，不宣称 mutable worktree、Dataset location 或宿主环境在 commit 后永远不变。

Freeze 不在持有数据库锁时执行完整 `git ls-tree`、复制源码、读取网络依赖、扫描无限 Dataset、物化 Worktree 或启动外部进程。完整 `git ls-tree` 和 LFS pointer detection 在事务外有界执行；短 UoW 只验证其绑定并持久化 manifest。需要网络或非有界外部探测的能力不属于 WP1 Freeze。

## 7.8 SourceReferenceManifest 与 Entry

二者由 `experiments` 所有，表示 ProposalRevision 所绑定 clean commit 的完整 Git tree reference manifest，不是 Proposal 选择的源码子集。

Freeze 在事务外执行：

```text
git ls-tree -r --full-tree <frozen_commit>
→ normalize path / mode / object type / object identity
→ detect gitlink/submodule
→ detect Git LFS pointer
→ compute complete entries_manifest_hash
```

`SourceReferenceManifest` 最小字段：

```text
source_reference_manifest_id
project_id
proposal_revision_id
proposal_revision_identity
repository_registration_id
repository_snapshot_id
repository_snapshot_identity
frozen_commit_sha
git_tree_sha
reference_manifest_method = git_ls_tree_v1
manifest_schema_version
normalization_algorithm_version
entries_manifest_hash
source_reference_manifest_digest  # equals entries_manifest_hash
source_reference_manifest_identity
created_at
```

正式子对象 `SourceReferenceManifestEntry`：

```text
source_reference_manifest_entry_id
source_reference_manifest_id
entry_identity
repository_path
file_mode
object_type
object_identity
content_hash nullable
is_gitlink
is_lfs_pointer
stable_ordinal
created_at
```

Entry identity 和 `entries_manifest_hash` 不包含数据库随机 ID。Canonical ordering 至少按 normalized repository path、mode、object type、object identity 稳定排序。

Revision-scoped identity：

```text
hash(
  proposal_revision_identity,
  repository_snapshot_identity,
  frozen_commit_sha,
  git_tree_sha,
  reference_manifest_method,
  entries_manifest_hash,
  manifest_schema_version,
  normalization_algorithm_version
)
```

`entries_manifest_hash` 作为不包含 Revision 的完整 Git tree content identity，可用于比较不同 Revision 的实际源码树。

P0 检测到以下任一项时，不得创建 ExperimentVersion：

```text
mode = 160000 / object_type = commit 的 gitlink/submodule
Git LFS pointer file
```

Assessment 使用 `UnsupportedSourceDependency` check 记录证据。WP1 不尝试联网拉取 submodule/LFS 内容。

Freeze 还必须验证独立 `ProposalSourceSelectionManifest` 的 entrypoint 和 required source entries 均存在于该完整 Manifest 中；source role 不进入完整 Git tree Entry。

Manifest 构建必须受 `max_source_manifest_entries`、路径长度、单次 blob-header probe 和总处理时间限制。超过受支持上限时形成 completed failed Assessment check `source_manifest_limit_exceeded`，不持有数据库锁继续扫描。

数据库至少保证：

```text
UNIQUE(proposal_revision_id)
UNIQUE(source_reference_manifest_identity)
UNIQUE(source_reference_manifest_id, stable_ordinal)
UNIQUE(source_reference_manifest_id, entry_identity)
```

## 7.9 SourceSnapshot

`SourceSnapshot` 证明一个 Revision/Version 使用了哪一个精确 frozen commit 和完整 SourceReferenceManifest：

```text
source_snapshot_id
proposal_revision_id
proposal_revision_identity
source_reference_manifest_id
source_reference_manifest_identity
source_reference_manifest_digest
repository_registration_id
repository_snapshot_id
repository_snapshot_identity
frozen_commit_sha
git_tree_sha
reference_manifest_method = git_ls_tree_v1
materialization_method = git_archive
snapshot_content_hash
snapshot_schema_version
source_snapshot_identity
created_at
```

Revision-scoped identity：

```text
hash(
  proposal_revision_identity,
  source_reference_manifest_identity,
  repository_snapshot_identity,
  frozen_commit_sha,
  git_tree_sha,
  reference_manifest_method,
  materialization_method,
  snapshot_schema_version
)
```

`snapshot_content_hash` 由完整 `entries_manifest_hash`、frozen commit/tree identity 与 snapshot schema 计算，不包含 Revision，用于跨 Revision 内容比较。它不复制完整 Repository 文件内容。

WP3 创建 Attempt Workspace 时必须：

```text
git archive <frozen_commit>
→ safe extract
→ build materialized source manifest
→ compare semantic contents with SourceReferenceManifest
```

不得从主工作树执行，也不得在 materialization mismatch 后回退到主工作树。

数据库至少保证：

```text
UNIQUE(proposal_revision_id)
UNIQUE(source_snapshot_identity)
```

## 7.10 DatasetSnapshot

`DatasetSnapshot` 是 ProposalRevision 唯一规范化 `DatasetSpecification` 在 Freeze 时形成的 Revision-owned 不可变 observation：

```text
dataset_snapshot_id
proposal_revision_id
proposal_revision_identity
dataset_specification_identity
reference_type
path_or_uri
identity_type
identity_value
dataset_source_identity
observation_version
content_or_manifest_hash
reproducibility_level
manifest_metadata
manifest_metadata_hash
warning_codes
dataset_snapshot_identity
captured_at
```

`identity_type` 使用冻结契约：

```text
content_digest
manifest_digest
external_version
size_mtime_manifest
mutable_reference
```

`reproducibility_level`：

```text
strict
strong
best_effort
```

规则：

- `content_digest`、可靠 `manifest_digest`、可靠 `external_version` 可为 `strict`；
- `size_mtime_manifest` 通常为 `strong` 或 `best_effort`；
- `mutable_reference` 必须为 `best_effort`；
- best-effort DatasetSnapshot 可以进入 Version 和后续执行，但必须携带 warning；
- 若 Project Policy 或 Proposal Requirement 要求 strict reproducibility，而 observation 只能形成 strong/best_effort，则 Freeze Assessment failed；
- 完全无法形成任一受支持 identity 时，Assessment failed，不创建虚假 Snapshot。

Revision-scoped identity：

```text
hash(
  proposal_revision_identity,
  dataset_specification_identity,
  reference_type,
  identity_type,
  identity_value,
  reproducibility_level,
  content_or_manifest_hash,
  manifest_metadata_hash,
  canonical warning_codes,
  observation_version
)
```

`content_or_manifest_hash` 不包含 Revision，用于跨 Revision 内容比较。数据库至少保证：

```text
UNIQUE(proposal_revision_id)
UNIQUE(dataset_snapshot_identity)
```

不需要 Dataset 的 Proposal 也不得使用 nullable Dataset binding。其规范化 specification 为：

```text
DatasetSpecification.kind = none
```

Freeze 对该 specification 创建真实的空 DatasetSnapshot：

```text
reference_type = none
path_or_uri = null
identity_type = content_digest
identity_value = canonical_empty_dataset_manifest_hash
dataset_source_identity = no_dataset
content_or_manifest_hash = canonical_empty_dataset_manifest_hash
reproducibility_level = strict
manifest_metadata_hash = canonical_empty_dataset_metadata_hash
warning_codes = []
```

因此每个 ProposalRevision、成功 Freeze 和 ExperimentVersion 的 Dataset 顶层基数均为一；每个 ExperimentVersion 始终绑定一个非空、可审计的 `DatasetSnapshot`。

## 7.11 EnvironmentExpectation

`EnvironmentExpectation` 是 Revision/Version 对未来 Runner 环境的 Revision-owned 不可变要求，不是对当前宿主机运行状态的虚假 Snapshot：

```text
environment_expectation_id
proposal_revision_id
proposal_revision_identity
environment_specification_identity
runtime_requirements
dependency_constraints
hardware_expectations
expectation_content_hash
expectation_schema_version
environment_expectation_identity
created_at
```

Revision-scoped identity：

```text
hash(
  proposal_revision_identity,
  environment_specification_identity,
  expectation_content_hash,
  expectation_schema_version
)
```

`expectation_content_hash` 由规范化 expectation payload 计算且不包含 Revision，用于跨 Revision 内容比较。数据库至少保证：

```text
UNIQUE(proposal_revision_id)
UNIQUE(environment_expectation_identity)
```

它只从 Revision 已绑定、可验证的 specification 确定性构建。实际环境是否满足 Expectation 由 WP3 Runner/Verifier 在执行前后判断。

## 7.12 Freeze object atomicity and failure semantics

成功 Freeze 在同一 UoW 中提交：

```text
FreezePreconditionAssessment(passed)
SourceReferenceManifest
SourceReferenceManifestEntry[]
SourceSnapshot
DatasetSnapshot
EnvironmentExpectation
ExecutionConstraintManifest
BudgetConstraintManifest
ExperimentVersion
ProposalRevision: approved → version_created
RuntimeEvents / Outbox
CommandReceipt
```

完整 Assessment failed 时不创建上述 Version input objects；执行异常、绑定错误或不完整 observation 回滚且不创建 Assessment。

# 8. ExperimentVersion

## 8.1 Version-owned execution and budget constraints

WP1 不建立独立 `ExecutionProfile` 或 `BudgetPolicy` 聚合。formal Batch 的权威限制由 `ExperimentVersion` 拥有的两个不可变、Canonical value objects 提供：

```text
ExecutionConstraintManifest
- max_planned_concurrency
- allowed_batch_purposes
- constraint_schema_version
- resolution_profile_version
- source_binding_identities[]
- constraint_identity

BudgetConstraintManifest
- currency_or_unit
- maximum_planned_budget nullable
- allowed_override_rule
- constraint_schema_version
- resolution_profile_version
- source_binding_identities[]
- constraint_identity
```

来源只能是：

```text
Proposal payload 中的显式约束
+ ProposalRevision 绑定的 approved ProjectPolicyRevision
+ server-supported schema defaults
```

解析优先级、合并规则和默认值由版本化 `constraint_resolution_profile_version` 定义。不得在 CreateRunBatch 时读取环境变量、可变配置文件、Project 当前 Policy 或临时默认值。

在 ProposalRevision materialization 时，服务端确定性生成：

```text
execution_constraint_specification_identity
budget_constraint_specification_identity
```

并写入 `ProposalSemanticBindingManifest`。Deterministic Verifier 检查规范、Policy 允许范围和 schema 支持；DesignApproval/DesignRejection 通过 `proposal_revision_identity` 与 Semantic Binding Manifest 精确绑定这些 specification identities。

成功 Freeze 时，ContextFreezeOrchestrator 只从 Revision 已绑定的 specification 和同一 approved Policy binding 物化最终 Manifest；不得重新选择 latest/current Policy 或更换默认 profile。两个 Manifest 作为 Version-owned value objects 与 Version 同事务创建，不建立公开创建命令，也不作为独立聚合生命周期。

ExperimentVersion 至少持久化：

```text
execution_constraint_manifest_json
execution_constraint_manifest_hash
execution_constraint_identity
budget_constraint_manifest_json
budget_constraint_manifest_hash
budget_constraint_identity
constraint_resolution_profile_version
```

Batch 只依据这些 Version-owned manifests 校验 concurrency、purpose 和 budget。

## 8.2 创建与绑定

成功 Freeze 原子创建：

```text
FreezePreconditionAssessment(passed)
SourceReferenceManifest
SourceReferenceManifestEntry[]
SourceSnapshot
DatasetSnapshot
EnvironmentExpectation
ExecutionConstraintManifest
BudgetConstraintManifest
ExperimentVersion
ProposalRevision: approved → version_created
RuntimeEvents / Outbox
```

Version 至少绑定：

```text
proposal_revision_id / payload hash / requirement manifest hash / semantic binding hash / source selection hash
verifier_result_id / hash
design_approval_id / hash
freeze_assessment_id / hash
repository_snapshot_id / identity
research_context_revision_id / identity
project_policy_revision_id / identity
context_compilation_id / identity
source_reference_manifest_id / hash
source_snapshot_id / identity
dataset_snapshot_id / identity
environment_expectation_id / identity
execution_constraint_manifest hash / identity
budget_constraint_manifest hash / identity
constraint_resolution_profile_version
parameter and trial dimension specifications
replicate dimensions and minimum_planned_replicates_per_group
validation and summary policies
version schema / canonicalization versions
experiment_version_identity
```

Freeze 不读取 Project 当前指针替换 Revision 已绑定输入，也不使用“dataset snapshot or manifest”一类可选语义。每种正式冻结输入均有唯一对象类型和 identity。

## 8.3 不可变性

Version semantic content 创建后不可修改。显示名、标签、人工备注、archived flag 等非语义 metadata 必须与 semantic payload 分离。

## 8.4 唯一与幂等

```text
UNIQUE(experiment_id, version_number)
UNIQUE(experiment_versions.proposal_revision_id)
UNIQUE(experiment_versions.design_approval_id)
```

同一 Revision 重复 Freeze 返回既有 Version，不分配新 version number。

---

# 9. RunBatch 与 Trial

## 9.1 CreateRunBatch

客户端提交：

```text
experiment_version_id
requested_parameter_subset
requested_seed_or_replicate_subset
planned_concurrency
budget_override nullable
summary_rule_version
batch_purpose = formal
human_note nullable
idempotency_key
```

WP1 只支持 `batch_purpose = formal`。服务端：

1. 读取 immutable Version；
2. 验证参数、seed/replicate 选择位于 Version 允许集合；
3. 验证 `batch_purpose` 位于 Version `ExecutionConstraintManifest.allowed_batch_purposes`；
4. 验证 `planned_concurrency <= ExecutionConstraintManifest.max_planned_concurrency`；
5. 按 `BudgetConstraintManifest.allowed_override_rule` 验证 budget override，且不得超过 `maximum_planned_budget`（如非空）；
6. 验证 summary rule version 与 Version 兼容；
7. Canonicalize parameter/replicate/additional dimensions；
8. 计算笛卡尔积前验证资源上限；
9. 按 Version Summary Group 计算 planned replicate 数；
10. 验证每组达到 `minimum_planned_replicates_per_group`，禁止通过子集绕过获批设计；
11. 计算 Trial condition identities；
12. 检测 Batch 内重复；
13. 稳定排序；
14. 原子写入 Batch、全部 Trial、RuntimeEvent、Outbox 和 CommandReceipt。

最低 planned replicate 不满足时返回：

```text
InsufficientPlannedReplicates
```

不得先建空 Batch 后异步补 Trial。Smoke Test 不创建 RunBatch；诊断性小 Batch 推迟到 P1。

## 9.2 RunBatch

Batch 是不可变正式执行计划：

```text
run_batch_id
experiment_version_id
requested dimension manifest
resolved dimension manifest
planned_concurrency
resolved_execution_constraint_identity
resolved_budget_constraint_identity
budget_override nullable
summary_rule_version
batch_purpose = formal
human_note nullable
trial_count
trial_manifest_hash
first_ordinal
last_ordinal
batch schema version
expansion algorithm version
batch_identity
created_by
created_at
```

Batch identity：

```text
hash(
  experiment_version_identity,
  canonical requested parameter subset,
  canonical requested seed/replicate subset,
  planned_concurrency,
  resolved_execution_constraint_identity,
  resolved_budget_constraint_identity,
  canonical budget_override,
  summary_rule_version,
  batch_purpose,
  batch schema version,
  expansion algorithm version
)
```

`resolved_execution_constraint_identity` 必须等于目标 Version 的 `execution_constraint_identity`，`resolved_budget_constraint_identity` 必须等于目标 Version 的 `budget_constraint_identity`。Batch 不读取 Project 当前 Policy 或不存在的 `default_budget_policy_id`。

`human_note` 属于非语义 display metadata，不进入 `batch_identity`。

`trial_manifest_hash` 根据稳定排序后的语义条目计算：

```text
[
  {
    stable_ordinal,
    trial_condition_hash
  }
]
```

数据库随机 `trial_id` 不进入 Batch semantic manifest。该 Hash 证明 Batch 的完整 Trial 条件集合；相同 Version、请求维度与执行计划语义必须产生相同 Hash。

Formal Batch 必须保存并可验证：

```text
allowed_trial_dimensions
replicate_dimensions
minimum_planned_replicates_per_group
summary grouping identity
```

上述规则来自 Version；Batch 保存解析后的不可变计划和验证结果，不修改 Version。

## 9.3 Trial

Trial 表示 Batch 内一个精确 resolved condition：

```text
trial_id
run_batch_id
experiment_version_id
resolved parameters / hash
seed
additional dimensions
trial_condition_hash
stable_ordinal
selected_attempt_id nullable
selected_validation_report_id nullable
selection_revision
execution_state_projection
validation_state_projection
created_at
```

Trial condition identity：

```text
hash(
  experiment_version_identity,
  normalized resolved parameters,
  seed,
  normalized additional dimensions,
  trial identity schema version
)
```

数据库保证：

```text
UNIQUE(run_batch_id, trial_condition_hash)
```

同一 Version 的不同 Batch 可以包含相同条件；它们属于不同执行计划，不在 WP1 做跨 Batch 去重。

`selected_attempt_id` 可在 Attempt foundation 存在后建立真实外键。`selected_validation_report_id` 由冻结 Trial 契约要求，但 ValidationReport 在 WP4 才建立：

```text
WP1
→ 创建 nullable UUID
→ 添加 CHECK (selected_validation_report_id IS NULL)

WP4
→ 创建 ValidationReport
→ 删除该 CHECK
→ 添加真实 FK 与权威更新契约
```

仅依靠服务层不写非 null 不足以保护数据库完整性。

## 9.4 Deterministic ordering

相同 Version、请求维度和 expansion algorithm version 必须生成相同：

```text
Trial set
trial_condition_hash set
stable ordering
trial_count
```

排序基于 Canonical Trial condition identity，不依赖请求 JSON 字段顺序、Python dict 顺序或数据库返回顺序。

## 9.5 Initial Trial projections

WP1 创建 Trial 时必须固定初始化：

```text
selected_attempt_id = null
selected_validation_report_id = null
selection_revision = 0
execution_state_projection = not_started
validation_state_projection = not_validated
```

WP1 不提供修改这些执行/验证投影的业务命令。WP3/后续 Validation 只能根据权威 Attempt、Validation 和 cancellation facts 更新它们。

不得加入：

```text
active_attempt_id
attempt_count
result_state
eligible_for_rerun
```

除非后续冻结架构显式要求。

## 9.6 Resource bounds

服务端配置并测试：

```text
max_trials_per_batch
max_parameter_dimensions
max_values_per_dimension
max_canonical_payload_size
```

超过上限时在写入前返回结构化错误，避免笛卡尔积和 Event 数量失控。

---

# 10. Attempt Persistence Foundation

## 10.1 WP1 实现范围

WP1 只实现冻结规格已证明必要的字段和约束：

```text
attempt_id
trial_id
attempt_number
execution_status
created_at
```

只有冻结 Workflow/Executor 规格明确要求作为 WP1 数据库约束基础时，才增加：

```text
lease_owner
lease_generation
started_at
finished_at
terminal_reason
```

禁止仅因“未来可能需要”建立 nullable 占位列。

## 10.2 状态与 active set

Attempt 状态和 active set 不由 WP1 自行发明，直接引用冻结 Experiment Domain：

```text
created
preparing
submitted
running
succeeded
failed
cancelled
unknown
```

Active Attempt set：

```text
created | preparing | submitted | running | unknown
```

## 10.3 数据库约束

```text
UNIQUE(trial_id, attempt_number)
PARTIAL UNIQUE(attempts.trial_id)
WHERE execution_status IN (
  created, preparing, submitted, running, unknown
)
```

外键和删除限制必须保证有 Attempt 历史的 Trial、Batch、Version 不会被级联物理删除。

## 10.4 无业务创建命令

WP1 不实现：

```text
CreateAttempt
CreateTrialAttempt
StartAttempt
ClaimAttempt
HeartbeatAttempt
FinishAttempt
RetryAttempt
CancelAttempt
ReconcileAttempt
SelectAttemptResult
AuthorizeTrialRerun
```

`CreateRunBatch` 不创建初始 Attempt。

数据库集成测试可以直接插入 Attempt 验证约束，但不代表生产系统具备 Attempt 创建能力。

---

# 11. 公开 API 与内部命令边界

## 11.1 WP1-A public commands

```text
POST /projects
POST /projects/{project_id}/archive
POST /projects/{project_id}/repositories
POST /repositories/{repository_registration_id}/availability-checks
POST /repositories/{repository_registration_id}/revoke
POST /repositories/{repository_registration_id}/snapshots
POST /repository-snapshots/{snapshot_id}/freshness-checks
POST /repository-snapshots/{snapshot_id}/inspections
```

## 11.2 WP1-B public commands

```text
POST /projects/{project_id}/research-context-revisions
POST /projects/{project_id}/research-context-revisions/{revision_id}/activate
POST /projects/{project_id}/policy-revisions
POST /project-policy-revisions/{revision_id}/submit-for-approval
POST /project-policy-revisions/{revision_id}/approve
POST /project-policy-revisions/{revision_id}/revoke
POST /projects/{project_id}/context-compilations
POST /context-compilations/{context_compilation_id}/freshness-checks
```

## 11.3 WP1-C public commands

```text
POST /projects/{project_id}/experiments
POST /experiments/{experiment_id}/archive
POST /experiments/{experiment_id}/proposals
PUT  /proposals/{proposal_id}/draft
POST /proposals/{proposal_id}/close
POST /proposals/{proposal_id}/submit-for-verification
POST /proposal-revisions/{revision_id}/approve
POST /proposal-revisions/{revision_id}/reject
POST /proposal-revisions/{revision_id}/freeze
POST /experiment-versions/{version_id}/batches
```

`PUT /proposals/{proposal_id}/draft` 是完整替换命令，body 至少包含：

```text
draft_payload
expected_draft_version
```

服务端重新验证完整 schema 并计算完整 `draft_payload_hash`。局部变更必须使用未来的显式领域命令，不使用语义模糊的 generic PATCH。

Freeze body 只包含：

```text
expected_design_approval_id
expected_revision_version
```

`POST /experiment-versions/{version_id}/batches` body 至少包含：

```text
requested_parameter_subset
requested_seed_or_replicate_subset
planned_concurrency
budget_override nullable
summary_rule_version
batch_purpose = formal
human_note nullable
```

`Idempotency-Key` 位于 Header；Project 由目标对象权威关系解析。

## 11.4 Internal-only commands

```text
experiments.set_primary_repository_binding(...)
experiments.clear_primary_repository_binding(...)
experiments.set_active_research_context_binding(...)
experiments.set_active_project_policy_binding(...)
experiments.clear_active_project_policy_binding(...)
context.synchronize_project_context_sources(...)
experiments.freeze_experiment_version(FreezeExperimentVersionCommand)
experiments.invalidate_proposal_revision_from_freeze(
  InvalidateProposalRevisionFromFreezeCommand
)
```

`context.synchronize_project_context_sources(...)` 由 `ContextSourceRegistryService` 拥有，只接受服务端权威对象变化或显式内部维护调用，不接受客户端 authority/provenance/freshness 输入。

以及所有 direct ORM mutation、identity calculation 和 state transition handler 均不暴露为 Route。内部 handler 接受调用方 UoW，不开启嵌套事务。

## 11.5 Query API

查询接口返回权威对象、状态、identity 和 evidence refs，但不允许通过通用 PATCH 修改语义状态。至少支持：

```text
Project details and current pointers
Repository Snapshot / Freshness / Inspection / Evidence
Research Context and Policy revisions
ContextCompilation and typed preconditions
Proposal draft and Revision history
approved-unfrozen Revisions
Freeze assessments
ExperimentVersion
RunBatch and Trials
```

Proposal 查询必须区分：

```text
latest_revision
approved_unfrozen_revisions[]
version_created_revisions[]
stale_or_rejected_revisions[]
```

不能把 latest Revision 展示成唯一有效 Revision。

---

# 12. 错误与已提交领域结果模型

所有真正的领域错误响应至少包含：

```text
error_code
human_message
aggregate_type nullable
aggregate_id nullable
current_state nullable
expected_state nullable
retryable
details / failed requirement codes
```

Create、schema、path boundary 等尚无既存 Aggregate 的错误允许 aggregate/state 字段为 null；不得为满足响应 schema 伪造对象 ID 或状态。

必须先区分：

```text
completed committed domain result
command precondition / conflict error
authorization error
infrastructure / execution failure
```

规范 HTTP 语义：

```text
完整 Verifier 执行并判定 failed
→ 201 Created / 2xx
→ 返回 ProposalRevision + VerifierResult + outcome_kind=verification_completed_failed

完整 Freeze Assessment 执行并判定 failed
→ 201 Created / 2xx
→ 返回 Assessment + failure_effect + experiment_version=null
→ outcome_kind=freeze_assessment_completed_failed

状态/expected version/幂等冲突
→ 409

请求 schema 或 materialization precondition 不满足
→ 422

权限不足
→ 403

Verifier/Assessment/数据库/基础设施执行异常
→ 5xx / 503
```

已提交的 failed domain result 会创建成功 `CommandReceipt`，重放返回同一对象和 outcome；不得提交数据库后再以命令异常形式响应。

## 12.1 Repository

```text
RepositoryPathOutsideAllowedRoot
RepositorySymlinkEscape
RepositoryNotGit
RepositoryUnavailable
RepositoryRegistrationConflict
RepositorySnapshotDirty
RepositorySnapshotInspectionBundleLimitExceeded
RepositorySnapshotInspectionBundleUnsupportedEntry
RepositorySnapshotNotCurrent
RepositoryFreshnessUnverifiable
InspectionIncomplete
RequiredInspectorMissing
RequiredEvidenceMissing
```

## 12.2 Context / Policy

```text
ResearchContextRevisionConflict
ActiveResearchContextChanged
PolicyNotApproved
PolicyRevisionChanged
PolicyContentHashMismatch
ContextCompilationStale
ContextCompilationNonDeterministic
RequiredInformationMissing
RequiredCapabilityUnavailable
RequiredAuthorityMissing
EnvironmentAssumptionUnverified
```

## 12.3 Proposal / Verifier / Decision

命令错误：

```text
ProposalClosed
DraftVersionConflict
RevisionMaterializationPreconditionNotSatisfied
UnsupportedProposalSchema
VerifierExecutionFailed
ProposalRevisionNotVerified
ProposalRevisionAlreadyDecided
DesignApprovalConflict
DesignRejectionConflict
ProposalRevisionContextStale
```

已提交领域结果表达：

```text
ProposalRevision.status = verification_failed
VerifierResult.overall_result = failed
outcome_kind = verification_completed_failed
```

`ProposalVerificationFailed` 可作为查询/UI outcome code，但不得作为表示“事务未提交”的 command exception。

## 12.4 Freeze

命令错误：

```text
FreezeCommandStateInvalid
FreezeAssessmentExecutionFailed
FreezeAssessmentBindingMismatch
FreezeAssessmentVersionUnsupported
ApprovalStale
ProposalIntegrityViolation
ExperimentVersionInvariantConflict
UnsupportedSourceDependency
SourceManifestLimitExceeded
DatasetReproducibilityInsufficient
```

正常幂等命中：

```text
同一 ProposalRevision
+ same DesignApproval / Assessment authority binding
+ existing ExperimentVersion
→ 返回既有 ExperimentVersion
→ 200 / committed replay
```

只有已存在 Version 与当前 Revision/Approval/Assessment identity 发生不可能的冲突时，才返回 `ExperimentVersionInvariantConflict`。

已提交领域结果表达：

```text
FreezePreconditionAssessment.overall_result = failed
failure_effect = preserve_approved | invalidate_context_binding
ExperimentVersion = null
outcome_kind = freeze_assessment_completed_failed
```

`FreezeAssessmentFailed` 可作为查询/UI outcome code，但不得作为表示“事务未提交”的 command exception。

## 12.5 Batch / Trial / Attempt foundation

```text
BatchDimensionOutsideVersion
BatchExpansionLimitExceeded
InsufficientPlannedReplicates
PlannedConcurrencyOutsideLimit
BudgetOverrideOutsideLimit
SummaryRuleVersionUnsupported
DuplicateTrialCondition
BatchIdentityConflict
TrialConditionCanonicalizationFailed
AttemptStatusUnsupported
TrialAlreadyHasActiveAttempt
DuplicateAttemptNumber
```

# 13. Concurrency 与数据库不变量

## 13.1 Row locks

只在短事务使用：

```text
Project current pointer changes
Proposal draft submit / approve / reject / freeze
Version number allocation
Batch materialization
```

不得持锁等待 Git scan、网络、模型、外部进程或长时间文件读取。

## 13.2 Optimistic concurrency

至少用于：

```text
Project active pointers
RepositoryRegistration mutable availability
RepositorySnapshotFreshness
ExperimentProposal draft
```

命令携带 expected version；冲突返回结构化错误。

## 13.3 关键数据库约束

```text
CommandReceipt(scope_identity, command_name, command_version, idempotency_key) unique
one active primary Repository per Project
RepositorySnapshot identity unique
RepositorySnapshotFreshness(snapshot_id) unique
RepositorySnapshotInspectionBundle(snapshot_id) unique
RepositorySnapshotInspectionBundleEntry(bundle_id, stable_ordinal) unique
RepositorySnapshotInspectionBundleEntry(bundle_id, entry_identity) unique
ResearchContextRevision(project_id, revision_number) unique
ProjectPolicyRevision(root_id, revision_number) unique
ContextSourceReference(project_id, source_object_type, source_object_id) unique
ContextCompilation identity unique
ContextCompilationFreshness(compilation_id) unique
ContextCompilationFact(compilation_id, fact_identity) unique
ContextCompilationPrecondition(compilation_id, precondition_identity) unique
ContextCompilation child/source/segment bindings cannot cross Compilation
ProposalRevision(proposal_id, revision_number) unique
Revision materialization identity unique
ProposalRequirementManifest(proposal_revision_id) unique
ProposalSemanticBindingManifest(proposal_revision_id) unique
ProposalSourceSelectionManifest(proposal_revision_id) unique
ProposalSourceSelectionEntry(manifest_id, stable_ordinal) unique
ProposalSourceSelectionEntry(manifest_id, entry_identity) unique
one VerifierResult per ProposalRevision
one DesignApproval per ProposalRevision
one DesignRejection per ProposalRevision
one ExperimentVersion per ProposalRevision
one ExperimentVersion per DesignApproval
Freeze assessment identity unique
FreezePreconditionCheck(assessment_id, check_code) required-code uniqueness
SourceReferenceManifest(proposal_revision_id) unique
SourceReferenceManifest semantic identity unique
SourceReferenceManifestEntry(manifest_id, stable_ordinal) unique
SourceReferenceManifestEntry(manifest_id, entry_identity) unique
SourceSnapshot(proposal_revision_id) unique
SourceSnapshot semantic identity unique
DatasetSnapshot(proposal_revision_id) unique
DatasetSnapshot semantic identity unique
EnvironmentExpectation(proposal_revision_id) unique
EnvironmentExpectation semantic identity unique
RunBatch identity unique
Trial(batch_id, trial_condition_hash) unique
Attempt(trial_id, attempt_number) unique
one active Attempt per Trial partial unique
selected_validation_report_id IS NULL until WP4
```

Approval/Rejection 跨表互斥由 Revision 状态锁保证；不能只靠分别查询两张表。

以下为应用不变量，不伪装成普通 SQL CHECK：

```text
CommandReceipt Result Envelope canonical hash 与顶层 refs 一致
ProjectPolicyRoot.active_revision_id 与 Project.active_project_policy_revision_id 一致
clean Snapshot 不存在 Inspection Bundle；dirty Snapshot 恰好一个 Inspection Bundle
ContextSourceReference fields/identity 只能由 Registry Service 根据权威对象确定性同步
Freeze checks_manifest_hash 与 ordered check identities 一致
Revision / Version / Batch semantic identities 可按持久化字段重算
每个 ProposalRevision 恰好一个 DatasetSpecification，成功 Freeze/Version 恰好一个 DatasetSnapshot
RunBatch resolved constraint identities 等于目标 Version-owned constraint identities
```

这些关系通过同 UoW handler、Canonical identity helper 和 PostgreSQL integration tests 保证；未来若引入数据库 trigger/function，必须单独设计和版本化。

# 14. Migration 与模型注册策略

## 14.1 Migration chain

WP1 Migration 从：

```text
down_revision = 0002_runtime_events_outbox
```

继续，并按领域依赖形成连续 Migration：

```text
WP1-A
- generic CommandReceipt
- Project core schema
- Repository registration/snapshot/freshness
- Repository Snapshot Inspection Bundle / Entry
- Inspection/Evidence
- primary Repository binding

WP1-B
- Research Context / Policy
- Project active context/policy pointer columns、set/clear handlers and FKs
- ContextSourceReference current projection and registry lifecycle support
- ContextCompilation and all child records
- ContextCompilationFreshness

WP1-C
- Experiment / Proposal / Requirement/Semantic/SourceSelection Manifests / Revision / decisions
- Freeze Assessment and Check records
- ProposalSourceSelectionManifest / Entry
- complete Git-tree SourceReferenceManifest / Entry / SourceSnapshot
- single-cardinality DatasetSnapshot / EnvironmentExpectation
- ExperimentVersion with embedded ExecutionConstraintManifest / BudgetConstraintManifest
- RunBatch / Trial
- Attempt foundation and active partial unique constraint
```

不要求每张表一个 Migration，也禁止把全部 WP1 表一次性塞入一个不可审查 Migration。

`default_execution_profile_id`、`default_budget_policy_id` 等尚无权威目标对象的字段不在 WP1 建立占位外键。`selected_validation_report_id` 作为冻结 Trial 契约字段在 WP1 创建 nullable UUID，并添加 `CHECK (selected_validation_report_id IS NULL)`；WP4 创建 ValidationReport 后删除该 CHECK，再添加真实 FK。

## 14.2 SQLAlchemy metadata registration

当前 Alembic 只显式 import observability models。WP1 必须建立明确、可测试的 model registration root，使 Alembic、测试和 runtime 看到完全相同的 `Base.metadata`。

禁止依赖“某个测试恰好 import 了 models”使表偶然注册。

## 14.3 Delete policy

WP1 权威历史对象默认不物理级联删除：

```text
RepositorySnapshot
Evidence
ContextCompilation
ProposalRevision
VerifierResult
DesignApproval / DesignRejection
FreezePreconditionAssessment
ExperimentVersion
RunBatch
Trial
Attempt
RuntimeEvent
```

Project、Experiment、Proposal 使用 archive/close，不以 DELETE 清除审计链。

---

# 15. RuntimeEvent 设计

## 15.1 WP1-A

```text
project.created
project.archived
repository.registration.created
repository.registration.availability_changed
repository.registration.revoked
repository.snapshot.captured
repository.snapshot.freshness_assessed
repository.inspection.completed
repository.evidence_manifest.created
```

## 15.2 WP1-B

```text
research_context.revision_created
research_context.revision_activated
project_policy.revision_created
project_policy.revision_submitted
project_policy.revision_approved
project_policy.revision_revoked
project_policy.revision_superseded
context_source_registry.synchronized
context_compilation.created
context_compilation.freshness_assessed
```

## 15.3 WP1-C

```text
experiment.created
experiment.archived
proposal.created
proposal.draft_updated
proposal.closed
proposal.revision_verification_completed
proposal.revision_approved
proposal.revision_rejected
proposal.revision_context_stale
freeze_precondition.assessed
experiment_version.created
run_batch.created
```

WP1 不默认为每个 Trial 产生 `trial.created` Event。`run_batch.created` payload 至少包含：

```text
run_batch_id
experiment_version_id
batch_identity
trial_count
trial_manifest_hash
first_ordinal
last_ordinal
```

Trial 权威事实保存在 Trial 表。未来存在逐 Trial 投影消费者时，可通过新架构版本增加有界 chunk event，不回溯把 WP1 默认行为改成 N 条 Outbox。

# 16. 测试与验收

## 16.1 通用测试层

每个子阶段至少包含：

```text
pure domain / canonicalization unit tests
repository / service transaction tests
PostgreSQL constraint integration tests
API command and error mapping tests
RuntimeEvent / Outbox atomicity tests
module dependency tests
Migration upgrade / downgrade / upgrade tests
```

SQLite 不作为 PostgreSQL 部分唯一索引、row lock、JSONB 或数据库时钟语义的替代验收环境。

## 16.2 WP1-A acceptance

至少证明：

```text
CreateProject 在无 project_id 前提下可通过 actor/installation scope 幂等重放
CommandReceipt 重放返回原始有界 Result Envelope，不受 Project 后续 archive 影响
并发相同 Idempotency-Key loser rollback 后可读取 winner Receipt
CommandReceipt canonical envelope/hash 一致性由应用不变量测试证明
CommandReceipt 不依赖 experiments Project 外键
Project 可创建
Repository 路径安全注册与 revoke Route
一个 Project 只有一个 active primary Repository
Registration 创建/撤销与 Project primary pointer 原子一致
clean / dirty Snapshot identity 正确
相同 state 捕获幂等
Dirty Snapshot 通过 Bundle.repository_snapshot_id 单向绑定 immutable Inspection Bundle，不读取后续变化的工作树
Bundle 大小/文件数超限时不创建语义不完整 Snapshot
Clean Inspection 从 frozen Git objects 读取
Dirty Inspection 从 base objects + captured overlay 读取
Freshness current/stale/unverifiable/invalid 作为唯一 current projection 更新
并发 freshness 更新受 row_version / lock 保护
required inspector registry 完整性可验证
Inspection 与 Evidence 绑定同一 Snapshot exact content view
Evidence Manifest identity 确定
Event / Outbox / Receipt 与领域写入原子
```

## 16.3 WP1-B acceptance

至少证明：

```text
ResearchContextRevision 不可变且可激活
Context 通过 experiments-owned handler 更新 Project pointer
并发激活受 row_version / lock 保护
Policy exact content hash approval
新 Policy approval supersede 旧 Revision
PolicyRoot active pointer 与 Project active pointer 同 UoW 一致
Revoke 当前 active Policy 使用 ClearActiveProjectPolicyBinding 并原子清空双指针
Revoke 非 active Policy 不改变双指针
Context service 与 experiments set/clear pointer handlers 的验证职责分离
Repository source 变化不修改已批准 Policy
ContextSourceRegistryService 根据权威对象确定性 create/update current Reference projection
客户端不能提交 source authority/provenance/freshness 结论
ContextSourceReference 唯一键和 revision set identity 稳定
Reference 更新不修改既有 CompilationSourceBinding
Context Source authority/provenance 分离
ContextCompilation 只消费 immutable inputs
相同输入产生相同 identity
不同 input/version 产生不同 identity
Fact/Precondition 子记录可查询、可追溯且 manifest hash 稳定
SourceBinding / Segment / Fact / Precondition 不可跨 Compilation 引用
ContextCompilationFreshness status set 固定且是唯一 current projection，public freshness-check route 可用
Submit/Freeze 使用命令内新鲜 evaluation，不只依赖旧 projection
WP2 Runtime visibility overlay 边界可由契约测试证明
无关 missing_fact 不阻止下游 requirement evaluation
hard dependency 缺失可被结构化识别
Compilation 创建与 children 原子
```

## 16.4 WP1-C acceptance

至少证明：

```text
Proposal draft PUT 完整替换 + optimistic concurrency
strict materialization preconditions 使用命令内 Repository/Compilation probes
入口失败不持久化正式 Manifests、不消耗 revision_number
Requirement/Semantic Binding/Source Selection Manifests 与 Revision + VerifierResult 原子创建
Revision 可从持久化 draft/verifier/lineage 字段重算 materialization 与 semantic identity
完整 verifier failure 持久化并返回 2xx committed outcome
verifier exception 全部回滚
Approval/Rejection 显式绑定 proposal_revision_identity 与 Requirement/Semantic/SourceSelection/Verifier hashes
approve/reject 并发只有一个成功
rejected/context_stale 不可恢复
同一 Proposal 多个 approved Revision 合法
Freeze 请求不接受客户端 project_id
Freeze 使用 experiments-owned command DTO 且无 experiments → context import
Freeze Check identity、checks_manifest_hash、Assessment identity/hash 可重算
passed 与 invalidate_context_binding 使用不同 experiments-owned commands
完整 failed Assessment 持久化并返回 2xx committed outcome
failed Assessment 与 execution failure 区分
preserve_approved / invalidate_context_binding 确定性
SourceReferenceManifest 覆盖完整 git ls-tree，不是 Proposal source subset
SourceReferenceManifestEntry 包含 mode/type/object identity，并检测 gitlink/LFS
Source Manifest 资源上限产生 completed failed Assessment，而非长事务或部分 Manifest
ProposalSourceSelectionManifest entrypoint/required entries 必须存在于完整 Source Manifest
SourceSnapshot 固定 git_ls_tree_v1 + git_archive 物化契约
每个 Revision 恰好一个规范化 DatasetSpecification，成功 Freeze 恰好一个 DatasetSnapshot
Dataset identity_type / reproducibility_level 与 Policy 要求正确评估
mutable_reference best_effort 可审计，strict Policy 可拒绝
无 Dataset specification 生成规范化 strict 空 DatasetSnapshot
passed Assessment + 四类冻结对象 + Version 原子创建
同一 Revision 已存在一致 Version 为正常幂等重放
冲突 Version identity 才产生 ExperimentVersionInvariantConflict
一个 Revision 最多一个 Version
Version-owned ExecutionConstraintManifest / BudgetConstraintManifest 可重算且绑定 approved Revision
CreateRunBatch 只依据 Version-owned manifests 校验 purpose、concurrency 与 budget
Batch 保存 resolved execution/budget constraint identities、summary rule 与 formal purpose
Batch 子集不能绕过 minimum_planned_replicates_per_group
Batch deterministic expansion，trial_manifest_hash 不包含 trial_id
Batch/Trials/Event/Outbox/Receipt 原子
只产生 Batch 级 materialization Event
Trial 初始五个投影字段和值精确，selected_validation_report_id 受 CHECK IS NULL 保护
Attempt active partial unique constraint
WP1 无生产 Attempt 创建命令
```

Attempt 数据库测试至少覆盖：

```text
同一 Trial 多个历史终态 Attempt
同一 Trial 不能有两个 active Attempt
不同 Trial 可各自有 active Attempt
attempt_number 在 Trial 内唯一
事务回滚后约束状态正确
```

## 16.5 WP1 end-to-end acceptance

从 API 完成：

```text
create project
→ register repository
→ capture clean snapshot
→ assess freshness current projection
→ run deterministic inspection
→ persist evidence manifest
→ create and activate research context revision
→ create and approve project policy revision
→ synchronize current ContextSourceReference projections
→ compile ContextCompilation with Fact/Precondition children
→ assess ContextCompilation freshness
→ create experiment
→ create proposal and fully replace draft
→ submit and synchronously verify immutable revision
→ approve revision
→ evaluate freeze preconditions
→ create complete Git-tree SourceReferenceManifest / Entry / SourceSnapshot
→ create single DatasetSnapshot / EnvironmentExpectation
→ materialize Version-owned ExecutionConstraintManifest / BudgetConstraintManifest
→ create immutable ExperimentVersion
→ create formal RunBatch against Version-owned concurrency/budget constraints and summary rules
→ enforce minimum planned replicates
→ deterministically expand exact Trials with fixed initial projections
```

不得依赖：

```text
LLM
Agent Runtime
Runner
Coordinator
Web UI
Attempt creation
rerun authorization
```

# 17. 子阶段实施与合并治理

## 17.1 分支策略

```text
同一 feature/wp1-domain-context 分支持续实施
WP1-A / WP1-B / WP1-C 分别验收
每个 Task 独立 commit
WP1 全部完成后创建一个最终 PR 合并到 main
```

A/B/C 不是三个独立发布功能，不提前把半成品领域结构合并到 main。

## 17.2 阶段记录

每个阶段形成：

```text
Task implementation commits
execution evidence commit
review / approval commit
```

最终再形成：

```text
WP1 end-to-end evidence
WP1 final approval
one final PR
```

## 17.3 Accepted baseline 回改

阶段 approval commit 建立 accepted baseline。后续阶段需要修改已验收阶段时，必须新增：

```text
契约缺口说明
影响分析
修复 Task commit
受影响测试结果
阶段 re-approval record
```

不得静默 amend、覆盖旧 evidence，或把旧 approval 文档改写成问题从未存在。

## 17.4 分支同步

在以下时点检查 main：

```text
WP1-A 开始前
WP1-B 开始前
WP1-C 开始前
最终 PR 前
```

规则：

```text
未 push 且无人依赖
→ 可以 rebase

已 push 但仅本人使用
→ 可以 rebase + --force-with-lease
→ 记录旧 tip 并重跑当前阶段验收

已有协作者拉取、审查或继续开发
→ 默认 merge main，不重写历史
→ 如必须 rebase，先取得明确同意
```

## 17.5 最终 PR 记录

最终 PR 明确列出：

```text
WP1 base commit / tree
WP1-A accepted commit / tree
WP1-B accepted commit / tree
WP1-C accepted commit / tree
WP1 final tested commit / tree
migration chain
identity and constraint summary
test commands and results
dependency verification
deferred WP2 / WP3 / P1 scope
```

---

# 18. Planning Resolution Index

| Resolution | Adopted rule |
|---|---|
| Trial rerun | WP1 不实现授权对象或 eligible-Trial rerun；P1 再引入 |
| Contract mapping | Frozen logical/runtime contracts use explicit WP1 implementation-profile mappings without weakening invariants |
| Runtime Context | WP1 `ContextCompilation`，WP2 AgentRun-bound `ContextSnapshot` |
| Runtime rendering | WP2 引用整个 Compilation，但使用 run visibility overlay 选择 Segment 并生成新 render/hash |
| Freeze ownership | ContextFreezeOrchestrator 生成 Assessment，同 UoW 调用 experiments-owned command handler |
| Cross-module DTO | Experiments 不接收 Context entity；passed/invalidation 分别使用两个 experiments-owned command |
| Project pointer writes | Context 通过 experiments-owned set/clear handlers 更新 primary Repository、active Research Context 与 active Policy pointers；active Policy revoke 原子清空双指针 |
| Failed Assessment | 完整领域失败持久化并返回 2xx；执行失败回滚 |
| Failure effects | 版本化 `preserve_approved / invalidate_context_binding / command_error` |
| Stale recovery | `context_stale` 不可逆，必须新 Revision |
| Revision identity | Materialization 纳入 Requirement/Semantic/SourceSelection Manifests 与 verifier registry；semantic identity 纳入 lineage identity |
| Binding time | Revision 创建时绑定完整 semantic input set |
| Proposal editing | 可变 draft + 不可变 ProposalRevision；PUT 为完整替换 |
| Revision manifests | Requirement、Semantic Binding 与 Source Selection Manifests 只在 Revision materialization 成功时正式持久化 |
| Verification | 有界 deterministic verifier 与 Revision 同公开命令、同 UoW |
| Approval waiting | `verified` 直接等待决定，不持久化 `awaiting_approval` |
| Human decisions | 独立 `DesignApproval` 与 `DesignRejection`；DesignDecisionBinding 包含 proposal_revision_identity，无持久 generic DesignDecision |
| Multiple approvals | 同一 Proposal 可有多个 approved-unfrozen Revisions |
| Strict entry | 只要求当前 Proposal Requirement Manifest 的 hard prerequisites 完整 |
| Freeze selection | Freeze 不选择 latest/current，只验证已绑定输入 |
| Idempotency | 领域无关 scope + request hash + bounded canonical Result Envelope；unique race uses rollback + winner reload |
| Freshness | Snapshot 与 Compilation 各自一条 current mutable projection；历史由 Event/Assessment 保存 |
| Facts/preconditions | 结构化 child records + stable manifest hashes |
| Context Source Registry | ContextSourceReference 是内部 current projection；Registry Service 同步权威来源，Compilation Binding 保存不可变观察 |
| Dirty inspection | Snapshot 不反向保存 Bundle FK；dirty Snapshot 通过唯一 Bundle.repository_snapshot_id 单向拥有精确 content-addressed view |
| Freeze input objects | 四类对象为 Revision-owned；Source Manifest is complete Git tree，Proposal selection uses separate Manifest |
| Dataset cardinality | 每个 Revision 恰好一个规范化 DatasetSpecification；成功 Freeze/Version 恰好一个 DatasetSnapshot |
| Dataset reproducibility | Preserve identity_type and strict/strong/best_effort policy semantics |
| Version constraints | ExperimentVersion 内嵌不可变 ExecutionConstraintManifest / BudgetConstraintManifest；Batch 不读取 Project 当前 Policy |
| Batch boundary | CreateRunBatch 只创建 Batch 和 Trial；formal plan includes resolved Version constraints、summary/minimum replicates；Manifest 排除 trial_id |
| Trial projection | 固定初始投影；WP1 以 CHECK IS NULL 保护未来 ValidationReport 引用 |
| Attempt boundary | WP1 只建持久化和约束，无 Attempt 创建业务命令 |
| Merge strategy | A/B/C 独立验收，同一分支，一个最终 PR |

# 19. Traceability Matrix

| WP1 requirement | 设计落点 | 退出证据 |
|---|---|---|
| Generic command idempotency | 3.3 | scope isolation、Result Envelope replay、mutable-object drift tests |
| Project / Repository | 第 4 章 | primary pointer atomicity、exact clean/dirty content view、API、DB、Event tests |
| clean Snapshot | 4.3、6.5 | dirty 拒绝 + clean happy path |
| Repository freshness projection | 4.4 | unique projection、row version、Event tests |
| deterministic Inspection | 4.5–4.7 | inspector registry 与 Evidence tests |
| Research Context | 5.1 | immutable revision / activation tests |
| Approved Policy | 5.2 | exact hash approval、supersede、active revoke clear 双指针 tests |
| Context Source Registry | 5.3 | internal sync ownership、authority/provenance、unique projection、revision-set identity tests |
| Context Compiler | 5.4–5.8 | determinism / lineage / freshness tests |
| WP1 typed facts | 5.5–5.6 | child record、manifest、cross-binding tests |
| Proposal Revision | 6.2–6.7 | Requirement-aware identities + strict materialization + atomic verifier tests |
| Approval / Rejection | 6.8–6.10 | lock race、proposal_revision_identity binding、immutable decision tests |
| Freeze | 第 7 章 | passed/preserve/invalidate/error command-path tests |
| Source/Dataset/Environment freeze inputs | 7.8–7.12 | full Git tree、selection validation、single Dataset cardinality、reproducibility、no-dataset、atomicity tests |
| Experiment Version | 第 8 章 | one Revision → one Version、execution/budget constraint manifests tests |
| Batch / Trial | 第 9 章 | Version-owned constraint enforcement、minimum replicates、ID-independent manifest、Batch Event、CHECK-protected projections |
| Attempt active constraint | 第 10 章 | PostgreSQL partial unique tests |
| API committed outcomes | 第 11–12 章 | 2xx failed result + Receipt replay tests |
| RuntimeEvent / Outbox | 3.4、15 | transaction rollback / commit tests |
| A/B/C independent acceptance | 第 16–17 章 | evidence and approval commits |

# 20. 非目标与禁止实现

WP1 明确禁止：

```text
用 LLM 完成 Repository Inspector、Context Compiler 或 Verifier
让 RepositorySnapshot 保存 Inspection Bundle 反向 FK 或在创建后回写 Bundle identity
让 dirty Snapshot Inspector 重新读取 mutable working tree
用 Proposal source selection 子集替代完整 Git tree SourceReferenceManifest
在数据库事务中运行长时间 Git scan、Dataset scan、网络或外部进程
让 Router 自行判断 Freeze
experiments import context
Context 直接修改 experiments ORM Row
Freeze internal handler 接收 Context entity 类型
创建假的 AgentRun / Tool Manifest / runtime ContextSnapshot
把 ContextCompilation compiled output 直接视为所有 Run 的最终 Prompt
允许 nullable/deferred Proposal semantic bindings
在 Freeze 时替换 Revision 绑定为 latest/current 输入
允许客户端提交或覆盖 ContextSourceReference authority/provenance/freshness
将 typed facts/preconditions 只保存为不可查询的大 JSON blob
让 SourceBinding/Segment/Fact/Precondition 跨 Compilation 引用
把随机数据库 ID 放入可复现的 semantic manifest
为一个 ProposalRevision 创建多个顶层 DatasetSnapshot
丢失 DatasetSnapshot strict/strong/best_effort 复现等级
CreateRunBatch 读取 Project 当前 Policy、环境变量或临时默认值推导 concurrency/budget 限制
允许 formal Batch 子集绕过 minimum planned replicate 要求
让 failed Freeze 的 Context 代码直接更新 ProposalRevision ORM Row
通过重新读取已变化的对象来重建 CommandReceipt 原始结果
在 Receipt unique violation 后继续使用 failed transaction 查询 winner
使用“snapshot or manifest”一类未收敛冻结对象语义
用 generic PATCH 修改核心状态
让 failed domain result 掩盖执行异常或被错误映射为未提交 HTTP 异常
批准新 Revision 时自动撤销旧 Revision
CreateRunBatch 自动创建 Attempt
默认为每个 Trial 产生一条 RuntimeEvent/Outbox
提前实现 Coordinator / Runner / Retry / Reconciliation
提前实现 TrialRerunAuthorization
为尚不存在的权威对象建立无依据占位外键
静默修改已验收阶段
```

# 21. 已知基线治理事项

以下事项在设计盘点中发现，但不属于 WP1 领域功能：

1. 当前根 `FINAL-DOCUMENTS.sha256` 仍绑定旧版 `scripts/verify_architecture_dependencies.sh` hash，而 WP0 phase-specific dependency manifest 可通过。不得借 WP1 实现 Task 静默修改 WP0 evidence；如修复根 Manifest/README，应建立独立文档治理 Task 和审查记录。
2. 本次交接快照仍包含 `__pycache__` / `.pyc`。后续阶段 evidence package 和最终 PR 交付包必须排除构建缓存，但这不影响源码和设计基线。
3. 上传快照不包含 `.git`，因此 amend 后 commit hash、branch tip 和 clean working tree 必须由本地 Git 输出单独证明。

---

# 22. 完成定义

本文 v0.5 已通过最终一致性复审，状态为 `Approved Design Input`。本批准仅授权编写 WP1-A detailed implementation plan，不授权在计划审查通过前开始编码。

v0.5 最终一致性复审已经通过。下一步只生成：

```text
WP1-A detailed implementation plan
```

后续顺序固定为：

```text
WP1-A plan approved
→ WP1-A implementation and acceptance
→ 基于已验收 A 接口编写 WP1-B plan
→ WP1-B implementation and acceptance
→ 基于已验收 A/B 接口编写 WP1-C plan
```

计划必须：

1. 读取真实 WP0 与前置阶段已验收接口；
2. 按本文对象、命令、事务和验收边界拆 Task；
3. 每个 Task 定义文件、测试、命令和独立 commit；
4. 不允许后续计划提前猜测尚未验收的共享表结构、CommandReceipt 或 Project pointer 契约；
5. 不跨阶段静默修改 accepted contract；
6. 在对应计划审查通过前不进入代码实现。

WP1 完成的最终判定是：A/B/C 各自验收通过，端到端 API happy path 通过，PostgreSQL 约束、Migration、RuntimeEvent/Outbox、CommandReceipt、依赖规则和全部回归测试通过，并由一个最终 PR 从已验收 WP0 main 升级到完整 WP1。
