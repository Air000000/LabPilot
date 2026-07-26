# LabPilot Tools、Policy 与 Workspace 架构规格

```text
Document: 05-tools-policy-and-workspace.md
Version: v0.2
Status: Approved / Frozen
Date: 2026-07-24
Owner: LabPilot Architecture
```

---

## 0. 文档定位

### 0.1 目的

本文冻结并回答以下问题：

1. LabPilot 中什么才算一个可执行 Tool；
2. Tool Definition、Tool Manifest、ToolCall 与真实副作用之间如何分层；
3. Agent 能看到哪些 Tool，哪些 Tool 能执行，哪些必须审批；
4. 红、黄、绿风险如何由确定性策略计算，而不是由模型自报；
5. Tool 参数如何校验、规范化、哈希并绑定 Approval；
6. Workspace 如何限制读取、写入、命令、环境变量、凭据与网络范围；
7. Tool 在崩溃、超时、取消或结果未知时如何保持事实诚实；
8. 受限 Smoke Test 与正式 Experiment Attempt 如何严格分离；
9. 哪些边界属于本文，哪些必须留给 `06-workflow-and-executors.md`；
10. 哪些行为属于 V1 P0，哪些明确后移。

本文定义的是 **Tool、Policy、Approval 与 Workspace 的稳定架构契约**，不是具体 Python 类、数据库迁移或命令执行实现。

### 0.2 上游冻结契约

本文必须服从以下已经冻结的文档：

| 文档 | 版本 | SHA-256 |
|---|---:|---|
| `00-master-blueprint-v0.2.md` | v0.2 | `16f2c39bda46881bb2ea13975db15d914a3a420b04b9dd21d45ebe315674a6fa` |
| `01-v1-scope-and-acceptance-v0.2-frozen.md` | v0.2 Frozen | `7092a034a78e6ea8b9f13533521fbaa6989ad15a0a25686057a75f286e33dbd6` |
| `02-experiment-domain-v0.2-frozen.md` | v0.2 Frozen | `2c998e7814a116c0999b21b6cf078dacb0fb6f770c929062480dc8ee98df328a` |
| `03-agent-harness-v0.2-frozen.md` | v0.2 Frozen | `fcf1996547f73bed1f07e91b34e7c91af871a7641076d43a3ec2d33482552231` |
| `04-context-and-repository-v0.2-frozen.md` | v0.2 Frozen | `14c8746cfc68d7f94aeb178573d14b06daefa8b7f117a2cffa2f7368aa1ca4a2` |

冲突优先级：

```text
01 V1 Scope
> 02 Experiment Domain
> 03 Agent Harness
> 04 Context and Repository
> 05 Tools, Policy and Workspace
> later implementation documents
```

### 0.3 本文不改写的上游语义

本文不得改写：

- Agent Run、Agent Step、ModelCall 与 AgentDecision 生命周期；
- ToolCall 的 `execution_status` 与 `resolution_status`；
- Tool Approval 的数据库事务时间和 Wakeup 竞争规则；
- Experiment Design Approval；
- Proposal、Version、Batch、Trial、Attempt 与 Validation；
- 正式 Source Materialization 的 `git ls-tree` / `git archive` 契约；
- Context Snapshot、Tool Manifest 原子性和 Project Policy 权限模型；
- 正式训练只能由 Approved Experiment Version 进入 Coordinator / Executor。

### 0.4 下游文档

本文将被以下规格消费：

```text
06-workflow-and-executors.md
07-results-validation-and-agentops.md
implementation plans
```

`06` 可以定义长期任务提交、Runner、Attempt Workspace 生命周期和 Reconciliation 轮询，但不得重新定义本文冻结的 Tool Contract、Policy Decision、Approval Identity、Workspace Path Boundary 与 Side-effect 诚实性。

---

# 1. 核心结论

LabPilot V1 采用以下方案：

```text
Versioned Tool Contract
+ Deterministic Tool Registry
+ Atomic Tool Manifest
+ Static Red/Yellow/Green Policy
+ One-time Tool Execution Approval
+ Immutable Tool Execution Plan
+ Controlled Tool Workspace
+ Bounded Tool Execution
+ Explicit Unknown/Reconciliation
```

P0 不建设：

- 通用 Policy DSL；
- 任意 Shell Tool；
- 任意 Python Eval Tool；
- 常驻插件市场；
- 多租户 Capability Delegation；
- Kubernetes Sandbox；
- 对不可信仓库代码的强隔离执行；
- 通过 Tool 启动正式训练；
- 自动联网安装依赖；
- Tool 自主访问 Secret；
- Tool 对主仓库的默认写权限。

---

# 2. 设计原则

## 2.1 Tool 暴露不等于 Tool 授权

```text
Tool registered
≠ Tool available for this Project
≠ Tool exposed in this Context
≠ Tool allowed by Policy
≠ Tool approved by Human
≠ Tool executed successfully
```

五个阶段必须分别记录。

## 2.2 模型不决定风险

模型可以请求 Tool，但不能决定：

- Tool 是否存在；
- Tool 版本；
- Tool 风险等级；
- Tool 是否需要审批；
- Tool 可写路径；
- Tool 可读取 Secret；
- Tool 是否可以联网；
- Tool 是否可以作为正式实验执行。

这些由 Registry、Policy、Execution Profile 和 Workspace Contract 确定。

## 2.3 能由 Schema 和程序检查的内容不交给 LLM

以下均由确定性逻辑处理：

- Tool name/version 解析；
- 参数类型；
- enum；
- 数值范围；
- 路径边界；
- Command argv 构造；
- 环境变量白名单；
- Capability；
- Risk；
- Approval Identity；
- Timeout；
- Result Envelope；
- Output 大小；
- Side-effect 变更清单。

## 2.4 Approval 授权一次具体执行，不授权 Tool 类别

Tool Execution Approval 绑定不可变执行身份。

```text
批准 overwrite_artifact
≠ 批准以后所有 Artifact 覆盖
```

参数、目标路径、Policy、Tool Version、Workspace、Precondition 或执行计划变化后，旧 Approval 不得复用。

## 2.5 Workspace 隔离不等于安全 Sandbox

P0 Workspace 解决：

- 路径和输出隔离；
- Source Materialization；
- 命令与配置快照；
- 可写目录控制；
- Trace；
- 临时输出清理。

P0 不宣称能够安全运行任意恶意代码。执行 Repository 代码前必须满足 Project 的本地执行信任要求。更强 OS Sandbox 属于后续能力。

## 2.6 正式训练不是普通 Tool

```text
Agent Tool Call
≠ Experiment Attempt Launch
```

Tool 可以执行 30–60 秒受限检查或 Smoke Test，但不能：

- 创建 Trial；
- 创建 Attempt；
- 写入正式 Metric；
- 进入 Batch Summary；
- 使用 Tool Approval 代替 Design Approval；
- 通过 `shell_exec` 绕过 Coordinator。

## 2.7 默认最小权限

P0 默认：

```text
Repository read-only
Workspace scoped write
External network denied
Secret values unavailable to model
Shell disabled
Formal Artifact delete/overwrite denied or Red
Main repository write denied or Red
```

## 2.8 外部事实必须诚实

Tool 调用发生超时、进程中断或副作用无法确认时，不得猜测成功或失败。

```text
unknown + open
```

表示仍需 Inspect 或人工处置。

```text
unknown + abandoned
```

表示停止调查，但外部事实仍未知。

---

# 3. 架构边界

## 3.1 组件

```text
Experiment Planning Agent Runtime
        │ AgentDecision(CALL_TOOL)
        ▼
Tool Gateway
        │
        ├─ Tool Registry
        ├─ Argument Validator / Canonicalizer
        ├─ Capability Resolver
        ├─ Policy Evaluator
        ├─ Approval Service
        ├─ Workspace Service
        ├─ Tool Execution Planner
        ├─ Tool Executor Adapter
        └─ Result Validator / Redactor
```

## 3.2 Tool Gateway

Tool Gateway 是 Agent Runtime 调用 Tool 的唯一入口，负责：

1. 解析 Tool identity；
2. 检查 Tool 是否属于当前 exposed manifest；
3. 校验输入 Schema；
4. 规范化参数；
5. 解析路径、Workspace 和 Capability；
6. 构建不可变 Execution Plan；
7. 计算 Risk；
8. 评估 Policy；
9. 创建或消费 Approval；
10. 调用 Tool Adapter；
11. 校验 Result Envelope；
12. 保存 Observation 与 Audit。

任何模块不得绕过 Tool Gateway 直接执行 Agent 请求的副作用。

## 3.3 Tool Registry

Tool Registry 保存版本化 Tool Definition，不执行 Tool。

## 3.4 Policy Evaluator

Policy Evaluator 只产生确定性：

```text
allow
require_approval
deny
```

它不调用 LLM。

## 3.5 Workspace Service

Workspace Service：

- 创建受控 Tool Workspace；
- 物化只读 Source；
- 生成 Workspace Manifest；
- 解析 Path Binding；
- 检查读写范围；
- Seal 与清理临时 Workspace。

它不创建 Experiment Trial 或 Attempt。

## 3.6 Tool Executor Adapter

每个 Tool Adapter 将类型化参数转成确定性执行动作。P0 Adapter 不接受模型提供的任意 Shell 字符串。

## 3.7 Tool 执行位置

P0 对执行位置做明确区分：

- 纯 metadata/read-only Tool 可以由受控 Application Worker 执行；
- 任何会 import、启动 Python、运行 Repository entrypoint 或创建子进程的 Yellow Tool，必须交给宿主机上的 Local Runner Tool Lane；
- API 与 Experiment Coordinator 容器不得直接 fork Repository 代码；
- Tool Lane 使用 `ToolExecutionPlan`，不创建 Trial/Attempt，也不进入正式 Executor 状态机；
- Tool Lane 崩溃或响应丢失时，按 Tool unknown/reconciliation 处理。

具体队列、领取与进程封装可以由 `06` 的 Runner 实现复用，但不得把 Tool Execution Record 与 AttemptLaunch 混为一体。

## 3.8 Experiment Coordinator 与 Executor

正式 Experiment Attempt 的：

- prepare；
- submit；
- inspect；
- cancel；
- collect；
- reconcile；

由 `06` 定义。Tool Gateway 不能伪装成正式 Executor。

---

# 4. 核心对象

## 4.1 ToolDefinition

`ToolDefinition` 是完全不可变、版本化的 Tool 语义定义：

```text
ToolDefinition
- tool_definition_id
- tool_name
- tool_version
- display_name
- description
- adapter_type
- input_schema_ref
- input_schema_hash
- output_schema_ref
- output_schema_hash
- argument_schema_version
- argument_canonicalization_version
- result_schema_version
- default_risk_level
- default_approval_type
- required_capabilities
- execution_profile_id
- idempotency_class
- retry_semantics
- cancellation_semantics
- inspection_semantics
- timeout_policy
- output_limit_policy
- readable_scope_kinds
- writable_scope_kinds
- credential_scope_kinds
- network_requirement
- side_effect_class
- audit_metadata_schema_ref
- audit_metadata_schema_hash
- definition_identity
- created_at
```

一个已发布 ToolDefinition 不得原地修改。Schema、风险、Capability、执行 Profile、Side-effect、Retry、Cancel 或 Inspect 语义变化必须创建新的 `tool_version`。

`ToolDefinition` 不保存 `active/disabled/deprecated/removed`。可用性属于独立投影。

## 4.2 ToolDefinitionIdentity

```text
tool_definition_identity = SHA-256(
  "labpilot-tool-definition:v1\n"
  + canonical_json(tool_definition_identity_payload)
)
```

Identity payload 至少包含：

- tool name/version；
- input/output schema hash；
- canonicalization version；
- default risk 与 default approval type；
- required capabilities；
- execution profile identity；
- idempotency/cancel/inspect 语义；
- read/write/credential/network 范围；
- side-effect class。

不包含显示名称、文档排版、创建时间、数据库 ID、Availability 和 UI 标签。

## 4.3 ToolDefinitionAvailability

Tool 可用性是可变投影：

```text
ToolDefinitionAvailability
- tool_definition_id
- status
- availability_generation
- reason
- changed_by
- changed_at
- row_version
```

```text
status:
- active
- deprecated
- disabled
- removed
```

