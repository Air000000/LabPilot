# LabPilot Workflow 与 Executors 架构规格

```text
Version: v0.2
Status: Approved / Frozen
Date: 2026-07-24
Scope: P0 Local Workflow/Executor + P1 SSH Contract
```

---

# 0. 文档定位

## 0.1 目的

本文定义 LabPilot 正式实验从获批设计进入真实计算之后的持久化协调与执行契约，覆盖：

- 固定 Formal Experiment Workflow；
- PostgreSQL-backed Coordinator Job；
- Batch、Trial、Attempt 的协调边界；
- Attempt Workspace 准备；
- Source、Dataset、Environment preflight；
- Host-native Local Runner；
- `labpilot-run-wrapper`；
- Submit、Inspect、Log、Cancel、Timeout、Collect；
- Crash Recovery 与 Reconciliation；
- Tool Lane 与 Formal Attempt Lane 的基础设施复用边界；
- P1 SSH Executor 的兼容接口；
- 故障注入和发布验收。

本文不重新定义 Experiment、Version、Batch、Trial、Attempt、Validation、Tool Approval、Context 或 Agent Run 的领域语义。

## 0.2 上游冻结契约

本文绑定以下上游文件：

```text
00-master-blueprint-v0.2.md
SHA-256: 16f2c39bda46881bb2ea13975db15d914a3a420b04b9dd21d45ebe315674a6fa

01-v1-scope-and-acceptance-v0.2-frozen.md
SHA-256: 7092a034a78e6ea8b9f13533521fbaa6989ad15a0a25686057a75f286e33dbd6

02-experiment-domain-v0.2-frozen.md
SHA-256: 2c998e7814a116c0999b21b6cf078dacb0fb6f770c929062480dc8ee98df328a

03-agent-harness-v0.2-frozen.md
SHA-256: fcf1996547f73bed1f07e91b34e7c91af871a7641076d43a3ec2d33482552231

04-context-and-repository-v0.2-frozen.md
SHA-256: 14c8746cfc68d7f94aeb178573d14b06daefa8b7f117a2cffa2f7368aa1ca4a2

05-tools-policy-and-workspace-v0.2-frozen.md
SHA-256: af66e2fedc4edff65c6afeed793b5ca4f296cfecb204fcd86e8c468cd2d9320c
```

冻结本文前必须使用：

```text
scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/06-workflow-and-executors.dependencies.sha256
```

重新计算上述文件摘要。

## 0.3 约束优先级

冲突时采用：

```text
01 V1 Scope
> 02 Experiment Domain
> 03 Agent Harness
> 04 Context and Repository
> 05 Tools, Policy and Workspace
> 本文
> 实现细节
```

本文不能通过“执行方便”修改以下上游不变量：

- 一个 Trial 同一时刻最多一个 active Attempt；
- `unknown` Attempt 阻止自动重提；
- `target_started` 后重新执行必须创建新 Attempt 和新 token；
- Collection failure 不覆盖 Attempt execution state；
- Validation failure 不改写成功退出的 Attempt；
- eligible Trial 重跑必须消费一次性授权；
- Tool Lane 不创建 Trial/Attempt；
- Formal Training 不能伪装成 Agent Tool；
- Coordinator 不直接写 Attempt 表；
- Agent、UI、Runner 不直接指定 Trial projection。

## 0.4 下游文档

本文为以下规格提供上游契约：

```text
07-results-validation-and-agentops.md
08-api-ui-and-deployment.md
```

`07` 可以定义 Collection 后的 Artifact、Metric、Validation 和 Summary 处理，但不得重写本文的进程身份、Launch、Cancel 或 Reconciliation 语义。

---

# 1. 核心结论

P0 采用：

```text
PostgreSQL durable state
+ fixed workflow state machine
+ due-job polling
+ generation-fenced leases
+ host-native Local Runner
+ per-attempt workspace
+ OS-supervised execution unit
+ stable protocol wrapper
+ marker-based reconciliation
```

不采用：

```text
in-memory Future as truth
Celery result backend as domain truth
arbitrary DAG engine
Temporal-compatible replay runtime
Kubernetes / SLURM scheduler
process exactly-once claim
```

P0 的核心保证是：

```text
同一 submission token 不同时存在两个可继续的 target launch generation；
控制面重启后可以从数据库、Workspace marker 和进程身份恢复；
无法证明外部事实时进入 unknown / needs_attention，而不是猜测或重启。
```

---

# 2. 设计目标与非目标

## 2.1 设计目标

1. API、Coordinator 重启不终止真实训练进程；
2. Coordinator、Runner 和 wrapper 崩溃后可以恢复，且 wrapper 崩溃不会留下无人监督的 target；
3. 重复 Job、事务重试、网络超时不产生重复 Attempt；
4. 同一 Attempt 不产生第二代 target launch；
5. PID 复用时不误认进程；
6. 日志和退出记录不依赖内存流；
7. Cancel、Timeout 和自然退出竞争有确定规则；
8. Collection、Validation 和执行状态彼此分离；
9. Local 与 SSH 使用同一抽象，不提前引入分布式调度；
10. 故障注入可以证明恢复语义。

## 2.2 非目标

P0 不提供：

- 任意用户定义 Workflow；
- 通用 DAG；
- 动态 Activity 注册；
- Workflow code replay；
- Exactly-once OS process；
- GPU 资源调度器；
- 抢占式多租户公平调度；
- 容器编排；
- 自动跨主机迁移 running process；
- 训练进程 checkpoint 恢复；
- SSH 自动安装环境；
- SLURM、Kubernetes、Ray、Airflow 集成。

## 2.3 V1 规模假设

P0 默认：

```text
单用户
单 Project 主流程
单 PostgreSQL
单 Coordinator Worker
单 Local Runner host
低并发 Attempt
固定最大并发数
```

模型必须允许将来增加 Worker，但 P0 不为大规模调度优化。

---

# 3. 四层状态严格分离

## 3.1 Experiment Domain State

权威对象：

```text
ExperimentVersion
RunBatch
Trial
Attempt
AttemptLaunch
ValidationReport
BatchSummary
```

由 Experiment Domain Service 写入。

## 3.2 Workflow / Coordinator State

权威对象：

```text
FormalExperimentWorkflow
WorkflowStepRun
CoordinatorJob
CoordinatorLease
CancellationRequest
ReconciliationCase
```

它描述“系统接下来需要做什么”，不替代 Attempt 物理状态。

## 3.3 Executor / Process State

权威来源：

```text
AttemptExecutionRequest
AttemptHostExecutionPlan
Source/Dataset/Environment Execution Binding
AttemptWorkspace
Attempt Supervision Unit identity/state
Workspace runtime markers
wrapper identity
target identity
exit record
ExecutorObservation + Evidence Manifest
```

它们描述外部执行实际发生了什么。Runner claim 或 heartbeat 只是协调信号，不是 target 状态真相。

## 3.4 Result / Validation State

权威对象由 `07` 定义：

```text
Collection record
Artifact registration
Metric parsing
Validation report
Summary
```

## 3.5 禁止混合

禁止：

- Workflow `completed` 自动表示 Attempt `succeeded`；
- target exit code 0 自动表示 Trial `eligible`；
- Collection 失败把 Attempt 改成 `failed`；
- Coordinator Job 失败把 Trial 改成 `execution_exhausted`；
- UI projection 被用于进程恢复；
- Runtime Event 替代 marker 或 Process Identity。

---

# 4. 总体架构

```text
Web / API
   │ Formal run request / cancel / inspect
   ▼
Experiment Application Service
   │ domain command + outbox
   ▼
PostgreSQL
   ├─ Experiment Domain tables
   ├─ Workflow / Coordinator jobs
   ├─ Attempt executor jobs
   ├─ Logical Request / Host Execution Plan
   ├─ Executor observations / Reconciliation cases
   └─ Outbox / Runtime events

Coordinator Worker
   ├─ claim due CoordinatorJob
   ├─ invoke Domain Services
   ├─ enqueue prepare/submit/inspect/collect jobs
   └─ schedule next durable wakeup

Host-native Local Runner
   ├─ Formal Attempt Lane
   └─ Tool Lane

Formal Attempt Lane
   ├─ prepare workspace and execution bindings
   ├─ create immutable Host Execution Plan
   ├─ revalidate bindings and capabilities
   ├─ create OS Attempt Supervision Unit
   ├─ start labpilot-run-wrapper under Unit
   ├─ inspect Unit / process identity / markers
   ├─ cancel or timeout entire execution group
   ├─ read logs
   └─ collect execution output manifest

Attempt Supervision Unit
   ├─ contain wrapper + target descendants
   ├─ own wall timeout and stop semantics
   ├─ fail-stop on wrapper abnormal exit
   └─ expose stable unit identity/state

labpilot-run-wrapper
   ├─ validate Request + Host Plan
   ├─ acquire token lock
   ├─ persist wrapper_started / target_started
   ├─ start target from immutable Host Plan
   ├─ write logs and normal exit marker
   └─ assist graceful cancellation
```

# 5. 组件职责

## 5.1 Experiment Application Service

负责正式实验入口：

- 校验已冻结 Experiment Version；
- 创建或幂等获取 Run Batch；
- 启动 Formal Workflow；
- 请求取消；
- 查询执行总览。

它不直接调用宿主机进程接口。

## 5.2 Experiment Coordinator

Coordinator 是 durable orchestrator，不是 process supervisor。

职责：

- claim Coordinator Job；
- 推进固定 Workflow；
- 请求 Domain Service 创建 Trial/Attempt；
- 创建逻辑 AttemptExecutionRequest；
- 调度 prepare/submit/inspect/collect/reconcile；
- 保存 Executor Observation；
- 根据固定 Evidence Rule 请求领域状态推进；
- 创建 Retry、Validation 和 Finalization Job。

Coordinator 不直接写 Attempt、Trial 或 Batch 表。

## 5.3 Local Runner

Local Runner 是宿主机服务，负责真实主机动作：

- claim Formal Attempt Executor Job；
- 探测并声明宿主机监督与文件系统能力；
- 创建和验证 Attempt Workspace；
- 物化 frozen source；
- 捕获 Source、Dataset、Environment Binding；
- 生成不可变 AttemptHostExecutionPlan；
- 创建、查询和停止 Attempt Supervision Unit；
- inspect supervision unit、wrapper、target 和 marker；
- 读取日志；
- 收集 output manifest；
- cleanup 临时内容。

Runner 不拥有 Experiment Domain 状态。

## 5.4 Attempt Supervision Unit

