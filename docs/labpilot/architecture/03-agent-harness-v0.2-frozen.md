# LabPilot Agent Harness Architecture

> 版本：v0.2
> 状态：Approved / Frozen
> 日期：2026-07-23
> 上游约束：`../00-master-blueprint-v0.2.md`、`../01-v1-scope-and-acceptance-v0.2-frozen.md`、`02-experiment-domain-v0.2-frozen.md`
> 上游契约摘要：Master `16f2c39b...a6fa`；V1 Scope `7092a034...dbd6`；Experiment Domain `2c998e78...328a`

---

# 0. 文档目的与约束层级

## 0.1 文档目的

本文定义 LabPilot V1 的 Agent Harness 架构，回答以下问题：

1. 一次 Agent 任务如何被创建、执行、暂停、恢复和终止；
2. Agent 如何编译受控上下文，而不是直接依赖聊天历史；
3. 模型输出如何被规范化为可验证的结构化决策；
4. Tool 如何经过注册、Schema 校验、Policy 判断和 Approval 后执行；
5. Proposal 如何经过确定性与规则验证，才成为可供用户审批的正式草案；
6. Agent Runtime 如何持久化 Step、Decision、Tool Call、Verifier Result 与 Trace；
7. 服务崩溃或 Approval 延迟时，如何避免重复模型决策和重复副作用；
8. Agent Harness 如何与 Experiment Domain、Context、Policy、Workflow、Executor 和 AgentOps 保持清晰边界。

本文是 Agent Runtime 的架构契约，不是具体 ORM、API、Prompt 或 Provider SDK 实施说明。

## 0.2 上游约束优先级

若本文与上游文档冲突，优先级如下：

```text
01-v1-scope-and-acceptance-v0.2-frozen.md
    > 02-experiment-domain-v0.2-frozen.md
    > 00-master-blueprint-v0.2.md
    > 03-agent-harness-v0.2-frozen.md
```

## 0.2.1 绑定的冻结上游契约

本文冻结时绑定以下上游版本：

```text
../01-v1-scope-and-acceptance-v0.2-frozen.md
SHA-256: 7092a034a78e6ea8b9f13533521fbaa6989ad15a0a25686057a75f286e33dbd6

02-experiment-domain-v0.2-frozen.md
SHA-256: 2c998e7814a116c0999b21b6cf078dacb0fb6f770c929062480dc8ee98df328a
```

普通别名文件可以更新，但不得在没有显式版本升级和影响评估的情况下改变本文依赖的上游领域语义。

本文不得改写以下已冻结事实：

- Agent Run 不拥有 Experiment、Version、Trial、Attempt 或 Validation 的权威状态；
- 未批准 Proposal Revision 不得创建 Experiment Version；
- Agent 不得直接启动正式训练；
- Agent Run 不在训练期间长期挂起；
- Tool Execution Approval 与 Experiment Design Approval 分离；
- 系统恢复不创建 Agent Run，也不调用 LLM 判断外部进程事实；
- 正式执行只能由 Approved Experiment Version 经 Coordinator → Executor 触发。

## 0.3 本文与后续子规格的关系

本文确定 Agent Harness 自身的运行语义。

以下细节由后续文档展开：

- `04-context-and-repository.md`：Context Compiler、Repository Inspection、Project Guidance；
- `05-tools-policy-and-workspace.md`：Tool Contract、风险策略、Approval、Workspace；
- `06-workflow-and-executors.md`：Experiment Coordinator、Local Runner、SSH Executor；
- `07-results-validation-and-agentops.md`：Result、Validation、Runtime Event 投影与 Timeline。

这些文档可以细化接口，但不得改变本文定义的 Agent Run、Step、Decision、暂停恢复和领域边界。

---

# 1. 设计目标与非目标

## 1.1 设计目标

Agent Harness 必须满足：

1. **有界**：每次运行受 Step、模型调用、Token、成本与活动时间预算限制；
2. **可解释**：每个决策可追溯到 Context Snapshot、模型调用、Tool/Policy/Verifier 结果；
3. **受控**：模型不能绕过 Tool Registry、Policy 或 Approval 执行副作用；
4. **可恢复**：Run 暂停或服务重启后，可从持久化安全检查点继续；
5. **可验证**：Proposal 与 Tool 输入必须通过程序检查，不能只依赖模型自评；
6. **领域隔离**：模型只提出命令意图，由领域服务执行合法状态迁移；
7. **失败隔离**：Agent 失败不破坏已有 Experiment、Workflow 或 Attempt；
8. **V1 克制**：实现 LabPilot 所需运行语义，不建设通用 Agent 平台。

## 1.2 非目标

V1 Agent Harness 不建设：

- 常驻多 Agent 团队；
- 通用 Subagent 编排平台；
- 任意 DAG Agent Workflow；
- 长期自然语言 Memory；
- 向量记忆自动写入；
- Skill Marketplace；
- 自动模型路由与模型竞价；
- 通用 Prompt 管理 SaaS；
- Provider 级分布式高可用；
- 隐藏推理链存储或展示；
- 通过 Agent Event 重建全部领域状态；
- 让 LLM 承担进程恢复、Validation 或审批事实判断。

## 1.3 V1 核心价值

V1 的 Agent Harness 必须证明四件事：

```text
受控理解研究目标
→ 生成结构化实验 Proposal
→ 在 Tool/Policy/Verifier 约束下行动
→ 形成可审计、可终止、可恢复的 Agent Run
```

## 1.4 上游 Requirement 追踪

| V1 Requirement | 本文主要章节 |
|---|---|
| V1-FR-005 Agent Run | 4、5、12、13 |
| V1-FR-006 Context Compiler | 8 |
| V1-FR-007 Bounded Loop | 6、7 |
| V1-FR-008 Tool Registry | 3、10 |
| V1-FR-009 Tool Policy | 10、15 |
| V1-FR-010 Structured Trace | 4、14 |
| V1-FR-011 Experiment Proposal | 11、18 |
| V1-FR-012 Deterministic Verifier | 11 |
| V1-FR-013 Rule Verifier | 11 |
| V1-FR-014 Experiment Design Approval | 11、17 |
| V1-FR-015 Tool Execution Approval | 10、13 |

---

# 2. 核心设计原则

## 2.1 单 Agent 优先

P0 仅实现一个 `Experiment Planning Agent Runtime`。

该名称用于明确区分：

```text
Experiment Planning Agent Runtime
    负责仓库理解、受控 Tool 调用、Proposal 生成与修订

Experiment Coordinator
    负责 Trial / Attempt 的长期调度、等待、恢复与对账
```

原因：

- 当前任务链以 Repository 理解和 Proposal 生成为主；
- 多 Agent 会增加上下文复制、责任归属、Tool 冲突和 Trace 复杂度；
- 确定性 Verifier 与规则 Verifier已能承担大量“复核”职责；
- P1 的 LLM Semantic Verifier 是独立复核调用，不等同于常驻第二 Agent；
- Agent Runtime 不拥有长期实验执行状态。

## 2.2 每个 Step 只产生一个规范化动作

P0 每个 Agent Step 最多产生一个动作：

```text
CALL_TOOL
PRODUCE_PROPOSAL
REQUEST_HUMAN_INPUT
FINAL_RESPONSE
```

Provider Response 必须能够规范化为**恰好一个**动作。

以下响应均判定为 `decision_schema_violation`：

- 同时包含多个 Tool Call；
- Tool Call 与 Final Response 混合；
- Proposal 与 Tool Call 混合；
- 同时包含多个互斥动作；
- 无法确定唯一 action type。

处理规则：

```text
不执行任何动作
→ 当前 ModelCall = invalid_output
→ 在同一 AgentStep 创建新的 ModelCall Attempt
→ 消耗模型调用、Token、时间和 no-progress 预算
→ 连续失败时终止 invalid_model_output / no_progress
```

P0 不自动拆分多个 Tool Call，也不选择其中第一个执行。未来如需并行只读动作，必须通过新的 Decision Schema 版本显式引入。

原因：

- 保持 Policy 与 Approval 顺序明确；
- 避免多个 Tool 同时修改 Workspace；
- 简化预算、恢复和 causation；
- 使每个 Step 的输入、决策、动作与观察形成一一对应关系。

## 2.3 决策先持久化，动作后执行

任何 Tool 或领域命令执行前，必须先持久化模型的规范化 `AgentDecision`。

```text
Model Response
→ Normalize Decision
→ Persist Decision
→ Validate / Policy
→ Execute Action
→ Persist Observation
```

因此服务在“模型已经决定调用 Tool，但 Tool 尚未执行”时崩溃，恢复后继续处理既有 Decision，不重新调用模型生成一个可能不同的决定。

## 2.4 Agent 只提交意图，不直接写领域表

Agent 可以：

- 创建或修订 Proposal；
- 请求 Verifier；
- 请求 Tool Approval；
- 读取 Domain Summary；
- 输出解释性建议。

Agent 不可以：

- 直接创建 Experiment Version；
- 直接修改 Trial、Attempt、Validation 或 Batch 状态；
- 自动批准 Proposal；
- 绕过 Coordinator 启动正式实验；
- 将模型判断写为 Validation Report；
- 将聊天摘要覆盖为科研事实。

## 2.5 程序验证优先于 LLM 验证

验证顺序固定为：

```text
Schema Validation
→ Deterministic Verifier
→ Rule Verifier
→ Optional LLM Semantic Verifier
```

能通过代码证明的事实，不交给模型判断。

## 2.6 挂起不等于常驻进程

Agent Run 可以处于逻辑 `suspended` 状态，但不得保持一个等待数小时的 Python 调用栈、HTTP 请求或 LLM 会话。

暂停时：

- 所有运行状态和对应 Wakeup Job 已持久化；
- Worker 释放；
- 模型连接关闭；
- 等待 Tool Approval 的时间不计入活动执行时间，但计入日历截止时间；
- Approval 决定、Approval 到期或 Run Deadline 由持久化 Wakeup 触发；
- 恢复时取得新的 Lease generation，并从持久化 ToolCall 继续。

普通用户澄清不暂停同一 Run，而是结束当前 Run 后创建关联的新 Run。

## 2.7 不存储隐藏推理链

Trace 保存：

- 结构化决策；
- 简短、面向审计的 decision rationale；
- 证据引用；
- Tool/Policy/Verifier 结果；
- 模型 usage 和错误。

不得要求或保存 Provider 的隐藏 Chain-of-Thought。

---

# 3. 总体组件架构