规则：

- `active`：可进入新的 Available Tool Manifest；
- `deprecated`：新 Context 默认不暴露；历史调用可按冻结 Definition 恢复；
- `disabled`：未开始的调用、pending Approval 和未消费 Intent 失效；
- `removed`：只保留审计，不允许新执行；
- Availability 变化不修改历史 ToolDefinition；
- `availability_generation` 每次变化单调递增。

## 4.4 ToolManifest

Tool Manifest 是某一时刻可用于 Project/Runtime 的不可变 ToolDefinition 集合。

```text
ToolManifest
- tool_manifest_id
- manifest_version
- tool_entries
- system_policy_identity
- project_policy_revision_id nullable
- project_policy_constraint_set_hash nullable
- manifest_hash
- created_at
```

每个 `tool_entries` 条目至少绑定：

```text
tool_definition_identity
availability_generation
availability_status
```

条目按 `(tool_name UTF-8, semantic version)` 稳定排序。

## 4.5 ToolManifestFreshness

Manifest 自身不可变，当前适用性由独立投影表达：

```text
ToolManifestFreshness
- tool_manifest_id
- status
- current_applicable_manifest_hash
- reason
- checked_at
- row_version
```

```text
status:
- current
- stale
- invalid
```

Availability、Deployment Capability、Project Constraint Set 或 System Policy 变化后，旧 Manifest 可以变为 `stale`，但不得原地重写。

## 4.6 ExposedToolManifest

某个 Context Snapshot 实际向模型暴露的原子 Tool Schema 集合：

```text
ExposedToolManifest
- exposed_tool_manifest_id
- available_tool_manifest_id
- context_snapshot_id
- task_profile_id
- exposed_tool_definition_identities
- excluded_tool_definition_identities
- exclusion_reasons
- schema_serialization_version
- exposed_manifest_hash
- created_at
```

单个 Tool Schema 不得截断。

## 4.7 CapabilityDefinition

```text
CapabilityDefinition
- capability_name
- capability_version
- description
- minimum_risk_level
- system_availability
```

P0 建议 Capability：

```text
repository.read
repository.git_metadata_read
repository.execute_bounded
workspace.create
workspace.read
workspace.write
workspace.execute_bounded
artifact.metadata_read
artifact.content_read
artifact.create_temporary
artifact.overwrite
artifact.delete
repository.write
network.external
credential.read_reference
credential.ssh_use
remote.execute
safe_open_beneath
mount_boundary_enforcement
network_namespace_enforcement
readonly_mount_enforcement
```

## 4.8 ExecutionProfile

```text
ExecutionProfile
- execution_profile_id
- profile_name
- profile_version
- allowed_read_root_kinds
- allowed_write_root_kinds
- process_execution_allowed
- shell_allowed
- process_isolation
- filesystem_enforcement
- network_enforcement
- declared_network_mode
- max_wall_seconds
- max_stdout_bytes
- max_stderr_bytes
- max_result_bytes
- environment_allowlist
- environment_denylist
- credential_scope_kinds
- resource_limits
- required_runtime_capabilities
- residual_risk_statement
- profile_identity
```

枚举：

```text
process_isolation:
- not_applicable
- trusted_host_process
- constrained_namespace

filesystem_enforcement:
- adapter_enforced
- best_effort
- readonly_mount
- namespace_enforced

network_enforcement:
- not_applicable
- unavailable
- namespace_enforced
```

P0 Profile：

```text
repository_readonly
workspace_file_write
trusted_host_bounded_process
```

部署具备真实 namespace/mount 能力时，可以额外启用：

```text
namespace_bounded_process
```

`formal_attempt_process` 只作为 `06` 消费的 Workspace/Execution 边界，不作为 Agent Tool Profile。

Profile 只有在底层能力真实存在时，才能声明 `network-none`、`readonly-mount` 或 `workspace-only-write` 已被 OS 强制保证。

## 4.9 ProjectPolicyConstraintSet

人类可读 Project Policy 不能直接由确定性 Policy Evaluator 解释。每个已批准 Policy Revision 必须有经过 Schema 校验的结构化约束：

```text
ProjectPolicyConstraintSet
- project_policy_constraint_set_id
- project_policy_revision_id
- policy_schema_version
- normalized_constraints
- normalized_constraints_hash
- validation_status
- validation_findings
- created_at
```

P0 固定 Schema 至少包含：

```text
repository_write_policy
allowed_execution_profiles
network_policy
artifact_overwrite_policy
artifact_delete_policy
credential_policy
budget_thresholds
path_scope_constraints
required_artifacts
```

自由文本可以进入 Context，但只有 `validation_status = valid` 的结构化 Constraint Set 才能进入 Policy Evaluator。

P0 不建设通用 Policy DSL，也不在执行时调用 LLM 解释自由文本。

## 4.10 RepositoryExecutionTrustGrant

允许在本机运行某个 Repository 的代码，是独立的显式授权，不由 Policy 文本隐式产生：

```text
RepositoryExecutionTrustGrant
- trust_grant_id
- project_id
- repository_registration_id
- trust_mode
- allowed_execution_profile_identities
- max_wall_seconds
- granted_by
- granted_at
- expires_at nullable
- revoked_at nullable
- governing_policy_revision_id
- grant_generation
```

P0：

```text
trust_mode = trusted_host_bounded_execution
```

Trust Grant 绑定 Repository Registration；每次执行还必须绑定具体 `repository_snapshot_id`、`repository_state_digest` 和 `execution_profile_identity`，并重查 Freshness。

Project Policy 可以进一步限制或撤销执行，但不能通过自然语言自动创建 Trust Grant。

## 4.11 ToolExecutionPlan

Policy 与 Approval 执行的真正对象是不可变 `ToolExecutionPlan`。

```text
ToolExecutionPlan
- tool_execution_plan_id
- tool_call_id
- tool_definition_identity
- tool_availability_generation
- available_tool_manifest_hash
- exposed_tool_manifest_hash
- policy_manifest_hash
- project_policy_constraint_set_hash nullable
- repository_execution_trust_generation nullable
- canonical_arguments
- arguments_hash
- workspace_allocation_spec nullable
- workspace_layout_version nullable
- workspace_root_registration_identity nullable
- execution_profile_identity
- repository_execution_trust_grant_id nullable
- resolved_read_bindings
- resolved_write_bindings
- resolved_argv nullable
- resolved_workdir nullable
- environment_name_bindings
- credential_reference_ids
- declared_network_mode
- timeout_seconds
- resource_limits
- result_schema_hash
- idempotency_key
- precondition_snapshot
- precondition_hash
- execution_plan_hash
- created_at
```

约束：

```text
UNIQUE(tool_call_id)
INDEX(execution_plan_hash)
```

不同 ToolCall 可以拥有相同语义 `execution_plan_hash`。Plan 一旦创建不可更新；任何语义变化必须创建新 ToolCall。

## 4.12 ToolPolicyDecision

```text
ToolPolicyDecision
- policy_decision_id
- tool_call_id
- tool_execution_plan_id
- decision
- computed_risk_level
- matched_rule_ids
- reason_codes
- required_approval_type nullable
- system_policy_identity
- project_policy_revision_id nullable
- project_policy_constraint_set_hash nullable
- repository_execution_trust_grant_id nullable
- policy_manifest_hash
- precondition_hash
- created_at
```

## 4.13 PolicyManifest

```text
PolicyManifest
- policy_manifest_id
- system_policy_identity
- static_policy_version
- active_project_policy_revision_id nullable
- project_policy_constraint_set_hash nullable
- repository_execution_trust_generation nullable
- tool_manifest_hash
- execution_profile_identities
- capability_manifest_hash
- policy_manifest_hash
- created_at
```

## 4.14 PolicyManifestFreshness

```text
PolicyManifestFreshness
- policy_manifest_id
- status
- current_applicable_policy_manifest_hash
- reason
- checked_at
- row_version
```

历史 Policy Manifest 不可变。Project Policy、Constraint Set、Trust Grant、Tool Availability 或 Runtime Capability 变化后，旧 Manifest 变为 `stale`。

## 4.15 ToolApprovalRequest

```text
ToolApprovalRequest
- approval_request_id
- tool_call_id
- agent_run_id
- project_id
- tool_definition_identity
- tool_availability_generation
- execution_plan_hash
- arguments_hash
- precondition_hash
- policy_manifest_hash
- risk_level
- human_summary
- requested_at
- expires_at
- decision_status
- consumption_status
- decided_at nullable
- decided_by nullable
- decision_reason nullable
- consumed_at nullable
- consumed_by_execution_intent_id nullable
```

`decision_status`：

```text
pending
approved
rejected
expired
cancelled
```

`consumption_status`：

```text
not_available
available
consumed
invalidated
```

## 4.16 ToolExecutionIntent

每个 ToolCall 最多创建一个逻辑执行意图：

```text
ToolExecutionIntent
- tool_execution_intent_id
- tool_call_id
- tool_execution_plan_id
- idempotency_key
- approval_request_id nullable
- tool_workspace_id nullable
- intent_status
- created_at
- closed_at nullable
- close_reason nullable
```

`intent_status`：

```text
ready
executing
reconciling
succeeded
failed
cancelled
not_executed
unknown
```

约束：

```text
UNIQUE(tool_call_id)
```

Approval consumption 绑定 Intent，而不是某一次物理 Adapter 调用。

## 4.17 ToolExecutionAttempt

每一次真实 Adapter 调用都有独立记录：

```text
ToolExecutionAttempt
- tool_execution_attempt_id
- tool_execution_intent_id
- attempt_number
- adapter_execution_id
- status
- side_effect_state
- started_at
- ended_at nullable
- exit_code nullable
- timeout_observed
- cancellation_observed
- stdout_quarantine_ref nullable
- stderr_quarantine_ref nullable
- raw_result_quarantine_ref nullable
- redacted_stdout_ref nullable
- redacted_stderr_ref nullable
- result_envelope_ref nullable
- changed_path_manifest_ref nullable
- error_code nullable
```

`status`：

```text
created
starting
running
succeeded
failed
cancelled
unknown
```

约束：

```text
UNIQUE(tool_execution_intent_id, attempt_number)
```

同一 Intent 同一时刻最多一个 `created/starting/running` Attempt；数据库实现使用部分唯一索引兜底。

`side_effect_state`：

```text
not_applicable
not_crossed
crossed
unknown
```

ToolCall 的权威状态仍由 Agent Harness 持有；Intent/Attempt 保存 Tool 层逻辑授权和物理执行事实。

## 4.18 ToolResultEnvelope

```text
ToolResultEnvelope
- tool_call_id
- tool_execution_intent_id
- selected_tool_execution_attempt_id
- tool_definition_identity
- result_schema_version
- status
- summary
- structured_result
- stdout_ref nullable
- stderr_ref nullable
- changed_paths
- created_paths
- deleted_paths
- observed_external_ids
- warnings
- output_truncation
- duration_ms
- resource_usage
- result_hash
```

只有通过 Output Schema 校验、Secret Scan 和 Redaction 的 Result 才能成为 Agent Observation。

## 4.19 ToolWorkspace

```text
ToolWorkspace
- tool_workspace_id
- project_id
- owner_tool_execution_intent_id
- purpose
- workspace_root
- root_registration_id
- source_materialization_type
- repository_snapshot_id nullable
- repository_state_digest nullable
- source_manifest_digest nullable
- execution_profile_identity
- workspace_manifest_hash
- lifecycle_status
- evidence_retention_status
- created_at
- sealed_at nullable
- cleanup_at nullable
```

`purpose`：

```text
repository_inspection
bounded_smoke_test
temporary_artifact_generation
```

`evidence_retention_status`：

```text
none
hold
released
```

P0 ToolWorkspace 不使用 `formal_attempt` purpose。正式 Attempt Workspace 由 `06` 创建，但必须遵守本文的目录与 Path Boundary Contract。

# 5. Tool Registry

## 5.1 注册来源

P0 ToolDefinition 来自部署内受控代码和迁移，不接受模型、Repository 文本或用户自由 JSON 动态注册。

注册时必须验证：

- Tool name/version；
- input/output schema；
- default risk；
- approval type；
- Capability；
- Execution Profile；
- Adapter 存在；
- idempotency/cancel/inspect 语义；
- side-effect boundary；
- audit metadata schema。