Attempt Supervision Unit 是正式 Attempt 的物理生命周期监督边界，而不是普通数据库 Lease。

它必须由宿主机提供可证明的能力：

```text
process_tree_containment
kill_entire_execution_group
durable_timeout_enforcement
queryable_unit_identity
wrapper_exit_fail_stop
```

Linux P0 可以由 systemd transient service 或受控 cgroup v2 backend 实现，但领域规格只绑定能力，不绑定单一命令行实现。

职责：

- 将 wrapper 与 target 纳入同一受控执行组；
- 独立于 Coordinator、Runner Job 和 wrapper 进程执行 wall timeout；
- 接受 cancel/stop；
- 在 wrapper 异常退出时 fail-stop 整个执行组；
- 提供稳定、可查询的 supervision unit identity；
- 防止 target descendant 脱离执行组成为无人监督进程。

不具备这些能力的 Executor 不得注册为 Formal Attempt Executor。

## 5.5 `labpilot-run-wrapper`

wrapper 是稳定的启动与 Marker 协议进程，但不是唯一生命周期监督者。

职责：

- token-level launch lock；
- 校验逻辑 Request 与 Host Execution Plan；
- 原子写入 wrapper/target/exit marker；
- 创建 target process group/session；
- 打开 stdout/stderr；
- 记录 PID、start ticks、boot ID 和 command identity；
- 协助 graceful cancel；
- 正常路径写入 exit record。

wrapper 不访问 Agent、Proposal 或 Validation。wrapper 异常退出后，OS Supervision Unit 必须终止整个执行组。

## 5.6 Executor Adapter

统一接口：

```text
prepare
submit
inspect
read_logs
cancel
collect
cleanup
```

P0 实现 `LocalExecutorAdapter`；P1 实现具备远端等价监督能力的 `SSHExecutorAdapter`。

## 5.7 Workspace Service

Attempt Workspace 与 Tool Workspace 分离。

Attempt Workspace Service 负责：

- 分配唯一目录；
- 物化 frozen source；
- 写入 manifest；
- 路径与 Root identity 校验；
- Source Seal；
- retention；
- 临时清理。

## 5.8 Reconciliation Service

负责比较：

```text
Database expectation
vs Host Execution Plan
vs Supervision Unit
vs Workspace marker
vs Process Identity
```

并使用固定 `evidence_rule_id` 形成 Reconciliation Decision。

# 6. 固定 Formal Experiment Workflow

## 6.1 Workflow 类型

P0 只注册：

```text
formal_experiment_v1
```

不允许用户定义新类型。

## 6.2 主阶段

```text
await_design_approval
→ prepare_inputs
→ create_trials
→ submit_attempts
→ inspect_attempts
→ collect_results
→ validate_results
→ finalize_batch
```

Workflow 状态：

```text
pending
running
needs_attention
cancelled
failed
completed
completed_with_invalid_trials
closed_with_unresolved_execution
```

`needs_attention` 是非终态阻塞状态；`closed_with_unresolved_execution` 是人工停止调查后的终态。

## 6.3 两种合法入口

### 入口 A：Approval 前创建

```text
FormalExecutionRequest
→ Workflow(await_design_approval)
→ Design Approval / Version Frozen
→ bind version
→ create Batch
```

### 入口 B：已有 Frozen Version

```text
Create Formal Batch
→ Workflow 从 prepare_inputs 开始
```

P0 产品界面可以只暴露入口 B。

## 6.4 Workflow phase 不是进程事实

`current_phase` 表示当前最早尚未完成的协调阶段，不是 Attempt、Runner、target 或 Trial eligibility 状态。

Retry 期间 Workflow 可以仍位于执行协调阶段，同时为新 Attempt 生成 prepare/submit 子 Job。恢复必须读取权威对象，不能只看 phase。

## 6.5 阶段推进条件

### await_design_approval → prepare_inputs

要求 Proposal Revision approved、Experiment Version Frozen、Version identity current，并已创建或可幂等创建 Formal Batch。

### prepare_inputs → create_trials

要求 Version Source/Dataset reference 可解析、至少一个符合 Formal Profile 的 Executor 可用、Workflow 未取消。这里只进行 Batch 级预检查。

### create_trials → submit_attempts

要求 Trial expansion 完成、正式 replicate 约束满足，并可按领域规则创建初始 Attempt。

### submit_attempts → inspect_attempts

至少一个 Attempt 已进入提交观察，或全部 Trial 已在提交前明确失败/取消。

### inspect_attempts → collect_results

要求所有 Trial 处于以下之一：

```text
有 succeeded Attempt
execution_exhausted
cancelled
```

存在 active 或 unknown Attempt 时不得进入最终 Collection gate。unknown 必须使 Workflow 进入 `needs_attention`。

### collect_results → validate_results

要求所有 succeeded Attempt 均已产生有效 Collection Manifest；Collection 协调失败已解决。Artifact 注册和 Metric 解析不属于本阶段。

### validate_results → finalize_batch

要求可验证 Trial 已完成 Validation，invalid/exhausted/cancelled Trial 已明确分类，且不存在 unknown Trial。

### finalize_batch → terminal

要求 Batch projection 已重算，deterministic summary 已生成或明确不可生成，并且不存在 unresolved execution。

## 6.6 Retry 循环

Retry 不回退整个 Workflow：

```text
inspect Attempt terminal failure
→ classify failure
→ Domain Service checks retry policy
→ Create new Attempt
→ enqueue prepare/submit jobs
→ Workflow remains in execution coordination phase
```

新 Attempt 是新领域实体。

## 6.7 needs_attention 与终态

```text
needs_attention
    存在 unknown Attempt 或无法自动解决的 Reconciliation Case；
    不自动创建新 Attempt，不进入正式 Summary。

completed
    所有正式 Trial eligible，Summary 成功。

completed_with_invalid_trials
    没有 unknown execution，但存在明确 invalid/exhausted/cancelled Trial。

failed
    Workflow 基础设施自身无法继续，且不能形成其他可审计关闭状态。

cancelled
    取消已传播，所有 active execution 均已确认关闭；不能包含 unknown Attempt。

closed_with_unresolved_execution
    人工停止调查；Attempt 仍 unknown，Trial 保持 needs_attention/inconclusive，
    不纳入正式 Summary，并永久保留潜在残留进程或重复副作用警告。
```

`completed_with_invalid_trials` 不得包含 unknown。Batch 最终领域状态仍由 Experiment Domain 投影。

# 7. Workflow 核心对象

## 7.1 FormalExperimentWorkflow

```text
FormalExperimentWorkflow
- workflow_id
- workflow_type
- workflow_schema_version
- project_id
- experiment_id
- formal_execution_request_id nullable
- proposal_revision_id nullable
- experiment_version_id nullable
- run_batch_id nullable
- current_phase
- workflow_status
- cancellation_requested_at nullable
- terminal_reason nullable
- next_wakeup_at nullable
- lease_generation
- lease_owner nullable
- leased_until nullable
- row_version
- created_at
- updated_at
```

约束：

```text
UNIQUE(workflow_type, run_batch_id) WHERE run_batch_id IS NOT NULL
```

`CoordinatorJob.run_at` 是权威调度事实；`next_wakeup_at` 只是可从 pending Jobs 重算的查询投影。禁止只更新 `next_wakeup_at` 而不创建 durable Job。

## 7.2 WorkflowStepRun

每次执行固定阶段时创建不可变/追加记录：

```text
WorkflowStepRun
- workflow_step_run_id
- workflow_id
- phase
- phase_occurrence
- status
- input_state_hash
- started_at
- ended_at nullable
- outcome_code nullable
- error_code nullable
- coordinator_job_id
```

约束：

```text
UNIQUE(workflow_id, phase, phase_occurrence)
```

phase 可以因显式恢复或 Retry coordination 多次出现，但旧记录不覆盖。

## 7.3 WorkflowLease

Lease 语义使用递增 generation：

```text
lease_generation = previous + 1
```

所有推进 Workflow 的写入必须带：

```text
workflow_id
lease_generation
row_version
leased_until > database_current_time
```

随机 token 不能替代 generation fencing。

## 7.4 WorkflowCancellationRequest

```text
WorkflowCancellationRequest
- cancellation_request_id
- workflow_id
- scope
- reason_code
- human_note nullable
- requested_by
- requested_at
- status
```

`scope`：

```text
workflow
batch
trial
attempt
```

领域取消请求必须通过 Domain Service 产生对应事实。

---

# 8. Coordinator Job 模型

## 8.1 CoordinatorJob

```text
CoordinatorJob
- coordinator_job_id
- workflow_id
- job_type
- subject_type
- subject_id
- dedupe_key
- payload_ref
- payload_hash
- status
- run_at
- attempt_count
- max_attempts
- lease_owner nullable
- lease_generation
- leased_until nullable
- last_error_code nullable
- created_at
- updated_at
```

状态：

```text
pending
leased
running
completed
failed
cancelled
```

## 8.2 Job 类型

```text
advance_workflow
bind_approved_version
prepare_batch_inputs
expand_trials
create_initial_attempt
prepare_attempt
submit_attempt
inspect_attempt
cancel_attempt
reconcile_attempt
collect_attempt
request_validation
finalize_batch
cleanup_attempt_temporary
```

## 8.3 Dedupe Key

每个逻辑动作必须有稳定 key，例如：

```text
prepare-attempt:<attempt_id>
submit-attempt:<attempt_id>:<submission_token>
inspect-attempt:<attempt_id>:<observation_generation>
collect-attempt:<attempt_id>:<collection_schema_version>
finalize-batch:<batch_id>:<summary_policy_identity>
```

约束：

```text
UNIQUE(dedupe_key)
```

如果动作允许多次周期执行，key 必须包含明确 occurrence/generation，而不能依赖随机 UUID。

## 8.4 Job Claim

Worker claim：

```text
SELECT due job
→ atomic status/lease generation update
→ commit
→ execute outside transaction
→ re-lock and finalize with generation fencing
```

`SKIP LOCKED` 只用于领取优化，不是业务真相。

## 8.5 Job、Workflow 投影与领域事务

以下必须在同一 PostgreSQL 事务：

- Domain state change；
- Workflow projection update；
- critical event/outbox enqueue；
- next CoordinatorJob enqueue；
- 对应 `next_wakeup_at` 查询投影更新。

外部 Executor 调用不得放在数据库事务内部。

`CoordinatorJob.run_at` 是唯一调度权威。投影丢失时从 `pending/leased` Jobs 重算，不允许 Coordinator 仅凭 `next_wakeup_at` 执行动作。

