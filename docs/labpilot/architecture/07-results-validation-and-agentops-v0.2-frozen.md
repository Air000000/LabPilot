# LabPilot Results、Validation 与 AgentOps 架构规格

> 文档编号：LabPilot Architecture 07  
> 版本：v0.2  
> 状态：Approved / Frozen  
> 日期：2026-07-24  
> 适用范围：LabPilot V1 Core，P0 Local Reliable Core；P1 扩展边界单独标注

---

# 0. 文档定位

## 0.1 目的

本文定义 LabPilot 的结果处理、Artifact 注册、Metric 提取、确定性 Validation、Batch Summary、Runtime Event、Trace 与 AgentOps 查询投影。

本文回答以下问题：

1. `06-workflow-and-executors.md` 产生的 Collection Manifest 如何进入正式结果链；
2. Artifact 如何被注册、校验、保存和追溯；
3. Metric 如何从明确的 Artifact 或 Log 来源中确定性提取；
4. Validation 如何区分执行成功、结果无效与验证器错误；
5. Trial 如何依据不可变 Validation Report 成为 eligible、invalid 或 inconclusive；
6. Batch Summary 如何只使用 eligible Trial，并保持统计口径可复核；
7. Runtime Event 如何统一支持 Agent、Workflow、Trial/Attempt 三条 Timeline；
8. AgentOps 如何回答“发生了什么、为什么、基于什么证据”，但不替代权威领域状态；
9. Secret、Raw Output、Artifact 内容和 Event Payload 如何保持安全边界；
10. P0 与 P1 的能力边界在哪里。

本文不重新定义：

- Experiment、Version、Batch、Trial、Attempt 的领域语义；
- Agent Run、ModelCall、Tool Approval 与 Context 生命周期；
- Tool 风险、Workspace 与执行授权；
- Formal Attempt 的提交、监督、取消、Timeout 与 Reconciliation；
- LLM 科研结论或论文 Claim。

上述语义由上游冻结文档负责。

## 0.2 上游冻结契约

本文绑定以下不可改写的冻结文档：

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

06-workflow-and-executors-v0.2-frozen.md
SHA-256: dec726bea36cbbb6047f361be6ae4a4f99434405aaf96427efaad4434bd83732
```

正式冻结本文件前必须运行：

```bash
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/07-results-validation-and-agentops.dependencies.sha256
```

任一上游字节摘要不一致时，本文件不得标记为 Frozen。

## 0.3 约束优先级

出现冲突时，按以下顺序处理：

```text
上游冻结领域不变量
> 本文件明确不变量
> 本文件数据模型与状态机
> 示例与解释文字
```

本文不能通过新增 Result、Validation 或 AgentOps 对象，改变：

- Attempt 的物理执行事实；
- first-eligible 选择规则；
- selected Attempt 与 selected Validation Report 的关系；
- Summary 的 Decimal、样本标准差和 replicate dimension 语义；
- unknown execution 的阻塞语义；
- Tool/Attempt 两条 Lane 的分离；
- Runtime Event 非 Event Sourcing 的定位。

## 0.4 下游消费方

本文冻结后，以下模块可以直接消费：

```text
API / Query Service
Web UI / SSE Timeline
P1 Result Interpretation Agent
P1 MLflow Export Adapter
P1 OTel / External Observability Export
Implementation Plan
```

下游不得反向修改本文的 Artifact、Metric、Validation、Summary 和 Event 身份规则。

---

# 1. 核心结论

LabPilot V1 Result Plane 使用以下固定链路：

```text
Terminal Attempt
→ AttemptCollectionRun / Collection Manifest
→ ResultProcessingRoot / ResultProcessingRevision
→ Artifact Registration Logical Run / Execution Attempt
→ ArtifactPublishIntent / Managed Artifact
→ Metric Extraction Logical Run / Execution Attempt
→ Validation Input Snapshot
→ Validation Logical Run / Execution Attempt
→ Validation Report
→ Trial Result Selection / Authority Decision
→ Summary Logical Run / Execution Attempt
→ Batch Summary Revision
→ RuntimeEvent / Timeline Projection
```

核心边界：

```text
Attempt execution status
≠ Collection status
≠ Result-processing revision status
≠ Artifact registration status
≠ Metric extraction status
≠ Validation status
≠ Trial eligibility
≠ Batch Summary completeness
≠ Agent interpretation
```

P0 固定以下原则：

1. Artifact、Metric、Validation Report 和 Summary Revision 均不可原地覆盖；
2. Artifact 必须来自可追溯 Collection Manifest，并发布为受管、不可覆盖的 Managed Copy；
3. 正式科研 Metric 只能来自已注册 `metric_source` Artifact，不得把可清理 Runtime Log 作为唯一来源；
4. Validation Input Snapshot 必须能冻结 `missing`、`corrupted`、`parse_failed` 等负事实；
5. Validation 为确定性程序，不调用 LLM；
6. 确认科研要求不满足映射为 `failed/invalid`，无法确认事实映射为 `error/inconclusive`；
7. Worker 重试复用同一 Logical Run；Extractor、Validator、Rule 或处理计划变化创建新的 Result Processing Revision；
8. first-eligible 选择通过 Trial 行锁和 Experiment Domain Service 完成，禁止按 Metric 表现自动替换；
9. selected Validation Report 的权威 successor 必须通过不可变 `ValidationAuthorityDecision`；
10. Summary 使用冻结的 `SummaryNumericsV1`，只聚合 selected eligible Result；
11. unknown execution 和 unresolved Workflow 禁止生成正式 Summary；
12. RuntimeEvent 在权威事务中创建，Outbox 只投递已存在的 Event；
13. AgentOps 用于解释和查询，不替代任何权威状态；
14. P0 Artifact 使用受管本地文件系统与元数据，不建设完整 CAS；
15. P1 LLM Result Interpretation 只能读取指定 Summary Revision 和 eligible Evidence。

---

# 2. 设计目标与非目标

## 2.1 设计目标

- 结果来源可追溯到 Version、Trial、Attempt、Host Plan、Collection Manifest、Artifact 和 Extractor；
- Artifact 内容身份稳定、不可静默覆盖；
- Metric 提取具有版本、配置、输入和选择规则；
- Validation 输入可冻结并可重放；
- Validation 失败、验证器错误和执行失败能够清楚区分；
- Summary 可确定性重算并产生新 Revision；
- 所有关键操作具有幂等键和事务边界；
- 三条 Timeline 可解释 Agent、Workflow 和 Trial/Attempt 的完整链路；
- 查询投影可丢失并重建，但权威领域对象不可依赖投影恢复；
- 单机本地部署可落地，同时保留 S3/MinIO、MLflow、OTel 等 Adapter 扩展点。

## 2.2 非目标

P0 不建设：

- 完整 Content-Addressable Storage；
- 跨区域复制和复杂 Artifact Lifecycle；
- 自动清理正式科研结果；
- 任意用户上传的 Python Metric Extractor；
- 自动选择“最好模型”或“最好 Attempt”；
- 多变量高级统计检验平台；
- 自动生成论文 Claim；
- 全量 OpenTelemetry、Prometheus、Grafana 平台；
- 完整 Event Sourcing；
- 日志全文搜索集群；
- 向量化长期实验记忆；
- MLflow 作为领域真相来源；
- 自动把 Tool 临时输出升级为正式 Artifact。

## 2.3 P0 规模假设

- 单用户或单研究者；
- 单 PostgreSQL；
- 本地受管 Artifact Root；
- 低并发 Batch；
- 每个 Attempt 的结果量可由本地文件系统承载；
- Artifact 注册和 Validation 可由少量后台 Worker 执行；
- Timeline 查询以项目、Run、Workflow、Trial、Attempt 为主键；
- 不需要对亿级 Event 做实时分析。

---

# 3. 六类状态严格分离

## 3.1 Execution Fact

来源：

- Attempt；
- AttemptLaunch；
- Host Execution Plan；
- Supervision Unit；
- Executor Observation；
- Reconciliation Case。

回答：

```text
外部程序是否启动、运行、退出或状态未知？
```

Result Plane 不得改写该事实。

## 3.2 Collection Fact

来源：

- AttemptCollectionRun；
- Collection Manifest。

回答：

```text
执行输出中发现了哪些候选文件和日志？
```

Collection 成功不等于 Artifact 注册成功。

## 3.3 Result Processing Fact

来源：

- ResultProcessingRoot；
- ResultProcessingRevision；
- 各阶段 Logical Run；
- 各阶段 Execution Attempt。

回答：

```text
针对同一不可变 Collection 输入，使用哪一版处理计划，执行过哪些物理处理尝试？
```

Worker 崩溃重试与科研输入/实现版本变化必须分开表达。

## 3.4 Result Content Fact

来源：

- ArtifactPublishIntent；
- Artifact；
- ArtifactAvailability；
- MetricObservation；
- ExtractionIssue。

回答：

```text
有哪些可追溯、可读取、可验证的结果内容？
```

## 3.5 Validation Fact

来源：

- ValidationInputSnapshot；
- ValidationCheckResult；
- ValidationReport；
- ValidationAuthorityDecision。

回答：

```text
该 Attempt 的结果是否满足冻结 Validation Protocol，哪个 Report 是当前权威结论？
```

## 3.6 Summary Fact

来源：

- SummaryInputSnapshot；
- SummaryGenerationRun；
- BatchSummaryRevision；
- Summary Group；
- Metric Aggregate。

回答：

```text
在指定时刻、指定 eligible Trial 集合上，确定性统计结果是什么？
```

## 3.7 Trace / Projection

来源：

- RuntimeEvent；
- OutboxRecord；
- Timeline Projection；
- AgentOps Query Projection。

回答：

```text
这些权威事实如何产生、由什么动作导致、用户如何理解？
```

Trace 不替代上述任何事实对象。

---

# 4. 总体架构

```text
Attempt Workspace
    │
    │ AttemptCollectionRun
    ▼
Collection Manifest
    │
    ▼
ResultProcessingRoot
    │
    └─ ResultProcessingRevision(processing_plan_identity)
          │
          ▼
Artifact Registration Logical Run
          │
          ├─ ArtifactRegistrationExecutionAttempt(s)
          └─ ArtifactPublishIntent(s)
                    │
                    ▼
             Managed Artifact
                    │
                    ▼
Metric Extraction Logical Run
          │
          └─ MetricExtractionExecutionAttempt(s)
                    │
                    ▼
          Metric Observations / Extraction Issues
                    │
                    ▼
          Validation Input Snapshot
                    │
                    ▼
          Validation Logical Run
          └─ ValidationExecutionAttempt(s)
                    │
                    ▼
          Validation Report
                    │
                    ▼
          Experiment Domain Service
          ├─ first-eligible selection
          ├─ ValidationAuthorityDecision
          └─ Summary invalidation trigger
                    │
                    ▼
          Summary Logical Run
          └─ SummaryGenerationExecutionAttempt(s)
                    │
                    ▼
          Batch Summary Revision

Every authority transaction
    → insert immutable RuntimeEvent
    → insert OutboxRecord(event_id)
    → Timeline / AgentOps Projections