```text
┌────────────────────────────────────────────────────────────┐
│ Product Surface                                            │
│ Start Run / View Trace / Edit Proposal / Decide Approval   │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ Agent Runtime Service                                      │
│ Run State / Lease Generation / Budget / Step Orchestration │
└──────────────┬───────────────────────┬─────────────────────┘
               │                       │
┌──────────────▼────────────┐ ┌────────▼─────────────────────┐
│ Context Compiler          │ │ Model Provider Adapter       │
│ Snapshot / Manifest       │ │ Structured Decision         │
└──────────────┬────────────┘ └────────┬─────────────────────┘
               │                       │
               └──────────────┬────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Agent Step Engine  │
                    │ Decision Dispatcher│
                    └──────┬───────┬─────┘
                           │       │
             ┌─────────────▼──┐ ┌──▼─────────────────────┐
             │ Tool Gateway    │ │ Proposal + Verifier    │
             │ Registry/Policy │ │ Domain Command Adapter │
             └──────┬──────────┘ └──────────┬─────────────┘
                    │                       │
             ┌──────▼──────────┐     ┌──────▼─────────────┐
             │ Tool Executor    │     │ Experiment Domain  │
             │ Read/Smoke/etc.  │     │ Proposal Revision  │
             └─────────────────┘     └────────────────────┘
                           │
                  ┌────────▼───────────┐
                  │ Runtime Wakeup     │
                  │ Approval/Deadline  │
                  └────────────────────┘

All components
    → Transactional Runtime Event Enqueue
    → Agent Run Timeline Projection
```

## 3.1 组件职责

### Agent Runtime Service

负责：

- 创建 Agent Run；
- 管理 Run Lease；
- 推进 Run/Step 状态机；
- 检查预算；
- 调用 Context Compiler；
- 调用 Provider Adapter；
- 分派规范化 Decision；
- 暂停、恢复、终止；
- 调度固定用途 AgentRuntimeWakeupJob；
- 事务性登记 Runtime Event。

### Context Compiler

负责：

- 从受信来源选择本次任务所需上下文；
- 生成不可变 Context Snapshot；
- 标记来源、版本、哈希、Instruction Authority 与 Factual Provenance；
- 应用脱敏、裁剪和 Token 预算。

Context Compiler 的内部算法由 `04-context-and-repository.md` 定义。

### Model Provider Adapter

负责：

- 将统一请求映射到 Provider API；
- 使用 Structured Output / Tool Calling；
- 将 Provider 响应规范化为 `AgentDecision`；
- 记录 usage、finish reason 和错误类型；
- 隔离 Provider 特有字段。

### Agent Step Engine

负责：

- 为每个 Step 创建稳定 sequence；
- 调用模型；
- 持久化 Decision；
- 分派 Tool、Proposal、Human Input 或 Final Response；
- 持久化 Observation；
- 运行 no-progress 检测。

### Tool Gateway

负责：

- 解析 Tool name/version；
- 校验输入 Schema；
- 校验当前 Run 是否暴露该 Tool；
- 请求 Policy Decision；
- 创建或消费 Tool Execution Approval；
- 调用 Tool Executor；
- inspect/cancel/reconcile Unknown ToolCall；
- 规范化 Tool Result 与 ToolCallResolution。

Tool Contract 与策略由 `05-tools-policy-and-workspace.md` 定义。

### Proposal + Verifier Adapter

负责：

- 调用 Proposal Domain Service 创建 Revision；
- 执行 Deterministic / Rule Verifier；
- 在预算内将阻塞 Finding 返回 Agent 修复；
- Verifier 通过后结束 Agent Run，并把 Proposal 提交给用户审阅；
- P1 可调用 LLM Semantic Verifier，但不能自动批准。

---

# 4. Runtime 核心对象

## 4.1 AgentRun

`AgentRun` 表示一次有界、可审计的 Agent 任务。

至少包含：

```text
agent_run_id
project_id
trigger_type
user_goal
status
outcome
pause_reason
termination_reason
provider
model
budget_policy
context_snapshot_id
current_step_number
started_at
calendar_deadline_at
active_execution_seconds
suspended_at
ended_at
created_by
row_version
root_agent_run_id
parent_agent_run_id nullable
continuation_reason nullable
user_response_to_step_id nullable
interaction_correlation_id
```

P0 trigger type：

```text
interactive
```

P0 continuation reason：

```text
human_clarification
manual_retry
follow_up_request
```

不包含：

```text
system_recovery
```

系统恢复由 Coordinator、Executor Observation、Reconciliation 与 Runtime Event 表达。

Run lineage 规则：

- 根 Run 的 `root_agent_run_id = agent_run_id`；
- continuation Run 使用新的 `agent_run_id` 和新的预算；
- `parent_agent_run_id` 指向直接前序 Run；
- 同一用户任务链共享 `root_agent_run_id` 和 `interaction_correlation_id`；
- 新 Run 必须重新编译 Context Snapshot，不得直接恢复旧 Prompt。

## 4.2 AgentRunLease

同一 Agent Run 同一时刻最多一个 Runtime Worker 推进。

概念字段：

```text
agent_run_id
lease_owner
lease_generation
leased_until
heartbeat_at
```

规则：

- 每次成功领取 Lease，`lease_generation` 单调递增；
- 推进 Step 前必须取得 Lease；
- 所有推进 Run、Step 或消费 Approval 的写入必须携带当前 generation；
- 写入条件至少验证 `lease_generation`、`leased_until` 和 `row_version`；
- 旧 Worker 即使网络恢复，也不得覆盖新 Worker 的结果；
- 外部 Model/Tool 调用返回后，写回前必须重新验证 generation；
- Lease 已失效的外部结果只能保存为 detached response/observation，不直接推进 Run；
- `suspended` 或 terminal Run 不持有长期 Lease；
- Lease 只保护 Agent Runtime，不替代 Experiment Coordinator Lease。

## 4.3 AgentStep

一个 Step 表示一次逻辑决策周期，可以包含多个 Provider 调用 Attempt，但最多形成一个规范化 Decision。

至少包含：

```text
agent_step_id
agent_run_id
step_number
status
input_state_hash
selected_model_call_id nullable
agent_decision_id nullable
action_type nullable
tool_call_id nullable
proposal_revision_id nullable
observation_ref nullable
started_at
completed_at
row_version
```

约束：

```text
UNIQUE(agent_run_id, step_number)
UNIQUE(agent_decisions.agent_step_id)
```

P0 一个 Step 最多包含一个规范化动作。

## 4.4 ModelCall

保存一次具体 Provider 调用 Attempt 的可审计事实：

```text
model_call_id
agent_run_id
agent_step_id
attempt_number
retry_of_model_call_id nullable
superseded_by_model_call_id nullable
model_request_snapshot_id
provider
model
provider_profile_id
response_hash nullable
status
finish_reason
estimated_input_tokens
reserved_output_tokens
input_tokens nullable
output_tokens nullable
cached_tokens nullable
settled_total_tokens
usage_source nullable
provider_request_id nullable
started_at
ended_at
error_code nullable
```

`status`：

```text
intent_persisted
in_flight
completed
failed
unknown
invalid_output
```

约束：

```text
UNIQUE(agent_step_id, attempt_number)
```

`usage_source`：

```text
provider
local_estimate
conservative_fallback
```

规则：

- 一个 Step 可以有多个 ModelCall Attempt；
- failed、unknown 和 invalid_output 调用永久保留；创建 Retry 时，旧调用通过 `superseded_by_model_call_id` 显式失去 Decision 关联资格，但原物理调用状态不被覆盖；
- 最多一个 ModelCall 可以产生 AgentDecision；
- Decision 持久化后不得为该 Step 创建新 ModelCall；
- `selected_model_call_id` 指向产生 Decision 的 ModelCall；
- 请求发出前必须完成 Token 预算预留；
- Provider 未返回 usage 时不得将 Token 记为零；
- Timeout、未知响应和格式错误的调用同样消耗预算。

## 4.5 ModelRequestSnapshot

`ModelRequestSnapshot` 是一次逻辑模型请求的不可变输入快照，用于保证 Provider 重试使用同一请求语义。

至少包含：

```text
model_request_snapshot_id
agent_run_id
agent_step_id
context_snapshot_id
continuation_snapshot_id nullable
request_renderer_version
system_template_identity
system_template_hash
decision_schema_version
tool_manifest_ref
tool_manifest_hash
provider_profile_id
provider_parameters
canonicalization_version
canonical_request_ref
canonical_request_hash
redaction_version
retention_status
created_at
```

`canonical_request_ref` 指向已经完成 Secret 处理、消息排序、Tool Schema 固化与 Provider Envelope 规范化的逻辑请求载荷。它不包含 Provider 自动生成的 request ID、调用时间戳、网络重试标记或其他非语义传输字段。

规则：

- ModelCall intent 持久化前必须先绑定 Request Snapshot；
- 同一 Step 的 Provider retry 必须直接读取同一个不可变 `canonical_request_ref`，不得根据当前数据库状态重新渲染；
- Tool Manifest、System Template、Decision Schema、Provider Profile 或 Provider 参数变化时，不能视为同一逻辑 retry；
- Step 活跃期间，`canonical_request_ref` 必须可读取；
- Step 结束后，可以根据保留策略加密保存、脱敏或删除正文，但 Hash、Renderer Version、Tool Manifest、来源引用和保留动作必须继续保留；
- Secret 必须在形成 canonical request 之前完成删除、替换或引用化；
- 不保存 Provider 隐藏 Chain-of-Thought。

### 4.5.1 DetachedModelResponse

当外部模型响应到达时，原 Worker 已失去 Run Lease，响应必须先保存为不可变的 Detached Model Response：

```text
detached_response_id
model_call_id
agent_step_id
provider_request_id nullable
model_request_snapshot_id
response_hash
raw_response_ref nullable
parse_status
received_at
association_status
association_reason nullable
```

只有同时满足以下条件，当前持 Lease 的 Runtime Service 才能将该响应关联到 Step：

```text
ModelCall 属于当前 AgentStep
ModelRequestSnapshot 完全一致
AgentStep 尚无 AgentDecision
`superseded_by_model_call_id` 为空
没有后续 ModelCall 已经产生 Decision
当前 Worker 持有有效 lease_generation
AgentStep row_version CAS 成功
```

若后续 ModelCall 已经产生 Decision，迟到响应仅用于审计：不得更新 `selected_model_call_id`，不得创建第二个 Decision，不得执行其中的 Tool/Proposal 动作，也不得成为下一 Step 的 Observation。关联判断只能由当前 Runtime Service 按固定规则完成，不能交给旧 Worker、Provider Adapter、模型或前端。