## 8.6 Backoff

P0 使用固定、版本化的 backoff profile：

```text
5s
15s
30s
60s
120s max
```

不同 Job 类型可以绑定不同 profile，但不能由 LLM 动态生成。

## 8.7 Job failure 不等于 Attempt failure

CoordinatorJob 失败只表示协调动作失败。

例如：

```text
inspect API timeout
```

不能直接把 Attempt 标记为 failed；应重试 inspect 或进入 Reconciliation。

---

# 9. Formal Attempt Executor Job

## 9.1 与 CoordinatorJob 分离

`CoordinatorJob` 推进协调；`AttemptExecutorJob` 由 Local/SSH Runner 执行宿主机动作。

```text
AttemptExecutorJob
- executor_job_id
- attempt_id
- attempt_launch_id
- job_type
- executor_registration_id
- request_ref
- request_hash
- host_execution_plan_ref nullable
- host_execution_plan_hash nullable
- supervision_unit_ref nullable
- dedupe_key
- status
- run_at
- claim_generation
- lease_owner nullable
- leased_until nullable
- result_ref nullable
- result_hash nullable
- last_error_code nullable
- created_at
- updated_at
```

Prepare Job 可以尚无 Host Plan；Submit/Inspect/Cancel Job 必须绑定当前 Plan 或 Unit identity。

## 9.2 Job 类型

```text
prepare
submit
inspect
read_logs
cancel
collect
cleanup
```

## 9.3 Tool Lane 分离

Tool Lane 使用：

```text
ToolExecutorJob
ToolExecutionIntent
ToolExecutionAttempt
ToolWorkspace
```

Formal Attempt Lane 使用：

```text
AttemptExecutorJob
AttemptExecutionRequest
AttemptHostExecutionPlan
AttemptSupervisionUnitIdentity
AttemptLaunch
ExecutorObservation
AttemptWorkspace
```

可以复用 claim/lease、host identity reader、safe file/manifest、日志和底层进程库；不得复用 Tool Approval、ToolExecutionIntent、ToolWorkspace 或 Tool 状态机。Formal Supervision Unit 语义也不得被 Tool Lane 自动继承。

## 9.4 Runner Claim

Runner 对 Job 使用递增 `claim_generation`。

Job 完成写回要求：

```text
executor_job_id
claim_generation
leased_until > database_current_time
row_version match
```

旧 Runner 结果不能推进当前 Job。需要审计时保存为不可变：

```text
DetachedExecutorJobResult
- detached_executor_job_result_id
- executor_job_id
- observed_claim_generation
- current_claim_generation
- result_ref
- result_hash
- received_at
- detach_reason
```

Detached Result 只能作为 Reconciliation Evidence，不能直接推进 Workflow 或 Attempt。

---

# 10. Executor Registration 与能力

## 10.1 ExecutorRegistration

```text
ExecutorRegistration
- executor_registration_id
- executor_type
- executor_name
- host_identity
- runner_version
- status
- capability_profile_identity
- supervision_capability_identity
- runtime_filesystem_capability_identity
- workspace_root_registration_id
- heartbeat_at
- registration_generation
- created_at
```

P0 为 local，P1 为 ssh。

## 10.2 Host Identity

Linux P0 至少保存：

```text
machine_id_hash
boot_id
hostname_label
os_identity
architecture
```

不能仅用 hostname 判断进程身份。

## 10.3 Supervision Capability

```text
AttemptSupervisionCapability
- backend_type
- process_tree_containment
- kill_entire_execution_group
- durable_timeout_enforcement
- queryable_unit_identity
- wrapper_exit_fail_stop
- capability_probe_version
- capability_identity
```

Formal Attempt Executor 必须使上述五项为 true。

## 10.4 Runtime Filesystem Capability

```text
AttemptRuntimeFilesystemCapability
- filesystem_type
- mount_identity
- supports_process_shared_lock
- supports_atomic_same_directory_rename
- supports_file_fsync
- supports_directory_fsync
- supports_durable_create
- supports_stable_inode_identity
- capability_probe_version
- capability_identity
```

Formal Submit 至少要求：

```text
supports_process_shared_lock = true
supports_atomic_same_directory_rename = true
supports_file_fsync = true
supports_directory_fsync = true
supports_durable_create = true
```

P0 Marker Protocol 只支持通过运行时能力探测的 POSIX 文件系统。不能仅凭文件系统名称推断可靠性。

## 10.5 General Capability Profile

至少声明：

```text
supports_prepare
supports_submit
supports_inspect
supports_cancel
supports_log_read
supports_collect
supports_gpu_observation
supports_readonly_source_enforcement
```

## 10.6 Availability

Executor 可用性是独立投影：

```text
online
degraded
offline
disabled
```

历史 Attempt 不因 Executor offline 被修改。

## 10.7 Heartbeat

Runner heartbeat 只说明 Runner 服务活跃，不证明 Supervision Unit 或 target 状态。

# 11. AttemptExecutionRequest 与 Host Execution Plan

## 11.1 AttemptExecutionRequest：逻辑执行意图

Coordinator 在外部动作前创建不可变逻辑 Request：

```text
AttemptExecutionRequest
- attempt_execution_request_id
- attempt_id
- attempt_launch_id
- experiment_version_identity
- trial_condition_hash
- submission_token
- source_snapshot_identity
- source_reference_manifest_digest
- dataset_snapshot_identity
- environment_expectation_identity
- resolved_parameters_ref
- resolved_parameters_hash
- logical_entrypoint
- logical_argv
- logical_workdir_relative_path
- allowed_environment_names
- credential_reference_ids
- timeout_policy
- cancel_policy
- expected_output_contract_ref
- formal_execution_profile_identity
- request_schema_version
- request_hash
- created_at
```

约束：

```text
UNIQUE(attempt_id)
UNIQUE(submission_token)
```

逻辑 Request 不保存由某台宿主机临时解析出的 executable 绝对路径、设备或环境实现。

## 11.2 Request 不可变性

创建后不得修改参数、Source、Dataset、Environment Expectation、Timeout、Expected Output 或 Formal Profile。科研语义变化遵循新 Version/Trial；物理重跑创建新 Attempt。

## 11.3 argv-only

逻辑命令只包含 entrypoint 和 typed argv，不允许 Shell 字符串、`shell=True`、模型提供的管道或重定向。

## 11.4 AttemptHostExecutionPlan：宿主机计划

Prepare 后创建不可变：

```text
AttemptHostExecutionPlan
- host_execution_plan_id
- attempt_execution_request_id
- plan_generation
- plan_status
- executor_registration_id
- executor_registration_generation
- workspace_identity
- resolved_executable_path
- executable_identity
- resolved_argv
- resolved_workdir
- resolved_nonsecret_environment
- credential_reference_ids
- source_execution_binding_id
- dataset_execution_binding_id
- environment_execution_binding_id
- observed_environment_hash
- supervision_profile_identity
- runtime_filesystem_capability_identity
- resource_limit_identity
- plan_schema_version
- plan_hash
- superseded_by_plan_id nullable
- created_at
```

状态：

```text
prepared
superseded
submitted
```

约束：

```text
UNIQUE(attempt_execution_request_id, plan_generation)
```

同一 Request 同时最多一个 `prepared` Plan。Plan 在 target 从未启动且旧 Plan 尚未 submitted 时可以被新一代 Plan supersede；一旦 submitted/target_started，不得替换。

## 11.5 三个执行绑定

```text
SourceExecutionBinding
- source_manifest_digest
- source_directory_identity
- source_seal_mode
- source_enforcement_capability
- binding_hash

DatasetExecutionBinding
- dataset_snapshot_identity
- reproducibility_level
- observed_reference_identity
- access_mode
- binding_hash

EnvironmentExecutionBinding
- environment_expectation_identity
- python_executable_identity
- environment_profile_identity
- framework_or_lock_identity
- device_class
- driver_runtime_identity nullable
- binding_hash
```

它们进入 Host Plan，并在 Submit/Wrapper 启动前重验。

## 11.6 Secret

Secret 仅以 Credential Reference 进入 Request/Plan，在 Runner 边界解析。Secret 值不得进入 Hash、Marker、Event 或日志。

# 12. Attempt Workspace

## 12.1 数据结构与身份

```text
AttemptWorkspace
- attempt_workspace_id
- attempt_id
- root_registration_id
- relative_path
- workspace_identity
- lifecycle_status
- source_manifest_digest nullable
- runtime_manifest_digest nullable
- evidence_retention_status
- created_at
- sealed_at nullable
- cleaned_at nullable
```

约束：

```text
UNIQUE(attempt_id)
UNIQUE(root_registration_id, relative_path)
```

身份固定为：

```text
SHA-256(
  "labpilot-attempt-workspace:v1\n"
  + canonical_json({
      "attempt_id": ...,
      "submission_token": ...,
      "root_registration_identity": ...,
      "relative_path": ...,
      "experiment_version_identity": ...,
      "source_snapshot_identity": ...,
      "formal_execution_profile_identity": ...
    })
)
```

绝对宿主机路径只用于定位，不进入可迁移业务身份。

## 12.2 目录布局

```text
attempts/<attempt_id>/
├─ source/
├─ resolved-config/
├─ logs/
│  ├─ stdout.log
│  └─ stderr.log
├─ outputs/
├─ runtime/
│  ├─ launch-request.json
│  ├─ launch.lock
│  ├─ wrapper-started.json
│  ├─ target-started.json
│  ├─ wrapper-heartbeat.json
│  ├─ cancel-request.json
│  ├─ signal-record.json
│  ├─ target-exited.json
│  └─ exit.json
├─ collection/
└─ manifest.json
```

## 12.3 Lifecycle

```text
allocated
→ materializing
→ preflighting
→ ready
→ active
→ sealed
→ cleanup_eligible
→ cleaned
```

异常状态：

```text
failed
retention_hold
```

## 12.4 Source

正式执行：

```text
frozen clean commit
→ git archive
→ safe extraction
→ materialized source manifest
→ compare with Version reference manifest
```

Runner 只能从：

```text
workspace/source/
```

执行，禁止回退到主工作树。

## 12.5 Source 安全

- 拒绝绝对路径；
- 拒绝 `..` traversal；
- 校验 symlink target；
- 检测 gitlink/submodule；
- 检测 Git LFS pointer；
- P0 对未支持依赖 fail closed；
- manifest 记录类型、模式、内容摘要。

## 12.6 Resolved Config