```

Runtime Log 位于 Execution/Diagnostic Plane。它可以用于 Expected Progress、错误诊断和 Timeline excerpt，但 P0 正式 Metric 只允许从 Managed `metric_source` Artifact 提取。

---

# 5. 组件职责

## 5.1 Result Processing Coordinator

负责：

- 接收 `06` 的 Collection handoff；
- 创建或读取唯一 `ResultProcessingRoot`；
- 选择不可变 `ResultProcessingRevision`；
- 调度 Artifact Registration、Metric Extraction、Validation 和 Summary Logical Run；
- 对 Worker 崩溃使用阶段 Execution Attempt 重试；
- 调用 Experiment Domain Service 更新选择和派生状态；
- 不直接改写 Attempt execution status。

## 5.2 Artifact Registry Service

负责：

- 读取 Collection Manifest；
- 验证候选路径和 Digest；
- 创建 Artifact Registration Logical Run 和 Execution Attempt；
- 通过 `ArtifactPublishIntent` 将正式 Artifact 发布到受管 Artifact Root；
- 生成不可变 Artifact 记录；
- 维护 ArtifactAvailability Projection；
- 应用 Retention Hold；
- 不解析科研 Metric。

## 5.3 Metric Extraction Service

负责：

- 选择冻结 Validation Protocol 声明的 Extractor；
- 只从已注册 `metric_source` Artifact 读取正式科研输入；
- 生成不可变 MetricExtractionRun 和 Execution Attempt；
- 保存 MetricObservation 与 ExtractionIssue；
- 不决定 Trial eligibility；
- 不执行任意用户 Python；
- 不把 Runtime Log 作为正式 Summary Metric 的唯一来源。

## 5.4 Validation Engine

负责：

- 构建可表达 missing/corrupted/parse_failed 的 ValidationInputSnapshot；
- 按冻结顺序执行确定性检查；
- 生成 ValidationCheckResult；
- 通过 ValidationRun/ExecutionAttempt 完成不可变 ValidationReport；
- 将结果交给 Experiment Domain Service；
- 不选择最好 Metric；
- 不执行 LLM 语义判断。

## 5.5 Summary Engine

负责：

- 构建 SummaryInputSnapshot；
- 根据 Version Summary Policy 分组；
- 使用 `SummaryNumericsV1` 计算 Decimal 聚合；
- 创建不可变 BatchSummaryRevision；
- 记录纳入/排除 Trial 和全部警告；
- 不产生科研 Claim。

## 5.6 Runtime Event Service

负责：

- 在权威状态事务中分配 Aggregate Sequence；
- 在同一事务中创建不可变 RuntimeEvent；
- 创建引用该 Event 的 OutboxRecord；
- 执行 Payload Schema 校验和 Redaction；
- 不作为权威业务写入口。

## 5.7 Projection Worker

负责：

- At-least-once 消费已经存在的 RuntimeEvent；
- 幂等构建 Timeline 与 Query Projection；
- 保存 Projection Watermark；
- 支持重建；
- 不回写领域事实。

## 5.8 AgentOps Query Service

负责：

- 读取权威摘要与投影；
- 提供 Agent、Workflow、Trial/Attempt Timeline；
- 返回 Projection Freshness；
- 提供处理 Revision、Worker Attempt、Validation、Authority Decision 和 Approval 查询；
- 不把 Event Payload 当作完整原始日志。

---

# 6. 来自 Workflow/Executor 的输入契约

## 6.1 正式 Result Pipeline 合法入口

Artifact/Metric/Validation 正式链只接受：

```text
Attempt.execution_status = succeeded
+ successful AttemptCollectionRun
+ verified Collection Manifest
+ frozen Validation Protocol
```

以下状态不得进入正式 Result Pipeline：

```text
Attempt = created/preparing/submitted/running
Attempt = failed/cancelled/unknown
Workflow = needs_attention
Collection status != succeeded
```

unknown execution 不得通过“结果文件看起来存在”被推断为 succeeded。

failed/cancelled/unknown Attempt 的诊断信息继续由 `06` 的 Runtime Log、ExecutorObservation、EvidenceManifest、ReconciliationCase 和 WorkflowClosureReport 保存，不创建普通 ResultProcessingRoot，也不进入 Metric/Validation/Summary。

## 6.2 Collection Manifest 是候选事实

Collection Manifest 仅说明：

- Workspace 中发现了哪些路径；
- 文件类型、大小和 Digest；
- 候选角色；
- Runtime Log 引用；
- Collection Schema 和输入身份。

它不说明：

- 文件已经成为正式 Artifact；
- Artifact 满足 Protocol；
- Metric 已经可用；
- Trial 已经 eligible。

## 6.3 Input Handoff Identity

```text
result_handoff_identity = SHA-256(
  "labpilot-result-handoff:v1\n"
  + canonical_json({
      "attempt_id": ...,
      "attempt_launch_id": ...,
      "host_execution_plan_hash": ...,
      "workspace_identity": ...,
      "collection_run_id": ...,
      "collection_manifest_hash": ...,
      "validation_protocol_identity": ...
    })
)
```

相同 Handoff 重复到达必须返回同一个 `ResultProcessingRoot`。Extractor、Validator、Artifact Rule 或 Redaction Policy 的变化不能创建第二个 Root，而应创建新的 `ResultProcessingRevision`。

---

# 7. Result Processing Root、Revision 与阶段执行

## 7.1 固定阶段

```text
received
→ register_artifacts
→ extract_metrics
→ build_validation_input
→ run_validation
→ apply_validation_result
→ request_summary
→ completed
```

可能的阻塞状态：

```text
retry_wait
needs_attention
failed
```

## 7.2 ResultProcessingRoot

```text
ResultProcessingRoot
- result_processing_root_id
- attempt_id
- result_handoff_identity
- created_at
```

约束：

```text
UNIQUE(result_handoff_identity)
```

Root 只表示“这一份不可变 Collection Handoff 的处理族”，不绑定某一版 Extractor、Validator 或 Artifact Rule。

## 7.3 ResultProcessingRevision

```text
ResultProcessingRevision
- result_processing_revision_id
- result_processing_root_id
- processing_revision_number
- processing_plan_identity
- reason_code
- status
- current_stage
- last_error_code nullable
- created_by
- created_at
- updated_at
- completed_at nullable
- row_version
```

约束：

```text
UNIQUE(result_processing_root_id, processing_revision_number)
UNIQUE(result_processing_root_id, processing_plan_identity)
```

`processing_plan_identity` 至少绑定：

```text
artifact_registration_schema_version
artifact_rule_set_identity
extractor_set_identity
validator_definition_identity
validation_protocol_identity
result_processing_schema_version
redaction_policy_identity
```

新 Revision 的合法原因：

```text
initial_processing
extractor_bug_fix
validator_bug_fix
artifact_rule_fix
redaction_policy_change
artifact_integrity_change
manual_reprocessing
```

改变科研语义仍必须创建新 Experiment Version，不能通过 ResultProcessingRevision 偷换 Protocol。

## 7.4 Logical Run 与 Execution Attempt

每个处理阶段都采用相同语义模板，但保留阶段专属表：

```text
Logical Run
└─ Execution Attempt #1
└─ Execution Attempt #2
...
```

规则：

- Logical Run 由不可变输入和实现身份确定；
- Worker crash、临时 I/O、Lease 失效在同一 Logical Run 下创建新 Execution Attempt；
- 输入、Extractor、Validator、Rule、Summary Engine 或 Processing Revision 变化时创建新 Logical Run；
- 已产生不可变输出后，重复请求返回已有输出；
- 不建设跨阶段通用 Workflow DSL。

## 7.5 ResultProcessingJob

```text
ResultProcessingJob
- result_processing_job_id
- result_processing_revision_id
- logical_run_type
- logical_run_id
- job_type
- dedupe_key
- status
- run_at
- lease_owner nullable
- lease_generation
- leased_until nullable
- attempt_count
- last_error_code nullable
- created_at
- updated_at
```

`run_at` 是权威调度事实。任何 UI 投影中的 `next_wakeup_at` 都是派生值。

---

# 8. Collection Manifest

## 8.1 CollectionManifest

```text
CollectionManifest
- collection_manifest_id
- collection_run_id
- attempt_id
- workspace_identity
- host_execution_plan_hash
- manifest_schema_version
- entries
- runtime_log_refs
- manifest_hash
- created_at
```

Collection Manifest 创建后不可修改。

## 8.2 Entry

```text
CollectionManifestEntry
- relative_path
- file_type
- size
- content_digest_type
- content_digest
- candidate_role
- declared_by_output_contract
- diagnostic_mtime
- discovery_status
```

`file_type`：

```text
regular_file
directory
symlink
special
```

P0：

- special file 不得成为 Artifact；
- symlink 必须解析并保持在 Attempt Workspace Root 内；
- mtime 不进入内容身份；
- relative path 使用 `/` 规范形式；
- Entry 按 UTF-8 path 稳定排序。

## 8.3 Candidate Role

```text
checkpoint
metrics_source
figure
resolved_config
model_output
diagnostic
report
generic
```

Candidate Role 不是 Artifact Type 的最终判定。Artifact Rule 会根据冻结 Protocol 再验证。

## 8.4 Manifest Identity

```text
collection_manifest_hash = SHA-256(
  "labpilot-collection-manifest:v1\n"
  + canonical_json(manifest_payload)
)
```

禁止直接 Hash `find`、`ls` 或其他命令的非规范文本输出。

---

# 9. Artifact Candidate、Logical Run 与发布协议

## 9.1 ArtifactCandidate

ArtifactCandidate 是 Collection Manifest Entry 与 Protocol Rule 的匹配结果：

```text
ArtifactCandidate
- artifact_candidate_id
- collection_manifest_id
- collection_entry_path
- candidate_role
- matched_rule_id nullable
- expected_artifact_type nullable
- source_digest
- registration_status
- created_at
```

Candidate 不是正式 Artifact。Required Artifact 没有匹配 Candidate 时，也必须生成显式 `artifact_match_result = confirmed_missing`，供 Validation Input Snapshot 使用。

## 9.2 ArtifactRegistrationRun

```text
ArtifactRegistrationRun
- artifact_registration_run_id
- result_processing_revision_id
- attempt_id
- collection_manifest_id
- registration_input_hash
- registration_schema_version
- artifact_rule_set_identity
- logical_run_key
- status
- successful_execution_attempt_id nullable
- artifact_count
- issue_count
- error_code nullable
- created_at
- completed_at nullable
```

约束：

```text
UNIQUE(logical_run_key)
```

`status`：

```text
pending
running
succeeded
failed
error
```

- `failed`：确定性的候选/Rule/内容问题，Validation 可以将结构化问题作为输入；
- `error`：Worker、Storage 或内部服务错误，允许同 Logical Run 下重试。

`logical_run_key`：

```text
SHA-256(
  "labpilot-artifact-registration-run:v1\n"
  + canonical_json({
      "result_processing_revision_id": ...,
      "registration_input_hash": ...,
      "artifact_rule_set_identity": ...,
      "registration_schema_version": ...
    })
)
```

## 9.3 ArtifactRegistrationExecutionAttempt

```text
ArtifactRegistrationExecutionAttempt
- artifact_registration_execution_attempt_id
- artifact_registration_run_id
- attempt_number
- lease_generation
- status
- started_at
- ended_at nullable
- error_code nullable
```

约束：

```text
UNIQUE(artifact_registration_run_id, attempt_number)
```

Worker crash、临时 Storage I/O 或 Lease 失效创建新的 Execution Attempt，不创建第二个语义相同的 ArtifactRegistrationRun。

## 9.4 ArtifactPublishIntent

文件系统与 PostgreSQL 不能组成一个原子事务，因此每个 Artifact 发布必须先创建：

```text
ArtifactPublishIntent
- artifact_publish_intent_id
- artifact_registration_run_id
- artifact_candidate_id
- publish_key
- expected_artifact_id
- expected_content_identity
- expected_digest
- staging_relative_path
- final_relative_path
- status
- created_at
- published_at nullable
- reconciled_at nullable
- row_version
```

状态：

```text
planned
staging
filesystem_published
database_committed
failed
orphaned
```

约束：

```text
UNIQUE(publish_key)
UNIQUE(expected_artifact_id)
```

最终路径固定为：

```text
artifacts/<artifact_id>/
```

不得按逻辑文件名复用或覆盖旧目录。

## 9.5 发布流程

```text
transaction:
    allocate artifact_id
    persist ArtifactPublishIntent
    commit

copy to staging
→ verify source and destination digest
→ fsync staged file/tree
→ atomic publish to artifacts/<artifact_id>/
→ write artifact-publish-marker.json
→ fsync final directory

transaction:
    lock ArtifactPublishIntent
    verify marker + digest + expected identity
    persist Artifact
    persist ArtifactAvailability
    mark Intent database_committed
    insert RuntimeEvent
    insert OutboxRecord(event_id)
    commit
```

源文件在 Collection 后发生变化时：

```text
ARTIFACT_SOURCE_CHANGED_AFTER_COLLECTION
```

Registration Run 失败；不能注册新内容并继续沿用旧 Manifest。

## 9.6 发布恢复

| 崩溃点 | 恢复 |
|---|---|
| staging 前 | 重新执行同一 Publish Intent |
| staging 中 | 清理不完整 staging，再执行同一 Intent |
| filesystem publish 后、DB 提交前 | 根据 Marker、Publish Key 和 Digest 恢复同一 Artifact |
| DB Artifact 存在、文件缺失 | Availability=`missing`，打开 Reconciliation |
| final path 已存在但身份不符 | fail closed，绝不覆盖 |

不得仅凭文件名或目录存在恢复 Artifact。

## 9.7 幂等

相同：

```text
attempt_id
+ source entry identity
+ source digest
+ registration schema
+ artifact rule set
+ target artifact type
```

返回同一 Logical Run、Publish Intent 和 Artifact。

同一路径内容不同必须由新的 Collection Manifest 和新的 ResultProcessingRoot 表达。

---

# 10. Artifact 权威对象

## 10.1 Artifact

```text
Artifact
- artifact_id
- project_id
- experiment_version_id
- batch_id
- trial_id
- attempt_id
- collection_manifest_id
- artifact_registration_run_id
- artifact_type
- logical_name
- storage_mode
- managed_storage_ref
- original_relative_path
- content_kind
- media_type nullable
- size
- integrity_type
- integrity_value
- content_access_class
- created_at
```

Artifact 对象不可更新。

## 10.2 Artifact Type

P0 允许：

```text
checkpoint
metric_source
figure
resolved_config
model_output
report
diagnostic
generic
```

Validation Protocol 使用 `rule_id + artifact_type + logical_name` 匹配 Required Artifact。

## 10.3 Storage Mode

P0 固定：

```text
managed_copy
```

P1 可扩展：

```text
external_reference
object_store
mlflow_reference
```

P0 不以 Attempt Workspace 中的可变原路径作为长期正式 Artifact 真相。

## 10.4 Artifact Identity

```text
artifact_content_identity = SHA-256(
  "labpilot-artifact-content:v1\n"
  + canonical_json({
      "artifact_type": ...,
      "content_kind": ...,
      "integrity_type": ...,
      "integrity_value": ...,
      "size": ...
    })
)
```

`artifact_id` 是实体身份；`artifact_content_identity` 是内容身份。

不同 Attempt 可以产生相同 Content Identity，但 P0 不做跨 Attempt 自动去重。

---

# 11. 文件与目录 Artifact

## 11.1 文件 Artifact

P0 File Artifact 必须：

- 是 regular file；
- 具有 SHA-256；
- 完整复制到 staging；
- Destination Digest 与 Source Digest 一致；
- Atomic publish 到唯一目录；
- 不允许后续原地覆盖。

推荐布局：

```text
artifact-root/
└─ projects/<project_id>/
   └─ artifacts/<artifact_id>/
      ├─ content/<safe_filename>
      └─ artifact-manifest.json