## 4.6 AgentDecision

Provider 响应必须被转换为统一结构：

```text
agent_decision_id
agent_step_id
model_call_id
decision_schema_version
action_type
action_payload
concise_rationale
evidence_refs
raw_response_ref nullable
canonical_decision_hash
created_at
```

P0 action type：

```text
CALL_TOOL
PRODUCE_PROPOSAL
REQUEST_HUMAN_INPUT
FINAL_RESPONSE
```

`concise_rationale` 仅用于说明决策依据，不要求暴露内部思维链。`raw_response_ref` 只能指向经过脱敏和字段过滤的 Provider 响应；Provider 专有 reasoning/hidden-thought 字段不得持久化。

约束：

```text
UNIQUE(model_call_id)
UNIQUE(agent_step_id)
```

## 4.7 ToolCall

至少包含：

```text
tool_call_id
agent_run_id
agent_step_id
agent_decision_id
tool_name
tool_version
argument_schema_version
argument_canonicalization_version
canonical_arguments
arguments_hash
risk_level
policy_decision_id
approval_request_id nullable
execution_status
resolution_status
outcome_reason nullable
idempotency_key
precondition_hash
result_ref nullable
result_summary nullable
started_at
ended_at
error_code nullable
```

ToolCall 是 Agent Runtime 事实，不是正式 Experiment Attempt。`risk_level` 必须由 Tool Registry 与 Policy 计算，不能采信模型在 Decision 中自报的风险级别。

`execution_status` 描述外部动作实际发生了什么；`resolution_status` 描述系统是否仍在调查或处置。两者不得合并成单一状态。

Approval 身份至少绑定：

```text
tool_name
tool_version
argument_schema_version
argument_canonicalization_version
arguments_hash
policy_version
precondition_hash
```

## 4.8 ToolCallResolution

当 ToolCall 的物理副作用无法自动确认时，使用不可变 `ToolCallResolution` 记录人工或证据驱动的处置结果。

```text
tool_call_resolution_id
tool_call_id
resolution_type
evidence_refs
resolved_by
resolved_at
note
```

`resolution_type`：

```text
confirmed_succeeded
confirmed_failed
confirmed_not_executed
abandoned_unknown
```

映射：

| Resolution | execution_status | resolution_status |
|---|---|---|
| confirmed_succeeded | succeeded | resolved |
| confirmed_failed | failed | resolved |
| confirmed_not_executed | not_executed | resolved |
| abandoned_unknown | unknown | abandoned |

规则：

- `unknown + open` 表示仍需 Tool inspect、外部对账或人工判断，属于 active work；
- `unknown + abandoned` 表示停止自动调查，但保留潜在重复副作用警告，不再属于自动工作队列；
- `abandoned_unknown` 不满足任何依赖 Tool 成功的业务前置条件；
- Resolution 不得伪造原始执行事实；
- 原 ToolCall 不得自动重试；
- 后续再次执行必须创建新 Run 和新 ToolCall，并明确潜在重复副作用；
- 只有具有外部证据的 confirmed resolution 才可据此推进业务后续。

## 4.9 VerifierRun 与 VerifierResult

```text
verifier_run_id
proposal_revision_id
context_snapshot_id
verifier_type
verifier_version
status
started_at
ended_at
```

```text
verifier_result_id
verifier_run_id
overall_status
findings
result_hash
created_at
```

Finding 至少包含：

```text
code
severity
blocking
target_path
message
evidence_refs
suggested_fix
```

## 4.10 ContextSnapshot

Context Snapshot 是本次 Run 的不可变输入快照，不是长期 Memory。

至少包含：

```text
context_snapshot_id
project_id
compiler_version
source_manifest
rendered_context_ref nullable
rendered_context_hash
redaction_summary
token_estimate
created_at
```

详细结构由 `04-context-and-repository.md` 冻结。

## 4.11 AgentRuntimeWakeupJob

`AgentRuntimeWakeupJob` 是 suspended Run 的固定用途持久化唤醒对象，不是通用 Workflow DSL。

```text
wakeup_job_id
agent_run_id
wakeup_type
business_trigger_id
wakeup_at
status
lease_owner nullable
lease_generation
leased_until nullable
created_at
processed_at nullable
```

P0 wakeup type：

```text
approval_decided
approval_expiration
run_calendar_deadline
manual_resume
```

规则：

- Run 进入 suspended 时，必须事务性创建 Approval expiration 和 Run deadline wakeup；
- Approval Decision 事务成功后创建或激活 `approval_decided` wakeup；
- Wakeup 只是调度通知，Approval、AgentRun 和 ToolCall 表中的事务结果才是权威事实；
- Approval 相关 Wakeup 取得 Lease 后，先将仍可恢复的 Run 从 `suspended` 转为 `running`，再记录 Observation 并进行确定性处理；
- Run calendar deadline 可以直接将 `suspended` Run 推进为 `exhausted`，不恢复模型循环；
- Run cancel 可以直接将 `suspended` Run 推进为 `cancelled`，并使 Approval/ToolCall 失效；
- Run 已 terminal 时，后到 Wakeup 幂等忽略；
- Wakeup Job 不持有长期 Worker。

# 5. Agent Run 状态机

## 5.1 状态

```text
created
compiling_context
running
suspended
completed
failed
cancelled
exhausted
```

终态：

```text
completed
failed
cancelled
exhausted
```

## 5.2 允许迁移

```text
created
  → compiling_context
  → cancelled

compiling_context
  → running
  → failed
  → cancelled

running
  → suspended
  → completed
  → failed
  → cancelled
  → exhausted

suspended
  → running       # Approval 决定、拒绝、过期或人工恢复
  → cancelled     # Run Cancel
  → exhausted     # Run calendar deadline
```

终态不可逆。

Approval 相关 Wakeup 不得直接从 `suspended` 执行 Tool 或创建下一模型调用，必须先进入 `running`，由统一 Runtime 入口重新检查 Approval、Policy、Run Budget 与 Tool 前提。Run deadline 和显式取消属于 Run 自身的不可逆终止条件，可以直接进入终态。

## 5.3 Outcome 与 Termination Reason 分离

`status` 表示运行生命周期。

`outcome` 表示业务结果：

```text
proposal_ready
final_response_ready
human_input_required
no_deliverable
```

ToolCall 成功只是中间 Observation，不是 Agent Run 的业务交付结果。

`termination_reason` 表示结束原因：

```text
proposal_produced
final_response_produced
human_input_required
max_steps_exceeded
max_model_calls_exceeded
token_budget_exceeded
cost_budget_exceeded
active_time_exceeded
calendar_deadline_exceeded
no_progress
policy_blocked
approval_rejected
approval_expired
context_compile_failed
model_provider_failed
invalid_model_output
tool_failed
tool_state_unknown
verifier_blocked
internal_consistency_error
user_cancelled
```

不是所有失败都映射到 `failed`：

- 达到预算：`exhausted`；
- 用户取消：`cancelled`；
- Proposal 通过：`completed + proposal_ready`；
- 需要普通澄清：`completed + human_input_required`；
- Tool Approval 待决：`suspended`，尚无 termination reason。

## 5.4 Pause Reason 与 Run Continuation

P0 同一 Run 仅支持以下暂停原因：

```text
tool_approval_pending
```

普通业务澄清不保持同一 Run 长期暂停：

```text
REQUEST_HUMAN_INPUT
→ 当前 Run = completed + human_input_required
→ 用户回答后创建新的 interactive Run
```

新 Run 必须：

- 使用新的 `agent_run_id`；
- 分配新的 Run Budget；
- 重新编译 Context Snapshot；
- 将用户回答作为新的 `current_user_request`；
- 只引用上一 Run 的结构化 Deliverable、未解决问题和证据；
- 不直接拼接上一 Run 的全部 Prompt、Provider 原文或隐藏历史；
- 设置 `parent_agent_run_id`、`root_agent_run_id`、`continuation_reason` 和 `user_response_to_step_id`；
- 沿用同一 `interaction_correlation_id`。

此取舍避免把任意人机对话变成长期可恢复 Workflow。

# 6. Step 执行协议

## 6.1 Step 主流程

```text
1. Acquire AgentRun Lease generation
2. Verify Run is runnable
3. Check calendar deadline and non-token budgets
4. Build continuation input
5. Persist ModelRequestSnapshot and canonical request
6. Estimate input tokens and reserve maximum output tokens
7. Persist ModelCall Attempt intent and budget reservation
8. Call Provider using the immutable canonical request
9. Validate and normalize response
10. Settle token usage with provider/estimate/fallback accounting
11. Persist AgentDecision
12. Dispatch exactly one action
13. Persist Observation
14. Complete Step
15. Update Budget and Run state
16. Enqueue Runtime Event transactionally
```

任何步骤写回前都必须重新验证当前 `lease_generation`。预算不足时不得发起 Provider 请求。

## 6.2 模型决策持久化边界

关键边界：

```text
AgentDecision persisted
```

之后 Runtime 崩溃：

- 恢复时不重新调用模型；
- 继续处理既有 Decision；
- 若 Decision 为 Tool Call，继续 Schema/Policy/Approval/Execution；
- 若 Decision 为 Proposal，继续 Proposal Domain Command 和 Verifier。

## 6.3 Step 状态

```text
created
model_calling
decision_recorded
action_processing
observation_recorded
completed
failed
unknown
```

`unknown` 只用于无法确定外部 Tool 副作用是否发生的场景。

普通只读 Tool 不应长期停留在 `unknown`；有副作用 Tool 必须依据 Tool Contract 决定 reconcile、人工处理或失败关闭。

## 6.4 Provider 调用、重试与崩溃语义

一个 AgentStep 可以包含多个 `ModelCall Attempt`，但最多只有一个 Attempt 产生 AgentDecision。

### 请求尚未发送

允许重试当前 ModelCall 的发送操作；若实现无法证明请求未发送，则不得复用同一调用记录宣称“尚未发送”。

### 请求已发送但响应未知

若 Provider 支持通过 request ID inspect/retrieve：

- 优先查询原请求；
- 不立即重复生成。

若 Provider 不支持：

- 原 ModelCall 标记 `unknown`；
- 在同一 AgentStep 创建新的 ModelCall Attempt；
- `retry_of_model_call_id` 指向原调用；
- 原调用设置 `superseded_by_model_call_id` 指向新 Attempt；
- 新 Attempt 复用同一 ModelRequestSnapshot 与 canonical request；
- Trace 保留可能发生重复推理的事实；
- 因尚未持久化 Tool/Domain Decision，不会直接重复副作用。

### Provider 返回多个动作或无效结构