resolved config 必须来自 Version/Trial 的确定性解析，不允许 Runner 自行选择参数。

## 12.7 Formal outputs

`outputs/` 仅作为候选输出根；文件出现不等于已注册 Artifact。

## 12.8 Cleanup

P0：

- 不自动删除正式输出；
- 不删除 unresolved/unknown evidence；
- 可清理 materialization 临时文件；
- Cleanup 必须是幂等 Job；
- Collection/Validation 所需文件存在前不得清理。

---

# 13. Prepare、Preflight 与启动前重验

## 13.1 Prepare 主流程

```text
claim prepare job
→ validate Executor registration/capabilities
→ allocate/check Workspace
→ validate Root and filesystem capability
→ materialize frozen source
→ compute/compare source manifest
→ seal source
→ write resolved config
→ capture Dataset/Environment observations
→ resolve executable/argv/workdir
→ create Source/Dataset/Environment Execution Bindings
→ create immutable AttemptHostExecutionPlan
→ write attempt manifest
→ mark Workspace ready
→ return PrepareResult
```

## 13.2 PrepareResult

```text
PrepareResult
- attempt_id
- workspace_identity
- host_execution_plan_id nullable
- host_execution_plan_hash nullable
- source_execution_binding_hash nullable
- dataset_execution_binding_hash nullable
- environment_execution_binding_hash nullable
- preparation_status
- rejection_codes
- prepared_at
```

## 13.3 Submit 前 TOCTOU 重验

Submit 前和 wrapper 启动前必须重验：

### Source

- source manifest digest；
- directory identity；
- source seal；
- readonly enforcement capability。

若只有 pre/post digest observation，不得声称 OS 强制只读。退出后发现 source 被修改时，Attempt 的进程终态保持真实，但 `07` Validation 必须失败。

### Dataset

- strict/strong：重新核对 stable identity；
- best_effort：保存启动时 Observation 和复现警告；
- Version 要求 strict 而无法稳定绑定：拒绝 Submit。

### Environment

重新验证 executable、environment profile、framework/lock、device class 和 formal profile。变化使当前 Host Plan 失效；不得在 wrapper 内自由重新解析。

若 target 确定从未启动且变化不改变科研语义，可以 supersede 旧 Host Plan 并重新 Prepare；否则关闭 Attempt 并按领域规则处理。

## 13.4 Preflight rejection 与 Failure Classification

| Prepare rejection | Failure class | 默认自动重试 |
|---|---|---|
| temporary staging I/O | `temporary_filesystem_failure` | 是 |
| executable missing | `preparation_environment_failure` | 否 |
| environment requirement unsatisfied | `preparation_semantic_rejection` | 否 |
| dataset adapter temporary unavailable | `temporary_dataset_access_failure` | 有条件 |
| dataset identity mismatch | `dataset_binding_mismatch` | 否 |
| source manifest mismatch | `source_binding_mismatch` | 否 |
| unsupported submodule/LFS | `unsupported_source_dependency` | 否 |
| supervision/filesystem capability missing | `executor_capability_rejection` | 否 |
| invalid workdir/argv | `execution_plan_invalid` | 否 |

Preflight rejection 表示 target 从未启动，不得伪装成 target command failure 或 Validation failure。

## 13.5 Prepare 幂等与崩溃

相同 Request、Workspace 和有效 Plan 返回现有结果。materialization 中崩溃时验证 staging，删除不完整 staging 后重建；正式 `source/` 只通过原子目录切换发布。已 active Workspace 不重新 materialize。

# 14. Launch 请求与 Marker Protocol

## 14.1 文件系统前提与原子写入

Formal Submit 前必须绑定有效 `AttemptRuntimeFilesystemCapability`。

关键 marker：

```text
write temporary file
→ fsync file
→ atomic same-directory rename
→ fsync runtime directory
```

`runtime/launch.lock` 固定使用 process-shared advisory lock（Linux P0 为 `flock` 类语义）。Host reboot 后锁会释放，但是否允许启动仍由 Marker、Supervision Unit 和 Evidence Rule 决定。

## 14.2 `launch-request.json`

```text
schema_version
attempt_id
attempt_launch_id
submission_token
request_hash
host_execution_plan_hash
workspace_identity
supervision_profile_identity
runtime_filesystem_capability_identity
created_at
```

## 14.3 `supervision-unit-started.json`

```text
schema_version
attempt_id
submission_token
host_execution_plan_hash
supervision_unit_identity
host_identity
boot_id
started_at
```

## 14.4 `wrapper-started.json`

```text
schema_version
attempt_id
submission_token
host_execution_plan_hash
supervision_unit_identity
host_identity
boot_id
wrapper_pid
wrapper_process_start_ticks
wrapper_executable_identity
started_at
```

## 14.5 `target-started.json`

```text
schema_version
attempt_id
submission_token
host_execution_plan_hash
supervision_unit_identity
host_identity
boot_id
target_pid
target_process_start_ticks
process_group_id
executable_identity
started_at
```

## 14.6 `target-exited.json` 与 `exit.json`

记录：

```text
attempt_id
submission_token
host_execution_plan_hash
supervision_unit_identity
exit_observed_at
exit_code nullable
termination_signal nullable
timeout_triggered
cancel_requested
process_identity_verified
log final offsets
runtime duration
signal history
marker hashes
```

Wrapper 正常路径写 `exit.json`；wrapper 异常退出时，Supervision Unit 与 Reconciliation Evidence 补充物理终止事实，但不得伪造未知 exit code。

## 14.7 Marker identity

每个 Marker 必须绑定：

```text
attempt_id
submission_token
request_hash
host_execution_plan_hash
workspace_identity
supervision_unit_identity（创建后）
```

# 15. Supervision Unit 与 Wrapper Launch Protocol

## 15.1 Launch lock 与启动顺序

Runner 首先检查现有 Marker/Supervision Unit，再获取 `runtime/launch.lock`。固定顺序：

```text
validate Request/Host Plan/Bindings
→ create Attempt Supervision Unit
→ persist supervision unit identity
→ start wrapper as supervised main process
→ wrapper acquires/validates launch lock
→ wrapper starts target
```

重复调用发现现有 Unit 或 Marker时，不得创建第二个 Unit/target，必须进入 inspect。

## 15.2 Wrapper 启动前检查

1. 校验 launch request、Request hash、Host Plan hash；
2. 重验 Source/Dataset/Environment Binding；
3. 检查 `target-started.json`；
4. 检查 Cancel 和 Deadline；
5. 原子写 `wrapper-started`。

## 15.3 Target 启动

Wrapper 使用 Host Plan 中已解析的 executable/argv/workdir/environment，不得重新搜索环境。它创建独立 process group/session，打开持久化日志，读取真实 PID/start ticks，并原子写 `target-started`。

## 15.4 `target_started` 后不可重放

只要 `target-started.json` 已可靠持久化：

```text
same submission token relaunch = forbidden
```

重新执行必须创建新 Attempt。

## 15.5 Wrapper fail-stop

Wrapper 是 Supervision Unit 的受监督主进程：

```text
wrapper abnormal exit
→ OS Supervision Unit terminates entire execution group
→ descendants residual check
→ same token never relaunches after target_started
→ Reconciliation records external_termination or unknown evidence
```

Timeout owner 和 Cancel owner 始终是 Supervision Unit/其控制接口，不因 wrapper 消失而丢失。

## 15.6 崩溃窗口

### Supervision Unit 尚未创建

无 Marker、无 Unit、无匹配进程时，可以继续相同逻辑 Submit。

### Unit/wrapper 已启动但 target 未启动

只有 Evidence Rule 确认没有 target、Unit 已终止且 lock 释放时，才可继续相同 Request；否则进入 unknown。

### target_started 后 DB 未更新

通过 Marker、Unit 和 Process Identity Reconciliation 恢复，绝不重新启动。

### Target 启动但 marker 写入失败

Wrapper 必须请求 Unit fail-stop；无法确认时为 unknown。系统不声称消除所有 OS 崩溃窗口。

## 15.7 保证边界

P0 保证：

```text
at-most-one-active continuable target per submission token
no intentionally unsupervised formal target process
```

不声称 OS process exactly-once。

# 16. Supervision 与 Process Identity

## 16.1 AttemptSupervisionUnitIdentity

```text
AttemptSupervisionUnitIdentity
- executor_registration_id
- executor_registration_generation
- host_identity
- boot_id
- backend_type
- backend_unit_name
- backend_unit_generation
- host_execution_plan_hash
```

## 16.2 LocalProcessIdentity

```text
LocalProcessIdentity
- executor_registration_id
- host_identity
- boot_id
- pid
- process_start_ticks
- process_group_id
- executable_identity
- host_execution_plan_hash
```

## 16.3 PID 复用

Inspect 必须比较 boot ID、PID、start ticks、executable/plan identity；`kill(pid, 0)` 单独无效。

## 16.4 Boot change

Marker boot ID 与当前不同意味着旧进程不可能仍在当前 boot。可按 Evidence Rule 记录 host reboot termination，exit code 未知。

## 16.5 Wrapper、Target 与 Unit 分离

分别记录三类身份。Wrapper 不存在不等于 target 自然退出；但 Formal P0 的 `wrapper_exit_fail_stop` 能力要求 Unit 最终终止整个执行组。

## 16.6 Process group 与 descendants

Cancel/Timeout 针对 Supervision Unit 的整个执行组。完成条件包含 descendant residual check，而不是只检查 target PID。

# 17. Submit 协议

## 17.1 Submit 前置条件

- Attempt 处于允许的 preparation/submission 状态；
- Workspace ready；
- 当前 Host Execution Plan 为 prepared；
- Request、Plan、token、Workspace identity 一致；
- Source/Dataset/Environment Binding 重验成功；
- Executor registration generation 未变化；
- Supervision 与 Filesystem Capability 仍有效；
- 没有已有 target_started；
- 未取消且 deadline 未过。

## 17.2 Runner Submit

```text
claim submit job
→ inspect existing Unit/markers first
→ if existing launch, return observation
→ revalidate bindings/capabilities
→ create supervised execution unit
→ start wrapper under unit
→ wait only for bounded supervision/wrapper acknowledgement
→ return SubmitResult
```

Runner 不等待训练结束。

## 17.3 SubmitResult

```text
SubmitResult
- attempt_id
- submission_token
- host_execution_plan_hash
- supervision_unit_identity nullable
- observed_launch_phase
- wrapper_identity nullable
- target_identity nullable
- result_code
- marker_manifest_hash
- observed_at
```