```

Artifact ID 目录不是 CAS key；相同内容不会自动共用文件。

## 11.2 目录 Artifact

P0 允许受控目录 Artifact，但必须使用 Manifest-backed snapshot：

```text
DirectoryArtifactManifest
- relative entries
- file type
- mode
- size
- content digest
- symlink target where permitted
- manifest_digest
```

注册时复制整个目录树到唯一 Artifact Root，并拒绝：

- Root escape；
- special file；
- 循环 symlink；
- 外部 symlink target；
- 注册过程中内容变化。

目录 Artifact 的 Integrity 是 `manifest_digest`，而不是目录 mtime。

## 11.3 大文件

P0 允许 Checkpoint 大文件，但：

- 使用流式复制和流式 SHA-256；
- 不将内容加载到内存；
- 不将二进制内容写入 Event 或 Agent Prompt；
- 注册失败保留 staging cleanup record；
- 不执行自动内容去重。

---

# 12. Artifact Availability 与 Retention

## 12.1 ArtifactAvailability

```text
ArtifactAvailability
- artifact_id
- status
- reason_code nullable
- last_verified_at
- current_storage_observation_hash nullable
- row_version
```

状态：

```text
available
missing
corrupted
quarantined
deleted
unverifiable
```

Availability 是可变投影，不进入 Artifact Identity。

## 12.2 Integrity Verification

读取 Artifact 用于 Validation 或 Summary 前：

- 至少验证 Availability；
- Strict Required Artifact 验证 Digest；
- 大文件可使用策略化周期重验，但第一次 Validation 必须验证；
- Digest mismatch 标记 corrupted；
- 不能用“文件存在”代替 Integrity。

## 12.3 Retention Hold

```text
ArtifactRetentionHold
- artifact_retention_hold_id
- artifact_id
- hold_type
- held_by_reference_type
- held_by_reference_id
- status
- created_at
- released_at nullable
```

Hold Type：

```text
selected_validation_report
batch_summary_revision
open_reconciliation
manual_preserve
```

存在 active Hold 时：

- Red Delete Tool 必须 fail closed；
- Artifact 不得物理删除；
- 不能只删除数据库行；
- 必须先解除所有合法引用或生成新权威结果。

## 12.4 自动清理

P0 不自动删除正式 Artifact。

可以自动清理：

- Registration staging；
- 已处理的临时复制；
- 不含证据的失败 staging；
- 满足 05 证据保留规则的 Tool Workspace。

---

# 13. Artifact 内容访问与 Secret 边界

## 13.1 Content Access Class

```text
metadata_only
redacted_text
restricted_binary
restricted_text
```

默认：

- Checkpoint：restricted_binary；
- Metric JSON/CSV：redacted_text；
- Figure：metadata_only，UI 可按授权展示；
- Config：redacted_text；
- Raw Log：不注册为普通 Agent-readable Artifact。

## 13.2 Text Scan

文本类 Artifact 发布为 Agent-readable 前：

```text
raw registration staging
→ secret scanner
→ redacted persistent copy or restricted classification
→ metadata/event exposure
```

Scanner 失败时：

- Artifact 可以保持 quarantined；
- 不生成 Agent Observation；
- Validation 若依赖内容读取则返回 error/inconclusive；
- 不把 Secret 值写入原因码或 Event。

## 13.3 Binary Artifact

P0 不声称能对任意二进制 Checkpoint 完成 Secret 检测。

Binary Artifact：

- 默认只暴露 metadata；
- 不进入 LLM Prompt；
- 不生成自动文本 preview；
- Validation 只执行大小、Digest、命名、结构化 metadata 等明确检查。

---

# 14. Metric Extractor Registry

## 14.1 MetricExtractorDefinition

```text
MetricExtractorDefinition
- metric_extractor_definition_id
- extractor_name
- extractor_version
- extractor_type
- input_media_types
- output_schema_version
- configuration_schema
- deterministic
- implementation_identity
- definition_identity
- created_at
```

Definition 不可变。

## 14.2 P0 Extractor Type

```text
json_path
jsonl_record
csv_column
regex_line
key_value_text
```

P0 不允许：

```text
arbitrary_python
shell_command
repository_supplied_extractor
notebook_execution
```

如研究仓库需要自定义计算，应在正式 Experiment Version 中定义 evaluation entrypoint，由 Attempt 执行并生成结构化 Metric Source Artifact，而不是让 Result Worker执行任意仓库代码。

## 14.3 Extractor Availability

```text
MetricExtractorAvailability
- metric_extractor_definition_id
- status
- availability_generation
- reason nullable
- changed_at
- row_version
```

状态：

```text
active
deprecated
disabled
removed
```

旧 Validation Report 继续引用历史 Extractor Identity。

## 14.4 Extractor Configuration

Extractor 配置属于 Validation Protocol Identity，至少包含：

```text
source_selector
metric_name
value_selector
step_selector nullable
label_selectors
selection_rule
multiplicity_rule
required
numeric_policy
```

改变 Metric 科研语义必须创建新 Experiment Version。

仅修复实现 Bug 且不改变规则含义，可以发布新的 Extractor Version 并重评估。

---

# 15. Metric Extraction Logical Run

## 15.1 MetricExtractionInputSnapshot

```text
MetricExtractionInputSnapshot
- metric_extraction_input_snapshot_id
- result_processing_revision_id
- attempt_id
- validation_protocol_identity
- artifact_ids_and_content_identities
- extractor_definitions_and_generations
- extractor_configurations
- input_snapshot_hash
- created_at
```

P0 正式科研 Metric 只允许从已注册：

```text
artifact_type = metric_source
```

提取。Runtime Log 不进入该 Snapshot。

Input Snapshot 不可变。

## 15.2 MetricExtractionRun

```text
MetricExtractionRun
- metric_extraction_run_id
- result_processing_revision_id
- attempt_id
- metric_extraction_input_snapshot_id
- input_snapshot_hash
- extraction_schema_version
- extractor_set_identity
- logical_run_key
- status
- successful_execution_attempt_id nullable
- metric_count
- issue_count
- error_code nullable
- created_at
- completed_at nullable
```

状态：

```text
pending
running
succeeded
failed
error
```

约束：

```text
UNIQUE(logical_run_key)
```

`logical_run_key`：

```text
SHA-256(
  "labpilot-metric-extraction-run:v1\n"
  + canonical_json({
      "result_processing_revision_id": ...,
      "input_snapshot_hash": ...,
      "extractor_set_identity": ...,
      "extraction_schema_version": ...
    })
)
```

## 15.3 MetricExtractionExecutionAttempt

```text
MetricExtractionExecutionAttempt
- metric_extraction_execution_attempt_id
- metric_extraction_run_id
- attempt_number
- lease_generation
- status
- started_at
- ended_at nullable
- error_code nullable
```

约束：

```text
UNIQUE(metric_extraction_run_id, attempt_number)
```

同一输入上的 Worker crash 或临时 Storage 错误创建新的 Execution Attempt。Extractor Version、Configuration、Input Snapshot 或 Processing Revision 变化创建新的 Logical Run。

## 15.4 失败分类

```text
SOURCE_ARTIFACT_UNAVAILABLE
INPUT_DIGEST_MISMATCH
EXTRACTOR_DISABLED
EXTRACTOR_CONFIG_INVALID
METRIC_PARSE_FAILED
METRIC_MULTIPLICITY_VIOLATION
EXTRACTOR_INTERNAL_ERROR
```

这些失败不改变 Attempt execution status。

状态语义：

- `failed`：源 Artifact 内容与冻结 Extractor/Protocol 的确定性不匹配，例如确认的 parse failure 或 multiplicity violation；
- `error`：Worker、Storage、Lease 或 Extractor 内部异常，无法形成科研结论。

其中：

- 确认存在的 Metric Artifact 无法按冻结 Protocol 解析，形成 `METRIC_PARSE_FAILED`，Validation 可判定 invalid；
- Artifact Store 暂时不可访问，形成 processing error，Validation 只能 inconclusive；
- Extractor 内部 Bug 需要发布新 Extractor Version，并创建新的 ResultProcessingRevision。

---

# 16. Metric Observation

## 16.1 MetricObservation

```text
MetricObservation
- metric_id
- project_id
- batch_id
- trial_id
- attempt_id
- metric_extraction_run_id
- metric_name
- value_kind
- raw_value_literal
- normalized_decimal_value nullable
- step_or_epoch nullable
- labels
- extractor_name
- extractor_version
- extractor_configuration_hash
- source_artifact_id
- source_content_identity
- source_location_within_artifact
- observed_at
- metric_identity
```

MetricObservation 不可更新。

`source_artifact_id` 为 P0 必填，不支持 `source_log_ref`。

## 16.2 Value Kind

```text
finite_decimal
non_finite_numeric
string
boolean
parse_error
```

正式数值 Summary 只接受：

```text
finite_decimal
```

NaN、Infinity 和非法数值可以形成 ExtractionIssue 或非有效 Observation，但不得进入聚合。

## 16.3 Decimal

数值必须从原始字符串直接构造 Decimal，禁止先转换为二进制 float。

Metric 持久化使用 canonical decimal literal；Summary 的具体中间精度和算法由 `SummaryNumericsV1` 冻结。

## 16.4 Metric Identity

```text
metric_identity = SHA-256(
  "labpilot-metric-observation:v1\n"
  + canonical_json({
      "attempt_id": ...,
      "metric_name": ...,
      "raw_value_literal": ...,
      "step_or_epoch": ...,
      "labels": ...,
      "extractor_identity": ...,
      "source_artifact_id": ...,
      "source_content_identity": ...,
      "source_location_within_artifact": ...
    })
)
```

## 16.5 Runtime Log 边界

Runtime Log 可以用于：

- Expected Progress Check；
- 错误诊断；
- Timeline excerpt；
- Executor/Reconciliation Evidence。

Runtime Log 不得作为进入正式 Summary 的 Metric 唯一来源。只输出 stdout Metric 的科研入口必须同时生成结构化 `metrics.json/jsonl/csv` Artifact。

## 16.6 禁止手工覆盖

P0 不允许直接编辑 Metric 值。

修复方式：

```text
新 Extractor Version
→ 新 ResultProcessingRevision
→ 新 MetricExtractionRun
→ 新 MetricObservation
→ 新 ValidationReport
```

旧记录继续保留。

---

# 17. Metric Selection 与 Multiplicity

## 17.1 Selection Rule

P0 支持确定性规则：

```text
exactly_one
last_by_step
first_by_step
max_step
named_final_record
```

不默认支持：

```text
best_value
minimum_metric
maximum_metric
```

因为“最好值”可能导致结果选择偏差。若科研 Protocol 确实要求该规则，必须作为 Version 语义明确冻结并通过 Verifier。

## 17.2 Duplicate

当 `exactly_one` 发现多个值时：

```text
METRIC_MULTIPLICITY_VIOLATION
```

不能静默取最后一个。

## 17.3 Step/Epoch

step/epoch 是 Metric 维度，不用于改变 Attempt Identity。

Summary 默认聚合 Protocol 选择后的标量结果，而不是所有训练过程点。

---

# 18. Validation Input Snapshot

## 18.1 目的

Validation 不能在运行过程中自由读取“当前最新”数据库内容。它必须绑定一个不可变输入快照，并能够冻结“缺失、损坏、解析失败”这些负事实。

## 18.2 ValidationInputSnapshot

```text
ValidationInputSnapshot
- validation_input_snapshot_id
- result_processing_revision_id
- attempt_id
- attempt_execution_fact_identity
- host_execution_plan_hash
- collection_manifest_id
- collection_manifest_hash
- artifact_registration_run_id
- artifact_ids_and_content_identities
- artifact_expectation_entries
- artifact_match_results
- artifact_availability_observation_ids
- artifact_integrity_check_results
- metric_extraction_run_id nullable
- metric_ids_and_identities
- metric_extraction_issue_ids
- required_metric_presence_results
- source_execution_binding_hash
- dataset_execution_binding_hash
- environment_execution_binding_hash
- validation_protocol_identity
- validator_definition_identity
- input_snapshot_hash
- created_at
```

## 18.3 可冻结的负事实

以下情况属于确定性的 Validation 输入，不阻止 Snapshot 构建：

```text
required artifact confirmed_missing
artifact digest confirmed_mismatch
required metric confirmed_missing
metric content confirmed_parse_failed
metric value confirmed_non_finite
```

只有以下情况阻止确定性结论，并形成 `error/inconclusive`：

```text
authority state unreadable
input identity cannot be confirmed
artifact storage temporarily unavailable
snapshot manifest corrupted
validator dependency unavailable
```

## 18.4 Build Preconditions

- Attempt 确认为 succeeded；
- Collection Manifest 身份可验证；
- Artifact Registration Logical Run 已达到可供判断的终态；
- Metric Extraction Logical Run 已达到可供判断的终态，或 Protocol 明确没有 Metric；
- Protocol Identity 与 Experiment Version 一致；
- 不存在 unknown execution；
- missing/corrupted/parse-failed 以结构化 Observation/Issue 进入 Snapshot，而不是被过滤掉。

## 18.5 固定映射

| 输入事实 | Validation 结果 |
|---|---|
| Required Artifact 确认不存在 | `failed / invalid` |
| Artifact Digest 确认不匹配 | `failed / invalid` |
| Required Metric 确认缺失 | `failed / invalid` |
| Metric 内容确认无法按 Protocol 解析 | `failed / invalid` |
| Artifact Store 暂时不可访问 | `error / inconclusive` |
| Artifact 内容身份无法确认 | `error / inconclusive` |
| Validator 内部异常 | `error / inconclusive` |

## 18.6 Snapshot Identity

```text
input_snapshot_hash = SHA-256(
  "labpilot-validation-input:v1\n"
  + canonical_json(validation_input_payload)
)
```

相同输入和 Validator Definition 必须得到相同检查结果。

---

# 19. Validation Engine 与执行模型

## 19.1 ValidationDefinition

```text
ValidationDefinition
- validator_definition_id
- validator_name
- validator_version
- supported_protocol_schema_versions
- check_registry_identity
- implementation_identity
- deterministic
- definition_identity
```

P0 Validator 必须 deterministic。

## 19.2 执行顺序

冻结顺序：

```text
1. Execution Check
2. Source/Dataset/Environment Snapshot Check
3. Expected Progress Check
4. Required Artifact Check
5. Metric Parse and Validity Check
6. Domain Rule Check
7. Eligibility Decision
```

前置失败后，后续可继续收集诊断，但不能伪造缺失前置条件。

例如 Required Artifact 缺失时，依赖该 Artifact 的 Metric Check 标记：

```text
skipped_due_to_missing_prerequisite
```

而不是同时报告误导性的 Validator Bug。

## 19.3 ValidationRun

```text
ValidationRun
- validation_run_id
- result_processing_revision_id
- attempt_id
- validation_sequence
- validation_input_snapshot_id
- input_snapshot_hash
- protocol_identity
- validator_definition_identity
- validation_run_key
- status
- successful_execution_attempt_id nullable
- created_at
- completed_at nullable
- error_code nullable
```

约束：

```text
UNIQUE(attempt_id, validation_sequence)
UNIQUE(validation_run_key)
```

`validation_run_key`：

```text
SHA-256(
  "labpilot-validation-run:v1\n"
  + canonical_json({
      "result_processing_revision_id": ...,
      "attempt_id": ...,
      "input_snapshot_hash": ...,
      "protocol_identity": ...,
      "validator_definition_identity": ...
    })
)
```

## 19.4 ValidationExecutionAttempt

```text
ValidationExecutionAttempt
- validation_execution_attempt_id
- validation_run_id
- attempt_number
- lease_generation
- status
- started_at
- ended_at nullable
- error_code nullable
```

约束：

```text
UNIQUE(validation_run_id, attempt_number)
```

Validator Worker crash、临时数据库/存储错误在同一 ValidationRun 下创建新的 Execution Attempt。Validator Definition、Input Snapshot、Protocol 或 Result Processing Revision 变化创建新的 ValidationRun。

---

# 20. Validation Check Result

## 20.1 对象

```text
ValidationCheckResult
- validation_check_result_id
- validation_run_id
- check_order
- rule_id
- rule_version
- check_category
- status
- severity
- reason_code nullable
- evidence_refs
- observed_value_ref nullable
- expected_value_ref nullable
- message_template_id
- created_at
```

状态：

```text
passed
failed
error
skipped
```

Severity：

```text
blocking
warning
informational
```

## 20.2 Evidence

Evidence 只能引用：

- Attempt execution fact；
- Execution Binding；
- Artifact expectation/match/availability/integrity Observation；
- Artifact ID/Content Identity；
- Metric ID/Identity；
- MetricExtractionIssue；
- Collection Manifest Entry；
- 受控、脱敏的 Runtime Log Evidence；
- 固定 Protocol Rule。

Runtime Log Evidence 只能支撑 Expected Progress 或诊断，不得作为正式 Summary Metric 来源。

禁止在 Report 中直接嵌入大文件或 Raw Secret 内容。

## 20.3 Reason Code

ValidationReport 对外使用稳定 Reason Code：

```text
EXECUTION_NOT_SUCCESSFUL
EXPECTED_PROGRESS_NOT_REACHED
REQUIRED_ARTIFACT_MISSING
ARTIFACT_INTEGRITY_FAILED
METRIC_PARSE_FAILED
METRIC_NAN_OR_INF
REQUIRED_METRIC_MISSING
SOURCE_SNAPSHOT_MISMATCH
DATASET_SNAPSHOT_MISMATCH
ENVIRONMENT_MISMATCH
BEST_EFFORT_DATASET_WARNING
DOMAIN_RULE_FAILED
VALIDATION_INPUT_UNAVAILABLE
ARTIFACT_IDENTITY_UNVERIFIABLE
INTERNAL_ARTIFACT_CONSISTENCY_FAILURE
VALIDATOR_INTERNAL_ERROR
```

Result Processing 内部错误码可以更细，但必须映射到稳定外部语义或 Validation `error`。

---

# 21. Validation Report

## 21.1 对象

```text
ValidationReport
- validation_report_id
- validation_run_id
- attempt_id
- validation_sequence
- validation_run_key
- protocol_identity
- validator_version
- validation_input_snapshot_id
- status
- eligibility_decision
- reason_codes
- blocking_failure_count
- warning_count
- evidence_manifest_hash
- created_at
```

Report 不可修改。

## 21.2 Status

```text
pending
running
passed
failed
error
```

- passed：Protocol 完整执行且无 blocking failure；
- failed：Protocol 完整执行并发现明确 blocking failure；
- error：Validator 或输入读取发生错误，无法形成确定性结论。

## 21.3 Eligibility

```text
eligible
invalid
inconclusive
```

固定映射：

```text
status = passed
→ eligibility = eligible