- 当前 ModelCall 标记 `invalid_output`；
- 不执行任何动作；
- 可在同一 Step 创建新的 ModelCall Attempt；
- 消耗 Token、模型调用和 no-progress 预算。

### 响应与 Decision 已持久化

- 设置 `selected_model_call_id`；
- 不得再为该 Step 创建 ModelCall；
- 恢复时继续处理既有 Decision。

### Lease 在外部调用期间失效

- 外部响应先保存为 `DetachedModelResponse`；
- 旧 Worker 不得直接推进 Run；
- 当前持 Lease 的 Runtime Service 按 4.5.1 的固定条件和 AgentStep CAS 尝试关联；
- 后续 ModelCall 已经产生 Decision 时，迟到响应只用于审计；
- 涉及 Tool 副作用时，进入 Tool inspect/reconciliation，而不是盲目重试。

# 7. Bounded Agent Loop

## 7.1 逻辑流程

```text
Load Goal
→ Compile Context
→ Model Decision
→ Validate Decision
→ Policy / Tool / Proposal Action
→ Observe
→ Verify
→ Continue or Terminate
```

## 7.2 RunBudget

```text
max_steps
max_model_calls
max_tool_calls
max_input_tokens
max_output_tokens
max_total_tokens
max_cost
max_active_seconds
max_calendar_seconds
max_proposal_repair_cycles
```

P0 在 Provider 无货币成本信息时，至少执行 Token 预算。

`max_calendar_seconds` 从 Run 首次开始计算，包含暂停等待时间；`max_active_seconds` 只累计 Context、Model、Tool 和 Verifier 的实际处理时间。Run 不得超过 calendar deadline，Approval 不得超过自身 expiration。

### 7.2.1 Model Profile 计量契约

每个 P0 Model Profile 必须声明：

```text
usage_accounting_mode
token_estimator
maximum_output_tokens
conservative_margin
```

不提供 Provider usage 且无法通过本地估算或保守上界结算的 Provider Profile，不满足 P0 bounded-loop 要求。

## 7.3 预算预留与结算

预算检查发生在：

- Context Compile 后；
- 每次 Model Call 前；
- 每次 Tool Call 前；
- Tool/Model Call 后更新 usage 时；
- 从 Approval 恢复时。

模型调用前，Runtime 必须预留：

```text
estimated_input_tokens
+ reserved_max_output_tokens
+ conservative_margin
```

预留后预算不足则不得发送请求。

调用后按以下优先级结算：

1. Provider 返回 usage：使用 Provider 数值，`usage_source = provider`；
2. Provider 未返回：使用与 Model Profile 绑定的本地 tokenizer/估算器，`usage_source = local_estimate`；
3. 只有近似估算器：使用保守上界，`usage_source = conservative_fallback`；
4. 无法可靠估算：终止 Run，`termination_reason = usage_unavailable`。

额外规则：

- 缺失 usage 不得记为零；
- Timeout、Provider Error、Invalid Output、Unknown Response 和 Retry 同样计入预算；
- 请求已经发出但响应未知时，至少保留调用前预留值；
- 后续获得 Provider usage 时允许修正 Token 账目，但不能恢复已消耗的 Step/ModelCall 次数；
- 等待 Approval 的日历时间不计入 `active_seconds`，但计入 Run calendar deadline；
- 模型不能自行提升预算；预算变更只能来自系统配置或明确用户操作，并记录 Runtime Event。

## 7.4 No-Progress Detector

出现以下任一条件时，Runtime 可终止为 `no_progress`：

1. 连续产生相同 Tool、相同参数、相同失败结果；
2. 连续生成相同 Proposal payload hash，且 Verifier 阻塞项未变化；
3. 连续生成无法通过 Decision Schema 的响应；
4. 连续请求未暴露 Tool；
5. Agent 在无新 Observation 时重复同一动作。

P0 默认阈值可配置，但不得无限自修复。

## 7.5 Proposal Repair Cycle

```text
PRODUCE_PROPOSAL
→ Create Proposal Revision
→ Deterministic Verifier
→ Rule Verifier
```

若存在 blocking finding：

- Findings 作为新的 Observation 返回 Agent；
- Agent 可以创建新 Proposal Revision；
- 每次修订消耗一个 repair cycle；
- 达到上限后终止 `verifier_blocked`；
- 不得自动批准或静默降级 blocking finding。

P0 建议默认：

```text
max_proposal_repair_cycles = 2
```

这是默认配置，不是领域不变量。

---

# 8. Context Compiler 契约

## 8.1 P0 输入层

```text
1. System Safety and Runtime Rules
2. Approved Project Policy
3. Current User Request
4. Repository Snapshot and Inspection Result
5. User-selected Experiment / Batch Summaries
6. Current Domain Object State
7. Exposed Tool Schemas and Risk Metadata
8. Run Budget and Limits
9. Non-authoritative repository/external content excerpts
```

## 8.2 Instruction Authority

Instruction Authority 决定一段内容是否有权要求 Agent 改变行为。

```text
system_policy
approved_project_policy
current_user_request
non_authoritative_content
```

优先级：

```text
system_policy
> approved_project_policy
> current_user_request
> non_authoritative_content
```

规则：

- Repository 内容默认属于 `non_authoritative_content`；
- README、源码注释、日志、论文和 Tool 输出不能改变 System Policy、Tool Risk 或 Approval 要求；
- 仓库中发现的 `LABPILOT.md` 默认不是高权限指令；
- 只有用户显式注册、批准并绑定内容哈希后，文件才能成为 `approved_project_policy`；
- Approved Project Policy 变化后，依赖旧 Policy 的 Context Snapshot 标记 stale，并在新 Run/恢复前重新编译。

## 8.3 Factual Provenance

Factual Provenance 描述事实来源，不授予控制权限。

```text
domain_fact
repository_observation
user_claim
agent_inference
external_untrusted_content
```

规则：

- `domain_fact` 表示由权威领域对象提供的版本化事实；
- `repository_observation` 表示基于 Snapshot/Inspection 得到的可定位观察；
- `user_claim` 可作为任务输入，但不自动覆盖已冻结领域事实；
- `agent_inference` 必须带证据引用和置信说明；
- `external_untrusted_content` 只作为数据，不得成为指令；
- 事实来源等级不得自动获得 Instruction Authority。

## 8.4 Snapshot 要求

Context Snapshot 必须记录：

- 来源对象 ID；
- 来源版本或内容哈希；
- Instruction Authority；
- Factual Provenance；
- 选择原因；
- 裁剪/摘要方式；
- Compiler 版本；
- Approved Project Policy 内容哈希；
- 脱敏结果；
- Token 估算。

## 8.5 Run 内上下文演进

初始 Context Snapshot 不原地修改。

后续 Step 使用：

```text
Initial Context Snapshot
+ Ordered AgentStep Decisions
+ Tool Observations
+ Verifier Findings
+ Approval Decisions
```

构建 continuation input。

需要压缩时，生成新的 `ContinuationContextSnapshot`，保留其输入 Step 范围和摘要哈希，不覆盖原始记录。

普通澄清创建的新 Run 不直接继承旧 continuation prompt；Context Compiler 仅引用上一 Run 的结构化 Deliverable、未解决问题与证据。

## 8.6 明确排除

P0 不自动加载：

- 全部聊天历史；
- 全部项目文件；
- 全部历史 Experiment；
- 无版本的自然语言 Memory；
- 未批准的 Repository 指令文件作为 Project Policy；
- Secret；
- SSH 私钥；
- 完整超大日志；
- 与当前任务无关的 Artifact 内容。

# 9. Model Provider Adapter

## 9.1 统一接口

概念接口：

```text
generate_decision(
    model_profile,
    context_snapshot,
    continuation_state,
    exposed_tools,
    decision_schema,
    budget
) -> ModelResponse
```

## 9.2 Provider Thread 边界

Provider 的 conversation/thread/response ID 仅作为传输和诊断元数据：

- 不等同于 AgentRun；
- 不拥有 Step、ToolCall、Approval 或 Proposal 状态；
- Provider Thread 丢失时，LabPilot 仍以 Context Snapshot 和 Runtime 对象为事实来源；
- 不通过 Provider 对话历史恢复科研领域状态。

## 9.3 P0 Provider 能力

P0 Provider Adapter 至少支持：

- structured output / JSON schema；
- tool calling；
- streaming 可选；
- Provider usage 接收；Provider 不提供时支持绑定 Model Profile 的本地估算或保守 fallback；
- provider request ID（若供应商提供）；
- 禁用或约束 parallel tool calls；
- 将原始 Provider Response 规范化为单一 Decision Candidate。

P0 不依赖 Provider Thread 作为恢复真相。

## 9.4 输出校验

模型输出必须经过：

```text
JSON parse
→ Decision Schema validation
→ exactly-one-action validation
→ action_type validation
→ action-specific payload validation
→ tool exposure check
```

多个 Tool Call、Tool 与 Final 混合或多个互斥动作均判定为 `multiple_actions_not_allowed`，不执行任何动作。

自然语言中提到“我已经执行”不产生任何系统事实。只有持久化 Decision 和后续系统动作才有效。

## 9.5 Model Error Classification

```text
provider_unavailable
rate_limited
timeout
invalid_response
schema_violation
multiple_actions_not_allowed
context_too_large
content_blocked
usage_unavailable
unknown_provider_error
```

是否重试由 Runtime 策略决定，模型不得自行判断重试次数。Provider 未返回 usage 本身是计量来源缺失，不自动视为 Model Failure；只有本地估算和保守 fallback 都不可用时才产生 `usage_unavailable`。

---

# 10. Tool 调用运行语义

## 10.1 Tool 暴露

Runtime 只向模型暴露本次 Context 与 Policy 允许发现的 Tool。

模型无法通过猜测 Tool 名称绕过 Registry。

## 10.2 Tool Call 流程

```text
AgentDecision(CALL_TOOL)
→ Resolve Tool name/version
→ Validate Input Schema
→ Canonicalize Arguments with versioned algorithm
→ Determine Risk
→ Policy Evaluation
→ allow / require_approval / deny
→ Execute or Suspend
→ Validate Tool Result Envelope
→ Persist Observation
```

## 10.3 ToolCall 状态机

### execution_status

```text
created
  → validating
  → awaiting_approval | executing | not_executed

awaiting_approval
  → executing | not_executed | cancelled

executing
  → succeeded | failed | unknown | cancelled

unknown
  → succeeded | failed | not_executed | unknown
```

物理终态：

```text
succeeded
failed
cancelled
not_executed
```

### resolution_status