## 17.4 Coordinator 投影

Coordinator 保存 Observation 后，通过 Domain Service 单调推进 Launch Phase。

## 17.5 Submit timeout

Submit ack 超时后不得再次创建 Unit。必须 inspect Unit、lock、Marker 和 Process Identity；无法确认时创建 Reconciliation Case。

# 18. Inspect、ExecutorObservation 与 Evidence Matrix

## 18.1 ExecutorObservation

```text
ExecutorObservation
- executor_observation_id
- attempt_id
- attempt_launch_id
- observation_sequence
- executor_registration_id
- source
- observed_launch_phase
- supervision_unit_state
- supervision_unit_identity nullable
- wrapper_process_state
- target_process_state
- wrapper_identity nullable
- target_identity nullable
- exit_record_ref nullable
- marker_manifest_hash
- evidence_rule_id
- evidence_manifest_hash
- confidence_class
- permitted_transition
- observed_at
- created_at
```

约束：

```text
UNIQUE(attempt_launch_id, observation_sequence)
```

## 18.2 Source

```text
runner_live_inspect
workspace_marker
supervision_backend
wrapper_report
reconciliation
manual_evidence
ssh_remote_inspect
```

## 18.3 Evidence Rule Matrix

| evidence_rule_id | 结论 | 必需证据 | confidence | 允许动作 |
|---|---|---|---|---|
| `E-RUNNING-001` | target running | matching target marker + Unit + boot ID + PID + start ticks + executable/plan identity | confirmed | running |
| `E-EXITED-001` | target exited | verified exit/target-exited marker + token/plan/unit identity | confirmed | succeeded/failed/cancelled |
| `E-NEVER-STARTED-001` | never started | 无 target marker、Unit terminated、lock released、无匹配 process、Workspace identity完整 | strong | 允许恢复同一 launch request |
| `E-WRAPPER-FAILSTOP-001` | wrapper crash terminated group | Unit terminal reason + no descendants + matching Unit/plan identity | confirmed | external_termination |
| `E-REBOOT-001` | host reboot terminated process | marker boot ID 与当前 boot 不同 | strong | external_termination，exit code unknown |
| `E-MISSING-001` | target disappeared | target_started 存在、无 process、无 exit record | insufficient | unknown |
| `E-PID-MISMATCH-001` | PID mismatch/reuse | PID 存在但 start ticks/boot/plan 不符 | insufficient | unknown |
| `E-MANUAL-UNVERIFIED-001` | manual assertion | 仅人工文本 | insufficient | 不推进状态 |

`strong` 只能用于矩阵中预先列出的有限决策。人工证据必须先转换为结构化 Evidence Manifest。

## 18.4 Inspect 算法

1. 读取 Request、当前 Host Plan；
2. 查询 Supervision Unit；
3. 读取并验证 Marker；
4. 验证 host/boot/PID/start ticks；
5. 读取 exit record；
6. 匹配固定 Evidence Rule；
7. 生成不可变 Observation。

## 18.5 Inspect 是只读事实发现

Executor Adapter 不直接写 Attempt。Coordinator 持久化 Observation 后调用 Attempt Domain Service。

## 18.6 周期

P0 可使用版本化 inspect profile；具体间隔是部署配置，不进入科研身份。

# 19. Attempt 状态推进接口

## 19.1 原则

Coordinator 不执行 SQL 更新 Attempt。

它调用领域命令，例如：

```text
RecordAttemptPreparationStarted
RecordAttemptPreparationFailed
RecordAttemptSubmitted
RecordAttemptRunning
RecordAttemptSucceeded
RecordAttemptFailed
RecordAttemptCancelled
RecordAttemptUnknown
RecordAttemptReconciled
```

命令名称可在实现中收敛，但必须经过 Attempt Domain Service。

## 19.2 Observation 到状态

### confirmed target running

```text
submitted → running
launch_phase → target_started
```

### confirmed exit code 0

```text
running/submitted → succeeded
launch_phase → target_exited
```

### confirmed non-zero exit

```text
running/submitted → failed
failure_code = command_failure or classified reason
```

### confirmed cancellation

```text
running/submitted → cancelled
```

### identity insufficient

```text
active state → unknown
```

## 19.3 Terminal immutability

后到普通 Observation 不得覆盖：

```text
succeeded
failed
cancelled
```

发现证据冲突时创建 Reconciliation/Audit Case，不直接改写历史终态。

---

# 20. Logs

## 20.1 权威来源

stdout/stderr 持久化文件是日志事实源。

SSE、WebSocket 或消息流只是投影。

## 20.2 LogCursor

```text
LogCursor
- attempt_id
- stream
- file_identity
- byte_offset
- observed_size
- observed_at
```

客户端使用 offset 增量读取。

## 20.3 Rotation

P0 默认不做运行中 rotation，避免 offset 和审计复杂度。

超过限制时：

- wrapper 继续保存受控上限；
- 记录 truncation；
- 不因日志截断改变 target exit state。

## 20.4 Secret

正式训练日志也必须经过 `07` 定义的 redaction/visibility 策略后才进入普通 UI。

原始日志文件访问受限。

## 20.5 日志读取失败

```text
log_access_failure
```

不改变 Attempt execution status。

---

# 21. Timeout

## 21.1 Enforcement owner

Wall-clock timeout 由 Attempt Supervision Unit 在宿主机本地强制执行，不依赖 Coordinator、Runner Job 或 wrapper 持续存活。

Wrapper 可以记录和协助 graceful termination，但不是唯一 owner。

## 21.2 Clock 与 Deadline

Supervision backend 使用 monotonic duration 或等价可证明的宿主机机制；UTC 时间仅用于审计。Host Plan 固定 wall/TERM/KILL 参数。

## 21.3 Timeout 流程

```text
deadline reached by Supervision Unit
→ persist/query timeout evidence
→ graceful stop execution group
→ wait term grace
→ force kill remaining descendants
→ residual check
→ Runner inspect and persist Observation
```

## 21.4 Timeout 与自然退出

若自然退出先被确认，保留真实 exit code并记录 timeout requested。不能仅因 Coordinator deadline job 触发就标记 cancelled。

## 21.5 Wrapper 崩溃

Wrapper 崩溃不取消 timeout ownership；Supervision Unit fail-stop 立即终止组，形成 `external_termination` 或 unknown evidence。

# 22. Cancel

## 22.1 Cancel request

Cancel 是持久化领域事实，包含 requested_at、reason_code、requested_by；重复请求幂等。

## 22.2 传播

```text
Batch cancel
→ Trial cancel facts
→ active Attempt cancel jobs
→ Supervision Unit stop request
```

## 22.3 `cancel-request.json`

Marker 绑定 attempt、token、Host Plan、request ID 和 reason；它是审计/协助输入，不是唯一 cancel owner。

## 22.4 Cancel 执行

Runner 调用 Supervision backend：

1. 验证 Unit identity 与 Host Plan；
2. 请求 graceful stop；
3. 等待 term grace；
4. 必要时 kill entire execution group；
5. 检查 descendants；
6. inspect 真实结果。

Wrapper 存活时可协助写 Signal Marker；wrapper 不存活时不影响 Unit 执行取消。

## 22.5 竞争规则

- 自然退出先确认：保留 succeeded/failed，另记 cancellation requested；
- Unit 取消确认且无自然终态：Attempt cancelled；
- 无法确认：Attempt unknown。

## 22.6 Terminal Attempt

重复 Cancel 不发送信号，不覆盖终态。

## 22.7 残留检查

Cancel/Timeout 完成必须证明 Supervision Unit 中无可识别 descendants。发现逃逸或证据不足时进入 Reconciliation，不得宣称取消完成。

# 23. Collection

## 23.1 Collection 边界

`06` 的 Collection 只负责：

```text
execution output discovery
output manifest
log/runtime metadata collection
candidate artifact handoff
```

它不执行 Artifact registration、Metric extraction/parsing、Validation 或 Summary；这些属于 `07`。

## 23.2 AttemptCollectionRun

```text
AttemptCollectionRun
- collection_run_id
- attempt_id
- collection_sequence
- collection_schema_version
- status
- input_manifest_hash
- output_manifest_ref nullable
- error_code nullable
- started_at
- ended_at nullable
```

约束：

```text
UNIQUE(attempt_id, collection_sequence)
```

## 23.3 幂等

相同 input manifest 和 schema 返回已有成功 Collection，不覆盖旧文件。

## 23.4 Collection failure

仅使用：

```text
collection_failure
output_manifest_invalid
collection_storage_failure
```

不改变 Attempt `succeeded`。`artifact_registration_failure` 和 `metric_parse_failure` 由 `07` 定义。

## 23.5 Output manifest

记录 relative path、file type、size、content digest、诊断 mtime 和 candidate role；mtime 不作为内容身份。

## 23.6 Handoff

Collection 输出只是候选输入；正式 Artifact/Metric/Validation 由 `07` 消费。

# 24. Validation 与 Summary 协调

## 24.1 Validation 调用

Coordinator 只在：

- Attempt succeeded；
- Collection 满足协议输入；
- Validation Protocol 已冻结；

时请求 Validation Engine。

## 24.2 Validation failure

Validation invalid 不改写 Attempt execution status。

## 24.3 Revalidation

Revalidation 创建新 Validation Report sequence，不覆盖旧报告。

## 24.4 Summary

Coordinator 请求 deterministic Summary；统计语义完全来自 Experiment Domain frozen policy。

## 24.5 Workflow terminal

只有不存在 unknown Attempt 时，Workflow 才能进入 `completed` 或 `completed_with_invalid_trials`。

存在 unknown 时必须进入 `needs_attention`；人工停止调查后使用 `closed_with_unresolved_execution`，并禁止该 Trial进入正式 Summary。

# 25. Retry

## 25.1 Retry owner

Retry 决策由 Coordinator 请求，Attempt Domain Service 根据：

- Trial 状态；
- active Attempt；
- Version retry policy；
- failure classification；
- Attempt count；
- rerun authorization；

决定能否创建新 Attempt。

## 25.2 自动可重试与 Prepare 映射

默认可自动重试：

```text
preemption
temporary_filesystem_failure
temporary_dataset_access_failure（Adapter 明确可恢复时）
```

需固定 Evidence Rule 判断：

```text
external_termination
```

默认不自动重试：

```text
resource_exhaustion
command_failure
user_cancelled
unknown_executor_state
preparation_environment_failure
preparation_semantic_rejection
dataset_binding_mismatch
source_binding_mismatch
unsupported_source_dependency
executor_capability_rejection
execution_plan_invalid
```