status = failed
→ eligibility = invalid

status = error
→ eligibility = inconclusive
```

Warning 不自动使 Trial invalid，除非 Protocol Rule 明确为 blocking。

## 21.4 Evidence Manifest

```text
ValidationEvidenceManifest
- validation_report_id
- check_result_ids
- artifact_ids_and_content_identities
- metric_ids_and_identities
- execution_binding_hashes
- runtime_log_evidence_refs
- manifest_hash
```

Summary 和 P1 Agent 只通过此 Manifest 追溯证据，不自由搜索 Attempt Workspace。

---

# 22. Validation 完成、first-eligible 与权威 successor

## 22.1 Validation Completion Transaction

完成 Validation 时，在同一 PostgreSQL 应用事务中：

```text
lock ValidationRun
→ persist ValidationCheckResults
→ persist immutable ValidationReport
→ call Experiment Domain Service
→ update Trial selection/projection where permitted
→ allocate aggregate sequence
→ insert immutable RuntimeEvent
→ insert OutboxRecord(event_id)
→ commit
```

Validation Engine 不直接写 Trial 表。Outbox 不负责创建 RuntimeEvent。

## 22.2 First Eligible

对于尚无 selected Attempt 的 Trial：

```text
第一个事务性完成 eligible ValidationReport 的 Attempt
→ selected_attempt_id
→ selected_validation_report_id
```

不能依据：

- Metric 大小；
- 性能表现；
- Agent 推荐；
- “看起来更稳定”。

## 22.3 已有 Selected Result

新 Attempt 即使 eligible：

- 不自动替换 selected Attempt；
- 保留 Validation Report；
- 需要 P1 `SelectTrialAttemptResult` 显式命令；
- Summary 继续使用当前 selected Result。

## 22.4 ValidationAuthorityDecision

同一 selected Attempt 的权威 Validation 结论变化必须创建不可变：

```text
ValidationAuthorityDecision
- validation_authority_decision_id
- trial_id
- attempt_id
- predecessor_validation_report_id
- successor_validation_report_id
- reason_code
- authority_type
- requested_by
- applied_at
- decision_identity
```

`authority_type`：

```text
mandatory_integrity_revalidation
extractor_bug_fix
validator_bug_fix
manual_authoritative_revalidation
```

命令：

```text
ApplyAuthoritativeRevalidationResult(
    trial_id,
    expected_selected_attempt_id,
    expected_selected_validation_report_id,
    successor_validation_report_id,
    reason_code,
    requested_by
)
```

前置条件：

1. Successor 属于当前 selected Attempt；
2. Predecessor 是当前 selected Validation Report；
3. Successor 使用允许的 Protocol、Extractor、Validator 和 Processing Revision lineage；
4. Trial 行锁或 CAS 成功；
5. Metric 表现不参与决定；
6. 重复命令按 `decision_identity` 幂等；
7. 不能通过该命令切换到另一个 Attempt。

## 22.5 Successor 效果

Authority Decision 成功后：

- 更新 `selected_validation_report_id`；
- Trial eligibility projection 按 Successor 更新；
- 触发新的 SummaryInputSnapshot/Summary Revision；
- 保留旧 Report 和 Decision lineage；
- 不自动选择其他 Attempt。

如果 Trial 尚未 selected，新 Revision 中第一个 eligible Report仍通过 first-eligible 规则参与选择。

## 22.6 Validator Error

Validator error：

- Attempt 仍 succeeded；
- Trial validation projection 为 inconclusive；
- Worker transient error在同一 ValidationRun 下创建新 Execution Attempt；
- Validator Bug 修复创建新 ResultProcessingRevision 和 ValidationRun；
- 不创建新 Attempt；
- 不进入 Summary。

---

# 23. Artifact、Extractor 与权威重处理

## 23.1 已选结果的 Retention

Selected Validation Report 和已发布 Summary 引用的 Artifact 必须持有 Retention Hold。

P0 正常路径下，不应出现“Selected Artifact 被用户删除后 Report 仍声称有效”。

## 23.2 Artifact Corruption 固定映射

```text
Digest 已确认与 immutable Content Identity 不一致
→ ARTIFACT_INTEGRITY_FAILED
→ Validation failed / invalid
```

```text
Artifact Store 暂时不可访问
→ VALIDATION_INPUT_UNAVAILABLE
→ Validation error / inconclusive
```

```text
Artifact metadata 存在，但内容身份无法验证
→ ARTIFACT_IDENTITY_UNVERIFIABLE
→ Validation error / inconclusive
```

```text
受 Retention Hold 保护的 Artifact 被记录删除或内容永久丢失
→ INTERNAL_ARTIFACT_CONSISTENCY_FAILURE
→ Validation error / inconclusive
→ 相关 Summary Revision 标记失效
→ 打开 Reconciliation
```

确认的科研要求不满足才是 invalid；系统无法确认事实时必须是 inconclusive。

## 23.3 新 Processing Revision

以下变化对同一 ResultProcessingRoot 创建新的 Revision：

- Extractor Bug Fix；
- Validator Bug Fix；
- Artifact Rule Fix；
- Redaction/Secret Scanner Policy 变化；
- Artifact Availability/Integrity 变化；
- 人工要求重新处理。

流程：

```text
same immutable Collection Handoff
→ new ResultProcessingRevision
→ new Artifact/Metric/Validation Logical Runs as needed
→ new immutable outputs
```

不得重跑 Attempt，也不得覆盖旧 Artifact、Metric 或 Report。

## 23.4 科研语义变化

如果改变：

- required Artifact；
- Metric selection/multiplicity；
- Eligibility Rule；
- Summary Policy；
- Evaluation Protocol 的科研含义；

必须创建新的 Experiment Version，不能仅创建 ResultProcessingRevision。

---

# 24. Summary Input Snapshot

## 24.1 Summary Preconditions

生成正式 Summary 前：

- Workflow 不得处于 `needs_attention`；
- Workflow 不得为 `closed_with_unresolved_execution`；
- Batch 不存在 unknown Attempt；
- Trial 选择和 Validation Projection 已稳定；
- Version Summary Policy 已冻结；
- 所有纳入 Trial 的 selected Artifact/Metric 可读取；
- Summary Engine Definition 可用。

## 24.2 SummaryInputSnapshot

```text
SummaryInputSnapshot
- summary_input_snapshot_id
- batch_id
- experiment_version_identity
- summary_policy_identity
- replicate_dimensions
- trial_selection_entries
- selected_validation_report_ids
- included_metric_ids_and_identities
- included_artifact_ids_and_content_identities
- excluded_trial_ids_and_reasons
- batch_execution_counts
- input_snapshot_hash
- created_at
```

`trial_selection_entries` 至少包含：

```text
trial_id
trial_condition_hash
selected_attempt_id nullable
selected_validation_report_id nullable
validation_state
resolved_trial_dimensions
```

## 24.3 Identity

```text
input_snapshot_hash = SHA-256(
  "labpilot-summary-input:v1\n"
  + canonical_json(summary_input_payload)
)
```

相同输入和 Summary Engine Version 必须产生相同 Summary 内容。

---

# 25. Summary Group

## 25.1 Group Identity

```text
summary_group_key = SHA-256(
  "labpilot-summary-group:v1\n"
  + canonical_json({
      "experiment_version_identity": ...,
      "resolved_dimensions_excluding_replicates": ...
    })
)
```

P0 replicate dimension：

```text
seed
```

## 25.2 Group Object

```text
BatchSummaryGroup
- batch_summary_group_id
- batch_summary_revision_id
- group_key
- group_dimensions
- eligible_replicate_count
- minimum_required_replicates
- completeness_status
- included_trial_ids
- excluded_trial_ids_and_reasons
- created_at
```

`completeness_status`：

```text
satisfies_minimum
insufficient_replicates
no_eligible_replicates
```

## 25.3 禁止跨组默认聚合

不同非 replicate 参数 Group 不计算默认总体均值。

只有 Version Summary Policy 明确声明科研上有意义的 Cross-group Rule 时才能执行；P0 默认不支持自定义 Cross-group Rule。

---

# 26. Metric Aggregate 与 SummaryNumericsV1

## 26.1 MetricAggregate

```text
MetricAggregate
- metric_aggregate_id
- batch_summary_group_id
- metric_name
- count
- missing_count
- mean_decimal nullable
- std_decimal nullable
- min_decimal nullable
- max_decimal nullable
- warning_codes
- contributing_metric_ids
- aggregate_identity
```

## 26.2 SummaryNumericsV1

P0 冻结以下确定性实现规范：

1. 输入 Metric 必须是 canonical decimal literal；
2. 输入按 `trial_condition_hash → seed → trial_id → metric_id` 稳定排序；
3. 使用 Python `decimal.Decimal`；
4. 中间 Context precision 固定为 50；
5. rounding 固定为 `ROUND_HALF_EVEN`；
6. Mean 使用稳定顺序 sequential sum；
7. Variance 使用固定 two-pass 算法：

   ```text
   mean = sum(x) / n
   variance = sum((x - mean)^2) / (n - 1)
   ```

8. Standard deviation 使用同一 Decimal Context 的 square root；
9. 最终 mean/std/min/max 规范化为 34 位有效数字；
10. 输出为 canonical decimal string；
11. 禁止 NaN、Infinity；
12. `-0` 规范化为 `0`；
13. `count < 2` 时 `std = null`；
14. `count < 2` 时增加 `INSUFFICIENT_REPLICATES_FOR_STD`。

`summary_numerics_identity` 必须进入 `summary_engine_identity` 和 Summary Revision。

P0 只承诺冻结的 Python Decimal `SummaryNumericsV1` 可以确定性重现。其他语言实现必须通过同一 conformance vectors 后才能声明兼容。

## 26.3 Required/Optional Metric

Required Metric 缺失：

- 对应 Trial 应已在 Validation 阶段 invalid；
- 不进入 Summary。

Optional Metric 缺失：

- Trial 仍可纳入其他 Metric；
- 该 Metric 独立记录 `count` 与 `missing_count`。

## 26.4 Non-finite

NaN/Infinity 不得进入 Aggregate。

若 SummaryInputSnapshot 中出现 non-finite selected Metric，Summary Engine fail closed，并要求修复 Validation 一致性。

## 26.5 Aggregate Identity

```text
aggregate_identity = SHA-256(
  "labpilot-metric-aggregate:v1\n"
  + canonical_json({
      "summary_group_identity": ...,
      "metric_name": ...,
      "contributing_metric_identities": ...,
      "summary_numerics_identity": ...,
      "canonical_output_values": ...
    })
)
```

---

# 27. Summary Logical Run 与 Batch Summary Revision

## 27.1 SummaryGenerationRun

```text
SummaryGenerationRun
- summary_generation_run_id
- result_processing_revision_id nullable
- batch_id
- summary_sequence
- summary_input_snapshot_id
- summary_input_snapshot_hash
- summary_engine_identity
- summary_numerics_identity
- logical_run_key
- status
- successful_execution_attempt_id nullable
- error_code nullable
- created_at
- completed_at nullable
```

约束：

```text
UNIQUE(batch_id, summary_sequence)
UNIQUE(logical_run_key)
```

`logical_run_key`：

```text
SHA-256(
  "labpilot-summary-generation-run:v1\n"
  + canonical_json({
      "summary_input_snapshot_hash": ...,
      "summary_engine_identity": ...,
      "summary_numerics_identity": ...
    })
)
```

## 27.2 SummaryGenerationExecutionAttempt

```text
SummaryGenerationExecutionAttempt
- summary_generation_execution_attempt_id
- summary_generation_run_id
- attempt_number
- lease_generation
- status
- started_at
- ended_at nullable
- error_code nullable
```

约束：

```text
UNIQUE(summary_generation_run_id, attempt_number)
```

Worker crash、临时 I/O 或 Lease 失效创建新的 Execution Attempt。Input Snapshot 或 Summary Engine/Numerics 变化创建新的 Logical Run。

## 27.3 BatchSummaryRevision

```text
BatchSummaryRevision
- batch_summary_revision_id
- batch_id
- summary_revision_number
- summary_input_snapshot_id
- experiment_version_identity
- summary_policy_identity
- summary_engine_identity
- summary_numerics_identity
- status
- total_trials
- eligible_trials
- invalid_trials
- inconclusive_trials
- failed_or_exhausted_trials
- cancelled_trials
- attempt_count
- retry_count
- confirmed_total_target_runtime_seconds
- runtime_unknown_attempt_count
- reproducibility_warnings
- summary_content_hash
- generated_at
```

Report 不可覆盖。

## 27.4 Revision Trigger

以下变化产生新 Revision：

- Trial selected Attempt 改变；
- selected Validation Report 改变；
- Trial 从 eligible 变为 invalid/inconclusive；
- 新 Trial eligible；
- Artifact/Metric 权威重评估变化；
- Summary Engine 或 SummaryNumerics Bug Fix 发布新版本；
- Included/Excluded 集合变化。

## 27.5 Runtime 统计

`confirmed_total_target_runtime_seconds`：

- 只累加具有 confirmed start/end 的 target duration；
- 不含 Prepare、Collection、Validation 时间；
- unknown duration 不猜测；
- 单独记录 `runtime_unknown_attempt_count`。

## 27.6 Summary Status

```text
complete
incomplete_replicates
no_eligible_results
error
```

`complete` 只表示 Summary 生成过程完整且所有 Group 满足最小重复要求，不表示所有 Trial 都成功。

---

# 28. 不完整 Batch 与 unresolved execution

## 28.1 可以生成 Summary 的情况

Batch 存在以下 Trial 时仍可生成 Summary：

```text
invalid
failed/exhausted
cancelled
```

但必须显示完整计数和排除原因。

## 28.2 不允许生成正式 Summary 的情况

```text
存在 Attempt = unknown
Workflow = needs_attention
Workflow = closed_with_unresolved_execution
```

此时生成的是：

```text
WorkflowClosureReport
```

而不是 BatchSummaryRevision。

## 28.3 WorkflowClosureReport

```text
WorkflowClosureReport
- workflow_closure_report_id
- workflow_id
- batch_id
- closure_status
- unresolved_attempt_ids
- reconciliation_case_ids
- evidence_manifest_hashes
- known_completed_trial_counts
- warnings
- created_at
```

它用于审计，不得被 P1 Agent 当作科研 Summary。

---

# 29. P1 Result Interpretation

## 29.1 边界

P1 Agent 可以生成：

- Result Interpretation Draft；
- Anomaly Explanation Candidate；
- Follow-up Experiment Proposal；
- Figure/Report Draft。

不能生成权威：

- Validation eligibility；
- selected Attempt；
- Batch Summary 数值；
- 正式科研 Claim；
- 自动重跑授权。

## 29.2 输入白名单

P1 只能读取：

- 指定 BatchSummaryRevision；
- included eligible Trial；
- selected Validation Report；
- Validation Evidence Manifest；
- Agent-readable Artifact metadata/redacted text；
- Experiment Version 与 Research Context。

不得读取：

- invalid Trial 作为正向科研证据；
- unknown Attempt；
- raw quarantine；
- 未脱敏 Log；
- 未选中的更优 Metric 进行 cherry-pick。

## 29.3 输出定位

LLM 输出保存为：

```text
ResultInterpretationProposal
```

它是解释提案，不是 Summary Revision 或论文事实。

---

# 30. Runtime Event

## 30.1 定位

RuntimeEvent 用于：

- 审计；
- Timeline；
- 诊断；
- AgentOps 查询投影；
- 外部 Observability Export。

RuntimeEvent 不用于：

- 代替 Domain Row；
- 从零恢复所有权威状态；
- 保存大日志和 Artifact；
- 保存隐藏 Chain-of-Thought。

## 30.2 Schema

```text
RuntimeEvent
- event_id
- project_id
- aggregate_type
- aggregate_id
- aggregate_sequence
- event_type
- event_schema_version
- payload_json
- payload_hash
- occurred_at
- recorded_at
- interaction_correlation_id nullable
- workflow_correlation_id nullable
- causation_id nullable
- actor_type
- actor_id nullable
- visibility_class
- created_at
```

约束：

```text
UNIQUE(aggregate_type, aggregate_id, aggregate_sequence)
UNIQUE(event_id)
```

## 30.3 权威事务创建

RuntimeEvent 必须由拥有权威状态的 Application/Domain Service 在同一事务中创建：

```text
update authority row
→ allocate aggregate sequence
→ insert immutable RuntimeEvent
→ insert OutboxRecord(event_id)
→ commit
```

Publisher 和 Projection Worker 都不得创建第二个 RuntimeEvent。

## 30.4 Event ID 与顺序

P0 使用数据库生成的全局唯一 ID。排序不能只依赖 Event ID。

每个 Aggregate 内由 `aggregate_sequence` 保证顺序。跨 Aggregate 只提供：

- `occurred_at`；
- `recorded_at`；
- causation edge；
- correlation；
- deterministic tie-break。

不承诺全系统线性总序。

## 30.5 Payload

Payload 只保存：

- ID；
- 状态变化；
- Reason Code；
- Hash；
- 小型结构化摘要；
- Reference。

禁止保存：

- Raw stdout/stderr；
- Secret；
- Model 原始隐藏推理；
- Checkpoint 内容；
- 大型 Artifact；
- 完整 Prompt，除非引用受控 ModelRequestSnapshot。

---

# 31. Transactional Outbox

## 31.1 原子性

每个关键状态变化必须在同一 PostgreSQL 事务中：

```text
update authority row
+ allocate aggregate sequence
+ insert immutable RuntimeEvent
+ insert OutboxRecord referencing event_id
```

不能先写状态、后异步“尽力创建事件”。

## 31.2 OutboxRecord

```text
OutboxRecord
- outbox_id
- event_id
- topic
- partition_key
- payload_ref
- status
- available_at
- attempt_count
- leased_until nullable
- published_at nullable
- last_error nullable
```

## 31.3 Delivery

P0 使用 At-least-once delivery。

Outbox 只负责投递已经存在的 RuntimeEvent。Projection Worker 必须按 `event_id` 幂等。

Outbox 重发只能重复投递同一个 `event_id`，不得产生第二个 RuntimeEvent。

即使 Publisher 长期失败，RuntimeEvent 仍与权威状态同时存在。

## 31.4 Event Schema

每个 Event Type 具有固定 `event_schema_version`。

Schema 变化：

- 向后兼容字段可提升 minor version；
- 语义变化必须新 Event Type 或 major version；
- Projection Worker 必须拒绝未知必需版本，而不是猜测。

---

# 32. Correlation 与 Causation

## 32.1 Correlation

```text
interaction_correlation_id
```

连接一次用户任务及其 continuation Agent Runs。

```text
workflow_correlation_id
```

连接 Proposal Approval 后的 Version、Batch、Workflow、Trial、Attempt、Validation 和 Summary。

## 32.2 Causation

`causation_id` 指向直接导致当前 Event 的：

- RuntimeEvent；
- Domain Command；
- AgentDecision；
- Approval Decision；
- Coordinator Job；
- Executor Observation。

Causation 不等于“时间上前一个 Event”。

## 32.3 Root Trace

每个正式 Experiment Workflow 至少可以追溯：

```text
Proposal Revision
→ Design Approval
→ Experiment Version
→ Batch
→ Trial
→ Attempt
→ Host Plan
→ Collection Manifest
→ Artifact/Metric
→ Validation Report
→ Summary Revision
```

---

# 33. 三条核心 Timeline

## 33.1 Agent Run Timeline

展示：

- Run started/completed/suspended；
- Context Snapshot；
- ModelCall attempts；
- AgentDecision；
- ToolCall；
- Tool Approval；
- Proposal Revision；
- Continuation lineage。

权威来源仍为 AgentRun/Step/ModelCall/ToolCall 等对象。

## 33.2 Workflow Timeline

展示：

- Design Approval；
- Version Freeze；
- Workflow phases；
- Coordinator Jobs；
- Attempt prepare/submit/inspect；
- Supervision Unit；
- Cancel/Timeout；
- Reconciliation；
- Collection；
- Validation；
- Summary；
- Closure。

## 33.3 Trial / Attempt Timeline

展示：

- Trial identity；
- Attempt creation；
- Launch phases；
- execution terminal；
- Collection Manifest；
- Artifact Registration；
- Metric Extraction；
- Validation Reports；
- selected result changes；
- Summary inclusion/exclusion。

## 33.4 Timeline Entry

```text
TimelineEntry
- timeline_entry_id
- timeline_type
- owner_id
- source_event_id
- occurred_at
- aggregate_type
- aggregate_id
- category
- display_status
- title_template_id
- detail_ref nullable
- correlation_id nullable
- causation_id nullable
- projection_version
```

Timeline Entry 不存大型 detail body。

---

# 34. Timeline Projection 语义

## 34.1 Eventual Consistency

Timeline 是最终一致投影。

API 应返回：

```text
projection_watermark
projection_lag_seconds
last_authority_updated_at
```

UI 在关键状态页必须同时读取权威对象当前状态。

## 34.2 Projection Watermark

```text
ProjectionWatermark
- projection_name
- partition_key
- last_event_recorded_at
- last_event_id
- updated_at
- status
```

## 34.3 重建

Projection 可以：

- 删除；
- 从 RuntimeEvent 重建；
- 增量修复。

不能通过 Projection 重建缺失的 Artifact 文件、ValidationReport 或 Trial selection。

## 34.4 跨 Aggregate 排序

Timeline 展示顺序：

```text
occurred_at
→ recorded_at
→ aggregate_type
→ aggregate_id
→ aggregate_sequence
→ event_id
```

并使用 Causation Edge 解释因果，不宣称全局严格时钟顺序。

---

# 35. AgentOps Query Projection

## 35.1 P0 查询目标

必须回答：

1. Agent 看到了哪个 Context Snapshot；
2. 哪个 ModelCall 产生了 Decision；
3. Tool 为什么需要 Approval；
4. Workflow 为什么 Retry、停止或进入 needs_attention；
5. Attempt 对应哪个 Host Plan 和 Supervision Unit；
6. Artifact 来自哪个 Attempt/路径/Digest；
7. Metric 来自哪个 Artifact/Log/Extractor；
8. Validation 为什么 eligible、invalid 或 inconclusive；
9. Summary 纳入和排除了哪些 Trial；
10. selected Result 是否发生变化；
11. 未解决执行是否仍存在；
12. 哪些 Event/Projection 处于延迟或失败状态。

## 35.2 AgentRunOpsProjection

```text
AgentRunOpsProjection
- agent_run_id
- status
- step_count
- model_call_count
- tool_call_count
- approval_count
- total_token_usage
- elapsed_active_seconds
- elapsed_calendar_seconds
- terminal_outcome
- last_event_at
```

## 35.3 WorkflowOpsProjection

```text
WorkflowOpsProjection
- workflow_id
- batch_id
- status
- current_phase
- job_retry_count
- active_attempt_count
- unknown_attempt_count
- open_reconciliation_count
- collection_pending_count
- validation_pending_count
- last_progress_at
```

## 35.4 TrialResultProjection

```text
TrialResultProjection
- trial_id
- selected_attempt_id nullable
- selected_validation_report_id nullable
- execution_state
- validation_state
- artifact_count
- metric_count
- latest_reason_codes
- summary_revision_ids
- last_updated_at
```

这些 Projection 不允许作为领域命令前置条件的唯一来源。

---

# 36. Operational Metrics

## 36.1 P0 指标

AgentOps 可以计算：

```text
agent_run_count by outcome
model_call retry/error count
tool approval approve/reject/expire count
workflow duration by phase
attempt count by execution outcome
reconciliation case count by type
artifact registration failure count
metric extraction issue count
validation eligible/invalid/inconclusive count
summary generation failure count
projection lag
```

## 36.2 不使用科研 Metric 作为运维 Metric

科研 Metric 与 AgentOps Operational Metric 必须使用不同命名空间和存储对象。

例如：

```text
research.metric.reconstruction_mae
ops.validation.invalid_count
```

不能把模型准确率等科研结果发送到运维告警系统后再当作权威科研数据。

## 36.3 P0 不建设 Prometheus 平台

P0 可以通过 PostgreSQL Projection 和 API 查询。

P1 可以增加 Export Adapter：

- OpenTelemetry；
- Prometheus；
- MLflow；
- JSONL audit export。

External Export 不成为权威来源。

---

# 37. Log、Trace 与 Artifact 的边界

## 37.1 Runtime Log

Runtime Log 文件由 `06` 管理：

- stdout；
- stderr；
- wrapper log；
- supervision log；
- collection log。

`07` 保存 Ref、Digest 和 Redacted View，不复制所有日志到 Event Payload。

## 37.2 Trace

Trace 保存：

- Context/Request/Decision identity；
- Command/Plan/Approval identity；
- State transition；
- Evidence refs；
- Reason code；
- Correlation/Causation。

## 37.3 Artifact

Artifact 是正式结果内容。

日志只有在 Validation Protocol 明确声明为所需结果并完成 Artifact Registration 后，才成为 Artifact。

## 37.4 Observation

Agent Observation 是脱敏、大小受限的结构化结果。

Observation 不是原始 Artifact，也不自动进入 Summary。

---

# 38. Redaction 与数据安全

## 38.1 Payload Redaction

所有 RuntimeEvent 在提交前执行：

- Schema allowlist；
- Secret field denylist；
- Path normalization；
- 长度限制；
- Raw text 禁止规则；
- Reference 替换。

## 38.2 RuntimeEventRedactionRecord

若后续发现 Event Payload 泄漏敏感信息：

```text
RuntimeEventRedactionRecord
- redaction_record_id
- event_id
- reason_code
- affected_json_paths
- replacement_payload_ref
- created_at
- created_by
```

P0 查询投影应用 Redaction Overlay。

原始敏感 Payload 进入受限存储并从普通查询隔离；安全修复优先于普通事件不可变性展示。

## 38.3 Artifact Path

普通 UI/Event 不展示绝对宿主机路径。

只展示：

- logical name；
- managed Artifact ID；
- safe relative display path；
- size/digest/type。

## 38.4 Prompt Boundary

Agent/LLM 只能读取：

- redacted text；
- Artifact metadata；
- 明确批准的内容 excerpt。

Checkpoint、raw logs 和 quarantine 不进入 Prompt。

---

# 39. Ownership Matrix

| 对象 | 持久化写入服务 | 授权调用者 |
|---|---|---|
| CollectionManifest | Collection Service | Workflow Coordinator |
| ResultProcessingRoot/Revision | Result Processing Service | Result Processing Coordinator / Authorized Reprocessing Command |
| ArtifactRegistrationRun/ExecutionAttempt | Artifact Registry | Result Processing Coordinator / Worker |
| ArtifactPublishIntent | Artifact Registry | Artifact Registration Domain Service |
| Artifact | Artifact Registry | Artifact Publish Completion Service |
| ArtifactAvailability | Artifact Registry Projection | Integrity Verifier / Red Tool Result / Reconciliation |
| MetricExtractionRun/ExecutionAttempt | Metric Extraction Service | Result Processing Coordinator / Worker |
| MetricObservation | Metric Extraction Service | Successful Extraction Completion |
| ValidationInputSnapshot | Validation Engine | Result Processing Coordinator |
| ValidationRun/ExecutionAttempt/Report | Validation Engine | Result Processing Coordinator / Worker / Validation Completion Service |
| ValidationAuthorityDecision | Experiment Domain Service | Authorized Revalidation Application Service |
| Trial selection/projection | Experiment Domain Service | Validation Completion / Authority Decision Service |
| SummaryGenerationRun/ExecutionAttempt | Summary Engine | Result Processing Coordinator / Worker |
| BatchSummaryRevision | Summary Engine | Summary Completion Service |
| RuntimeEvent/Outbox | Authority-owning Service | Same authority transaction |
| Timeline Projection | Projection Worker | RuntimeEvent consumer |

禁止：

- UI 直接插入 Artifact/Metric；
- Agent 直接设置 Validation eligible；
- Summary Engine 直接修改 Trial selection；
- Projection Worker 回写权威状态；
- Artifact Registry 修改 Attempt execution status；
- Result Worker 通过新 Worker Run 覆盖旧 immutable output。

---

# 40. Commands

## 40.1 P0 命令

```text
StartResultProcessing
CreateResultProcessingRevision
StartArtifactRegistration
ReconcileArtifactPublishIntent
VerifyArtifactIntegrity
StartMetricExtraction
BuildValidationInputSnapshot
RunAttemptValidation
CompleteValidationReport
ApplyAuthoritativeRevalidationResult
GenerateBatchSummary
RebuildTimelineProjection
RebuildAgentOpsProjection
RecordArtifactAvailabilityObservation
```

`CreateResultProcessingRevision` 必须提供 `processing_plan_identity` 和合法 `reason_code`。

`ApplyAuthoritativeRevalidationResult` 使用第 22.4 节的完整 CAS/lineage 前置条件。

## 40.2 P1 命令

```text
GenerateResultInterpretationProposal
ExportBatchToMLflow
ExportTraceToOpenTelemetry
RegisterExternalArtifactReference
SelectTrialAttemptResult
```

## 40.3 Query

```text
GetArtifact
ListAttemptArtifacts
ListAttemptMetrics
GetResultProcessingRoot
ListResultProcessingRevisions
GetValidationReport
ListValidationReports
ListValidationAuthorityDecisions
GetBatchSummaryRevision
ListBatchSummaryRevisions
GetAgentRunTimeline
GetWorkflowTimeline
GetTrialAttemptTimeline
GetOpenResultProcessingFailures
GetOpenArtifactPublishIntents
GetProjectionStatus
```

---

# 41. Transaction Boundaries

## 41.1 Artifact Publish Intent 创建

```text
allocate artifact_id
+ persist ArtifactPublishIntent(planned)
+ insert RuntimeEvent
+ insert OutboxRecord(event_id)
+ commit
```

文件复制和发布在数据库事务外执行，但只允许使用已持久化 Intent。

## 41.2 Artifact Publish Completion

```text
lock ArtifactPublishIntent
+ verify publish marker/digest/identity
+ persist Artifact
+ persist ArtifactAvailability
+ mark Intent database_committed
+ insert RuntimeEvent
+ insert OutboxRecord(event_id)
+ commit
```

## 41.3 Stage Logical Run Completion

一次成功阶段执行的：

- immutable outputs；
- stage Logical Run terminal status；
- successful_execution_attempt_id；
- RuntimeEvent；
- OutboxRecord；

在一个事务中完成提交，或使用阶段 staging manifest 后原子 publish。

## 41.4 Validation Completion

Validation Report、Domain selection/projection、RuntimeEvent 和 OutboxRecord 在同一应用事务中完成，防止：

```text
Report eligible 但 Trial 未 selected
```

或：

```text
Trial eligible 但 Report 不存在
```

## 41.5 Authority Decision

ValidationAuthorityDecision、selected Validation Report CAS 更新、Trial projection、Summary invalidation Event 在同一事务中完成。

## 41.6 Summary Publish

Summary Content、Groups、Aggregates、Revision terminal status、RuntimeEvent 和 OutboxRecord 原子提交。

---

# 42. 幂等与并发

## 42.1 Result Processing Root/Revision

相同 `result_handoff_identity` 返回同一 Root。

相同 Root + `processing_plan_identity` 返回同一 Revision。

## 42.2 Artifact Registration

相同 `logical_run_key` 返回已有 ArtifactRegistrationRun。

Worker transient failure创建新的 ArtifactRegistrationExecutionAttempt。相同 `publish_key` 只能发布同一个 Artifact。

## 42.3 Metric Extraction

相同 `logical_run_key` 返回已有 MetricExtractionRun。

失败的 Worker Attempt 不允许通过新的 extraction sequence 创建第二份相同 Logical Run；只能创建新的 Execution Attempt。

## 42.4 Validation

`validation_run_key` 全局唯一。

同一 Key 并发请求只产生一个 ValidationRun；Worker 重试创建新的 ValidationExecutionAttempt。

新的 Processing Revision、Input Snapshot 或 Validator Identity 才能产生新的 ValidationRun。

## 42.5 First Eligible Race

锁定 Trial 行：

```text
if selected_attempt_id is null
and report eligible
then select current attempt/report
else retain existing selection
```

不得发生 last-writer-wins 替换。

## 42.6 Authority Decision Race

命令必须携带：

```text
expected_selected_attempt_id
expected_selected_validation_report_id
```

任一不匹配时 CAS 失败，不得覆盖并发产生的新权威结论。

## 42.7 Summary

相同 `logical_run_key` 返回已有 SummaryGenerationRun/Revision。Worker transient failure只创建新的 SummaryGenerationExecutionAttempt。

---

# 43. Failure Classification

## 43.1 Artifact Registration/Publish

```text
artifact_source_missing
artifact_source_changed
artifact_copy_failure
artifact_digest_mismatch
artifact_storage_failure
artifact_secret_scan_failure
artifact_unsupported_file_type
artifact_publish_identity_conflict
```

## 43.2 Metric Extraction

```text
metric_source_unavailable
metric_parse_failure
metric_multiplicity_violation
extractor_disabled
extractor_internal_error
```

## 43.3 Validation

```text
validation_blocking_failure
validation_input_unavailable
artifact_identity_unverifiable
internal_artifact_consistency_failure
validator_internal_error
```

## 43.4 Summary

```text
summary_input_inconsistent
summary_nonfinite_input
summary_policy_mismatch
summary_engine_error
```

## 43.5 状态影响

| Failure | Attempt execution | Trial eligibility | 自动处理 |
|---|---|---|---|
| Artifact copy temporary I/O | 不变 | 尚未验证 | 同 Logical Run 新 Execution Attempt |
| Required Artifact confirmed missing | 不变 | invalid | 不重试 Attempt |
| Artifact digest confirmed mismatch | 不变 | invalid | 新 Authority Revalidation；不开新 Attempt |
| Artifact storage temporary unavailable | 不变 | inconclusive | 同 Logical Run 重试 |
| Metric content confirmed parse failure | 不变 | invalid | Protocol 不变时不重试；Extractor Bug Fix 新 Revision |
| Required Metric missing | 不变 | invalid | 否 |
| Validator Worker transient error | 不变 | inconclusive | 同 ValidationRun 新 Execution Attempt |
| Validator implementation bug | 不变 | inconclusive | 新 Validator Version + Processing Revision |
| Summary engine transient error | 不变 | 不变 | 同 SummaryRun 新 Execution Attempt |
| Projection error | 不变 | 不变 | 重建/重投递 |

原则：确认科研要求不满足映射 invalid；系统无法获取或确认事实映射 inconclusive。

---

# 44. Reconciliation

## 44.1 Result Processing Reconciliation

触发：

- ArtifactPublishIntent 已 `filesystem_published` 但数据库未提交 Artifact；
- DB Artifact 存在但 Managed Copy 丢失；
- 任一 Stage Execution Attempt 为 running 且 Lease 过期；
- Validation Report 存在但 Trial selection/Authority Decision 未完成；
- Summary Revision staging 存在但未发布；
- RuntimeEvent 已存在但 Outbox 未投递或 Projection 未消费。

## 44.2 Artifact Publish Reconciliation

比较：

```text
ArtifactPublishIntent
vs artifact-publish-marker.json
vs final path identity
vs expected digest/content identity
vs Artifact DB record
```

恢复必须复用原 `artifact_id` 和 `publish_key`。不得仅凭文件名恢复，也不得覆盖身份不符的 final path。

## 44.3 Logical Run Reconciliation

若 Logical Run 为 running 而 Worker Lease 已过期：

- 关闭旧 Execution Attempt；
- 判断 immutable output 是否已提交；
- 已提交则幂等完成 Logical Run；
- 未提交且允许重试则创建新 Execution Attempt；
- 不创建第二个 Logical Run。

## 44.4 Validation Reconciliation

如果 Report 已存在但 Application transaction 未完成，必须根据唯一 `validation_run_key` 重放幂等 Domain Completion，而不是生成第二个 Report。

如果 Authority Decision 已存在但 selected Report 未更新，按 Decision Identity 和 CAS 前置条件继续或标记 conflict，不能新建第二个 Decision。

## 44.5 Projection Reconciliation

Projection 丢失直接从 RuntimeEvent 重建，不开领域 Reconciliation Case。Outbox 重投递引用相同 `event_id`。

---

# 45. Retention 与删除

## 45.1 P0 默认

- 正式 Artifact 不自动删除；
- Validation Report、Authority Decision、Metric、Summary、RuntimeEvent 元数据长期保留；
- ArtifactPublishIntent 和最小发布 Marker 长期保留用于审计；
- Raw quarantine 短期保留；
- Redacted persistent logs 按项目策略；
- Projection 可删除重建。

## 45.2 删除 Artifact

真实 Artifact 删除必须：

- 使用 05 的 Red Tool；
- 检查 Retention Hold；
- 绑定 Artifact ID、Content Identity、Availability Generation；
- 执行后验证；
- 更新 Availability Projection；
- 在同一事务中创建 RuntimeEvent/OutboxRecord；
- 不删除历史 Metadata。

## 45.3 Summary 与 Validation 引用

Summary Revision 和 selected Validation Report 引用的 Artifact/Metric/ValidationReport Metadata 永久保留。

Artifact 文件如未来被合法归档到外部存储，必须通过新 Availability/Location Revision 表达，不修改历史 Content Identity。

## 45.4 Diagnostic Evidence

failed/cancelled/unknown Attempt 的 Runtime Log 和 Evidence Retention 由 `06` 管理。它们不因为没有 ResultProcessingRoot 而被自动删除，也不被升级为科研 Artifact。

---

# 46. LG-CausalDiffAE 示例

## 46.1 实验结构

示例 Version：

```text
3 个参数条件
× seeds [1, 2, 3]
= 9 Trials
```

`replicate_dimensions = [seed]`，因此形成 3 个 Summary Groups。

## 46.2 Required Artifact

示例 Protocol：

```text
required artifacts:
- logical_name: ema_checkpoint
  artifact_type: checkpoint
  path pattern: ema_checkpoint.pt
  integrity: sha256