## 5.2 版本规则

以下变化必须提升 Tool Version：

- input/output schema；
- 参数 canonicalization；
- side-effect class；
- default risk；
- Capability；
- path scope；
- credential scope；
- network requirement；
- idempotency；
- cancel/inspect 语义；
- Adapter 的业务语义。

仅文案、示例或 UI 标签变化可以不提升语义版本，但不得改变 Definition Identity。

## 5.3 Availability

Availability 只能通过独立投影变化：

```text
PublishToolDefinition
SetToolDefinitionAvailability
DeprecateToolDefinition
DisableToolDefinition
RemoveToolDefinition
```

规则：

- ToolDefinition 本身不更新；
- `availability_generation` 单调递增；
- `disabled/removed` 后不可创建新 ToolCall；
- pending Approval、未消费 Execution Intent 和未开始 Attempt 必须失效或重评；
- 已发生或可能发生的副作用继续按原 Definition、Plan 和 Intent 对账。

## 5.4 Tool Manifest 生成

Available Tool Manifest 由以下确定性输入生成：

```text
System Tool Registry
+ ToolDefinitionAvailability generation
+ Deployment capabilities
+ Project capabilities
+ Active ProjectPolicyConstraintSet
+ Active RepositoryExecutionTrustGrant
+ Runtime platform capabilities
```

Exposed Tool Manifest 再根据：

```text
Agent task profile
+ Context token budget
+ current Run purpose
+ deterministic exposure rules
```

选择原子 Tool Schema。

Available/Exposed Manifest 都是历史快照；当前适用性由 `ToolManifestFreshness` 表达。

## 5.5 未暴露 Tool

模型请求未暴露 Tool 时：

```text
ToolNotExposed
```

不得仅因为 Tool 在 Registry 中存在就执行。

# 6. Tool 参数校验与身份

## 6.1 处理顺序

```text
Raw model arguments
→ JSON parse
→ Input Schema validation
→ Semantic validation
→ Argument canonicalization
→ Path and reference binding
→ Execution Plan resolution
→ arguments_hash / precondition_hash / plan_hash
```

任何阶段失败都不执行 Tool。

## 6.2 Canonical JSON V1

Tool 参数复用上游 Canonical JSON 基础规则，并使用独立命名空间：

```text
arguments_hash = SHA-256(
  "labpilot-tool-arguments:v1\n"
  + canonical_json(canonical_arguments)
)
```

规则：

- object key 按 UTF-8 排序；
- list 保持语义顺序；
- set-like 字段由 Schema 明确声明并在排序后编码；
- integer 使用规范十进制；
- Decimal 从原 token/string 构造；
- 禁止 NaN、Infinity 与负零歧义；
- 路径使用规范相对路径，不使用平台临时绝对路径作为业务身份；
- Secret 值不能进入 canonical arguments；
- Credential 使用 opaque reference ID。

## 6.3 Path 参数

Schema 中的路径字段必须声明：

```text
path_role
root_kind
access_mode
existence_requirement
expected_file_type
content_identity_requirement
```

示例：

```text
path_role = target_artifact
root_kind = artifact_root
access_mode = overwrite
existence_requirement = must_exist
expected_file_type = regular_file
content_identity_requirement = sha256_or_verified_manifest
```

## 6.4 任意命令字符串禁止

P0 不注册：

```text
shell_exec(command: string)
python_eval(code: string)
arbitrary_subprocess(argv_from_model: list)
```

命令由 Tool Adapter 根据类型化参数构造：

```text
resolved_argv = ["python", "-m", module_name, "--help"]
```

模型只能提供 Schema 允许的字段，不能提供 Shell metacharacter 语义。

## 6.5 Secret 参数

禁止模型提供 Secret 明文。

```text
ssh_private_key = "-----BEGIN..."
```

必须拒绝。

只允许：

```text
credential_reference_id = "cred_..."
```

Credential 是否可用于该 Tool 由 Policy 和 Execution Profile 决定。

---

# 7. Capability 与风险模型

## 7.1 风险等级

```text
green < yellow < red
```

风险等级描述执行前需要的控制强度，不表示 Tool 执行成功概率，也不等同于 Sandbox 强度。

## 7.2 Green

Green 必须同时满足：

- 只读；
- 不执行 Repository 代码；
- 不使用 Credential；
- 不创建持久副作用；
- 读取范围属于明确 Root；
- 不读取 Secret；
- 输出大小受限；
- Adapter 本身不实现外部网络动作。

典型：受控文本、Git metadata、实验历史、Metric/Artifact metadata 读取。

## 7.3 Yellow

Yellow 是一个具有完整业务语义的高层 ToolCall，例如：

```text
python_import_check
entrypoint_help
config_parse_check
bounded_smoke_test
```

Workspace 创建、源码物化、resolved config、Seal 与 Cleanup 是该高层 Tool 的内部基础设施步骤，不是独立模型 Tool。

Yellow 默认不逐次人工审批，但必须：

- active `ProjectPolicyConstraintSet` 允许对应 Profile；
- active `RepositoryExecutionTrustGrant` 覆盖该 Repository 和 Profile；
- 使用类型化 ToolExecutionPlan；
- 一个 ToolExecutionIntent 独占一个可写 ToolWorkspace；
- argv-only；
- wall time、输出和资源限制完整；
- 不写主仓库或正式 Artifact；
- 真实 Runtime Capability 与 Profile 声明匹配；
- 对 trusted host 残余风险进行显式披露。

任一条件不满足时，Policy 升级为 Red 或直接 deny。Repository 子进程只有在真实 namespace/network/mount enforcement 存在时，才能获得相应强保证。

## 7.4 Red

以下默认 Red：

- 覆盖或删除现有 Artifact；
- 写主仓库；
- 修改 Git state；
- 使用 SSH 或其他 Credential；
- 外部网络上传；
- 远程执行；
- 访问敏感文件；
- 超过项目预算阈值；
- 写 Workspace 外部路径；
- 改变不可逆外部状态。

Red 必须创建一次性 Tool Execution Approval。真实高风险文件写入还必须具备安全 open-at-root Capability；用户批准不能弥补底层能力缺失。

## 7.5 Deny 不等于 Red

某些动作即使用户批准也不允许 P0 执行：

```text
arbitrary shell
automatic dependency installation
formal experiment launch through Tool
unsafe root escape
untrusted repository execution
unsupported capability
invalid ProjectPolicyConstraintSet
real artifact overwrite without safe-open capability
```

## 7.6 动态风险计算

```text
computed_risk = max(
  tool_default_risk,
  capability_minimum_risk,
  argument_risk,
  path_risk,
  credential_risk,
  network_risk,
  budget_risk,
  project_policy_risk,
  execution_profile_residual_risk
)
```

Project Policy 只能升级风险或 deny：

```text
Green → Yellow/Red/Deny
Yellow → Red/Deny
Red → Deny
```

Current User Request 和 Trust Grant 都不能降低 System Risk；Trust Grant 只解决“是否允许在本机运行该仓库”，不证明代码安全。

# 8. 静态 Policy

## 8.1 P0 Policy 模型

P0 使用版本化静态规则表和固定 Schema 的 `ProjectPolicyConstraintSet`，不建设通用 Policy DSL。

```text
PolicyRule
- rule_id
- policy_version
- priority
- match_conditions
- decision
- risk_override nullable
- reason_code
```

自由文本 Policy 不参与运行时匹配；Policy Evaluator 只读取经过 Schema 校验、用户批准并绑定 Revision 的结构化约束。

## 8.2 决策优先级

```text
deny
> require_approval
> allow
```

多个规则命中时选择控制更严格的结果，并记录关键 `matched_rule_ids`。

## 8.3 Policy 输入

至少包括：

- Tool Definition Identity 与 Availability Generation；
- Available/Exposed Tool Manifest 及 Freshness；
- Project；
- Agent Run purpose；
- canonical arguments；
- Capability；
- Execution Profile 与实际 Runtime Capability；
- Workspace Allocation Spec；
- Path Binding；
- Credential Reference；
- declared network mode 与真实 enforcement；
- budget；
- active Project Policy Revision；
- active `ProjectPolicyConstraintSet`；
- active `RepositoryExecutionTrustGrant`；
- current Preconditions。

## 8.4 强制 deny 规则

P0 至少包含：

```text
POL-DENY-001 Tool not registered
POL-DENY-002 Tool not exposed
POL-DENY-003 Input schema invalid
POL-DENY-004 Path outside allowed root
POL-DENY-005 Symlink escape
POL-DENY-006 Secret literal detected
POL-DENY-007 Unsupported capability
POL-DENY-008 Declared isolation capability unavailable
POL-DENY-009 Arbitrary shell or eval
POL-DENY-010 Formal experiment launch through Tool
POL-DENY-011 Source materialization identity mismatch
POL-DENY-012 Repository execution trust absent/revoked/expired
POL-DENY-013 Unsupported submodule/LFS materialization
POL-DENY-014 Tool Definition unavailable
POL-DENY-015 Required precondition unverifiable
POL-DENY-016 Project Policy constraint set invalid
POL-DENY-017 Manifest stale or invalid
POL-DENY-018 Safe open-at-root capability required but unavailable
```

## 8.5 强制 Approval 规则

```text
POL-RED-001 Existing Artifact overwrite
POL-RED-002 Artifact delete
POL-RED-003 Main repository write
POL-RED-004 Git state mutation
POL-RED-005 Credential use
POL-RED-006 External network upload
POL-RED-007 Remote command execution
POL-RED-008 Sensitive file access
POL-RED-009 Budget threshold exceeded
```

## 8.6 Yellow allow 规则

动态 Yellow Tool 只有满足全部边界才允许：

```text
owner = current ToolExecutionIntent
managed writes under ToolWorkspace writable roots
active RepositoryExecutionTrustGrant
execution profile allowed by ProjectPolicyConstraintSet
shell = false
wall time <= profile and trust grant maximum
no credential unless explicitly modeled
no existing formal artifact overwrite
no main repository write
```

对于 Repository 子进程：

- `trusted_host_process` 只表示用户明确授权并接受残余风险；
- 只有 Profile 具备真实 `network_enforcement` 时才能宣称 `network-none`；
- 只有 Profile 具备真实 mount/namespace enforcement 时才能宣称 source 只读或写入仅限 Workspace；
- 不具备强 enforcement 时，UI、Policy Decision 和 Audit 必须显示残余风险。

## 8.7 Policy Manifest

Policy Manifest 使用 4.13 的不可变对象。变化后：

- 未执行 ToolCall 必须重新评估；
- pending Approval 失效或重评；
- 未开始 ToolExecutionAttempt 不得继续；
- 已开始或可能发生的副作用不能假装未发生；
- Context Snapshot Freshness 按 `04` 更新。

执行前必须满足：

```text
plan.available_tool_manifest_hash
    == current_applicable_tool_manifest_hash

plan.policy_manifest_hash
    == current_applicable_policy_manifest_hash
```

对象仍存在不等于它仍然适用。

# 9. Preconditions

## 9.1 PreconditionSnapshot

执行前必须保存：

```text
PreconditionSnapshot
- project_id
- agent_run_id
- tool_definition_identity
- tool_availability_generation
- available_tool_manifest_hash
- exposed_tool_manifest_hash
- system_policy_identity
- project_policy_revision_id nullable
- project_policy_constraint_set_hash nullable
- repository_execution_trust_grant_id nullable
- repository_execution_trust_generation nullable
- policy_manifest_hash
- repository_snapshot_identity nullable
- repository_state_digest nullable
- workspace_identity nullable
- root_registration_identities
- path_binding_identities
- target_content_identities
- credential_reference_identities
- budget_snapshot
- environment_profile_identity
- runtime_capability_identity
- captured_at
```

## 9.2 Precondition Hash

```text
precondition_hash = SHA-256(
  "labpilot-tool-precondition:v1\n"
  + canonical_json(precondition_payload)
)
```

## 9.3 执行前重检

Tool 从 Approval 恢复、创建 Intent 或进入每次 Adapter Attempt 前重新检查：