`executor_start_failure` 仅用于 Host Plan 已通过但 Unit/wrapper 确认未成功启动的基础设施故障，不能吞并所有 Prepare rejection。

## 25.3 OOM

OOM 属于 `resource_exhaustion`，默认不自动重试。

调整 batch size/precision 是新 Trial 或 Version，不是 Attempt retry。

## 25.4 Unknown

`unknown` 永远不自动创建新 Attempt。

## 25.5 eligible Trial

eligible Trial 再运行必须消费：

```text
TrialRerunAuthorization
```

新结果不自动替换 selected Attempt。

## 25.6 Retry 的提交身份

每个新 Attempt 获得：

```text
new attempt_id
new attempt_number
new submission_token
new workspace
new AttemptExecutionRequest
```

不能复用旧 token 或 Workspace。

---

# 26. Reconciliation

## 26.1 触发条件

- Job lease 过期；
- Submit timeout；
- DB、Host Plan、Unit、Marker 不一致；
- PID identity 不匹配；
- wrapper/target/Unit 之一失踪；
- exit marker 缺失；
- Runner/Coordinator restart；
- host reboot；
- cancel/timeout 结果未知；
- Collection 发现 runtime manifest 不一致。

## 26.2 ReconciliationCase

```text
ReconciliationCase
- reconciliation_case_id
- attempt_id
- attempt_launch_id
- case_type
- status
- expected_state_ref
- observation_ids
- evidence_manifest_ref
- evidence_rule_id nullable
- decision_code nullable
- opened_at
- resolved_at nullable
- resolved_by nullable
```

状态：

```text
open
inspecting
resolved
needs_attention
abandoned
```

## 26.3 固定算法

```text
lock Attempt/Launch
→ read Domain state and current Host Plan
→ inspect Supervision Unit
→ read/verify markers
→ validate process identities
→ match Evidence Rule Matrix
→ persist ExecutorObservation
→ apply deterministic decision
→ call RecordAttemptReconciled
→ enqueue next job/event
```

## 26.4 典型决策

- DB requested + `E-RUNNING-001`：恢复 running，不 relaunch；
- DB running + `E-EXITED-001`：推进真实终态；
- DB submitted + `E-NEVER-STARTED-001`：允许恢复同一 launch request；
- wrapper crash + `E-WRAPPER-FAILSTOP-001`：external termination，同 token 不重启；
- target_started + target missing + no exit：`E-MISSING-001 → unknown`；
- PID mismatch：`E-PID-MISMATCH-001 → unknown`。

## 26.5 人工处理

人工可提供结构化证据、请求取消或停止调查；不能直接改状态、伪造成功、复用 target_started token。

## 26.6 needs_attention 与关闭

Attempt unknown 使 Trial/Workflow projection 为 needs_attention。

人工选择停止调查时：

```text
ReconciliationCase = abandoned
Workflow = closed_with_unresolved_execution
Attempt = unknown
Trial = needs_attention/inconclusive
```

不得自动重跑，不纳入 Summary，并保留长期警告和最小 Evidence Package。

# 27. Crash Recovery Matrix

## 27.1 API / Coordinator 崩溃

不影响 Unit/target。Lease 到期后新 Coordinator 从 DB、Host Plan、Unit 和 Marker 恢复。

## 27.2 Runner 在 Prepare 中崩溃

验证 staging 后幂等清理/重建；不完整 source 不发布。

## 27.3 Runner 在创建 Unit 前崩溃

确认无 Unit、无 Marker、无匹配进程后可以继续同一 Submit。

## 27.4 Runner 创建 Unit 后、DB ack 前崩溃

Inspect Unit/Marker；不得直接创建第二个 Unit。

## 27.5 Wrapper 在 target 前崩溃

Supervision Unit fail-stop。只有 `E-NEVER-STARTED-001` 成立时才允许恢复相同 launch request。

## 27.6 Wrapper 在 target_started 后崩溃

Unit 必须终止整个执行组，timeout/cancel ownership 不丢失，执行 descendant residual check；同 token 永不 relaunch。根据 Evidence Rule 记录 external termination 或 unknown。

## 27.7 Target 退出，Marker 已写但 DB 未更新

通过 `E-EXITED-001` Reconcile 到终态。

## 27.8 Target/Wrapper 退出但 Exit Marker 缺失

查询 Unit terminal result 与 Process Evidence；证据不足为 unknown。

## 27.9 PostgreSQL 暂时不可用

Unit 继续执行 timeout/fail-stop；wrapper 继续写日志/Marker。数据库恢复后 Reconcile。

## 27.10 Host reboot

通过 boot ID 与 supervision backend evidence 判断；不得匹配旧 PID。

## 27.11 Workspace storage unavailable

不猜测进程状态，进入 Reconciliation。

## 27.12 Wrapper death 发布验收

必须验证：

```text
supervision unit remains authoritative
timeout owner still exists
cancel remains executable
all descendants terminated
no duplicate target
same token never relaunches after target_started
final Observation/Reconciliation is explainable
```

# 28. Local Runner 两条 Lane

## 28.1 Formal Attempt Lane

处理：

```text
AttemptExecutorJob
AttemptWorkspace
AttemptExecutionRequest
AttemptHostExecutionPlan
AttemptSupervisionUnitIdentity
AttemptLaunch
ExecutorObservation / Evidence Manifest
```

## 28.2 Tool Lane

处理：

```text
ToolExecutorJob
ToolExecutionIntent
ToolExecutionAttempt
ToolWorkspace
```

## 28.3 可共享实现

- host daemon；
- claim/lease library；
- process launcher；
- process identity reader；
- log file reader；
- signal utility；
- safe manifest utility；
- capability reporting。

## 28.4 必须分离的配置

```text
workspace root
queue/table
job schema
request/plan schema
supervision backend profile
wrapper mode
retention policy
result processor
state transition adapter
```

## 28.5 调度优先级

P0 可使用固定优先级：

```text
cancel/reconcile
> inspect running formal attempt
> submit/prepare formal attempt
> tool interactive job
> cleanup
```

但 Tool Lane 不能改变 formal Attempt 的领域状态。

---

# 29. Executor Contract

## 29.1 Prepare

```text
prepare(AttemptExecutionRequest) -> PrepareResult + AttemptHostExecutionPlan
```

Prepare 不启动 target。

## 29.2 Submit

```text
submit(AttemptHostExecutionPlan) -> SubmitResult
```

Submit 只能执行 Plan，不能重新解析命令/环境。

## 29.3 Inspect

```text
inspect(AttemptLaunchIdentity, HostPlanHash) -> ExecutorObservation
```

## 29.4 Read Logs

按受控 Cursor 读取事实源文件。

## 29.5 Cancel

```text
cancel(SupervisionUnitIdentity, CancelRequest) -> Observation
```

## 29.6 Collect

仅生成 Output Manifest 和 runtime metadata。

## 29.7 Cleanup

不能删除 unresolved evidence 或正式结果。

## 29.8 Contract rules

- 所有方法幂等或具有稳定 idempotency identity；
- Submit timeout 后先 Inspect；
- Inspect 不写 Domain 状态；
- Transport failure 不等于 target failure；
- Local/SSH 都必须提供等价 Supervision 与 Filesystem Capability；
- Tool Lane 不使用 AttemptExecutionRequest/HostPlan。

# 30. SSH Executor（P1）

## 30.1 目标

SSH Executor 复用相同 Contract，将 Workspace、Supervision Unit、wrapper 和 Marker 放到固定远端主机。

## 30.2 必须等价的能力

远端必须具备：

- durable process-tree supervision；
- wrapper exit fail-stop；
- timeout/cancel ownership；
- runtime filesystem capability probe；
- stable remote host/boot/unit identity；
- Host Execution Plan binding。

SSH 连接本身不是监督者。

## 30.3 Remote identity

保存远端 registration generation、machine/boot identity、supervision unit identity、workspace identity 和 Host Plan hash。

## 30.4 Transport failure

SSH timeout/断开只表示 transport/inspect 失败，不代表远端 target 失败。先远端 inspect/reconcile；无法确认则 unknown。

## 30.5 Credential

SSH Credential 通过受控 Reference 解析，不进入 Marker、Hash 或普通日志。

## 30.6 P1 不做

不自动安装环境、不动态迁移 running process、不引入远端调度器、不开放任意远程 Shell Tool。

# 31. 并发控制与唯一约束

## 31.1 Workflow

```text
UNIQUE(workflow_type, run_batch_id)
```

## 31.2 Attempt Execution Request

```text
UNIQUE(attempt_id)
UNIQUE(submission_token)
```

## 31.3 Host Execution Plan

```text
UNIQUE(attempt_execution_request_id, plan_generation)
```

同一 Request 同时最多一个 `prepared` Plan；submitted Plan 不得 supersede。

## 31.4 Attempt Workspace

```text
UNIQUE(attempt_id)
UNIQUE(root_registration_id, relative_path)
```

## 31.5 Launch 与 Supervision Unit

```text
UNIQUE(attempt_id)
UNIQUE(submission_token)
UNIQUE(attempt_launch_id) on supervision unit binding
```

## 31.6 Jobs 与 Observations

```text
UNIQUE(coordinator_job.dedupe_key)
UNIQUE(attempt_launch_id, observation_sequence)
```

## 31.7 Active Attempt

使用 02 已冻结的部分唯一索引；unknown 属于 active blocker。

## 31.8 Locks

允许短事务锁处理创建 Attempt、Workflow transition、Cancel/Reconcile、Finalization。外部调用不得持有数据库锁。`launch.lock` 只保护本地启动临界区，不能替代 Marker/Unit Evidence。

# 32. Ownership Matrix

| 对象/事实 | 持久化写入服务 | 授权调用者 |
|---|---|---|
| FormalExperimentWorkflow | Workflow Service | Application / Coordinator |
| CoordinatorJob | Coordinator Job Service | Domain/Workflow transaction |
| ExecutorRegistration/Capability | Runner Registry | Deployment / Runner probe |
| AttemptExecutionRequest | Execution Planning Service | Coordinator after Attempt creation |
| AttemptHostExecutionPlan | Host Planning Service | Local/SSH Runner prepare path |
| Source/Dataset/Environment Binding | Host Planning Service | Runner deterministic probe |
| AttemptWorkspace | Attempt Workspace Service | Coordinator / Runner |
| Supervision Unit Binding | Executor Runtime Service | Runner submit path |
| runtime markers | wrapper / Runner | immutable Host Plan |
| ExecutorObservation | Observation Service | Runner / Reconciliation |
| Attempt state | Attempt Domain Service | Coordinator with permitted Evidence Rule |
| Collection Run/Manifest | Result Collection Service | Coordinator |
| ReconciliationCase | Reconciliation Service | Coordinator / Human resolution |
| ValidationReport | Validation Service | `07` coordination |
| BatchSummary | Summary Service | `07` coordination |