- logical_name: final_metrics
  artifact_type: metric_source
  path pattern: metrics.json
  integrity: sha256
```

## 46.3 Metric Extraction

```text
extractor: json_path@1
source: final_metrics
metrics:
- reconstruction_mae
- target_mae
- graph_shd
```

Extractor 只从已注册 `metrics.json` 读取。

若 `graph_shd = NaN`：

```text
MetricObservation.value_kind = non_finite_numeric
Validation reason = METRIC_NAN_OR_INF
Trial = invalid
Attempt execution remains succeeded
```

## 46.4 Missing Checkpoint

训练 exit code 0，但缺 `ema_checkpoint.pt`：

```text
ArtifactCandidate not found
→ REQUIRED_ARTIFACT_MISSING
→ ValidationReport.status = failed
→ eligibility = invalid
→ Trial excluded from Summary
```

## 46.5 Summary

每个参数 Group 对三个 eligible seed 计算：

```text
count
mean
sample std (ddof=1)
min
max
```

如果只有两个 eligible seed：

- 保留两个 seed 的统计；
- Group `completeness_status = insufficient_replicates`；
- 明确警告最小重复数未满足；
- 不把 Batch 描述为完整比较。

## 46.6 Trace

用户可以从 Summary Group 追溯：

```text
Metric Aggregate
→ Metric Observations
→ Metric Source Artifact
→ Artifact Registration Run
→ Collection Manifest
→ Attempt
→ Host Execution Plan
→ Experiment Version
```

---

# 47. API 与 UI 读模型

## 47.1 Artifact 页面

显示：

- Artifact Type/Logical Name；
- Attempt/Trial/Batch；
- Size/Digest；
- Availability；
- Validation/Summary 引用；
- Agent-readable 状态；
- Safe download action。

不显示默认绝对路径。

## 47.2 Validation 页面

显示：

- Execution fact 与 Validation fact 分栏；
- 每个 Check Result；
- Reason Code；
- Evidence references；
- Report sequence 和 Validator Version；
- selected 状态；
- revalidation lineage。

## 47.3 Summary 页面

显示：

- Summary Revision；
- Included/Excluded Trial；
- Group dimensions；
- count/mean/std/min/max；
- minimum replicate warning；
- Reproducibility Warning；
- Source Version Identity。

## 47.4 Timeline 页面

Timeline 需明确标签：

```text
Agent
Workflow
Execution
Result
Validation
Summary
Approval
Reconciliation
```

不能将执行成功和验证成功合并为一个“成功”图标。

---

# 48. 测试策略

## 48.1 Unit Tests

- Canonical Artifact/Metric/Summary Identity；
- ResultProcessingRoot/Revision identity；
- Logical Run Key 与 Execution Attempt sequence；
- SummaryNumericsV1 conformance vectors；
- sample std ddof=1；
- Artifact Manifest path normalization；
- Validation missing/corruption reason mapping；
- Event payload redaction；
- Aggregate sequence allocation；
- Projection idempotency。

## 48.2 Transaction Tests

- ArtifactPublishIntent 创建、filesystem publish、DB completion；
- filesystem publish 后 DB failure reconciliation；
- Stage Logical Run completion 与 successful Attempt pointer；
- Validation Report + Trial selection + RuntimeEvent/Outbox 原子性；
- first-eligible race；
- concurrent ValidationAuthorityDecision CAS；
- revalidation invalidates selected Report；
- Summary Revision race；
- RuntimeEvent + OutboxRecord 原子性；
- Outbox at-least-once duplicate delivery。

## 48.3 Fault Injection

1. Collection 后 Artifact 源文件变化；
2. Artifact copy 中断；
3. Destination fsync/rename 后 DB 崩溃；
4. Final path 已存在但 Marker/Digest 不匹配；
5. Secret Scanner 失败；
6. Metric JSON 格式错误；
7. 一个 scalar Metric 出现多个值；
8. NaN/Infinity；
9. Required Artifact 确认缺失；
10. Artifact Store 暂时不可访问；
11. Validator Worker 崩溃并创建新 Execution Attempt；
12. Report 已保存、Trial selection 前崩溃；
13. 两个权威 Revalidation 并发；
14. Summary Input 构建后 selected Report 改变；
15. Summary Worker 崩溃后复用 Logical Run；
16. Projection Worker 重复消费 Event；
17. Projection DB 清空后重建；
18. Artifact 在 selected Report 下尝试删除；
19. RuntimeEvent Payload 注入 Secret；
20. Workflow closed_with_unresolved_execution 尝试生成 Summary；
21. Extractor Bug Fix 对同一 Root 创建新 Processing Revision；
22. failed/cancelled Attempt 尝试启动正式 Result Pipeline。

## 48.4 Core Demo C

必须验证：

```text
Attempt = succeeded
Required Artifact/Metric confirmed missing or invalid
Validation = invalid
Trial excluded from Summary
Timeline displays execution and validation facts separately
```

## 48.5 Provenance Test

任一 Summary Metric 必须能够在固定查询次数内追溯到：

- Trial；
- selected Attempt；
- selected Validation Report；
- ValidationAuthorityDecision（如有）；
- MetricObservation；
- metric_source Artifact；
- ArtifactPublishIntent；
- Collection Manifest；
- ResultProcessingRevision；
- Experiment Version。

---

# 49. 局部不变量

```text
RV-INV-001
Attempt execution status 不得由 Result/Validation/AgentOps 模块改写。