- Run Lease generation；
- Tool Definition Availability Generation；
- Available/Exposed Tool Manifest Freshness；
- Policy Manifest Freshness；
- Active Project Policy Revision 与 Constraint Set；
- RepositoryExecutionTrustGrant 是否 active；
- arguments hash 与 execution plan hash；
- Workspace lifecycle、owner 和 evidence hold；
- Root Registration identity/generation；
- Path target identity 与安全 open capability；
- Credential availability；
- Budget；
- Runtime capability；
- Run calendar deadline；
- Approval decision/consumption；
- 是否已有 active ToolExecutionAttempt。

## 9.4 Precondition 变化

关键前提变化且尚未开始副作用时：

```text
ToolCall.execution_status = not_executed
outcome_reason = precondition_changed
```

不得修改原参数后继续消费旧 Approval。

如仍需执行，必须创建新的 ToolCall、Execution Plan、Policy Decision 和 Approval。

已经开始或副作用状态为 `crossed/unknown` 时，不得用 `precondition_changed` 擦除物理事实，必须进入 Result Processing 或 Reconciliation。

# 10. Tool Execution Approval

## 10.1 与 Design Approval 分离

```text
Experiment Design Approval
    冻结科研设计

Tool Execution Approval
    授权一次具体高风险副作用
```

两者不能替代。

## 10.2 Approval Identity

Approval 至少绑定：

```text
project_id
agent_run_id
tool_call_id
tool_definition_identity
tool_availability_generation
argument_schema_version
argument_canonicalization_version
arguments_hash
execution_plan_hash
policy_manifest_hash
precondition_hash
risk_level
expires_at
```

## 10.3 人类可读摘要

Approval UI 必须展示：

- Tool 名称与版本；
- 风险等级；
- 将读取什么；
- 将写入/覆盖/删除什么；
- 是否使用 Credential；
- 是否联网及真实 enforcement；
- 目标 Project/Workspace/Artifact；
- 规范化参数摘要；
- 过期时间；
- 为什么需要 Approval；
- 无法确认副作用时的风险；
- Trusted Host Process 的残余风险（如适用）。

不能只展示模型生成的自然语言理由。

## 10.4 决策事务

复用 `03` 冻结规则：

```text
lock Approval, ToolCall, AgentRun
→ pending
→ database_current_time < expires_at
→ Run not terminal
→ database_current_time < Run calendar deadline
→ write decision with DB time
→ enqueue Wakeup
```

客户端时间不具有权威性。

## 10.5 一次性消费与 Intent 原子绑定

Approved 后：

```text
decision_status = approved
consumption_status = available
```

真正进入执行前，在同一事务中：

```text
lock Approval, ToolCall, Plan
→ recheck Plan/Preconditions
→ require consumption_status = available
→ create unique ToolExecutionIntent
→ allocate/bind ToolWorkspace when required
→ mark Approval consumed
→ bind consumed_by_execution_intent_id
→ commit
```

同一 Approval 最多消费一次。`consumed` 表示授权已经绑定给一个逻辑 Intent，不表示副作用或 Adapter Attempt 已发生。

如果事务提交后、第一次外部动作前服务崩溃：

- 恢复同一个 `tool_execution_intent_id`；
- 可以在该 Intent 下创建新的物理 `ToolExecutionAttempt`；
- 不再次消费 Approval；
- 在副作用状态仍为 `not_crossed` 且 Policy 已变为 deny 时，Intent 可以关闭为 `not_executed`；
- 一旦副作用为 `crossed/unknown`，必须 Inspect/Reconcile，不能创建新的 Non-repeatable Attempt。

## 10.6 Approval 失效

以下使 Approval `invalidated`：

- Tool Version 或 Availability Generation 变化；
- Tool disabled/removed；
- args hash 变化；
- execution plan hash 变化；
- Policy/Constraint Set/Trust Grant 变严或失效；
- Available/Policy Manifest stale；
- path target identity 变化；
- Workspace 不再 valid；
- Credential 引用变化或撤销；
- Run cancelled/expired；
- Project Policy Revision 变化；
- Precondition 无法验证。

## 10.7 Approval 不代表执行成功

```text
approved
≠ intent created
≠ adapter attempt started
≠ side effect crossed
≠ succeeded
```

# 11. Tool 执行流程

## 11.1 正常流程

```text
AgentDecision(CALL_TOOL)
→ resolve immutable ToolDefinition + Availability generation
→ verify ExposedToolManifest
→ validate/canonicalize input
→ resolve Plan/Path/Trust/Capabilities
→ create ToolExecutionPlan
→ evaluate deterministic Policy
→ allow | require_approval | deny
→ atomically create ToolExecutionIntent
→ internally allocate/prepare ToolWorkspace
→ create ToolExecutionAttempt
→ execute Adapter
→ quarantine raw output
→ schema/secret/path processing
→ create redacted ToolResultEnvelope
→ persist Observation
→ seal/retain/cleanup Workspace
```

模型不可见的 Workspace 内部步骤包括：

```text
CreateToolWorkspace
MaterializeRepositorySnapshotForTool
WriteResolvedConfigForTool
SealToolWorkspace
RequestToolWorkspaceCleanup
```

它们不是独立的模型 ToolCall。

## 11.2 持久化顺序

在外部副作用前至少持久化：

- ToolCall；
- ToolExecutionPlan；
- Policy Decision；
- Approval state；
- idempotency key；
- Precondition hash；
- ToolExecutionIntent；
- ToolWorkspace Manifest；
- ToolExecutionAttempt intent。

## 11.3 Side-effect Boundary

每个 ToolDefinition 必须说明何时认为外部副作用可能已经发生。

```text
read_file
    side_effect_state = not_applicable

write_new_file
    first successful create/open-for-write

overwrite_artifact
    first successful atomic replacement operation

remote_api_call
    request accepted by remote endpoint
```

一旦跨越边界后响应丢失，不得盲目重试。

## 11.4 原始输出隔离和 Result 处理

```text
raw_quarantine
→ encoding/size check
→ output schema validation
→ secret scan and redaction
→ path normalization
→ changed-path verification
→ result hash
→ redacted_persistent
→ Agent Observation
```

原始 stdout/stderr/result 首先写入访问受限的 quarantine。Agent、Timeline、普通日志和 Event 只能引用脱敏副本。

Secret Scanner 失败时：

- fail closed，不生成 Agent Observation；
- 不把原始输出复制到普通持久化位置；
- 不能把已经发生的外部副作用伪装成 failed/not_executed；
- Result Processing 状态与物理执行状态分别记录。

## 11.5 Output Truncation

stdout/stderr 超过限制时：

- raw 文件仍只在 quarantine；
- redacted persistent copy 可按限制保留；
- Context 只放确定性摘要和安全片段；
- `output_truncation = true`；
- required output 被截断无法解析时 Result Processing 失败。

## 11.6 Tool 超时

P0 Tool 必须是 bounded operation。

- read/parse 通常秒级；
- Smoke Test 最大 60 秒；
- 超时后执行 best-effort cancel；
- 副作用无法确认时进入 `unknown + open`；
- 需要长期运行的任务必须进入 `06`。

# 12. Idempotency、Retry 与 Unknown

## 12.1 Idempotency Class

```text
pure_read
deterministic_write_by_key
inspectable_side_effect
non_repeatable_side_effect
```

## 12.2 Intent 与 Attempt

```text
ToolCall
→ exactly one ToolExecutionIntent
→ one or more bounded ToolExecutionAttempt
```

Approval、idempotency key、Execution Plan 和 Workspace 绑定 Intent。每一次真实 Adapter 调用绑定独立 Attempt。

## 12.3 Pure Read

- 无持久副作用；
- 同一 Intent 下可按固定上限创建多个 Attempt；
- 每个 Attempt 都计入预算和 Trace；
- 不创建新的 ToolCall 或 Approval。

## 12.4 Deterministic Write by Key

- 外部系统必须支持相同 idempotency key 去重；
- 所有 Attempt 复用同一 Intent、key 和外部业务身份；
- 重试前优先 Inspect；
- 目标内容与 Plan 不一致时停止。

## 12.5 Inspectable Side Effect

- 响应未知时使用稳定外部 ID、marker 或目标内容 Inspect；
- 确认成功/失败/未执行后创建 ToolCallResolution；
- 未确认前保持 `unknown + open`。

## 12.6 Non-repeatable Side Effect

- `side_effect_state = crossed/unknown` 后不自动创建新 Attempt；
- 必须人工处置；
- 再次执行使用新 Run 和新 ToolCall；
- UI 明确提示潜在重复副作用。

## 12.7 Idempotency Key

```text
idempotency_key = SHA-256(
  "labpilot-tool-execution-intent:v1\n"
  + canonical_json({
      "tool_call_id": ...,
      "tool_definition_identity": ...,
      "execution_plan_hash": ...
    })
)
```

同一 Intent 的所有 Attempt 使用相同 Key。

## 12.8 Unknown

```text
ToolCall.execution_status = unknown
ToolCall.resolution_status = open
ToolExecutionIntent.intent_status = reconciling
ToolWorkspace.evidence_retention_status = hold
```

不得自动改成 failed、自动重复副作用、让模型猜测或将 unknown 当成成功 Observation。

## 12.9 Reconciliation

Tool Contract 可以定义：

```text
inspect(tool_execution_plan, external_identity)
cancel(tool_execution_plan, external_identity)
```

结果：

```text
confirmed_succeeded
confirmed_failed
confirmed_not_executed
still_unknown
```

## 12.10 Abandon 与证据保留

用户停止调查：

```text
execution_status = unknown
resolution_status = abandoned
resolution_type = abandoned_unknown
```

`abandoned` 只关闭自动调查，不表示可以立即删除证据。

在释放 Workspace hold 前，必须保存最小 Reconciliation Evidence Package：

```text
execution_plan_hash
intent/attempt identities
runtime markers
external identity
changed path manifest
redacted logs
resolution record
```

完成后：

```text
evidence_retention_status = released
```

大文件才可以按策略清理。该 ToolCall 永远不满足依赖 Tool 成功的业务前置条件。

# 13. Workspace Contract

## 13.1 P0 Tool Workspace 目录

```text
tool-workspace/
├─ source/
├─ manifest.json
├─ source-manifest.json
├─ resolved-config/
├─ logs/
├─ outputs/
├─ runtime/
├─ quarantine/
└─ tmp/
```

## 13.2 高层 Tool 独占 Workspace

P0 模型只调用高层业务 Tool。一个需要 Workspace 的高层 ToolCall 创建一个 ToolExecutionIntent，并由该 Intent 独占一个可写 ToolWorkspace。

以下只是 Tool Gateway/Adapter 内部基础设施动作，不是模型可见 Tool：

```text
CreateToolWorkspace
MaterializeRepositorySnapshotForTool
WriteResolvedConfigForTool
SealToolWorkspace
RequestToolWorkspaceCleanup
CompleteToolWorkspaceCleanup
```

这样保持：

```text
one ToolCall
→ one Execution Intent
→ one writable Tool Workspace
```

## 13.3 目录语义

### `source/`

- 来自明确 Repository Snapshot；
- Clean Snapshot 使用安全 `git archive` 物化；
- Dirty Snapshot 使用 `04` 冻结的内容 Manifest；
- 不回退当前主工作树；
- 在 `trusted_host_process` 下只做权限和 Manifest 的 best-effort 防护；
- 只有 Profile 具备只读 mount/namespace enforcement 时，才能宣称 Repository 子进程无法修改 source。

### `resolved-config/`

- Adapter 内部写入类型化配置；
- 每个文件绑定 content hash；
- 不是独立模型 Tool；
- 不修改 Repository 原文件。

### `logs/`

- 只保存通过 Secret Scan 后的 redacted persistent 日志；
- 原始输出不得写入这里。

### `outputs/`

- 临时诊断输出；
- 不属于正式 Experiment Artifact；
- 不进入 Batch Summary。

### `runtime/`

- PID/marker；
- Adapter Attempt 状态；
- cancellation marker；
- 外部身份引用。

### `quarantine/`

- 原始 stdout/stderr/result；
- 严格访问控制；
- 短保留期；
- Agent、Timeline 和普通日志不可直接读取。

### `tmp/`

- 可以清理的短期文件；
- 不保存正式科研结果或唯一 Reconciliation Evidence。

## 13.4 Workspace Identity 与 Manifest