```text
not_required
open
resolved
abandoned
```

规则：

- 普通非 unknown ToolCall 的 `resolution_status = not_required`；ToolCall 进入 unknown 时原子设置 `resolution_status = open`；
- `unknown + open` 属于 active work，需要 inspect、外部对账或人工处理；
- `unknown + abandoned` 不再自动调度，但物理事实仍保持 unknown；
- Policy deny、Approval rejected 或 Approval expired 在 Tool 尚未执行时使用 `execution_status = not_executed`，并记录 `outcome_reason`；
- Approval 只能使同一个 ToolCall 的 `execution_status` 从 `awaiting_approval` 进入 `executing`；
- 参数变化必须创建新的 ToolCall 和 Approval；
- 物理终态不得被后到普通结果覆盖；
- 人工 abandoned unknown 不会被伪装为 succeeded/failed；
- active work 查询必须同时检查 `execution_status` 与 `resolution_status`。

## 10.4 Policy Decision

Policy Decision 至少包含：

```text
policy_decision_id
tool_call_id
decision
risk_level
rule_ids
reason
required_approval_type
policy_version
precondition_hash
created_at
```

决策：

```text
allow
require_approval
deny
```

## 10.5 Tool Execution Approval 挂起

当 Policy 返回 `require_approval`：

1. 创建一次性 Tool Approval Request；
2. Approval 绑定 Agent Run、Tool identity、参数身份、Project、Policy、precondition 和 expiration；
3. ToolCall 的 `execution_status` 进入 `awaiting_approval`；
4. Agent Run 状态进入 `suspended`；
5. 在同一事务中创建 Approval Expiration 与 Run Deadline Wakeup；
6. Runtime 释放 Lease；
7. 不保持模型连接或 Worker；
8. 用户决定后创建/激活 `approval_decided` Wakeup。

Approval 身份必须包含：

```text
tool_name
tool_version
argument_schema_version
argument_canonicalization_version
arguments_hash
policy_version
precondition_hash
project_id
expiration
```

## 10.6 Wakeup 与到期竞争规则

V1 采用 PostgreSQL 事务顺序、数据库时钟和当前状态前置条件作为权威，不接受客户端声明的追溯性“事实时间”。

### Approval 决策事务

```text
lock Approval, ToolCall, AgentRun
→ require Approval.status = pending
→ require database_current_time < Approval.expires_at
→ require AgentRun not terminal
→ require database_current_time < AgentRun.calendar_deadline
→ write decision and database-generated decided_at
→ enqueue approval_decided Wakeup transactionally
```

### Approval 到期事务

```text
lock Approval, ToolCall, AgentRun
→ require Approval.status = pending
→ require database_current_time >= Approval.expires_at
→ mark Approval expired
→ set unexecuted ToolCall to not_executed / approval_expired
→ enqueue approval_expiration Wakeup transactionally
```

规则：

- 决策与到期事务只能有一个成功；
- `decided_at` 由服务端数据库生成，不能采信浏览器或客户端时间；
- Approval 一旦 `expired`，不接受追溯性批准；
- Approval 表是权威事实，Wakeup 只是调度通知；
- Approval 决策与 Run Cancel、Run Deadline 的竞争使用同样的行锁与状态前置条件；
- Approval 相关 Wakeup 在 Run 仍可恢复时先执行 `suspended → running`，再记录 Observation 并确定性处理；
- Run calendar deadline 可以直接执行 `suspended → exhausted`；
- Run/ToolCall 已终止时，后到 Wakeup 幂等忽略；
- 到期处理不调用 LLM 判断时间事实。

## 10.7 Approval 恢复检查

Approval 相关 Wakeup 取得当前 Run Lease 后，先将可恢复的 Run 从 `suspended` 转为 `running`，然后重新检查：

- Approval 事务结果为 approved；
- Approval 未被消费；
- Agent Run 未取消、未超过日历截止时间；
- Tool name/version 未变化；
- argument schema/canonicalization version 未变化；
- arguments hash 完全一致；
- Project 一致；
- Policy 仍未变为 deny；
- `precondition_hash` 对应的安全前提仍成立。

参数、Tool 版本、Canonicalization、Policy 或关键前提变化时，旧 Approval 失效，必须重新申请。

恢复后不得让模型重新生成已批准参数；Runtime 直接执行持久化 ToolCall。若 Approval 已 rejected/expired，则将结果作为 Observation，再进入下一模型 Step或按确定性规则结束。

## 10.8 Approval 拒绝

拒绝后：

- Tool 不执行；
- ToolCall 使用 `execution_status = not_executed`、`outcome_reason = approval_rejected`；
- Approval 和拒绝原因作为 Observation；
- Approval Wakeup 先恢复同一 Run 到 `running`；
- P0 默认允许 Agent 选择安全替代方案；
- 若无替代方案或预算不足，Run 结束为 `approval_rejected` 或 `policy_blocked`。

## 10.9 Unknown ToolCall Resolution

当 Tool Contract 无法自动 inspect 或确认副作用时，使用：

```text
ResolveUnknownToolCall
```

产生不可变 `ToolCallResolution`。

规则：

- ToolCall 进入 unknown 时，`resolution_status = open`；
- `confirmed_succeeded`、`confirmed_failed`、`confirmed_not_executed` 必须附外部证据，并将 `resolution_status = resolved`；
- `abandoned_unknown` 保留 `execution_status = unknown`，并将 `resolution_status = abandoned`；
- `unknown + abandoned` 不再进入自动 inspect 队列；
- Run 可以以 `tool_state_unknown` 结束，但必须保留潜在副作用警告；
- 原 ToolCall 不得自动重试；
- 再执行必须创建新 Run、新 ToolCall，并提示潜在重复副作用。

## 10.10 Run Cancel 传播

### Run 在等待 Approval 时取消

- Approval Request 失效或取消；
- ToolCall 转为 `execution_status = cancelled`；
- 后到 Approval Decision 不得消费。

### Tool 执行前取消

- ToolCall 转为 `execution_status = cancelled`；
- 副作用执行次数必须为 0。

### 只读 Tool 执行中取消

- best-effort cancel；
- 后到结果可以保存为 detached observation；
- 不得恢复已取消 Run。

### 副作用 Tool 执行中取消

- 调用 Tool Contract 的 cancel/inspect；
- 无法确认时 `execution_status = unknown`、`resolution_status = open`；
- Run 可结束为 `cancelled`，但必须保留 unresolved-side-effect warning；
- Run terminal 不表示外部副作用已经撤销。

## 10.11 Tool Result Envelope

```text
status
summary
structured_output
output_ref
side_effect_ref
warnings
error_code
retryable
duration_ms
content_hash
```

大型输出保存到受控 Runtime Blob 或文件引用，Trace 只保存摘要和哈希。除非由正式 Result 模块登记，否则 Agent Tool 输出不得自动成为科研 Experiment Artifact。

## 10.12 Tool 与正式实验边界

Yellow Smoke Tool：

- 只能在受控临时 Workspace；
- 有严格 timeout；
- 不创建 Trial/Attempt；
- 不产生正式 Metric；
- 不进入 Batch Summary；
- 不启动长训练。

正式训练只能：

```text
Approved Experiment Version
→ Run Batch / Trial / Attempt
→ Experiment Coordinator
→ Executor
```

# 11. Proposal 与 Verifier 流程

## 11.1 Proposal 生成

Agent 的 `PRODUCE_PROPOSAL` Decision 不直接构成 Experiment Version。

Runtime 调用 Proposal Domain Service：

```text
CreateProposalRevision(
    experiment_id,
    canonical_payload,
    provenance,
    context_snapshot_id,
    agent_run_id
)
```

Proposal Revision 为不可变记录。

## 11.2 Verifier 顺序

### Deterministic Verifier

检查：

- Schema；
- Entry point 存在性；
- 参数类型；
- seed 数量与唯一性；
- 参数矩阵展开；
- 输出路径冲突；
- Metric / Artifact 声明；
- timeout/retry；
- Red Tool 标注。

### Rule Verifier

检查：

- experiment_type 规则；
- Baseline/control；
- 参数与已知配置键；
- Trial 估算；
- Snapshot 新鲜度；
- Validation 覆盖度；
- Budget 限制。

### LLM Semantic Verifier（P1）

检查：

- 目标与设计是否匹配；
- 是否遗漏关键对照；
- 是否误用历史配置；
- Validation 是否能回答问题。

LLM Verifier 只输出 Finding，不自动批准、不覆盖程序 Finding。

## 11.3 Finding 严重度

```text
info
warning
error
```

`blocking=true` 的 Finding 阻止 Design Approval。

## 11.4 Proposal Ready 条件

只有同时满足：

```text
Deterministic Verifier passed
Rule Verifier passed
No blocking finding
Proposal Revision payload unchanged after verification
```

Agent Run 才能以：

```text
completed + proposal_ready
```

结束。

## 11.5 Design Approval 在 Agent Run 之外

用户审批 Proposal Revision 属于 Experiment Domain 流程，不是模型动作。

Agent：

- 不能批准自己的 Proposal；
- 不能把“建议批准”解释为批准；
- 不能因为 Tool Approval 已通过而推断 Design Approval；
- 不能在 Proposal 修改后复用旧 Verifier/Approval。

---

# 12. Agent Runtime 恢复语义

## 12.1 安全检查点

P0 支持从以下持久化边界恢复：

```text
Run created
Context compiled
Model Request Snapshot persisted
Model Decision persisted
Policy Decision persisted
Approval suspended + Wakeup persisted
Tool Result persisted
ToolCall Resolution persisted
Verifier Result persisted
Step completed
```

## 12.2 崩溃场景

### Decision 已持久化，动作未开始

继续执行既有 Decision。

### Model 请求已发出但结果未知

- 优先按 provider request ID inspect；
- 无法 inspect 时在同一 Step 创建新 ModelCall Attempt；
- 新 Attempt 复用原 ModelRequestSnapshot 与 canonical request；
- 不覆盖旧 ModelCall。

### Lease 过期后 Model Response 到达

- 响应保存为 `DetachedModelResponse`；
- 旧 generation 的 Worker 不得关联或推进 Run；
- 当前持 Lease 的 Runtime Service 按 4.5.1 的全部前置条件执行关联；
- 关联必须通过 AgentStep row_version CAS；
- 若后续 ModelCall 已经产生 Decision，迟到响应仅用于审计。

### Tool 已开始，结果未持久化

依据 Tool Contract：