RV-INV-002
failed、cancelled、unknown Attempt 不得进入正式 Result/Metric/Validation/Summary Pipeline。

RV-INV-003
Collection Manifest 只表示候选输出，不自动形成正式 Artifact。

RV-INV-004
正式 Artifact 必须具有 Attempt、Collection Manifest、Publish Intent 和内容身份来源。

RV-INV-005
P0 正式 Artifact 使用 managed copy，不以可变 Attempt path 作为长期真相。

RV-INV-006
Artifact、Metric、Validation Report、Authority Decision 和 Summary Revision 不得原地覆盖。

RV-INV-007
同一路径内容变化必须产生新 Manifest/Root/Artifact，不得复用旧 Digest。

RV-INV-008
ArtifactAvailability 是独立投影，不进入 Artifact Identity。

RV-INV-009
存在 active Retention Hold 的 Artifact 不得被删除。

RV-INV-010
Artifact 发布必须先存在持久化 ArtifactPublishIntent。

RV-INV-011
final Artifact path 已存在但身份不符时必须 fail closed，不得覆盖。

RV-INV-012
Binary Artifact 默认不得进入 Agent Prompt。

RV-INV-013
RuntimeEvent、Timeline 和普通日志不得引用 raw quarantine 内容。

RV-INV-014
P0 Metric Extractor 不得执行任意用户代码或 Shell。