```text
workspace_identity = SHA-256(
  "labpilot-tool-workspace:v2\n"
  + canonical_json({
      "project_id": ...,
      "owner_tool_execution_intent_id": ...,
      "purpose": ...,
      "repository_snapshot_identity": ...,
      "source_manifest_digest": ...,
      "execution_profile_identity": ...,
      "root_registration_identity": ...
    })
)
```

```text
WorkspaceManifest
- workspace_identity
- project_id
- owner_tool_execution_intent_id
- purpose
- repository_snapshot_identity
- repository_state_digest
- source_manifest_digest
- execution_profile_identity
- root_registration_identity
- allowed_read_roots
- managed_write_roots
- environment_variable_names
- credential_reference_ids
- enforcement_capabilities
- residual_risk_statement
- created_at
- manifest_hash
```

不保存 Secret 值。

## 13.5 Workspace Lifecycle

```text
requested
→ preparing
→ ready
→ active
→ sealed
→ cleanup_pending
→ cleaned

preparing/active
→ failed
```

规则：

- `ready` 前不执行 Adapter；
- `active` 只能由 owner Intent 的 Attempt 使用；
- `sealed` 后 LabPilot 管理的写操作被拒绝；
- `evidence_retention_status = hold` 时不得完成 Cleanup；
- `cleaned` 后保留 Manifest、Intent/Attempt 和审计证据；
- Cleanup 不删除正式 Artifact。

## 13.6 Attempt Workspace Layout

正式 Attempt Workspace 至少满足上游 V1：

```text
workspace/
├─ source/
├─ manifest.json
├─ source-manifest.json
├─ resolved-config/
├─ logs/
├─ outputs/
└─ runtime/
```

本文冻结目录、路径与写入边界。创建、提交、进程身份和恢复由 `06` 定义。

## 13.7 Workspace 不可共享写入

P0 同一 Tool Workspace 同时最多一个 active writer。

不同 ToolExecutionIntent 不得共享可写 Workspace。内容寻址的只读 Source Cache 可以复用，但每个 Intent 的 managed writable roots 必须独立。

# 14. Repository Execution Trust

## 14.1 默认只读

注册 Repository 后默认：

```text
repository_read = allowed
repository_execute_bounded = disabled
```

## 14.2 独立 Trust Grant

运行 Repository 代码要求 active `RepositoryExecutionTrustGrant`，不能只依赖 Project Policy 文本。

Grant 至少限制：

- Repository Registration；
- 允许的 Execution Profile；
- 最大 wall time；
- granted/revoked/expired 状态；
- governing Policy Revision。

每次执行还必须绑定当前 Repository Snapshot 和 State Digest。

## 14.3 Trusted Host Process

P0 默认允许的动态 Yellow 路径是：

```text
trust_mode = trusted_host_bounded_execution
process_isolation = trusted_host_process
filesystem_enforcement = best_effort
network_enforcement = unavailable
```

它表示：

- 用户明确授权在本机运行该 Repository；
- 命令、时间、环境和输出受到 LabPilot 管理；
- 但 Repository 子进程仍可能使用绝对路径、访问系统文件、启动子进程或联网；
- UI、Policy Decision 和 Audit 必须展示残余风险；
- 不得将它描述为强 Sandbox。

## 14.4 可执行的 best-effort 控制

Trusted Host Profile 至少：

- 从物化 Workspace 启动，不从主 Repository Root 启动；
- argv-only，禁止 Shell；
- 清理环境变量；
- 重定向 `HOME`、`TMPDIR` 和缓存目录到 Workspace；
- 对 `source/` 设置只读权限；
- 设置 `PYTHONDONTWRITEBYTECODE=1`；
- 固定 wall time、输出上限和 process group；
- 执行前后比较 Source/Workspace Manifest；
- 发现非预期修改时标记失败或 unknown，并保留证据。

这些措施不构成对恶意代码的 OS 隔离。

## 14.5 Namespace-enforced Profile

部署真正具备只读 mount、network namespace 和 process namespace 时，可以启用更强 Profile。

只有能力探测通过，Profile 才能声明：

```text
network_mode = none
filesystem = workspace-only-write
source = readonly-mount
```

Capability 不足时 fail closed，不得静默降级为 trusted host 后仍保留强安全标签。

## 14.6 不可信仓库

未知第三方或未获 Trust Grant 的 Repository：

- 允许 Green 静态读取；
- 不运行 import、`--help`、配置加载或 Smoke Test；
- Policy 返回 deny。

## 14.7 强 Sandbox 后移

P0 不要求 Bubblewrap/Container。未来加入强 Sandbox 时，Tool Contract、Plan、Approval 和 Intent/Attempt 模型保持不变，只替换 Execution Profile 与 Adapter。

# 15. Path Boundary

## 15.1 RootRegistration 与 Root Identity

```text
RootRegistration
- root_id
- root_kind
- project_id
- canonical_root_path
- device_id
- inode
- mount_id nullable
- filesystem_type nullable
- registration_generation
- access_capabilities
- active
- root_identity
```

```text
root_identity = SHA-256(
  "labpilot-root-registration:v1\n"
  + canonical_json({
      "root_kind": ...,
      "project_id": ...,
      "canonical_root_path": ...,
      "device_id": ...,
      "inode": ...,
      "mount_id": ...,
      "registration_generation": ...
    })
)
```

P0 Root Kind：

```text
repository_root
tool_workspace_root
artifact_root
attempt_workspace_root
demo_artifact_root
quarantine_root
```

执行前必须重检 canonical path、device/inode 和 active registration generation。

## 15.2 PathBinding

```text
PathBinding
- path_binding_id
- root_id
- root_registration_identity
- normalized_relative_path
- access_mode
- expected_file_type
- existence_requirement
- symlink_policy
- content_identity nullable
- nearest_parent_identity nullable
- required_open_capability nullable
- binding_hash
```

## 15.3 校验流程

```text
reject NUL / invalid encoding
→ normalize separators to '/'
→ reject absolute path when relative required
→ reject '..' traversal
→ verify RootRegistration identity/generation
→ join under registered root
→ lstat path components
→ detect symlink
→ resolve according to policy
→ verify final path under root
→ verify file type/existence/content identity
→ capture parent/target observation
```

## 15.4 Symlink

- Repository read Tool 可以读取 symlink metadata；
- 读取 target 内容前验证 target 仍在允许 Root；
- Workspace write 默认禁止经过 symlink；
- Artifact overwrite 拒绝 symlink target 变化；
- Root 外 target 返回 `PathEscapeDetected`。

## 15.5 新文件写入

目标不存在时绑定：

- normalized target path；
- nearest existing parent identity；
- target nonexistence；
- no-symlink policy；
- required safe-open capability。

执行前重新验证 parent 和 target。

## 15.6 覆盖文件

覆盖绑定：

- target path；
- expected file type；
- existing content identity；
- size；
- inode/device observation；
- artifact metadata identity。

内容变化后旧 Approval 失效。

## 15.7 Bind Mount 边界

`realpath` 和 symlink 检查不能识别 Root 内所有 bind mount。

P0 默认：

```text
信任部署者注册的 mount layout；
未启用 mountinfo enforcement 时，
不承诺识别 Root 内的不可信 bind mount。
```

部署启用 `mount_boundary_enforcement` 时，可以读取 `/proc/self/mountinfo`，绑定 mount identity 并拒绝未注册的跨 mount 边界。

测试不得在未启用该 Capability 时宣称“Root 外 bind path 已被拦截”。

## 15.8 Safe Open-at-root

高风险写操作需要 Capability：

```text
safe_open_beneath
atomic_replace_beneath
```

Linux Adapter 可以通过 `openat2` 的 `RESOLVE_BENEATH`、`RESOLVE_NO_SYMLINKS` 等能力实现，但规格不绑定唯一 syscall。

如果部署无法提供安全 open-at-root 语义：

- 真实 Artifact overwrite/delete Tool 禁用；
- Core Red Demo 使用合成 Adapter；
- 不允许退化为只在操作前做一次 `realpath` 检查。

## 15.9 TOCTOU

P0 必须：

- 在真实 open/replace 前立即重检；
- 使用 Capability-backed open-at-root 语义；
- 无法安全保持 Path Boundary 时拒绝高风险写入；
- 不将早期验证视为永久授权。

P0 不宣称完全抵御具有同宿主机写权限的恶意竞争进程。

# 16. Command 与 Process Profile

## 16.1 argv-only

P0 外部进程使用 argv 数组，禁止 `shell=True`、pipe、redirect string、command substitution、backtick 和模型生成脚本体。

## 16.2 Executable Allowlist

Execution Profile 明确允许的 executable 来自 Environment Profile，不从 Repository 文本自由解析。

## 16.3 Workdir

- 必须是 ToolWorkspace 内规范路径；
- bounded smoke 默认 `tool-workspace/source/`；
- 正式 Attempt 使用 `attempt-workspace/source/`；
- 不从主 Repository Root 直接启动。

## 16.4 Environment

只注入 Profile 白名单和 Adapter 固定变量。默认清除未声明云 Credential、SSH agent socket、高风险用户环境变量和隐式输出目录变量。

Trusted Host Process 至少重定向：

```text
HOME
TMPDIR
XDG_CACHE_HOME
PYTHONPYCACHEPREFIX or PYTHONDONTWRITEBYTECODE
```

Secret Reference 只在最接近执行边界时解析。

## 16.5 Resource Limits

Yellow bounded process：

- wall time <= 60s；
- stdout/stderr/result size cap；
- process group；
- child cleanup；
- optional CPU/memory limit；
- 不申请正式 GPU 长任务。

网络是否被强制禁止由 `network_enforcement` 决定，不能只根据 Environment 声明。

## 16.6 取消

- graceful termination；
- grace period 后终止 process group；
- 检查残留子进程；
- 无法确认副作用时 unknown；
- 取消不自动删除 outputs 或 Reconciliation Evidence。

# 17. Network 与 Credential

## 17.1 网络策略与真实 Enforcement

P0 不自动安装依赖、下载模型/数据、上传 Artifact 或调用任意外部 API。

对于 Adapter-only Tool，可以通过不实现网络功能严格保证无网络访问。

对于 Repository 子进程：

- `trusted_host_process` 的 `network_enforcement = unavailable`；
- 不能宣称绝对断网；
- 只有 Namespace-enforced Profile 才能声明 `declared_network_mode = none` 已被 OS 强制执行；
- Policy 必须匹配声明与实际 Capability，禁止虚假安全标签。

## 17.2 Dependency 安装

```text
P0 install_dependencies Tool = not exposed
```

未来若加入，必须绑定 lockfile、受控 mirror、网络/预算和独立环境，且不得修改系统全局环境。

## 17.3 CredentialReference

```text
CredentialReference
- credential_reference_id
- credential_type
- scope
- provider
- availability_status
- metadata_hash
```

数据库、Prompt、Trace、Manifest 和 Tool arguments 不保存 Secret 值。

## 17.4 Credential 解析

```text
Plan references credential ID
→ Policy requires Red Approval
→ Executor validates scope
→ Secret injected temporarily
→ raw output quarantined
→ reference audit retained
```

## 17.5 SSH

P0 不执行真实 SSH 实验。Demo 只能使用合成测试 Adapter，不读取真实 Credential。

P1 真正 SSH Executor 由 `06` 定义；正式远程训练仍源自 Approved Version/Attempt，而不是 Agent Tool。

# 18. Secret 与输出保护

## 18.1 三层输出区

```text
raw_quarantine
→ redacted_persistent
→ agent_observation
```

- `raw_quarantine`：严格访问控制，短保留期；
- `redacted_persistent`：通过 Scanner 后可用于审计和诊断；
- `agent_observation`：只包含最小结构化结果和安全片段。

## 18.2 Secret Detection

参数、环境、stdout、stderr、Result 在进入普通持久化和 Agent Context 前执行版本化 Secret Detection / Redaction。

至少检测：

- PEM/SSH private key；
- common API token pattern；
- `.env` key/value；
- credential URL；
- Authorization header；
- configured project secret patterns。

## 18.3 Secret 发现时

模型参数出现 Secret：

```text
deny
error = SecretLiteralNotAllowed
```

Tool 输出出现 Secret：