- 可 inspect：先 reconcile；
- 幂等且可安全重放：使用原 tool_call_id / idempotency key；
- 无法确认副作用：ToolCall 使用 `execution_status = unknown`、`resolution_status = open`，Run 进入人工处理或失败关闭；
- 不得盲目重复副作用。

### Approval 挂起期间服务重启

- Run 保持 `suspended`；
- Wakeup Job 在数据库中持久存在；
- Approval Decision、expiration 或 Run deadline 到达后重新入队；
- Approval 相关 Wakeup 先恢复到 `running`，Run deadline 直接进入 `exhausted`；
- Worker 取得新的 `lease_generation` 后处理；
- 不依赖常驻进程发现到期。

### Proposal Revision 已创建，Verifier 未完成

恢复 Verifier，不重新生成 Proposal。

### Step 已完成，Run 更新未提交

通过 Step sequence、Run row version 和 lease generation 重新计算下一状态，不重复执行 Step 动作。

### Lease 过期后旧 Worker 返回

- 旧 generation 的推进写入失败；
- 外部响应保存为 detached response/observation；
- 关联由当前 Runtime Service 依据固定规则和 CAS 完成；
- 不允许旧 Worker恢复 Run。

## 12.3 Agent Runtime 与 Experiment Recovery 分离

Agent Runtime 只恢复 Agent Run 自身。

它不负责：

- 恢复训练进程；
- 查询 PID；
- 重提 Attempt；
- 回收 Artifact；
- 判断 Validation；
- 调整 Trial 投影。

这些由 `06-workflow-and-executors.md` 和 `07-results-validation-and-agentops.md` 负责。

# 13. 并发控制与幂等

## 13.1 Run Lease Generation Fencing

同一 Run 只能由一个 Worker 推进。

领取语义：

```text
lock AgentRun / Lease row
→ verify runnable
→ lease_generation = lease_generation + 1
→ set lease_owner / leased_until
→ commit
```

所有推进写入至少带：

```text
agent_run_id
lease_generation
row_version
```

并验证 Lease 尚未过期。旧 generation 不得写入。

## 13.2 Step Sequence

```text
UNIQUE(agent_run_id, step_number)
```

Step number 在持有当前 Run Lease 且通过乐观锁版本检查时分配。

## 13.3 ModelCall Attempt

```text
UNIQUE(agent_step_id, attempt_number)
```

同一 Step 可以有多个 ModelCall Attempt，但 Decision 持久化后不得新增 Attempt。

## 13.4 Decision 幂等

```text
UNIQUE(agent_decisions.model_call_id)
UNIQUE(agent_decisions.agent_step_id)
```

因此：

- 一个 ModelCall 最多产生一个 Decision；
- 一个 Step 最多产生一个 Decision。

## 13.5 ToolCall 幂等

同一个 AgentDecision 的 Tool Action 只能创建一个 ToolCall：

```text
UNIQUE(agent_decision_id)
```

ToolCall 的实际副作用幂等语义由 Tool Contract 定义。

## 13.6 Approval 消费

一次性 Approval 最多消费一次。

Approval 决策、到期、取消和消费均必须锁定 Approval、ToolCall 与 AgentRun，并使用数据库时钟和当前状态前置条件。

消费要求：

```text
approval.status = approved
consumed_at is null
AgentRun.status = running
current lease_generation matches
ToolCall.execution_status = awaiting_approval
```

消费与 ToolCall 的 `execution_status` 从 `awaiting_approval` 进入 `executing` 必须位于同一事务。客户端提供的时间戳不能覆盖数据库生成的 `decided_at`。

## 13.7 Wakeup 幂等

同一 Run、同一 Wakeup Type 与业务触发身份不得重复产生多个 active Wakeup。

处理前重新检查 Run、Approval 和 ToolCall 当前状态：

- Approval 相关 Wakeup 仅在 Run 仍 suspended 且未超过 deadline 时恢复到 running；
- Run deadline Wakeup 可以直接将 suspended Run 标记 exhausted；
- terminal Run 的 Wakeup 幂等结束；
- Wakeup 重复投递不得重复消费 Approval 或重复执行 Tool。

## 13.8 Proposal Revision

同一个 `PRODUCE_PROPOSAL` Decision 只能创建一个 Proposal Revision。

# 14. Trace、事件与审计

## 14.1 权威 Runtime 对象

以下对象保存当前 Agent Runtime 事实：

- AgentRun；
- AgentStep；
- ModelCall；
- ModelRequestSnapshot；
- DetachedModelResponse；
- AgentDecision；
- ToolCall；
- ToolCallResolution；
- VerifierRun/Result；
- ContextSnapshot；
- AgentRuntimeWakeupJob；
- Approval Request/Decision。

## 14.2 Runtime Event

每个关键状态变化在同一 PostgreSQL 事务中登记 Runtime Event / Outbox Record。

关键事件：

```text
agent_run_created
context_compile_started
context_compiled
agent_run_started
agent_step_started
model_call_started
model_call_completed
model_call_failed
agent_decision_recorded
policy_evaluated
tool_approval_requested
tool_approval_decided
tool_approval_consumed
agent_run_suspended
agent_runtime_wakeup_scheduled
agent_runtime_wakeup_processed
agent_runtime_wakeup_expired
agent_run_resumed
tool_call_started
tool_call_completed
tool_call_failed
tool_call_unknown
tool_call_resolution_recorded
tool_call_abandoned_unknown
tool_call_cancel_requested
proposal_revision_created
verifier_started
verifier_completed
agent_step_completed
agent_run_completed
agent_run_failed
agent_run_cancelled
agent_run_exhausted
```

## 14.3 Event Sequence

```text
UNIQUE(aggregate_type, aggregate_id, sequence)
```

Sequence 分配与对象状态更新处于同一事务。

## 14.4 Correlation 与 Causation

- `interaction_correlation_id`：一次用户任务或端到端业务链；
- `causation_id`：直接导致当前事件的前序 Event / Decision / Command；
- ToolCall、Proposal Revision、Approval 和 Verifier Result 必须可追溯回 AgentRun 与 AgentStep。

## 14.5 Timeline 不是恢复真相

Timeline 用于解释与诊断。

不得删除 AgentRun/Step/ToolCall 等权威对象后，尝试仅通过 Timeline 恢复 Runtime。

---

# 15. 安全、隐私与 Prompt Injection 边界

## 15.1 Secret 处理

禁止进入 Prompt、Trace、Model Response、Tool Result Summary 或 Artifact：

- API Key；
- SSH Private Key；
- Access Token；
- Cookie；
- 完整 Connection String；
- Secret 环境变量值。

Context Compiler 和 Tool Gateway 必须应用脱敏规则。

## 15.2 Repository 内容、Project Policy 与 Prompt Injection

Repository 中的：

- README；
- 注释；
- 配置；
- 日志；
- 文档；
- 测试数据；
- 未注册的 `LABPILOT.md`；

均属于 `non_authoritative_content`，不能改变：

- System Policy；
- Approved Project Policy；
- Tool Risk；
- Approval 要求；
- Workspace 根；
- Domain Invariant。

仓库文件只有在用户显式注册、批准并绑定内容哈希后，才能作为 `approved_project_policy`。Policy 内容变化时，旧 Policy 失效，相关 Context Snapshot 标记 stale。

Repository Observation 可以提供事实证据，但事实来源不自动获得指令权限。

## 15.3 Schema 与路径校验

模型生成的 Tool 参数：

- 必须通过 JSON Schema；
- 路径必须由 Tool 层 canonicalize；
- 不允许通过自由 Shell 字符串绕过参数结构；
- 高风险 Tool 必须绑定参数哈希 Approval。

## 15.4 输出最小化

大型日志、源码和 Artifact 不直接送入模型。

优先使用：

- bounded excerpt；
- structured summary；
- content hash；
- file reference；
- line range；
- parser output。

---

# 16. 失败分类与降级

## 16.1 Context Failure

```text
context_source_missing
context_source_stale
context_compile_failed
context_too_large
secret_redaction_failed
```

默认 fail closed，不在缺失关键 Snapshot 时继续生成正式 Proposal。

## 16.2 Model Failure

```text
provider_unavailable
rate_limited
timeout
invalid_response
schema_violation
multiple_actions_not_allowed
context_too_large
content_blocked
usage_unavailable
unknown_provider_error
```

可重试错误不得无限重试，必须消耗 ModelCall、Token 预留和时间预算。

Provider 未返回 usage 本身不是零成本调用：Runtime 必须使用本地估算或保守上界结算；完全无法估算时终止为 `usage_unavailable`。

## 16.3 Tool Failure

```text
tool_not_registered
tool_version_unavailable
invalid_arguments
policy_denied
approval_rejected
approval_expired
tool_timeout
tool_execution_failed
tool_output_invalid
tool_state_unknown
tool_resolution_abandoned
```

## 16.4 Verifier Failure

```text
verifier_runtime_failed
blocking_finding
stale_snapshot
proposal_payload_changed
```

Verifier Runtime 失败与 Proposal 验证不通过必须分开记录。

## 16.5 降级原则

- Agent 失败不修改既有 Experiment Version；
- Tool 失败不伪造成 Tool 成功 Observation；
- Verifier 不通过不自动放宽规则；
- Provider 不可用时允许人工编辑 Proposal；
- Agent Run 失败不终止已存在 Workflow/Attempt；
- P0 不依赖 LLM Semantic Verifier，因此其不可用不阻塞 Core。

---

# 17. 领域命令与授权边界

## 17.1 Agent 可授权调用

```text
CreateProposalRoot
CreateProposalRevision
RequestProposalVerification
ReadExperimentSummary
ReadBatchSummary
ReadTrialSummary
ReadRepositoryInspectionResult
```

具体命令仍由对应 Domain Service 校验。

## 17.2 Agent 不可授权调用

```text
FreezeApprovedProposal
CreateRunBatch
CreateAttemptForTrial
RecordAttemptTerminalState
RecordAttemptReconciled
CompleteValidationReport
SelectTrialAttemptResult
AuthorizeTrialRerun
```

这些命令分别要求用户审批、Coordinator、Executor Observation、Validation Engine 或明确人工授权。

## 17.3 Design Approval

Design Approval 的发起界面可以显示 Agent 产生的 Proposal，但 Approval Decision 来自用户，不来自 Agent Runtime。

## 17.4 Tool Approval

Tool Approval 只授权具体 ToolCall，不授权 Experiment Design，也不授权未来参数变化后的 ToolCall。

---

# 18. 与 Experiment Domain 的接口

## 18.1 Proposal

Agent Harness 通过 Proposal Domain Service 创建不可变 Revision。

必须传递：