Runner、wrapper、UI 和模型不得直接写 Attempt/Trial/Batch 表。

# 33. Commands 与 Queries

## 33.1 Workflow Commands

```text
StartFormalExperimentWorkflow
BindApprovedExperimentVersion
AdvanceFormalExperimentWorkflow
EnterWorkflowNeedsAttention
CloseWorkflowWithUnresolvedExecution
RequestWorkflowCancellation
CompleteFormalExperimentWorkflow
```

## 33.2 Coordinator Job Commands

```text
EnqueueCoordinatorJob
ClaimCoordinatorJob
RenewCoordinatorJobLease
CompleteCoordinatorJob
FailCoordinatorJob
RescheduleCoordinatorJob
```

## 33.3 Executor Commands

```text
RegisterExecutor
ProbeExecutorCapabilities
HeartbeatExecutor
CreateAttemptExecutionRequest
PrepareAttemptHostExecutionPlan
SupersedeUnsubmittedHostExecutionPlan
CreateAttemptSupervisionUnit
SubmitAttemptHostExecutionPlan
RecordSubmitResult
RecordExecutorObservation
RequestAttemptInspection
RequestAttemptCancellation
RequestAttemptCollection
```

## 33.4 Reconciliation Commands

```text
OpenAttemptReconciliationCase
RecordReconciliationEvidence
ResolveAttemptReconciliationCase
AbandonAttemptReconciliationCase
```

## 33.5 Queries

```text
GetWorkflow
GetBatchExecutionOverview
GetAttemptExecutionRequest
GetCurrentAttemptHostExecutionPlan
GetAttemptWorkspace
GetAttemptSupervisionUnit
GetAttemptLaunchTimeline
GetLatestExecutorObservation
ReadAttemptLogs
ListOpenReconciliationCases
GetExecutorHealthAndCapabilities
```

## 33.6 禁止通用 PATCH

不存在任意修改 Attempt、Workflow Phase、Launch Phase、PID、Unit identity 或 Host Plan 的通用 PATCH。

# 34. Error Model

## 34.1 Workflow Errors

```text
WorkflowAlreadyExists
WorkflowLeaseLost
WorkflowStateConflict
WorkflowCancelled
WorkflowNeedsAttention
UnresolvedExecutionBlocksFinalization
UpstreamContractMismatch
```

## 34.2 Preparation Errors

```text
WorkspaceAllocationFailed
SourceMaterializationFailed
SourceBindingMismatch
UnsupportedSourceDependency
DatasetAccessTemporaryFailure
DatasetBindingMismatch
EnvironmentPreflightRejected
ExecutionPlanInvalid
ExecutorCapabilityRejected
RuntimeFilesystemCapabilityRejected
```

## 34.3 Launch/Supervision Errors

```text
LaunchRequestConflict
DuplicateLaunchDetected
SupervisionUnitCreateFailed
SupervisionUnitIdentityMismatch
WrapperStartFailed
WrapperFailStopObserved
TargetStartFailed
TargetAlreadyStarted
LaunchIdentityUnknown
```

## 34.4 Inspect/Cancel Errors

```text
ExecutorUnavailable
MarkerMissing
MarkerIdentityMismatch
ProcessIdentityMismatch
EvidenceRuleNotMatched
ProcessStateUnknown
CancelSignalFailed
CancelOutcomeUnknown
ResidualProcessDetected
```

## 34.5 Collection Errors

```text
CollectionFailure
OutputManifestInvalid
CollectionStorageFailure
```

Artifact registration/Metric parsing errors由 `07` 定义。

## 34.6 Error response

保存 error_code、stage、retryable、subject identity、correlation/causation 和脱敏诊断，不保存 Secret。

# 35. Events 与 Trace

## 35.1 关键事件

```text
FormalWorkflowStarted
FormalWorkflowPhaseChanged
FormalWorkflowNeedsAttention
FormalWorkflowClosedWithUnresolvedExecution
AttemptCreated
AttemptPreparationStarted
AttemptHostExecutionPlanPrepared
AttemptHostExecutionPlanSuperseded
AttemptPreparationRejected
AttemptLaunchRequested
AttemptSupervisionUnitCreated
WrapperStartedObserved
WrapperFailStopObserved
TargetStartedObserved
TargetExitedObserved
AttemptStateReconciled
AttemptCancellationRequested
AttemptCancellationConfirmed
AttemptCollectionCompleted
BatchFinalizationRequested
FormalWorkflowCompleted
ReconciliationCaseOpened
ReconciliationCaseResolved
```

## 35.2 事务边界

Domain/Workflow state change 与 critical event/outbox enqueue 原子提交。

## 35.3 Correlation

传播 project/experiment/version/batch/trial/attempt/workflow/submission token、Host Plan hash、Supervision Unit identity、correlation 和 causation。

## 35.4 Timeline

Timeline 是投影。恢复读取 Domain rows、Workflow/Jobs、Request、Host Plan、Unit、Marker、Process Identity、Observation 和 Evidence Manifest。

# 36. Security 与信任边界

## 36.1 Local Runner 权限

Runner 使用专用 OS identity，只访问注册 Workspace Root、Repository objects、受控 executable、必要设备、Supervision backend 和 Job channel。

## 36.2 Control plane

API/Coordinator 容器不得 fork Repository 训练代码。

## 36.3 Source

正式 Source 来自 frozen commit。是否真正只读由 `source_enforcement_capability` 表达：

- `readonly_enforced`：OS 层阻止写入；
- `digest_observed`：仅 pre/post 验证，不能宣称阻止写入。

## 36.4 Command

Host Plan 由 deterministic planner 构建，wrapper 不重新解释命令，不接受任意 Shell。

## 36.5 Supervision

Formal target 必须位于通过能力门控的 OS Supervision Unit；不允许故意启动无人监督的正式 target。

## 36.6 Network

网络能力由 Formal Profile 和真实 OS enforcement 声明；没有 enforcement 时不得声称断网。

## 36.7 Credentials

Credential 在 Runner 边界解析，不进入 Marker、Manifest、Event 或 Request Hash。

## 36.8 Marker trust

Marker 必须校验 schema、Request、Host Plan、Workspace、Unit 和 token identity；文件存在本身不构成事实证明。

# 37. LG-CausalDiffAE 示例

## 37.1 Frozen Version

```text
Experiment Version:
- source commit: frozen clean commit
- dataset: circuit snapshot
- parameter groups: 3
- seeds: [1, 2, 3]
- total Trials: 9
- timeout: fixed
- validation protocol: frozen
```

## 37.2 Batch 创建

```text
Start Formal Workflow
→ prepare_inputs
→ create 9 Trials
→ create initial Attempt for each Trial
```

## 37.3 Attempt Workspace

例如 Trial：

```text
parameter group = main_A_zpost_mean_nostd
seed = 2
```

Workspace：

```text
attempts/<attempt-id>/source/
attempts/<attempt-id>/resolved-config/config.json
attempts/<attempt-id>/logs/stdout.log
attempts/<attempt-id>/outputs/
attempts/<attempt-id>/runtime/
```

## 37.4 Prepare 与 Submit

Runner：

```text
materialize commit
→ verify/seal source
→ bind circuit Dataset snapshot
→ bind Python/CUDA environment
→ create immutable Host Execution Plan
→ revalidate bindings
→ create OS Supervision Unit
→ start wrapper under Unit
→ wrapper starts training from Host Plan
```

## 37.5 Coordinator 或 Wrapper restart

Coordinator 重启不影响 Unit/target；新 Coordinator通过 due inspect job恢复。

若 wrapper 在 target_started 后崩溃：

```text
Supervision Unit fail-stop entire group
→ no residual descendants
→ no same-token relaunch
→ external_termination/unknown Observation
→ Domain decides whether a new Attempt is allowed
```

## 37.6 OOM

目标退出并确认 OOM：

```text
Attempt = failed
failure = resource_exhaustion
retryable = false
```

系统不自动降低 batch size。

## 37.7 Collection failure

目标 exit 0，但 checkpoint scan 暂时失败：

```text
Attempt = succeeded
CollectionRun = failed
Coordinator retries Collection
```

## 37.8 Validation failure

checkpoint 存在但 required Metric 缺失：

```text
Attempt = succeeded
ValidationReport = invalid
Trial = invalid
```

## 37.9 一个 Trial Retry

若 `temporary_filesystem_failure`：

```text
Attempt #1 failed
→ Domain permits retry
→ Attempt #2
→ new token/workspace/request
```

Attempt #1 的 marker 和日志保留。

---

# 38. 测试策略

## 38.1 Workflow

测试 phase、needs_attention、unresolved closure、terminal 不可逆、Retry coordination、Lease fencing、Job dedupe 和 wakeup authority。

## 38.2 Workspace/Binding/Plan

测试 source materialization、Workspace identity、TOCTOU source/dataset/environment drift、Host Plan supersede 约束、capability rejection、partial staging crash。

## 38.3 Supervision/Wrapper

测试 Unit identity、duplicate submit、wrapper/target marker、wrapper fail-stop、wall timeout、cancel entire group、descendant residual、target_started 禁止 relaunch。

## 38.4 Filesystem Capability

测试 flock、atomic rename、file/directory fsync、durable create；能力不满足时拒绝 Formal Submit。

## 38.5 Process/Evidence

测试 PID reuse、wrong start ticks、boot change、Evidence Rule Matrix、manual unverified evidence、target missing/no exit。

## 38.6 Coordinator/Cancel/Collection

测试 API/Coordinator/Runner restart、submit response lost、cancel race、Collection retry、Metric parsing不在 Collection、Summary被 unknown 阻塞。

## 38.7 Lane separation

测试 Tool Job 不能创建 Attempt、Formal Lane 不消费 Tool Approval、Workspace/Execution Record 不混用。

# 39. Release-blocking Fault Injection