- 原始内容仅保留在 quarantine；
- 生成 redacted persistent copy；
- Agent/Timeline/Event 不得引用明文；
- 记录 `SecretLeakPrevented`，只含规则、位置和类型，不含 Secret 值；
- 必要时标记相关 Context Snapshot invalid。

Scanner 失败：

- 不生成 Observation；
- 不提升 raw output；
- 物理执行事实保持原状；
- 进入 Result Processing failure，而不是伪造 Tool 未执行。

## 18.4 Log

普通日志禁止完整环境变量 dump、Credential、Signed URL、Authorization header 和 Secret 文件正文。

# 19. Tool Output 与 Artifact 边界

## 19.1 Tool Output

Tool Output 是 Agent Observation 来源，不自动成为正式 Artifact。

## 19.2 Smoke Test Output

```text
bounded smoke output
- temporary
- diagnostic only
- summary_eligible = false
- no formal Metric
- no Batch Summary
```

## 19.3 Artifact 创建

P0 Yellow Tool 可以创建临时 Artifact，但正式 Experiment Artifact 必须由 Attempt collect/validation 流程登记。

## 19.4 Artifact 覆盖与删除

- 默认 Red；
- Approval 绑定 Artifact ID、path、现有 content identity 和 Plan；
- 使用原子替换；
- 删除前保存审计 metadata；
- Cleanup Tool 不得将正式 Artifact 当成临时文件。

## 19.5 Promotion 禁止

P0 不支持将 Smoke Test 输出“升级”为正式 Trial Result。需要正式结果时必须运行 Approved Version 的 Trial/Attempt。

---

# 20. 正式 Experiment 边界

## 20.1 允许 Tool 做的事

Tool 可以：

- 读取 Repository；
- 解析配置；
- 运行入口存在性检查；
- 运行 `--help`；
- 执行 30–60 秒 Smoke Test；
- 生成 Proposal 所需临时配置；
- 读取历史实验元数据；
- 读取 eligible Summary；
- 覆盖 Artifact 前请求 Red Approval。

## 20.2 Tool 禁止做的事

Tool 不能：

- 创建 ExperimentVersion；
- 创建 RunBatch；
- 创建 Trial；
- 创建 Attempt；
- 启动正式训练；
- 将临时 Metric 登记为正式 Metric；
- 将 Smoke 输出放入 Batch Summary；
- 通过 Approval 绕过 Design Approval；
- 直接修改 Attempt 状态。

## 20.3 Formal Training Request

模型需要正式执行时，应产生领域动作或 Proposal 流程：

```text
Proposal Ready
→ Human Design Approval
→ Immutable Experiment Version
→ Create Formal Batch
→ Coordinator
→ Attempt Workspace / Executor
```

不是：

```text
CALL_TOOL("run_training")
```

---

# 21. P0 最小 Tool Catalog

## 21.1 Green

### `repository_list_files@1`

- 读取受控目录树；
- size/type/path policy；
- 不跟随 Root 外 symlink。

### `repository_read_text@1`

- 读取允许类型与大小内文本；
- 返回 content hash；
- Secret 文件 deny。

### `git_status_snapshot@1`

- 读取 Git HEAD/status；
- 不修改 Git。

### `git_show_file@1`

- 从指定 clean commit 读取 tracked blob；
- 不读取当前 mutable worktree。

### `experiment_history_read@1`

- 读取不可变 Version/Trial/eligible Summary；
- 不将 failed/invalid 当科研证据。

### `artifact_metadata_read@1`

- 读取 Artifact metadata/availability；
- 不读取 Secret 内容。

## 21.2 Yellow 高层 Tool

### `python_import_check@1`

一次 ToolCall 内部完成 Workspace 创建、源码物化、环境准备、执行、输出处理和 Seal。

- 固定 Adapter；
- bounded；
- RepositoryExecutionTrustGrant required；
- 使用实际可用的 Execution Profile；
- 不暴露 Workspace 生命周期步骤给模型。

### `entrypoint_help@1`

- 运行批准的 entrypoint `--help`；
- argv-only；
- <= 30 秒；
- 一个 ToolCall 独占一个 Intent/Workspace。

### `config_parse_check@1`

- Adapter 内部生成 resolved config；
- 使用声明 parser；
- 不启动正式训练；
- 不修改 Repository。

### `bounded_smoke_test@1`

ToolExecutionPlan 一次性包含：

```text
repository_snapshot
entrypoint
typed config payload/ref
timeout
expected diagnostic output
execution profile
```

Gateway 内部完成：

```text
create workspace
→ materialize source
→ write resolved config
→ execute
→ quarantine/redact output
→ seal
→ cleanup or retention hold
```

限制：

- <= 60 秒；
- 输出仅进 Tool Workspace；
- 不产生正式 Metric/Artifact；
- 不创建 Trial/Attempt。

## 21.3 内部基础设施命令（不进入模型 Manifest）

```text
CreateToolWorkspace
MaterializeRepositorySnapshotForTool
WriteResolvedConfigForTool
SealToolWorkspace
RequestToolWorkspaceCleanup
CompleteToolWorkspaceCleanup
```

## 21.4 Red

### `overwrite_artifact@1`

- 真实部署只有具备 safe-open capability 时可用；
- 绑定旧 content identity；
- Approval required。

### `delete_artifact@1`

- 真实部署只有具备 safe-open capability 时可用；
- metadata 保留；
- Approval required。

### `write_repository_file@1`

- P0 生产默认不暴露；
- P1 Worktree 后再正式支持。

### `simulate_ssh_action@1`

- 仅测试/Demo Manifest；
- 合成副作用；
- 不读取真实 Credential。

## 21.5 明确不注册

```text
shell_exec
run_arbitrary_command
python_eval
install_package
submit_formal_training
modify_git_history
read_any_file
upload_anywhere
create_tool_workspace
materialize_repository_snapshot
write_resolved_config
cleanup_tool_workspace
```

# 22. ToolCall、Intent、Attempt 与 Workspace 状态关系

## 22.1 未执行

```text
ToolCall = created / validating / awaiting_approval
ToolExecutionIntent = absent
ToolWorkspace = absent
```

可以提前保存 Plan，但不得产生副作用。

## 22.2 Intent 已创建

```text
ToolExecutionIntent = ready
ToolWorkspace = preparing / ready
```

Approval 已绑定 Intent，但 Adapter Attempt 可以尚未开始。

## 22.3 执行中

```text
ToolCall.execution_status = executing
ToolExecutionIntent.intent_status = executing
ToolExecutionAttempt.status = running
ToolWorkspace.lifecycle_status = active
```

## 22.4 成功

- selected Attempt 和 Result Envelope 有效；
- Workspace seal；
- Observation 创建；
- 无 retention hold 时可进入 cleanup。

## 22.5 失败

- 保存 Attempt 和 redacted diagnostics；
- 不把部分输出当成功；
- Workspace seal；
- 根据证据策略保留或清理。

## 22.6 Unknown

```text
ToolCall = unknown/open
Intent = reconciling
Workspace.evidence_retention_status = hold
```

不自动清理或重试副作用。

## 22.7 Abandoned

```text
ToolCall = unknown/abandoned
```

保存最小 Evidence Package 后才能 `hold → released`；工作队列关闭，但成功前置条件仍不满足。

# 23. Ownership 与授权调用者

| 对象/事实 | 持久化写入服务 | 授权调用者 |
|---|---|---|
| ToolDefinition | Tool Registry Service | Deployment/Admin path |
| ToolDefinitionAvailability | Tool Registry Service | Deployment/Admin path |
| ToolManifest/Freshness | Tool Registry Service | Context Compiler / Runtime |
| ExposedToolManifest | Context Compiler | Planning Agent Runtime |
| ProjectPolicyConstraintSet | Policy Service | Project Policy approval flow |
| RepositoryExecutionTrustGrant | Trust/Policy Service | Authenticated User |
| ToolExecutionPlan | Tool Gateway | Planning Agent Runtime handling persisted Decision |
| ToolPolicyDecision | Policy Service | Tool Gateway |
| PolicyManifest/Freshness | Policy Service | Tool Gateway / Context Compiler |
| ToolApprovalRequest/Decision | Approval Service | Tool Gateway / Authenticated User |
| Approval consumption | Approval Service | Tool Gateway transaction creating Intent |
| ToolExecutionIntent | Tool Gateway | Planning Agent Runtime handling persisted Decision |
| ToolWorkspace | Workspace Service | Tool Gateway for one Intent |
| ToolExecutionAttempt | Tool Executor Adapter Service | Tool Gateway |
| ToolResultEnvelope | Tool Gateway | Adapter result processor |
| ToolCall execution/resolution projection | Agent Runtime Service | Tool Gateway observations |
| ToolCallResolution | Agent Runtime/Resolution Service | Inspect result or Human |

Coordinator、Repository Inspector、UI 和模型不得直接写这些表。

# 24. Domain Commands

## 24.1 Registry

```text
PublishToolDefinition
SetToolDefinitionAvailability
BuildAvailableToolManifest
BuildExposedToolManifest
RecomputeToolManifestFreshness
```

## 24.2 Policy 与 Trust

```text
ValidateProjectPolicyConstraintSet
ActivateProjectPolicyConstraintSet
GrantRepositoryExecutionTrust
RevokeRepositoryExecutionTrust
PrepareToolExecutionPlan
EvaluateToolPolicy
RecomputePolicyManifestFreshness
RecheckToolPreconditions
```

## 24.3 Approval

```text
RequestToolExecutionApproval
DecideToolExecutionApproval
ExpireToolExecutionApproval
InvalidateToolExecutionApproval
CreateToolExecutionIntentAndConsumeApproval
```

## 24.4 Workspace 内部命令

```text
CreateToolWorkspace
MaterializeRepositorySnapshotForTool
WriteResolvedConfigForTool
SealToolWorkspace
PlaceToolWorkspaceEvidenceHold
ReleaseToolWorkspaceEvidenceHold
RequestToolWorkspaceCleanup
CompleteToolWorkspaceCleanup
```

这些不是模型可见 Tool。

## 24.5 Execution

```text
CreateToolExecutionIntent
CreateToolExecutionAttempt
StartToolExecutionAttempt
RecordToolExecutionAttemptResult
ProcessQuarantinedToolOutput
InspectUnknownToolExecution
CancelToolExecution
ResolveUnknownToolCall
```

不存在供模型直接调用的：

```text
ExecuteAnyToolByName
RunShell
LaunchFormalExperimentTool
CreateOrCleanupWorkspaceTool
```

# 25. Query 接口

```text
GetToolDefinition
ListAvailableTools
GetAvailableToolManifest
GetExposedToolManifest
GetToolExecutionPlan
GetToolPolicyDecision
GetToolApprovalRequest
GetToolWorkspace
GetToolExecutionIntent
ListToolExecutionAttempts
GetToolResultEnvelope
ListUnknownToolCalls
ListToolAuditEvents
```

Query 不改变状态。

---

# 26. Runtime Events

至少记录：

```text
tool_definition_published
tool_availability_changed
tool_manifest_built
tool_manifest_marked_stale
tool_exposed
project_policy_constraint_set_validated
repository_execution_trust_granted
repository_execution_trust_revoked
tool_call_validated
tool_execution_plan_created
tool_policy_allowed
tool_policy_denied
tool_approval_requested
tool_approval_approved
tool_approval_rejected
tool_approval_expired
tool_approval_invalidated
tool_approval_consumed
tool_execution_intent_created
tool_workspace_created
tool_workspace_materialized
tool_execution_attempt_created
tool_execution_attempt_started
tool_execution_succeeded
tool_execution_failed
tool_execution_unknown
tool_execution_cancelled
tool_output_quarantined
tool_output_redacted
tool_unknown_resolved
tool_unknown_abandoned
tool_workspace_evidence_hold_placed
tool_workspace_evidence_hold_released
tool_workspace_sealed
tool_workspace_cleaned
secret_leak_prevented
path_escape_rejected
safe_open_unavailable
```

Event 用于 Timeline 和审计，不替代 ToolCall、Approval、Execution Intent/Attempt、Workspace 与 Result Envelope 权威对象。

Event Sequence 复用：

```text
UNIQUE(aggregate_type, aggregate_id, sequence)
```

---

# 27. 错误分类

## 27.1 Registry / Exposure