```text
agent_run_id
agent_step_id
context_snapshot_id
canonical_payload
provenance
```

## 18.2 Experiment Version

Agent Harness 只能读取 Version，不创建、不修改。

## 18.3 Batch / Trial / Attempt

Agent Harness 可读取结构化摘要，但不得直接推进状态。

P1 Result Interpretation 只能读取：

- eligible Trial；
- selected Attempt；
- selected Validation Report；
- 明确版本的 deterministic Batch Summary。

## 18.4 Follow-up Proposal（P1）

Agent 可以基于结果创建新的 Proposal Revision，但不得：

- 直接重跑 Trial；
- 根据最好 Metric 自动选择 Attempt；
- 把 invalid Trial 当作证据；
- 自动批准 Follow-up Experiment。

---

# 19. P0 API / Command Surface

本文不冻结 URL，但冻结概念命令：

```text
StartInteractiveAgentRun
StartContinuationAgentRun
CancelAgentRun
RecordToolApprovalDecision
ResolveUnknownToolCall
GetAgentRun
ListAgentSteps
GetAgentRunTimeline
```

内部命令：

```text
CompileAgentContext
AdvanceAgentRun
ExecutePersistedDecision
ScheduleAgentRuntimeWakeup
ProcessAgentRuntimeWakeup
RunProposalVerifier
TerminateAgentRun
```

`StartContinuationAgentRun` 必须提供 parent Run、continuation reason 和用户响应引用，并重新编译 Context。

任意通用：

```text
PATCH /agent-runs/{id}/status
```

不允许直接暴露。

# 20. LG-CausalDiffAE 端到端示例

## 20.1 用户目标

```text
使用 LG-CausalDiffAE 当前最终配置，
在 circuit 上比较三个图发现阈值，
每个配置运行三个 seed，
并确保图结构与重构指标都被收集。
```

## 20.2 Context Compile

Context Compiler 选择：

- Project Manifest；
- clean Repository Snapshot；
- Repository Inspection Result；
- 当前 circuit 最终配置摘要；
- 用户明确选择的历史 Experiment/Batch；
- train/eval 入口候选；
- 可用只读 Tool 与 Smoke Tool；
- Run Budget。

不自动加载全部论文对话或全部历史日志。

## 20.3 Agent Step 示例

### Step 1

```text
Decision: CALL_TOOL
Tool: repository.read_file
Target: training entrypoint
```

Policy：Green，允许。

Observation：入口参数、默认配置、输出目录规则。

### Step 2

```text
Decision: CALL_TOOL
Tool: repository.search_config_keys
```

Observation：threshold、seed、dataset、checkpoint 参数来源。

### Step 3

```text
Decision: PRODUCE_PROPOSAL
```

Proposal：

- experiment_type = parameter_sweep；
- thresholds = 3；
- seeds = 3；
- 预计 Trial = 9；
- required metrics = reconstruction MAE、graph metrics；
- required artifacts = checkpoint、learned graph、evaluation JSON。

## 20.4 Verifier 修复

Deterministic Verifier 发现：

```text
OUTPUT_DIRECTORY_COLLISION
```

原因：输出模板未包含 `trial_condition_hash`。

Finding 返回 Agent。

Agent 创建新 Revision，修正输出模板。

Rule Verifier 再检查：

- 三个 seed 满足最低重复数；
- 参数矩阵展开为九个 Trial；
- Validation 覆盖 learned graph 和 reconstruction Metric；
- Repository Snapshot 未变化。

通过后 Agent Run 结束：

```text
status = completed
outcome = proposal_ready
termination_reason = proposal_produced
```

## 20.5 Approval 与执行边界

用户在 Agent Run 之外批准 Proposal Revision。

Experiment Domain 冻结 Version。

后续 Run Batch、Trial、Attempt 和训练过程由 Coordinator/Executor 处理，Agent 不等待训练。

---

# 21. 测试策略

## 21.1 单元测试

必须覆盖：

- Agent Run 状态迁移；
- Step sequence 唯一性；
- 一个 Step 多个 ModelCall Attempt、最多一个 Decision；
- ModelRequestSnapshot canonical request 重放；
- Token 调用前预留、Provider usage 结算与 fallback 结算；
- Budget 与 calendar deadline 检查；
- Decision Schema；
- 多 Tool Call 输出统一拒绝；
- Detached Model Response 关联前置条件与 superseded ModelCall 的审计-only 规则；
- no-progress 检测；
- Proposal repair cycle；
- Tool Approval suspend/resume；
- Approval 数据库时钟与到期竞争；
- Approval 参数身份与 canonicalization version 失效；
- ToolCall execution/resolution 正交状态；
- terminal state 不可逆；
- Instruction Authority 与 Factual Provenance 分离；
- hidden reasoning 不进入 Trace。

## 21.2 数据库约束测试

- 一个 Run 同时只能一个有效 Lease generation；
- 旧 generation 写入失败；
- `UNIQUE(agent_run_id, step_number)`；
- `UNIQUE(agent_step_id, attempt_number)`；
- 一个 ModelCall 最多一个 Decision；
- 一个 Step 最多一个 Decision；
- 一个 Decision 只能一个 ToolCall 或 Proposal Revision；
- Approval 决策与 expiration 事务只能一个成功；
- Approval 最多消费一次；
- Detached Response 关联 CAS 最多成功一次；
- Runtime Event aggregate sequence 唯一；
- terminal Run 的后到 Wakeup 幂等忽略。

## 21.3 集成测试

### 正常 Proposal 链

```text
Start Run
→ Compile Context
→ Read Tools
→ Produce Proposal
→ Verifier Pass
→ Run Complete
```

### Provider 多动作输出

```text
Provider returns two Tool Calls
→ no Tool executes
→ ModelCall invalid_output
→ same Step gets retry ModelCall
```

### ModelCall Retry

```text
ModelCall #1 response unknown
→ ModelCall #2 uses same Request Snapshot
→ one Decision persisted
→ no third call after Decision
```

### Verifier Repair

```text
Proposal invalid
→ Finding
→ Agent Revision
→ Verifier Pass
```

### Tool Approval

```text
Red Tool
→ Run Suspended
→ Wakeup persisted
→ Worker Released
→ Approval Granted
→ Same ToolCall Executed
→ Run Resumed
```

### Approval 到期竞争

```text
Approval Decision and expiration arrive concurrently
→ both lock Approval/ToolCall/Run
→ database clock and pending precondition allow exactly one transaction to succeed
→ expired Approval cannot be approved retrospectively
```

### Approval 参数变化

```text
Approval Granted
→ Tool version/schema/canonicalization/arguments changed
→ Old Approval rejected
→ Tool not executed
```

### 决策后崩溃

```text
Decision persisted
→ Runtime crashes
→ restart
→ existing Decision executed
→ model not called again
```

### Tool 状态未知

```text
side-effect Tool started
→ execution_status unknown + resolution_status open
→ no blind retry
→ confirmed Resolution closes as resolved, or abandoned_unknown closes management work while preserving unknown fact
```

### Run 取消传播

```text
Run cancelled while approval/tool pending
→ approval invalidated or Tool inspected/cancelled
→ late result cannot resume Run
```

### 普通澄清

```text
Run A requests human input and completes
→ user answers
→ Run B created with parent/root lineage
→ new Context Snapshot compiled
→ old prompt not directly resumed
```

## 21.4 Agent Evals

沿用 V1 Scope：

### EVAL-001 入口识别

是否根据 Repository Evidence 识别正确 train/eval 入口，并给出来源位置。

### EVAL-002 Proposal 完整性

是否包含 experiment type、参数、seed、Metric、Artifact、Validation、预算。

### EVAL-003 历史配置选择

是否使用用户明确选择和版本化的历史配置，而不是无来源聊天记忆。

### EVAL-004 Tool 安全

是否请求正确 Tool、遵守风险级别、在 Red Tool 前请求 Approval。

### EVAL-005 终止控制

是否在预算耗尽、重复失败或 no-progress 时停止。

## 21.5 故障注入

至少覆盖：

1. Context Compiler 在保存 Snapshot 前失败；
2. Model Request Snapshot 保存后、Provider 请求前崩溃；
3. Model 请求发送后响应未知；
4. Provider 返回多个 Tool Call；
5. Decision 保存后 Runtime 崩溃；
6. Policy Decision 保存后 Tool 未执行；
7. Approval 挂起期间 API/Worker 重启；
8. Approval Decision 与 expiration 并发；
9. Approval approved 与 Run cancelled 并发；
10. Run calendar deadline 到达但没有在线 Worker；
11. Tool 执行后结果保存失败；
12. Tool unknown 后人工 abandoned；
13. Run 取消时副作用 Tool 无法确认；
14. Proposal Revision 创建后 Verifier 崩溃；
15. Event projection 失败但事务内 enqueue 成功；
16. Run Lease 过期后旧 Worker 尝试写入；
17. 普通澄清创建 continuation Run 时旧 Policy 已变化；
18. Provider 不返回 usage，Runtime 使用本地估算或保守上界结算；
19. ModelCall #1 迟到响应到达，但 ModelCall #2 已产生 Decision；
20. Approval expiration 事务先成功，随后客户端提交带早期时间戳的批准；
21. ToolCall unknown 被人工 abandoned 后不再进入自动工作队列。

# 22. 可观测性与运行指标

P0 至少记录：

```text
agent_runs_total
agent_runs_by_outcome
agent_run_active_seconds
agent_steps_per_run
model_calls_total
model_tokens_total
model_tokens_by_usage_source
model_token_reservations_total
model_usage_unavailable_total
model_errors_total
model_call_retries_total
model_detached_responses_total
model_detached_responses_associated_total
model_detached_responses_audit_only_total
model_multiple_action_violations_total
tool_calls_total
tool_calls_by_risk
tool_approval_requests_total
tool_approval_wait_seconds
agent_runtime_wakeups_total
agent_runtime_wakeup_lag_seconds
agent_run_stale_lease_writes_rejected_total
tool_calls_unknown_total
tool_calls_unknown_open_total
tool_calls_unknown_abandoned_total
tool_resolutions_total
verifier_blocking_findings_total
proposal_repair_cycles
runs_exhausted_total
runs_no_progress_total
```

V1 不要求 Prometheus/Grafana；可先由 PostgreSQL 查询和结构化日志提供。

---

# 23. V1 简化与后续演进

## 23.1 P0 简化