RV-INV-015
P0 正式 Metric 必须引用 metric_source Artifact，不得只引用 Runtime Log。

RV-INV-016
Metric 数值必须从原始字符串构造 Decimal，不得先转 binary float。

RV-INV-017
NaN/Infinity 不得进入正式 Summary。

RV-INV-018
Multiplicity 冲突不得静默选择最后一个值。

RV-INV-019
改变 Metric 科研语义必须创建新 Experiment Version。

RV-INV-020
同一 Handoff 必须复用 ResultProcessingRoot；处理实现变化创建新 Revision。

RV-INV-021
Worker 重试必须创建同一 Logical Run 下的新 Execution Attempt，不得创建第二个语义相同 Logical Run。

RV-INV-022
Validation 必须绑定不可变 ValidationInputSnapshot。

RV-INV-023
ValidationInputSnapshot 必须能够表达 confirmed missing、corrupted 和 parse-failed 输入。

RV-INV-024
确认科研要求不满足必须映射 invalid；无法确认事实必须映射 inconclusive。

RV-INV-025
Validation 必须按冻结 Protocol 顺序执行确定性检查。

RV-INV-026
Validation invalid 不得改写 Attempt succeeded。

RV-INV-027
Validator error 不得自动创建新 Attempt。

RV-INV-028
Validation Report 必须不可变并在 Attempt 内使用递增 sequence。