```text
FI-001 Coordinator dies after enqueue submit
FI-002 Runner dies before Unit creation
FI-003 Runner dies after Unit creation before DB result
FI-004 wrapper dies before target start
FI-005 wrapper dies after target start
FI-006 target exits before DB observes running
FI-007 target exit marker written, DB unavailable
FI-008 duplicate submit job
FI-009 PID reused
FI-010 cancel races natural exit
FI-011 timeout races natural exit
FI-012 collection fails after target success
FI-013 validation fails after target success
FI-014 source manifest mismatch
FI-015 source/dataset/environment changes after prepare
FI-016 filesystem capability missing
FI-017 workspace staging partial
FI-018 host boot identity changes
FI-019 inspect transport timeout
FI-020 target identity unknown
FI-021 Reconciliation abandoned
```

每个场景必须验证：

- 是否产生重复 target/Unit；
- timeout/cancel owner 是否仍存在；
- wrapper death 后 descendants 是否全部终止；
- Attempt/Workflow 最终状态；
- Marker、Host Plan、Observation、Evidence Rule；
- Reconciliation Case；
- Retry 是否被正确阻止或允许；
- unknown 是否阻止 Summary；
- Timeline 是否可解释。

# 40. 局部不变量

```text
WF-INV-001 Workflow state 不替代 Experiment Domain state。
WF-INV-002 Coordinator 不直接写 Attempt、Trial、Batch 领域表。
WF-INV-003 外部 Executor 调用不得在数据库事务内执行。
WF-INV-004 Workflow/Job lease 使用递增 generation fencing。
WF-INV-005 Coordinator Job 失败不自动等于 Attempt failure。
WF-INV-006 CoordinatorJob.run_at 是唯一 wakeup 权威事实。
WF-INV-007 每个逻辑 Coordinator Job 使用稳定 dedupe key。
WF-INV-008 一个 Attempt 最多一个逻辑 AttemptExecutionRequest。
WF-INV-009 AttemptExecutionRequest 创建后不可变。
WF-INV-010 Host Execution Plan 与逻辑 Request 分离且不可变。
WF-INV-011 Wrapper 只能执行绑定的 Host Execution Plan，不得重新解析命令。
WF-INV-012 同一 Request 同时最多一个 prepared Host Plan。
WF-INV-013 submitted/target_started 后不得 supersede Host Plan。
WF-INV-014 一个 Attempt 最多一个 Attempt Workspace。
WF-INV-015 Workspace identity 使用命名空间化 Canonical JSON V1。
WF-INV-016 Runner 只能从 frozen commit materialized source 执行。
WF-INV-017 Source/Dataset/Environment Binding 必须在 Submit 前重验。
WF-INV-018 Binding mismatch 时不得 Submit。
WF-INV-019 Formal Executor 必须通过 Supervision Capability 门控。
WF-INV-020 Formal Executor 必须通过 Runtime Filesystem Capability 门控。
WF-INV-021 Formal target 必须位于可查询的 OS Supervision Unit。
WF-INV-022 wrapper 不是唯一 timeout/cancel owner。
WF-INV-023 wrapper 异常退出必须 fail-stop 整个执行组。
WF-INV-024 wrapper death 后必须检查 descendant residual。
WF-INV-025 关键 Marker 必须原子持久化并绑定 Request、Plan、Workspace、token 和 Unit。
WF-INV-026 launch.lock 使用 process-shared lock，但不能单独决定是否可重启。
WF-INV-027 submission token 全局唯一。
WF-INV-028 同一 token 不得同时存在两个可继续 target。
WF-INV-029 target_started 后不得用同一 token relaunch。
WF-INV-030 P0 不声称 OS process exactly once。
WF-INV-031 PID 单独不能证明进程身份。
WF-INV-032 Submit 超时后必须 inspect，不能重复创建 Unit。
WF-INV-033 Executor Observation 必须保存 evidence_rule_id 和 evidence manifest。
WF-INV-034 strong evidence 只能来自固定 Evidence Matrix。
WF-INV-035 人工自由文本不能直接推进 Attempt 状态。
WF-INV-036 Observation 先持久化，再请求 Domain 状态推进。
WF-INV-037 普通 Observation 不得覆盖 terminal Attempt。
WF-INV-038 unknown Attempt 禁止自动重提。
WF-INV-039 unknown Attempt 必须阻塞正式 Finalization/Summary。
WF-INV-040 closed_with_unresolved_execution 不得伪装成 completed/invalid。
WF-INV-041 Retry 必须创建新 Attempt、token、Workspace 和 Request。
WF-INV-042 OOM 默认不自动 Retry。
WF-INV-043 Prepare rejection 必须使用固定 failure mapping。
WF-INV-044 Cancel request 不等于 cancel 已生效。
WF-INV-045 Timeout/Cancel 针对整个 Supervision Unit 执行组。
WF-INV-046 自然退出与 Cancel/Timeout 竞争时保留真实退出事实。
WF-INV-047 无法确认停止结果时必须为 unknown。
WF-INV-048 日志文件是事实源，实时流只是投影。
WF-INV-049 Collection 不包含 Artifact registration 或 Metric parsing。
WF-INV-050 Collection failure 不得覆盖 succeeded Attempt。
WF-INV-051 Validation invalid 不得覆盖 succeeded Attempt。
WF-INV-052 Tool Lane 与 Formal Attempt Lane 的领域记录不得混用。
WF-INV-053 Tool Lane 不得启动正式 Attempt，Formal Lane 不消费 Tool Approval。
WF-INV-054 Reconciliation 必须比较 DB、Host Plan、Unit、Marker 和 Process Identity。
WF-INV-055 证据不足时必须进入 unknown/needs_attention。
WF-INV-056 Runner heartbeat 不证明 target alive。
WF-INV-057 Host boot 变化时不得匹配旧 PID/Unit。
WF-INV-058 Secret 值不得进入 Request、Plan、Marker、Hash 或 Event。
WF-INV-059 Cleanup 不得删除 unresolved evidence 或正式结果。
WF-INV-060 eligible Trial 重跑必须消费一次性授权。
WF-INV-061 新 Attempt 不自动替换 selected Attempt。
WF-INV-062 SSH transport failure 不等于 remote target failure。
WF-INV-063 SSH Formal Executor 必须提供远端等价监督能力。
WF-INV-064 任何人不得通过通用 PATCH 修改 Launch、Attempt、Unit 或 Host Plan 状态。
```

# 41. 与相邻规格的接口

## 41.1 Experiment Domain

本文消费：

- Attempt active constraint；
- Launch phase；
- failure classification；
- Retry policy；
- Trial rerun authorization；
- selected Attempt；
- cancellation facts。

本文不重写这些语义。

## 41.2 Agent Harness

Agent 可以请求正式执行流程，但：

- 不能直接创建 Attempt；
- 不能调用 Runner；
- 不能根据日志写状态；
- 不能把 Formal Training 包装为 Tool。

## 41.3 Context/Repository

Source 使用冻结 commit 和 reference manifest；不使用当前 Context Snapshot 的 mutable repository state 启动正式执行。

## 41.4 Tools/Policy/Workspace

复用 Runner 基础设施时：

- Tool Lane 和 Formal Attempt Lane 分离；
- Tool Execution Intent 不等于 AttemptExecutionRequest；
- Tool Workspace 不等于 Attempt Workspace；
- Tool Approval 不授权正式训练。

## 41.5 Results/Validation/AgentOps

`07` 消费：

- terminal Attempt；
- exit record；
- output manifest；
- logs；
- environment capture；
- Collection Manifest；
- Host Execution Plan；
- Executor Observation / Evidence Manifest；
- Reconciliation Case / Workflow Closure；
- Workflow/Executor events。

---

# 42. P0 与 P1 边界

## 42.1 P0 必须实现

- fixed formal workflow；
- PostgreSQL jobs；
- generation leases；
- Local Runner；
- Attempt Workspace；
- source materialization；
- OS Supervision Unit；
- wrapper markers；
- Host Execution Plan 与三个 Execution Binding；
- process identity 与 Evidence Matrix；
- submit/inspect/log/cancel/timeout；
- Collection coordination；
- Retry；
- Reconciliation；
- crash recovery；
- fault injection。

## 42.2 P1

- SSH Executor；
- stronger namespace/container profile；
- editable worktree；
- richer remote log streaming；
- multiple executor selection；
- cost/capacity observation。

## 42.3 P2

- Temporal；
- general DAG；
- SLURM/Kubernetes；
- multi-host scheduler；
- checkpoint-aware migration；
- automatic queue optimization。

---

# 43. 架构决策摘要

```text
ADR-WF-001 Use PostgreSQL-backed fixed workflow instead of a general workflow engine.
ADR-WF-002 Use host-native Runner; control-plane containers never own training processes.
ADR-WF-003 Use an OS Supervision Unit as the physical lifecycle boundary.
ADR-WF-004 Keep wrapper as a marker/launch protocol process, not the sole supervisor.
ADR-WF-005 Separate immutable logical Request from immutable Host Execution Plan.
ADR-WF-006 Revalidate Source, Dataset and Environment Bindings immediately before launch.
ADR-WF-007 Gate Formal Executors by supervision and filesystem capabilities.
ADR-WF-008 Guarantee at-most-one-active launch generation, not process exactly-once.
ADR-WF-009 Model workflow, executor and experiment states separately.
ADR-WF-010 Use generation-fenced leases for Coordinator and Runner jobs.
ADR-WF-011 Treat unknown as a first-class blocking state with explicit unresolved closure.
ADR-WF-012 Use a fixed Evidence Rule Matrix for reconciliation transitions.
ADR-WF-013 Create a new Attempt for every physical rerun after target start.
ADR-WF-014 Keep Tool Lane and Formal Attempt Lane separate.
ADR-WF-015 Keep Collection discovery separate from Artifact/Metric/Validation processing.
```

# 44. 完成定义

本文冻结条件：

1. 上游 dependency hash 全部通过；
2. Workflow phase、needs_attention、Retry 和 unresolved closure 不矛盾；
3. Attempt/Launch 完全遵守 02；
4. Tool Lane 与 Attempt Lane 分离；
5. Logical Request 与 Host Plan 权威清楚；
6. Source/Dataset/Environment TOCTOU 有启动前重验；
7. Supervision Unit 能在 wrapper death 后继续拥有 timeout/cancel 或执行 fail-stop；
8. Marker 文件系统能力经过门控；
9. Evidence Matrix 决定可推进状态；
10. unknown 不进入正式 Summary；
11. Collection 不包含 Metric parsing；
12. Wrapper death、Cancel、Timeout、PID reuse 和 Host reboot 有发布阻塞测试；
13. 不含未决占位项；
14. 用户完成最终评审。

```text
Version: v0.2
Status: Approved / Frozen
```