- 单 Experiment Planning Agent；
- 单 Provider；
- 串行单动作 Step；
- 多 Tool Call Provider 输出统一拒绝；
- interactive trigger；
- Tool Approval 是唯一可恢复挂起；
- 固定 AgentRuntimeWakeup Job，不建设通用 Agent Workflow DSL；
- Context 由显式来源组成，并区分 Instruction Authority 与 Factual Provenance；
- Deterministic + Rule Verifier；
- 不保存自由文本长期 Memory；
- 不执行正式训练；
- Unknown ToolCall 需要 inspect 或显式 Resolution。

## 23.2 P1 演进

- LLM Semantic Verifier；
- Result Interpretation；
- Follow-up Proposal；
- 成本统计；
- richer Timeline；
- 更强 Context compression；
- 受控保留脱敏 Model Request/Response Artifact；
- 更丰富的 Tool Reconciliation Adapter。

## 23.3 P2 演进

- Subagent；
- 多 Provider 路由；
- Scheduled/Event-triggered Agent Run；
- 显式支持并行只读 Action Schema；
- Skill Registry；
- 跨项目只读参考；
- 受治理的长期 Project Memory；
- Agent benchmark suite。

# 24. 架构决策摘要

| 决策 | 选择 | 主要依据 | 代价 |
|---|---|---|---|
| Agent 数量 | P0 单 Planning Agent | 降低责任与 Tool 冲突 | 并行分析能力后移 |
| Step 模型 | 一 Step 一动作 | Trace、Approval、恢复清晰 | 并行 Tool 性能较低 |
| ModelCall | Step 对 Call 一对多，最多一个 Decision | 支持 Provider retry 且不覆盖历史 | 对象数量增加 |
| 动作顺序 | Decision 先持久化 | 防止崩溃后重新决策 | 增加 Runtime 状态对象 |
| 请求复现 | 不可变 canonical ModelRequestSnapshot | Provider retry 不重新渲染逻辑请求 | 活跃 Step 期间必须保留请求载荷 |
| Approval 挂起 | 同一 Run 逻辑挂起 + Wakeup Job | 不占用 Worker 且能处理到期 | 增加固定唤醒队列 |
| Lease | 递增 generation fencing | 阻止旧 Worker 写入 | 所有写路径需携带 generation |
| 普通澄清 | 结束 Run，新 Run 继续 | 不建设通用人机 Workflow | Run 数量增加 |
| Context 信任 | 指令权限与事实来源分离 | 防 Prompt Injection | 编译元数据更丰富 |
| Proposal 审批 | Run 外部完成 | Agent 不能自批 | 用户流程多一步 |
| Verifier | 程序优先，LLM P1 | 确定性与成本 | 规则需维护 |
| Tool 多动作 | P0 统一拒绝 | 保持一 Step 一动作 | 可能增加模型重试 |
| Unknown Tool | execution_status 与 resolution_status 正交 | 既不伪造外部事实，也能关闭管理工作 | 查询与处置逻辑更复杂 |
| Memory | 不建设自由文本长期 Memory | 防止事实漂移 | 依赖结构化 Domain/Context |
| Trace | 结构化决策，不存隐藏 CoT | 隐私与可解释性平衡 | 无法查看模型内部完整推理 |
| Token 预算 | 调用前预留、调用后结算 | usage 缺失时仍保持有界 | 需维护模型估算 Profile |
| Detached Response | 固定关联条件 + Step CAS | 迟到响应不会产生第二个 Decision | 增加审计对象 |
| Approval 竞争 | 数据库时钟 + 行锁前置条件 | 不接受追溯批准，竞争可证明 | 依赖单数据库权威时钟 |
| Provider | 单 OpenAI-compatible Adapter | 控制范围 | Provider 冗余后移 |
| 正式实验 | Agent 不直接执行 | 保持 Agent/Workflow/Executor 分离 | 需额外 Coordinator |

# 25. Agent Harness 局部不变量

```text
AH-INV-001
同一 Agent Run 同一时刻最多一个有效 Runtime Lease generation。

AH-INV-002
一个 AgentStep 可以包含多个 ModelCall Attempt，但最多只有一个 ModelCall 产生 AgentDecision。

AH-INV-003
AgentDecision 持久化后，恢复不得重新调用模型替换该 Decision。

AH-INV-004
P0 Provider Response 必须规范化为恰好一个动作；多个动作时不得执行其中任何一个。

AH-INV-005
未注册或未暴露 Tool 不得执行。

AH-INV-006
Tool 参数必须通过 Schema 和 Policy；Red Tool 必须消费匹配的 Approval。

AH-INV-007
Tool Approval 只授权具体 Tool/参数/Policy/Precondition 身份，不授权 Experiment Design。

AH-INV-008
Design Approval 位于 Agent Run 之外，Agent 不得批准自己的 Proposal。

AH-INV-009
Agent 不得直接写 Experiment Version、Trial、Attempt 或 Validation 权威状态。

AH-INV-010
Agent Run 结束、失败或取消不得自动终止既有 Workflow/Attempt。

AH-INV-011
Agent Run 暂停时不得保留常驻模型连接或等待 Worker。

AH-INV-012
Context Snapshot 必须可追溯到版本化来源，聊天摘要不得替代领域事实。

AH-INV-013
事实来源等级不得自动获得指令权限。

AH-INV-014
未经用户注册、批准和内容哈希绑定的仓库规则文件不得成为 approved_project_policy。

AH-INV-015
Secret 不得进入 Prompt、Trace、Model Output 或 Tool Result Summary。

AH-INV-016
Verifier blocking finding 未解决时，Proposal Revision 不得进入可批准状态。

AH-INV-017
模型自然语言声明不会直接产生 Tool、Approval、Validation 或领域状态事实。

AH-INV-018
有副作用 Tool 状态未知时不得盲目重复执行。

AH-INV-019
人工放弃 Unknown ToolCall 不得将未知物理事实伪装为 succeeded 或 failed。

AH-INV-020
Suspended Run 必须存在持久化 Wakeup Job，不得依赖常驻 Worker 等待。

AH-INV-021
所有推进 Run 的写入必须携带当前 lease_generation；旧 generation 不得写入。

AH-INV-022
普通澄清后的新 Run 必须重新编译 Context，不得直接恢复旧 Prompt。

AH-INV-023
达到预算、calendar deadline 或 no-progress 阈值时，Run 必须有明确终止结果。

AH-INV-024
Runtime Event 只用于审计和投影，不替代 AgentRun/Step/ToolCall 权威对象。

AH-INV-025
ModelCall retry 必须复用不可变 ModelRequestSnapshot，除非显式创建新的逻辑 Step。

AH-INV-026
Run 取消不得使后到 Approval、Tool Result 或 Model Response 恢复已终止 Run。

AH-INV-027
Approval 决策、到期与取消必须由数据库时钟、行锁和当前状态前置条件裁决；expired Approval 不得追溯批准。

AH-INV-028
同一 Step 的 Provider retry 必须读取同一个不可变 canonical request，不得重新渲染。

AH-INV-029
Provider 缺少 usage 时不得按零 Token 结算；无法估算的 Provider Profile 不得用于 P0。

AH-INV-030
ToolCall 的物理执行状态与管理处置状态必须分离；unknown + abandoned 不得被视为 Tool 成功。

AH-INV-031
Detached Model Response 只有在 Request Snapshot 一致、Step 尚无 Decision、当前 Lease 有效且 CAS 成功时才可关联。

AH-INV-032
Approval 相关 Wakeup 必须通过统一 Runtime 入口恢复到 running；Run deadline 和 cancel 可以直接进入不可逆终态。
```

# 26. 与后续文档的冻结接口

## 26.1 `04-context-and-repository.md` 必须提供

- Context Source Model；
- Instruction Authority；
- Factual Provenance；
- Approved Project Policy 注册、内容哈希和失效规则；
- Snapshot manifest；
- Repository Inspection evidence；
- token estimation Profile、Context token estimate 与预算输入；
- canonical Model Request 的 Context/Continuation 来源；
- redaction；
- continuation compression 与 continuation Run 输入选择。

不得改变本文的 Context Snapshot 不可变语义，也不得让 Repository 内容自动获得指令权限。

## 26.2 `05-tools-policy-and-workspace.md` 必须提供

- Tool Definition 与 Registry；
- Green/Yellow/Red 规则；
- argument schema 与 canonicalization version；
- Approval Request/Decision 身份；
- Tool Result Envelope；
- path/secret/workspace 安全；
- Tool inspect/cancel/reconcile/idempotency contract；
- Unknown ToolCall execution/resolution 双状态与 Resolution 证据要求。

不得允许 Tool 绕过本文的 Tool Gateway，也不得将人工 abandoned unknown 改写成确定执行事实。

## 26.3 `06-workflow-and-executors.md` 必须遵守

- 正式训练不由 Agent Tool 启动；
- Agent Run 不等待训练；
- Experiment Recovery 不创建 Agent Run；
- Coordinator 不依赖 Agent Runtime Lease/Wakeup；
- Agent 只能读取结构化执行摘要。

## 26.4 `07-results-validation-and-agentops.md` 必须提供

- Runtime Event 投影；
- Agent Run、continuation lineage 和 Wakeup Timeline；
- Model Request/Call、Tool/Resolution、Verifier 可观察视图；
- Secret redaction；
- DetachedModelResponse 的关联状态与审计视图；
- Token usage source、预算预留与结算视图；
- P1 eligible-result-only interpretation input。

# 27. 完成定义

本文冻结验收已确认以下条件：

1. Agent Run、Step、ModelCall Attempt、Decision、ToolCall、Resolution、Verifier 和 Context Snapshot 边界无冲突；
2. Run 状态机、Wakeup 和暂停恢复语义闭合；
3. 一 Step 一动作、多个 ModelCall Attempt、最多一个 Decision 的规则明确；
4. Tool Approval 与 Design Approval 完全分离；
5. Bounded Loop、Budget、calendar deadline 和 no-progress 有可执行定义；
6. Instruction Authority 与 Factual Provenance 已分离；
7. Lease generation fencing 能阻止旧 Worker 写入；
8. 普通澄清 continuation Run 有正式 lineage 和 Context 重编译契约；
9. Unknown ToolCall 的处置不伪造外部事实；
10. Agent 与 Experiment Domain、Coordinator、Validation 的写入边界清楚；
11. P0/P1/P2 范围没有混写；
12. 所有局部不变量可通过单元或集成测试验证；
13. Approval 决策/到期竞争、Token 计量、Detached Response 关联和 Unknown Tool 管理关闭均具有确定性规则；
14. 文档明确绑定冻结版 V1 Scope 与 Experiment Domain 上游契约；
15. 后续 `04–07` 能消费本文接口，而无需反向改写 Agent Runtime 核心语义。