RV-INV-029
第一个 eligible Result 只能通过 Trial 行锁和 Domain Service 选择。

RV-INV-030
Metric 表现不得触发自动 selected Attempt 替换。

RV-INV-031
Revalidation 不得覆盖旧 Report。

RV-INV-032
Selected Report 的权威 successor 必须由 ValidationAuthorityDecision 表达。

RV-INV-033
ApplyAuthoritativeRevalidationResult 不得切换 selected Attempt。

RV-INV-034
Selected Report 的权威 successor 变化必须触发新 Summary Revision。

RV-INV-035
正式 Summary 只能读取 eligible Trial 的 selected Result。

RV-INV-036
不同非 replicate 参数 Group 不得默认混合聚合。

RV-INV-037
Summary 必须使用冻结的 SummaryNumericsV1。

RV-INV-038
Summary std 固定为 sample standard deviation, ddof=1。

RV-INV-039
count < 2 时 std 必须为 null，不得返回 0。

RV-INV-040
Optional Metric 必须使用独立 count/missing_count。

RV-INV-041
存在 unknown Attempt 时不得生成正式 Batch Summary。

RV-INV-042
closed_with_unresolved_execution 只能生成 Closure Report。

RV-INV-043
P1 Result Interpretation 只能读取指定 Summary Revision 和 eligible Evidence。

RV-INV-044
RuntimeEvent 不得替代权威领域状态。

RV-INV-045
关键状态变化、RuntimeEvent 与 OutboxRecord 必须原子提交。

RV-INV-046
Outbox 只能投递已存在的 RuntimeEvent，不得创建 Event。

RV-INV-047
RuntimeEvent 必须具有每 Aggregate 唯一递增 sequence。

RV-INV-048
Projection 必须按 event_id 幂等消费。

RV-INV-049
Projection 可重建，但不得用于恢复缺失 Artifact 或 ValidationReport。

RV-INV-050
跨 Aggregate Timeline 不得声称全局线性总序。

RV-INV-051
Event Payload 不得保存 Secret、Raw Log、大 Artifact 或隐藏推理链。

RV-INV-052
Agent、Workflow、Trial/Attempt Timeline 必须使用同一 RuntimeEvent 基础模型。

RV-INV-053
AgentOps Operational Metric 与科研 Metric 必须使用不同对象和命名空间。

RV-INV-054
Artifact corruption 必须通过新 Availability Observation、Processing Revision 和 Authority Decision 处理，不得修改旧 Report。

RV-INV-055
Summary Input Snapshot 或 Summary Engine/Numerics 变化必须产生新 Logical Run 和 Summary Revision。

RV-INV-056
Artifact Registration failure、Metric Extraction failure、Summary failure 均不得覆盖 Attempt execution fact。

RV-INV-057
人工编辑数据库 Metric 不得作为合法修复路径。

RV-INV-058
Validation Evidence 必须引用结构化来源，不得依赖自由文本断言。

RV-INV-059
Tool 临时输出不得自动升级为正式 Artifact。

RV-INV-060
Projection Lag 必须可见，UI 不能把过期 Timeline 当成权威当前状态。

RV-INV-061
ResultProcessingRevision 不得改变已冻结 Experiment Version 的科研语义。

RV-INV-062
Artifact Store 暂时不可访问不得被误判为 Artifact 确认缺失或科研 invalid。

RV-INV-063
failed/cancelled/unknown Attempt 的诊断材料不得进入正式 Summary。

RV-INV-064
上游依赖哈希未通过时，本文件不得冻结。
```

---

# 50. P0 / P1 边界

## 50.1 P0

- ResultProcessingRoot / Revision；
- Stage Logical Run / Execution Attempt；
- Managed local Artifact storage；
- ArtifactPublishIntent 与崩溃恢复；
- File/Directory Artifact registration；
- SHA-256 / manifest integrity；
- Built-in Metric Extractors；
- 正式 Metric 仅来自 metric_source Artifact；
- Deterministic Validation；
- ValidationAuthorityDecision；
- first-eligible selection integration；
- SummaryNumericsV1；
- Deterministic Batch Summary；
- RuntimeEvent + Transactional Outbox；
- Agent/Workflow/Trial 三条 Timeline；
- Basic AgentOps queries；
- Secret-safe metadata and redacted text；
- Projection rebuild；
- LG-CausalDiffAE Core Demo。

## 50.2 P1

- Result Interpretation Agent；
- Follow-up Proposal；
- S3/MinIO Artifact Adapter；
- MLflow Export；
- OpenTelemetry/Prometheus Export；
- External Artifact Reference；
- Rich plots and report generation；
- Advanced retention policies；
- Controlled custom Extractor plugin；
- Cross-language SummaryNumerics conformance implementation；
- Cross-group statistical rules；
- Multi-project observability dashboard；
- 不可变 RuntimeLogEvidenceSnapshot（如未来允许 Log Metric）。

---

# 51. Architecture Decision Records

| ID | 决策 | 原因 | 代价 |
|---|---|---|---|
| RV-ADR-001 | Artifact 使用 managed copy，而非原地 path reference | 保证长期完整性与清理边界 | 增加本地存储占用 |
| RV-ADR-002 | P0 不建设完整 CAS | 单机 V1 降低复杂度 | 相同内容可能重复存储 |
| RV-ADR-003 | Metric Extractor 只允许内置确定性类型 | 避免隐藏执行路径 | 自定义指标需由 eval entrypoint 生成结构化文件 |
| RV-ADR-004 | P0 正式 Metric 只来自 metric_source Artifact | 避免日志清理破坏科研可重放性 | 仓库必须输出结构化 Metric 文件 |
| RV-ADR-005 | Validation Input Snapshot 可记录负事实 | 缺失 Artifact 也必须形成 invalid Report | Snapshot 模型更丰富 |
| RV-ADR-006 | Result Processing 使用 Root/Revision | 支持修复 Extractor/Validator 后重处理 | 增加 Revision lineage |
| RV-ADR-007 | Stage Logical Run 与 Worker Execution Attempt 分离 | 幂等科研输出与物理重试语义闭合 | 每阶段增加 Attempt 表 |
| RV-ADR-008 | Artifact 发布使用 ArtifactPublishIntent | 文件系统与 PostgreSQL 无法原子提交 | 增加 Reconciliation 对象 |
| RV-ADR-009 | Summary 仅聚合 selected eligible Result | 避免 cherry-pick | 需要明确重评估/替换流程 |
| RV-ADR-010 | Summary 使用 SummaryNumericsV1 | 保证 P0 统计可确定性复算 | 固定 Python Decimal 实现 |
| RV-ADR-011 | RuntimeEvent 在权威事务中创建，Outbox 只投递 | 防止状态与事件分离 | 权威 Service 需承担 Event 创建 |
| RV-ADR-012 | RuntimeEvent 非 Event Sourcing | 保持实现可控 | 权威表和事件需双重治理 |
| RV-ADR-013 | 三条 Timeline 共用 Event 基础 | 避免三套日志系统 | Projection 逻辑更复杂 |
| RV-ADR-014 | Cross-aggregate 不承诺全局总序 | 多 Worker 时钟现实 | UI 需使用 causation 解释 |
| RV-ADR-015 | unknown execution 禁止正式 Summary | 保持科研事实诚实 | 某些 Batch 只能生成 Closure Report |
| RV-ADR-016 | P1 Agent 只生成 Interpretation Proposal | 不把 LLM 推断变成正式科研结论 | 需要用户复核 |

---

# 52. 冻结检查清单

```text
[x] 00–06 上游 SHA-256 全部通过
[x] ResultProcessingRoot/Revision 能表达同一 Handoff 的重新处理
[x] Logical Run 与 Worker Execution Attempt 已分离
[x] Artifact managed copy、Publish Intent 与 Availability 边界无矛盾
[x] Metric Extractor 不包含任意代码执行入口
[x] 正式 Metric 只来自 metric_source Artifact
[x] Validation Snapshot 能表达 missing/corrupted/parse-failed
[x] Validation Report/Run/sequence 与 02 一致
[x] first-eligible 没有被 Summary 或 Agent 改写
[x] 权威 Revalidation 使用明确 Decision/CAS/lineage
[x] SummaryNumericsV1/std/replicate 规则与 02 一致
[x] unresolved Workflow 不能生成正式 Summary
[x] RuntimeEvent 在权威事务中创建，Outbox 只投递
[x] RuntimeEvent 不被描述为权威状态或 Event Sourcing
[x] Event Payload 无 Raw Log/Secret/Artifact 内容
[x] 三条 Timeline 均可从统一 Event 投影
[x] failed/cancelled/unknown Attempt 不进入正式 Result Pipeline
[x] 未决占位项为 0
[x] 代码围栏成对
[x] 规范副本字节一致
```

---

# 53. 实现顺序建议

本文冻结后，Implementation Plan 建议按以下顺序：

```text
1. RuntimeEvent / Outbox / Projection 基础
2. ResultProcessingRoot / Revision 与阶段 Job
3. ArtifactPublishIntent / Managed Artifact Registry
4. Built-in Metric Extractor Registry
5. Stage Logical Run / Execution Attempt 通用约定
6. Validation Input Snapshot / Validation Engine
7. first-eligible 与 ValidationAuthorityDecision 事务集成
8. SummaryNumericsV1 / Deterministic Summary Engine
9. Timeline / AgentOps Query
10. Fault injection and Core Demo C
11. P1 adapters later
```

实现过程中不得以“先做简化”为由：

- 将 Artifact 留在可变 Attempt path；
- 省略 ArtifactPublishIntent；
- 将 Worker retry 建成第二个语义相同 Logical Run；
- 直接保存手工 Metric；
- 从 Runtime Log 提取正式 Summary Metric；
- 把 Validation failure 写成 Attempt failure；
- 用 Event 替代领域表；
- 允许 invalid/unknown Trial 进入 Summary；
- 用新 ResultProcessingRevision 偷换 Experiment Version 的科研语义。

`02–07` 冻结后不再增加新的核心架构层。下一步是整理架构索引与 Implementation Plan。

---