```text
ToolNotRegistered
ToolVersionNotFound
ToolDefinitionUnavailable
ToolAvailabilityGenerationChanged
ToolNotExposed
ToolManifestStale
PolicyManifestStale
```

## 27.2 Input / Plan

```text
ToolInputSchemaInvalid
ToolArgumentCanonicalizationFailed
UnsupportedToolArgument
SecretLiteralNotAllowed
ToolExecutionPlanConflict
ProjectPolicyConstraintSetInvalid
```

## 27.3 Policy / Approval / Trust

```text
ToolPolicyDenied
ToolApprovalRequired
ToolApprovalRejected
ToolApprovalExpired
ToolApprovalCancelled
ToolApprovalInvalidated
ToolApprovalAlreadyConsumed
RepositoryExecutionNotTrusted
RepositoryExecutionTrustExpired
RepositoryExecutionTrustRevoked
```

## 27.4 Workspace / Path

```text
WorkspacePreparationFailed
WorkspaceNotReady
WorkspaceOwnershipMismatch
WorkspaceSealed
WorkspaceEvidenceHoldActive
RootRegistrationChanged
PathOutsideAllowedRoot
PathTraversalRejected
PathEscapeDetected
PathTargetChanged
PathTypeMismatch
MountBoundaryUnenforced
SafeOpenCapabilityUnavailable
SourceMaterializationIncomplete
```

## 27.5 Execution / Result Processing

```text
ToolExecutionTimeout
ToolExecutionCancelled
ToolExecutionFailed
ToolExecutionUnknown
ToolExecutionAttemptConflict
ToolResultSchemaInvalid
ToolOutputTooLarge
ToolOutputQuarantined
SecretScannerFailed
ToolSideEffectMismatch
ToolInspectUnavailable
```

## 27.6 Capability

```text
UnsupportedCapability
ExecutionProfileCapabilityUnavailable
NetworkIsolationUnavailable
ReadonlyMountUnavailable
CredentialUnavailable
CredentialScopeDenied
UnsupportedSourceDependency
FormalExperimentToolForbidden
```

# 28. Security Threat Model

## 28.1 P0 保护目标

- 模型不能绕过 Registry/Manifest；
- Repository 文本不能提权；
- LabPilot 管理的文件操作不能越过 PathBinding；
- Tool Workspace 由单一 Intent 独占；
- Red Tool 未批准时副作用次数为零；
- Approval 参数变化后不能复用；
- Secret 不进入 Prompt/Trace/普通日志；
- Shell injection 不可达；
- Tool 不能启动正式训练；
- Cleanup 不删除正式结果或未释放的 Reconciliation Evidence。

## 28.2 P0 信任假设

- 单用户；
- 本地 Linux 主机和部署配置由用户控制；
- LabPilot 服务账户可信；
- `trusted_host_process` 只用于用户明确授权且非主动恶意的 Repository；
- PostgreSQL 时钟和事务可信；
- Root/Mount 注册由部署者控制。

## 28.3 P0 不保证

- 在 trusted host profile 中阻止 Repository 子进程联网；
- 在 trusted host profile 中阻止绝对路径写入；
- 安全执行恶意 Repository；
- 识别所有 Root 内 bind mount；
- 防御同宿主机 root；
- 内核级隔离；
- 防御所有 TOCTOU；
- 任意第三方 Tool 插件安全。

只有 Namespace-enforced Profile 可以对其实际 Capability 作更强声明。

# 29. Agent 与 Tool 的交互约束

## 29.1 Agent 只能请求

AgentDecision 可以请求：

```text
CALL_TOOL(tool_name, version_constraint, arguments)
```

Runtime 解析到精确 ToolDefinition。

## 29.2 Agent 不直接提供

Agent 不提供：

- risk_level；
- approval_required；
- workspace_root；
- credential value；
- shell command；
- idempotency key；
- precondition hash；
- execution plan hash。

## 29.3 Tool Observation

模型看到：

- structured result；
- deterministic summary；
- warnings；
- redacted log excerpts；
- Tool status；
- changed path summary。

模型不自动看到：

- Secret；
-完整原始环境；
- 未经 Redaction 的 stderr；
- Approval 内部数据库字段；
- Root 外路径。

## 29.4 Policy Deny

Policy deny 是确定性 Observation。Agent 可以：

- 选择更安全 Tool；
- 修改 Proposal；
- 请求用户提供信息；
- 终止。

Agent 不能重复相同 deny Tool 形成循环。

---

# 30. 测试策略

## 30.1 Definition 与 Availability

- Definition Identity 稳定；
- Schema 变化必须新版本；
- Availability 变化不修改 Definition；
- Generation 单调递增；
- disabled Tool 不可新执行。

## 30.2 Tool Exposure 与 Freshness

- 未暴露 Tool 被拒；
- Tool Schema 不截断；
- Exposed Manifest hash 稳定；
- Availability/Policy 变化使旧 Manifest stale；
- Action Preconditions 检查 current applicable manifest。

## 30.3 Canonicalization

- object key 顺序不影响 hash；
- Decimal 稳定；
- path separator 规范；
- NaN/Infinity 被拒；
- Secret 不进入 canonical args；
- Schema/canonicalization version 进入 identity。

## 30.4 Policy 与 Trust

- 自由文本不被运行时直接解释；
- invalid Constraint Set fail closed；
- Green read allow；
- 未获 Trust Grant 的动态 Tool deny；
- expired/revoked Grant deny；
- Project Policy 只能升级风险；
- Runtime Capability 与 Profile 声明不匹配时 deny。

## 30.5 Approval 与 Intent

- 未批准副作用次数 = 0；
- Approval 与 unique Intent 原子绑定；
- Approval 只消费一次；
- Intent 下可以有多个物理 Attempt；
- args/target/manifest/trust 变化使 Approval 失效；
- approved 不自动标记成功。

## 30.6 Workspace

- 每 Intent 唯一 writable Workspace；
- 生命周期步骤不出现在模型 Tool Catalog；
- 不同 Intent 不共享可写 Workspace；
- sealed 后 LabPilot 管理写入被拒；
- evidence hold 阻止 Cleanup；
- Cleanup 不删除正式 Artifact。

## 30.7 Path 与 Root Security

- `..`、absolute path、symlink escape 被拒；
- Root device/inode/generation 变化被检测；
- overwrite content identity 变化被检测；
- 未启用 mount enforcement 时测试不得声称拦截 bind mount；
- Red write 缺少 safe-open capability 时 fail closed。

## 30.8 Process 与 Isolation

- `shell=True` 不可达；
- workdir Root 外被拒；
- env denylist 生效；
- trusted host profile 显示残余风险；
- namespace profile 缺少真实能力时不可启用；
- process group cancel 无残留或进入 unknown。

## 30.9 Secret Quarantine

- raw output 首先进入 quarantine；
- Agent/Timeline 只能看到 redacted copy；
- Scanner 失败时不生成 Observation；
- Secret event 不记录 Secret 值。

## 30.10 Unknown

- 副作用响应丢失进入 unknown/open；
- Workspace hold；
- 不自动重试；
- inspect confirmed result；
- abandoned 后保存 Evidence Package 才释放 hold；
- abandoned 不满足成功前置条件。

## 30.11 Formal Boundary

- Smoke 输出不进入正式 Metric；
- Tool 无法创建 Trial/Attempt；
- Tool Approval 不替代 Design Approval；
- `submit_formal_training` 不存在。

## 30.12 Synthetic Red Demo

- 使用 dedicated demo artifact root；
- 使用已知初始 content hash；
- 不操作真实 Checkpoint；
- 或使用测试专用合成副作用 Adapter。

# 31. 故障注入

P0 至少覆盖：

```text
FI-TOOL-001 Intent committed before first Attempt, worker crashes
FI-TOOL-002 Approval consumed, first Attempt not started, service crashes
FI-TOOL-003 Approval approved, target changes before Intent creation
FI-TOOL-004 Approval expires while user submits decision
FI-TOOL-005 Attempt writes stdout then times out
FI-TOOL-006 Side effect crosses boundary but response is lost
FI-TOOL-007 Symlink changes between validation and safe open
FI-TOOL-008 Root device/inode or generation changes
FI-TOOL-009 Workspace source manifest mismatch
FI-TOOL-010 Secret appears in stderr quarantine
FI-TOOL-011 Secret scanner crashes
FI-TOOL-012 Tool result violates output schema
FI-TOOL-013 Cleanup targets formal Artifact path
FI-TOOL-014 Cleanup runs while evidence hold active
FI-TOOL-015 Model requests unexposed infrastructure command
FI-TOOL-016 Availability generation changes after Approval
FI-TOOL-017 Policy/Tool Manifest becomes stale before execution
FI-TOOL-018 Trust Grant revoked before Attempt starts
FI-TOOL-019 Trusted host process writes source unexpectedly
FI-TOOL-020 Namespace profile requested but capability unavailable
FI-TOOL-021 Non-repeatable Attempt unknown then scheduler tries retry
FI-TOOL-022 Two workers create Attempt #1 concurrently
FI-TOOL-023 Same Intent receives duplicate Adapter callback
FI-TOOL-024 Synthetic Red Demo approval rejected/expired/approved
```

每个场景必须断言：

- 物理副作用次数；
- Approval consumption 次数；
- Intent/Attempt 数量；
- ToolCall execution/resolution；
- Workspace lifecycle/hold；
- Runtime Event；
- Agent Observation 是否生成；
- Secret 是否被隔离。

# 32. LG-CausalDiffAE 示例

## 32.1 读取训练脚本

Agent 请求：

```text
repository_read_text(
  path="train_util.py"
)
```

处理：

- Tool exposed；
- path under Repository Root；
- Secret policy pass；
- Green allow；
- 返回 content hash 和受限文本；
- Repository 内容仍是 non-authoritative content。

## 32.2 识别 `--help`

Agent 请求：

```text
entrypoint_help(
  entrypoint="scripts/train.py"
)
```

要求：

- Repository Snapshot current；
- active RepositoryExecutionTrustGrant；
- Tool Gateway 在同一 Intent 内创建并物化 Workspace；
- argv-only；
- workdir = Tool Workspace source；
- <= 30 秒；
- trusted host Profile 明确显示网络与绝对路径残余风险；
- namespace Profile 只有在真实 Capability 存在时才可声明强制断网；
- Yellow allow。

## 32.3 配置解析

Agent 生成候选配置：

```text
warmup = 10000
causal_update_freq = 4000
steps = 40000
source = z_post
pooling = mean
standardization = false
```

`config_parse_check` 的 Adapter 在同一 ToolExecutionIntent 内部生成：

```text
tool-workspace/resolved-config/candidate.json
```

该基础设施写入不作为独立模型 ToolCall，也不能覆盖仓库原配置。

## 32.4 Smoke Test

```text
bounded_smoke_test(
  entrypoint="scripts/train.py",
  config_ref="resolved-config/candidate.json",
  max_steps=2
)
```

- 最长 60 秒；
- 输出写 Tool Workspace；
- 日志可供入口/参数检查；
- 不生成正式 Trial；
- 不将 loss 写入正式 Metric；
- 不进入论文结果表。

## 32.5 覆盖 Checkpoint

Core Approval Demo 不覆盖真实 Checkpoint。测试使用：

```text
demo-artifacts/synthetic-checkpoint.bin
```

真实 `overwrite_artifact` 只在 safe-open capability 可用且用户明确选择真实 Artifact 时开放。

Policy：

```text
risk = red
require_approval
```

Approval 展示：

- Artifact identity；
- 当前 SHA-256 或已验证 content identity；
- 目标路径；
- 新内容来源；
- 不可逆风险；
- expiration。

内容在 Approval 后变化时，旧 Approval 失效。

## 32.6 正式训练

Agent 不能调用：

```text
run_training(steps=40000)
```

正确链路：

```text
ProposalRevision
→ Verifier
→ Design Approval
→ ExperimentVersion
→ formal RunBatch
→ Trial / Attempt
→ Coordinator / Local Runner
```

---

# 33. P1 扩展边界

## 33.1 Worktree 写入

Agent 开始修改代码时：

- 使用独立 Worktree；
- Repository main 默认零写入；
- patch/diff 保存；
- write Tool 仍按 Yellow/Red Policy；
- Worktree 不等于安全 Sandbox。

## 33.2 SSH

P1：

- SSH Credential Reference；
- remote root/path binding；
- host key identity；
- command plan hash；
- Approval；
- remote inspect/cancel；
- 正式 Attempt 仍由 SSH Executor，而不是 Tool。

## 33.3 Network Tool

未来外部 API Tool：

- endpoint allowlist；
- request schema；
- data classification；
- egress policy；
- credential scope；
- idempotency；
- audit；
- Red Approval。

## 33.4 强 Sandbox

如引入 Container/Bubblewrap：

- ExecutionProfile 的 `process_isolation`、`filesystem_enforcement` 与 `network_enforcement` 升级；
- Tool Contract 不变；
- Path/Capability/Approval 仍是上层约束。

---

# 34. 局部不变量

```text
TPW-INV-001
模型只能请求高层 Tool，不能决定风险、Capability、Approval 或 Workspace 生命周期。

TPW-INV-002
未注册、未 active、未进入 current Manifest 或未暴露的 Tool 不得执行。

TPW-INV-003
ToolDefinition 完全不可变；Availability 通过独立 generation 投影变化。

TPW-INV-004
Tool Schema 是 Context 中不可截断的原子单元。

TPW-INV-005
Tool 参数必须先 Schema 校验和版本化 Canonicalization，再进入 Policy。

TPW-INV-006
Secret 明文不得进入 Tool arguments、Prompt、Trace、Manifest 或普通日志。

TPW-INV-007
自由文本 Project Policy 不得由确定性 Policy Evaluator 直接解释。

TPW-INV-008
Project Policy 只能提高风险或 deny，不能降低 System Risk。

TPW-INV-009
Repository 代码执行必须有独立、active 的 RepositoryExecutionTrustGrant。

TPW-INV-010
Red Tool 必须消费一次性 Tool Execution Approval。

TPW-INV-011
Approval 必须绑定 Tool Definition、Availability Generation、Arguments、Plan、Policy 和 Preconditions。

TPW-INV-012
Approval consumption 必须与唯一 ToolExecutionIntent 原子绑定。

TPW-INV-013
Approval consumed 不等于 Attempt started、side effect crossed 或 succeeded。

TPW-INV-014
一个 ToolCall 最多一个 ToolExecutionIntent；一个 Intent 可以有多个受约束的物理 Attempt。

TPW-INV-015
Non-repeatable side effect 在 crossed/unknown 后不得自动创建新 Attempt。

TPW-INV-016
外部副作用前必须持久化 Plan、Intent、Attempt intent 和 idempotency key。

TPW-INV-017
P0 不允许模型提供任意 Shell、任意命令或 Python Eval。

TPW-INV-018
需要 Workspace 的高层 ToolCall 必须由其 Execution Intent 独占一个可写 Workspace。

TPW-INV-019
Workspace 创建、物化、写配置、Seal 和 Cleanup 是内部命令，不是模型 Tool。

TPW-INV-020
LabPilot 管理的文件写入必须位于绑定 Workspace Root；Repository 子进程的更强保证取决于真实 OS enforcement。

TPW-INV-021
Tool Workspace 不得作为正式 Experiment Result 来源。

TPW-INV-022
Smoke Test 不创建 Trial、Attempt、正式 Metric 或 Batch Summary。

TPW-INV-023
正式训练不得通过 Tool Call 启动。

TPW-INV-024
Trusted Host Process 必须明确披露网络、绝对路径和子进程残余风险。

TPW-INV-025
Profile 只有在真实 Capability 存在时才能声明 network-none、readonly-mount 或 workspace-only-write。

TPW-INV-026
Credential 只以 Reference 进入 Plan，Secret 仅在执行边界解析。

TPW-INV-027
原始 Tool 输出必须先进入 quarantine；Agent 只能看到 redacted Observation。

TPW-INV-028
Secret Scanner 失败时不得提升 raw output，也不得改写物理执行事实。

TPW-INV-029
副作用不确定时必须使用 unknown，不得猜测成功或失败。

TPW-INV-030
unknown + open 不得自动重试相同副作用，且 Workspace 必须 evidence hold。

TPW-INV-031
unknown + abandoned 只有保存最小证据包后才能释放 hold，且不满足成功前置条件。

TPW-INV-032
Workspace sealed 后 LabPilot 管理写入被拒；同一 Workspace 同时最多一个 active writer。

TPW-INV-033
Path Precondition 与 Root Identity 必须在真实 open/replace 前重新检查。

TPW-INV-034
未启用 mount enforcement 时，系统不得宣称能识别 Root 内所有 bind mount。

TPW-INV-035
真实高风险写入缺少 safe-open-at-root capability 时必须 fail closed。

TPW-INV-036
Tool/Policy Manifest stale 后，未执行动作必须重评；对象仍存在不等于仍适用。

TPW-INV-037
Repository Inspector 不得绕过 Tool Gateway 运行动态检查。

TPW-INV-038
Design Approval 不得替代 Tool Execution Approval，反之亦然。

TPW-INV-039
P0 不暴露自动依赖安装 Tool。

TPW-INV-040
API 与 Experiment Coordinator 容器不得直接 fork Repository 代码；bounded process Tool 必须走宿主机 Runner Tool Lane。

TPW-INV-041
Infrastructure Workspace 命令不得进入 Exposed Tool Manifest。

TPW-INV-042
Core Red Approval Demo 必须使用合成 Artifact 或测试专用 Adapter，不得覆盖真实科研结果。

TPW-INV-043
正式 Artifact overwrite/delete 只有在 Path/Root/Safe-open 能力完整时才能注册为 active。

TPW-INV-044
Execution Plan hash 可重复，不得作为全局唯一业务键。

TPW-INV-045
不同 ToolExecutionIntent 不得共享可写 Workspace。

TPW-INV-046
Policy Evaluator 只消费已验证的 ProjectPolicyConstraintSet。
```

# 35. V1 验收

## 35.1 Registry、Availability 与 Manifest

- Definition 版本化且不可变；
- Availability generation 可变且可审计；
- Context 只暴露原子 Tool Schema；
- Manifest/Freshness 可重建；
- stale Manifest 阻止未执行动作。

## 35.2 Green

- 可读取注册 Repository 文本和 Git metadata；
- Path escape 被拒；
- Secret 文件正文不进入 Context。

## 35.3 Yellow

- 模型只请求高层 Tool；
- 一个 Intent 独占一个 Workspace；
- Workspace 基础设施步骤不进入模型 Manifest；
- 未获 Trust Grant 时拒绝运行 Repository 代码；
- trusted host profile 显示残余风险；
- namespace profile 只有 Capability 存在时可启用；
- 60 秒超时有效；
- Smoke 输出不写正式结果。

## 35.4 Red Approval Demo

- 使用 synthetic test artifact 或测试 Adapter；
- 未批准副作用次数为 0；
- Approval 与 unique Intent 原子绑定；
- 参数/Plan/Target/Manifest 变化后旧 Approval 不可复用；
- approve/reject/expire 均有 Trace；
- 不操作真实 Checkpoint。

## 35.5 Intent/Attempt Recovery

- Approval 消费后崩溃仍恢复同一 Intent；
- Pure Read 可以创建新 Attempt；
- crossed/unknown 的 Non-repeatable Tool 不自动创建新 Attempt；
- duplicate callback 幂等。

## 35.6 Unknown、Evidence 与 Secret

- unknown/open 自动持有 Workspace evidence hold；
- abandoned 后先保存 Evidence Package 再清理；
- raw output 进入 quarantine；
- Scanner 失败不生成 Observation；
- Secret 不进入 Event payload。

## 35.7 Root 与 Path

- Root device/inode/generation 重检；
- safe-open capability 缺失时真实 Red write disabled；
- 未启用 mount enforcement 时不声称拦截 bind mount。

## 35.8 Formal Boundary

- Tool 无法创建 Trial/Attempt；
- Smoke Metric 不进入正式表；
- 正式训练只能经 Coordinator。

# 36. ADR 摘要

| ID | 决策 | 原因 | 代价 |
|---|---|---|---|
| TPW-ADR-01 | ToolDefinition 与 Availability 分离 | 保持定义不可变又允许禁用 | 多一个投影 |
| TPW-ADR-02 | 静态规则 + 结构化 Constraint Set | 确定性执行且避免 DSL | Policy Schema 需维护 |
| TPW-ADR-03 | 独立 RepositoryExecutionTrustGrant | 执行授权不混入自然语言 Policy | 首次执行多一步授权 |
| TPW-ADR-04 | 高层 Tool 独占 Workspace | 解决多 Tool 共享可写状态冲突 | Adapter 需要封装基础设施步骤 |
| TPW-ADR-05 | ToolExecutionIntent/Attempt 分离 | Approval 一次、物理调用可审计重试 | 对象数量增加 |
| TPW-ADR-06 | Trusted Host 与 Namespace Profile 分离 | 不虚假承诺 Sandbox | P0 残余风险需展示 |
| TPW-ADR-07 | 不提供任意 Shell/Python Eval | 阻断权限绕过 | 常见动作需专用 Adapter |
| TPW-ADR-08 | Root identity + Capability-backed safe open | 高风险写入可验证 | 部署能力不足时 Tool disabled |
| TPW-ADR-09 | Bind mount 默认信任部署 | realpath 无法解决 mount 边界 | 非恶意部署假设 |
| TPW-ADR-10 | Raw quarantine 三层输出 | 防 Secret 进入普通日志/Agent | 存储与清理更复杂 |
| TPW-ADR-11 | Unknown Evidence Hold | 保留对账证据 | Cleanup 延迟 |
| TPW-ADR-12 | Tool/Policy Manifest Freshness | 历史不可变且当前适用性明确 | 需要 Projection |
| TPW-ADR-13 | 正式训练不是 Tool | 防绕过 Design Approval/Coordinator | Agent 需走领域链路 |
| TPW-ADR-14 | Synthetic Red Demo | 不为演示破坏科研结果 | 需测试 Artifact Root/Adapter |
| TPW-ADR-15 | P0 不自动安装依赖 | 控制网络与环境污染 | 环境需提前准备 |

# 37. 冻结决议与依赖核验

本文已完成以下冻结决议：

1. Tool Workspace 绑定高层 ToolExecutionIntent；
2. Workspace 生命周期命令退出模型 Catalog；
3. ToolDefinition 与 Availability Generation 分离；
4. Project Policy 使用结构化 Constraint Set；
5. Repository Execution Trust 独立授权；
6. Trusted Host 与 Namespace-enforced Profile 分离；
7. ToolExecutionIntent 与 ToolExecutionAttempt 分离；
8. Root Identity、Mount Boundary 和 Safe-open Capability 明确；
9. Manifest 使用独立 Freshness Projection；
10. Unknown Workspace 使用 Evidence Hold；
11. 原始输出使用 Secret Quarantine；
12. Red Demo 使用合成对象。

冻结依赖由：

```text
docs/labpilot/architecture/05-tools-policy-and-workspace.dependencies.sha256
```

声明，并通过通用脚本：

```text
scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/05-tools-policy-and-workspace.dependencies.sha256
```

校验。任一依赖缺失、Hash 不匹配或参数错误时，脚本必须返回非零状态。

# 38. 结论

LabPilot Tool 系统的冻结链路是：

```text
模型请求高层 Tool
→ immutable Definition + Availability Generation
→ atomic Tool Manifest
→ 参数/Path/Root/Trust/Capability 解析
→ immutable Execution Plan
→ deterministic Policy Constraint Set
→ 一次性 Approval（如需）
→ unique Execution Intent
→ intent-owned Workspace
→ bounded physical Attempt
→ raw quarantine / redacted result
→ Observation 或 honest Unknown
→ reconciliation evidence retention
```

P0 的安全目标是：在用户自有、明确注册并显式授予执行信任的科研仓库上，以最小权限完成静态读取、入口/配置检查和受限 Smoke Test，同时诚实区分 Adapter 管理边界、Trusted Host Process 与真正的 OS 隔离能力。

本文不把 Workspace 包装成强 Sandbox，不允许基础设施 Workspace 命令暴露给模型，不允许 Tool 绕过 Experiment Domain 与 Coordinator 启动正式训练，也不为演示覆盖真实科研 Artifact。
