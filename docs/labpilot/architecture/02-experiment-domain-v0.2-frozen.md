# LabPilot Experiment Domain Architecture

> 文档类型：Architecture Specification / 实验领域架构子规格
> 上位文档：`../00-master-blueprint-v0.2.md`
> 范围合同：`../01-v1-scope-and-acceptance-v0.2-frozen.md`
> 版本：v0.2
> 状态：Approved / Frozen
> 日期：2026-07-23
> 适用对象：LabPilot 设计者、后端实现者、Coordinator/Executor 实现者、测试人员、辅助开发 Agent

---

# 0. 文档目的

本文定义 LabPilot V1 的实验领域模型、生命周期、状态所有权、身份规则、领域命令、事务边界和稳定不变量。

本文要解决的核心问题不是“数据库有哪些表”，而是：

1. 什么是一次正式实验设计；
2. 什么变化必须产生新 Experiment Version；
3. 什么变化必须产生新 Trial；
4. 什么失败只产生新 Attempt；
5. 物理进程完成后，何时才能认为 Trial 可用于统计；
6. Batch、Trial、Attempt、Validation 和 Workflow 的状态分别由谁负责；
7. 如何保证同一实验条件可稳定识别、追溯和复现；
8. 如何避免 Agent、UI、Workflow 和 Executor 越权修改科研事实。

本文不定义 Local Runner 的进程创建协议、Lease 实现、SSH 命令封装和 Reconciliation 轮询算法；这些由 `06-workflow-and-executors.md` 负责。本文只定义这些模块必须遵守的领域契约。

---

# 1. 设计目标与非目标

## 1.1 设计目标

实验领域模型必须满足：

- **设计可审核**：自然语言提案不能直接成为正式实验；
- **版本不可变**：获批实验设计不能被原地修改；
- **身份稳定**：相同逻辑实验在重试、重启和对账后仍保持相同 Trial 身份；
- **执行独立**：基础设施失败不会改变科研实验条件；
- **验证独立**：进程成功不自动等于实验结果有效；
- **来源完整**：结果可追溯到代码、参数、数据、环境、Attempt 和 Artifact；
- **状态单一所有者**：不同模块不能竞争写入同一事实；
- **可渐进实现**：V1 使用模块化单体和 PostgreSQL，不要求事件溯源或通用实验本体。

## 1.2 非目标

本文不建设：

- 通用科研知识图谱；
- Observation、Evidence、Claim 或论文结论审核；
- 通用超参数优化平台；
- GPU 调度、资源公平性或集群队列；
- MLflow Run 的完整兼容层；
- 任意 Workflow DSL；
- 多租户权限模型；
- 跨 Project 自动复用实验事实；
- 自动判断研究结论是否成立。

---

# 2. 领域边界与统一语言

## 2.1 核心对象层级

```text
Project
└─ Experiment
   ├─ Experiment Proposal (draft, revisable)
   └─ Experiment Version (approved, immutable)
      └─ Run Batch
         └─ Trial
            ├─ Attempt #1
            ├─ Attempt #2
            └─ ...
```

结果链：

```text
Attempt
├─ Logs
├─ Metrics
├─ Artifacts
├─ Executor Observation
└─ Validation Report
      ↓
Trial Eligibility
      ↓
Run Batch Summary
```

## 2.2 对象定义

### Project

一个受项目规范、Repository、Artifact 根目录和默认执行策略约束的科研工作空间。

### Experiment

长期实验主题或研究问题容器。Experiment 负责聚合一系列版本化设计，但不直接持有一次运行的参数结果。

示例：

```text
“分析 LG-CausalDiffAE 在 circuit 上缺失边的原因”
```

### Experiment Proposal / Proposal Revision

`ExperimentProposal` 是持续讨论的根对象；`ProposalRevision` 是某一版不可变设计内容。Agent 或用户通过创建新 Revision 修改提案，Verifier、Approval 和 Version 均绑定具体 Revision。Proposal 及其 Revision 都不是正式科研事实。

### Experiment Version

由某个已批准 Proposal revision 冻结得到的不可变正式实验设计。它规定后续 Trial 的语义边界。

### Run Batch

对某个 Experiment Version 的一次具体展开与执行计划。Batch 将参数矩阵和 seed 展开为有限 Trial 集合，并承载并发、预算和汇总规则。

### Trial

一个固定逻辑实验条件。只要代码、参数、seed、数据或环境期望的科研语义未变，Trial 身份不变。

### Attempt

某个 Trial 的一次物理执行。基础设施重试、节点抢占或临时启动失败产生新 Attempt，不产生新 Trial。

### Validation Report

对 Attempt 输出进行确定性检查的版本化报告。它判断该 Attempt 是否形成可用 Trial 结果，但不修改进程真实终态。

### Batch Summary

只基于 eligible Trial 生成的确定性汇总，不包含 LLM 科研解释。

---

# 3. 聚合边界与一致性边界

## 3.1 聚合划分

V1 不把整个 Experiment 树作为一个巨型事务聚合。建议采用以下聚合边界：

| 聚合 | 聚合根 | 负责维护的不变量 |
|---|---|---|
| Proposal Aggregate | ExperimentProposal | ProposalRevision 序号、payload hash、Verifier/Approval 绑定 |
| Experiment Aggregate | Experiment | Version 序号、Version 不可变、Experiment 生命周期 |
| Batch Aggregate | Run Batch | Trial 成员唯一性、预算、派生进度、汇总版本 |
| Trial Aggregate | Trial | trial_condition_hash、Attempt 序号、selected result、eligibility |
| Attempt Aggregate | Attempt | 物理执行状态、AttemptLaunch 一对一、executor identity、时间、失败分类 |
| Validation Aggregate | Validation Report | protocol version、检查结果、reason codes、eligibility decision |

## 3.2 为什么不使用单一巨大聚合

一个 Batch 可包含 20 个 Trial，每个 Trial 可包含多个 Attempt。若所有变化都锁定同一个 Experiment 聚合：

- Coordinator 更新 Attempt 会与 Summary、UI 查询和取消操作竞争；
- 单个 Attempt 状态推进会放大事务范围；
- 后续 SSH 或更多 Runner 会提高冲突概率；
- 重试和 Validation 难以独立处理。

因此，跨聚合一致性使用：

```text
领域命令
+ 数据库事务
+ 唯一约束
+ 事务内 Runtime Event/Outbox enqueue
+ 幂等消费者
```

不使用完整 Event Sourcing。

## 3.3 强一致与最终一致

必须强一致：

- Approved Proposal Revision 与唯一 Experiment Version；
- Batch 创建与初始 Trial 集合；
- Batch 内 Trial 与其 trial_condition_hash 唯一约束；
- Attempt 创建与 attempt number；
- Attempt 状态迁移与关键 Runtime Event enqueue；
- Validation Report 与 Trial eligibility 更新；
- Batch Summary 与纳入 Trial 集合。

允许短暂最终一致：

- Batch 进度缓存；
- Timeline 投影；
- SSE 通知；
- 页面统计；
- 外部可观测系统导出。

---

# 4. Experiment Proposal

## 4.1 Proposal Root 与 Proposal Revision

Proposal 使用“根对象 + 不可变修订”的两层模型：

```text
ExperimentProposal
- proposal_id
- experiment_id
- current_revision_number
- root_status
- created_at
```

```text
ProposalRevision
- proposal_revision_id
- proposal_id
- revision_number
- canonical_payload
- payload_hash
- revision_status
- created_at
```

`ExperimentProposal` 表示一个持续讨论的提案主题；`ProposalRevision` 表示某一版不可变内容。Verifier、Design Approval 和 Experiment Version 必须绑定 `proposal_revision_id`，不能只绑定可变的 Proposal Root。

## 4.2 Proposal 生命周期

Proposal Root 的生命周期反映当前讨论状态；每个 Revision 自创建后不可修改。

```text
draft
  ↓ submit_for_verification
verifying
  ├─ verification_failed → draft (new revision when edited)
  └─ verified
        ↓ request_approval
      awaiting_approval
        ├─ reject → rejected
        ├─ edit → draft (new revision)
        └─ approve → approved
                         ↓ freeze
                       version_created
```

`rejected` 不删除历史内容。用户或 Agent 可基于任意旧 Revision 创建新 Revision，但旧 Revision、Verifier Result 和 Approval 永久保留。

## 4.3 Revision 规则

每次影响正式实验语义的修改都必须：

1. 创建新的 `ProposalRevision`；
2. 分配递增 `revision_number`；
3. 重新计算 canonical payload hash；
4. 使旧 Verifier Result 和 Design Approval 对新 Revision 不再适用；
5. 保留旧 Revision 供审计。

相同 payload 可以在不同 Revision 中再次出现。系统可以提示重复，但不得通过 `UNIQUE(proposal_id, payload_hash)` 禁止它，因为“讨论后恢复到旧方案”仍是有意义的独立审计事实。

仅展示字段变化，例如 UI 排序、折叠状态，不属于 Proposal Revision。

## 4.4 Proposal 必填语义

Proposal Revision 至少包含：

```text
objective
experiment_type
baseline_or_control
train_entrypoint
evaluation_mode
eval_entrypoint (conditional)
working_directory
parameter_template
parameter_matrix
trial_dimensions
replicate_dimensions
seeds
dataset_reference
environment_profile
expected_trial_count
trial_timeout
max_attempts_per_trial
minimum_planned_replicates_per_group
minimum_eligible_replicates_per_group
required_metrics
required_artifacts
validation_protocol
risk_actions
budget_summary
```

`evaluation_mode` 固定为：

```text
separate_entrypoint
integrated_in_training
artifact_only
```

只有 `separate_entrypoint` 强制要求 `eval_entrypoint`。另外两种模式由 Verifier 检查训练入口或 Artifact/Validation 配置是否足以完成评估。

## 4.5 experiment_type 固定规则

V1 使用有限枚举：

| 类型 | Baseline | Control | Seed 最低要求 | 典型用途 |
|---|---|---|---:|---|
| reproduction | 可选，通常为目标论文配置 | 不强制 | 1 | 复现单配置 |
| comparison | 必填 | 至少两个比较组 | 由 Project Policy 决定，默认 3 | 方法比较 |
| ablation | 必填 | 完整模型作为 control | 默认 3 | 去除模块/机制 |
| parameter_sweep | 可选 | 可指定 reference config | 默认 1/配置 | 参数敏感性 |
| stability | 必填目标配置 | 同一配置多 seed | 默认 3 | 稳定性评估 |

这组规则是 Verifier 的固定输入，不是通用科研本体。

## 4.6 Design Approval 契约

Design Approval 必须绑定：

- `proposal_revision_id`；
- canonical payload hash；
- Repository Snapshot ID；
- Verifier Result IDs；
- approver；
- approved_at。

创建 Experiment Version 时必须重新检查：

```text
approved proposal_revision_id still exists
current payload hash == approved payload hash
repository snapshot is still eligible
repository is clean
verifier results are current and passed
approval is active
```

任何一项不满足都拒绝冻结。

## 4.7 Freeze 幂等性

同一个 approved Proposal Revision 最多生成一个 Experiment Version：

```text
UNIQUE(experiment_versions.proposal_revision_id)
```

重复执行 `FreezeApprovedProposal` 时：

- 若 Version 尚不存在，创建并返回；
- 若 Version 已存在，返回既有 Version；
- 不得再次分配新的 `version_number`。

# 5. Experiment 与 Experiment Version

## 5.1 Experiment 生命周期

V1 使用最小状态：

```text
active
archived
```

Experiment 不使用 `completed` 作为强终态，因为同一主题可以继续创建新 Version 和 Batch。是否已有充分结果由用户和未来科研治理层决定，不由 V1 自动判断。

## 5.2 Experiment Version 创建

Experiment Version 只能通过领域命令创建：

```text
FreezeApprovedProposal
```

该命令必须在单一事务中：

1. 锁定 approved Proposal Revision；
2. 验证 Approval 和 Verifier；
3. 验证 Repository Snapshot 对应 clean commit；
4. 使用 `git ls-tree` 构建 Source Reference Manifest，并建立 Source Snapshot；
5. 建立 DatasetSnapshot；
6. 固化 Parameter Specification、Trial Dimensions、Replicate Dimensions 与最低重复要求；
7. 固化 Environment Expectation；
8. 固化 Validation Protocol 与 Summary Policy；
9. 创建递增 Version number；
10. 按 Version Canonicalization V1 计算稳定 `experiment_version_identity`；
11. 事务内登记 `experiment_version_created` Event。

## 5.3 Version 不可变内容

Experiment Version 创建后，下列内容不得原地修改：

- Proposal Revision 和 payload hash；
- experiment type；
- objective；
- train/evaluation mode/eval entrypoint；
- resolved working directory；
- parameter template/matrix definition；
- Trial Dimensions、Replicate Dimensions、seed specification；
- `minimum_planned_replicates_per_group` 与 `minimum_eligible_replicates_per_group`；
- Source Snapshot；
- DatasetSnapshot；
- Environment Expectation；
- timeout/retry/budget semantic policy；
- Metric/Artifact requirements；
- Validation Protocol；
- Summary Policy；
- Design Approval reference。

可修改的仅是非语义元数据：

- display name；
- tags；
- human note；
- archived flag。

非语义字段必须存放在独立 metadata 区域，避免误用普通 update API 修改 Version payload。

## 5.4 Stable Version Identity

Version Identity 使用独立、可升级的规范：

```text
version_identity_schema_version = 1
version_canonicalization_version = 1
```

基础标量规则与 Trial Canonicalization V1 一致：

- string 使用 JSON string；
- integer 使用规范十进制整数；
- Decimal 去除无意义尾零，禁止 NaN、Infinity 和负零歧义；
- object key 按 UTF-8 排序；
- 有语义顺序的 list 保持顺序；
- 无语义顺序的集合在 canonicalize 前按其规范值排序；
- 创建人、创建时间、展示名称、标签和说明文字等非语义 metadata 不进入 identity；
- identity schema 或 canonicalization 规则变化时必须提升版本前缀，不能静默改变既有 identity。

`experiment_version_identity` 由 Version 的不可变语义内容生成：

```text
sha256(
  "labpilot-experiment-version:v1\n"
  + canonical_version_payload
)
```

数据库主键仍用于关系引用，但 Trial 条件身份、导入导出和跨备份比对应使用稳定内容身份，不使用普通数据库 ID。

## 5.5 新 Version / 新 Trial / 新 Attempt 的三级规则

### 必须创建新 Experiment Version

下列变化属于正式实验设计变化：

- objective 或 experiment type；
- train/evaluation entrypoint 或 evaluation mode；
- Source Snapshot；
- DatasetSnapshot；
- Environment Expectation；
- parameter space、默认配置或 Trial Dimensions；
- seed specification 或 Replicate Dimensions；
- Validation Protocol；
- Metric/Artifact requirement；
- Summary Policy；
- timeout、retry、budget 中会改变实验解释的语义规则。

处理方式：

```text
new Proposal Revision
→ new Design Approval
→ new Experiment Version
→ new Trials under that Version
```

不得在同一 Version 中替换 Source、Dataset、Environment 或 Validation Protocol 后再创建 Trial。

### 在同一 Version 下创建新 Trial

只允许改变 Version 已声明的 Trial 维度：

- 不同 resolved parameters；
- 不同 seed；
- Version 预先声明的 fold、split、replicate 等维度。

### 为同一 Trial 创建新 Attempt

只用于实验条件不变时的物理重试：

- 瞬时启动失败；
- preemption；
- Runner/节点临时故障；
- 等价执行环境上的重新执行；
- 确认未改变代码、数据、参数、seed 和环境语义的基础设施重试。

Local/SSH 或不同 Runner 之间只有在满足同一个 Environment Expectation 时才能作为新 Attempt；资源或软件环境具有实验语义时，必须通过新 Version 或预先声明的 Trial 维度表达。

# 6. Source Snapshot 与源码物化

## 6.1 Source Snapshot 定义

P0 的 Source Snapshot 是不可变的执行源代码身份，至少包含：

```text
repository_id
frozen_commit_sha
git_tree_sha
source_reference_manifest_digest
reference_manifest_method = git_ls_tree_v1
materialization_method = git_archive
created_at
```

它不等同于主仓库当前工作树。`source_reference_manifest_digest` 表示冻结时基于 Git 对象构建的规范化参考清单摘要，不是 tar archive 原始字节 hash。

## 6.2 Reference Manifest 与 Materialization 规则

冻结 Experiment Version 时执行：

```text
git ls-tree -r --full-tree <frozen_commit>
→ normalize path / mode / object type / object identity
→ detect gitlink and Git LFS pointer
→ compute source_reference_manifest_digest
```

创建 Attempt Workspace 前执行：

```text
git archive <frozen_commit>
→ safe extract to workspace/source/
→ build materialized source manifest
→ compute materialized_source_manifest_digest
→ compare semantic contents with Source Reference Manifest
```

安全要求：

- 拒绝 archive 中的绝对路径；
- 拒绝 `..` traversal；
- 记录普通文件、目录和 symlink 类型；
- symlink target 不得逃出 `workspace/source/`；
- Runner 启动前再次对 working directory 和 entrypoint 执行 realpath 校验；
- 物化结果与 Source Snapshot 参考清单不一致时拒绝提交。

Runner 只能从 `workspace/source/` 执行正式训练。

禁止：

- 从主仓库当前工作树执行；
- 物化失败后退回主仓库执行；
- 在 `workspace/source/` 中运行前静默修改代码；
- 使用未绑定 Version 的 commit。

## 6.3 P0 不支持的 Source Dependency

P0 不支持未物化的 Git submodule 或 Git LFS 内容：

- `git ls-tree` 中 `mode = 160000` / `type = commit` 视为 gitlink/submodule；
- 文件头匹配 Git LFS pointer 规范时，不能将 pointer 文件当作真实内容；
- 检测到任一 gitlink 或 LFS pointer 时，P0 直接拒绝冻结或提交，不尝试判断该依赖是否会在运行中使用；
- Runner 不得临时联网拉取未知 submodule/LFS 内容；
- 返回 `UnsupportedSourceDependency` 或 `SourceMaterializationIncomplete`。

未来可通过 Source Materializer Adapter 支持，但不得改变既有 Source Snapshot 身份语义。

## 6.4 P0 与 P1 Worktree 边界

P0：

- 使用不可编辑的 Source Materialization；
- Agent 不修改正式源码；
- Smoke Test 可以在受限临时 Workspace 执行，但不形成正式 Trial。

P1：

- Agent 修改代码时使用独立 Worktree；
- 修改经人审和 commit 后形成新的 clean commit；
- 新 commit 再进入 Proposal/Approval/Version 流程。

# 7. DatasetSnapshot

## 7.1 目的

DatasetSnapshot 解决“相同路径但内容已改变”的问题。Dataset identity 进入不可变 Experiment Version identity，Trial Condition 因而间接绑定稳定数据身份，而不是单纯路径字符串。

## 7.2 数据结构

```text
DatasetSnapshot
- dataset_snapshot_id
- reference_type
- path_or_uri
- identity_type
- identity_value
- captured_at
- reproducibility_level
- manifest_metadata
- warning_codes
```

## 7.3 identity_type

| identity_type | 适用场景 | 稳定性 |
|---|---|---|
| content_digest | 单文件或可完整哈希的小型数据 | strict |
| manifest_digest | 目录数据，相对路径与文件属性形成 Manifest | strict/strong |
| external_version | DVC、对象存储 Version ID、数据库快照号 | strict |
| size_mtime_manifest | 大型目录的路径、大小、mtime 与关键文件摘要 | best_effort/strong |
| mutable_reference | 无法获得稳定身份的路径或 URI | best_effort |

## 7.4 reproducibility_level

```text
strict
strong
best_effort
```

规则：

- `content_digest`、可靠 `manifest_digest`、可靠 `external_version` 可标记 `strict`；
- `size_mtime_manifest` 通常标记 `strong` 或 `best_effort`；
- `mutable_reference` 必须为 `best_effort`；
- best-effort Trial 可以执行，但 Validation Report 和 Batch Summary 必须显示警告；
- 若 Project Policy 要求 strict reproducibility，则 best-effort DatasetSnapshot 禁止创建 Batch。

## 7.5 Dataset 变化规则

- Dataset identity、identity type 或 reproducibility requirement 发生变化：必须创建新 Experiment Version；
- reference path 改变但稳定 identity 完全相同：可以创建新 Proposal Revision 并由审核者决定是否仍视为同一 DatasetSnapshot；正式 Version 不允许原地替换；
- 数据仅在 Attempt 运行中被发现与冻结 Snapshot 不匹配：Attempt 保留真实执行终态，Validation 必须失败或判定 invalid；
- `mutable_reference` 可以执行，但其 `best_effort` 复现等级必须进入 Validation Report 和 Batch Summary。

---

# 8. Environment Expectation 与 Observed Environment

## 8.1 Environment Expectation

Experiment Version 固化期望环境：

```text
profile_id
python_version_constraint
framework_versions
cuda_requirement
device_class
required_env_keys (names only)
container_or_conda_identity
resource_expectation
```

Secret 值不得进入 Version、Trace 或 Snapshot。

## 8.2 Observed Environment

Attempt 记录真实观察：

```text
hostname/runner_id
os/kernel
python_version
framework_versions
gpu_model
gpu_driver/cuda
resolved_environment_identity
resource_observation
```

## 8.3 环境变化与 Version/Attempt 边界

Environment Expectation 是 Experiment Version 的不可变部分。会改变实验解释的环境要求必须通过新 Version 表达，例如：

- CPU 与 GPU 后端；
- 不兼容的框架主版本；
- mixed precision 策略；
- 明确指定的 GPU profile；
- 容器镜像 digest。

纯执行位置变化通常不改变实验条件，例如在两台满足同一 Environment Expectation 的等价 Runner 上重试；这种情况产生新 Attempt。Observed Environment 与期望不一致时，Attempt 保留真实终态，由 Validation 判定 ENVIRONMENT_MISMATCH。

---

# 9. Run Batch

## 9.1 Batch 目的

Run Batch 表示“这一次准备运行哪些 Trial、使用何种并发与预算、按何种规则汇总”。同一 Experiment Version 可以创建多个 Batch。

## 9.2 Batch 创建命令

```text
CreateRunBatch
```

输入：

- Experiment Version ID；
- 参数选择或完整矩阵；
- seed/replicate 选择；
- planned concurrency；
- budget override（不得超过 Version/Project 上限）；
- summary rule version；
- `batch_purpose = formal`；
- optional human note。

P0 只支持正式 Batch。参数或 seed 可以从 Version 允许集合中选择，但选择结果必须满足 Version 的最低 planned replicate 约束，不能借子集绕过获批设计。

## 9.3 Trial 展开算法

Batch 创建时必须确定性展开：

1. 读取 Version 的 parameter matrix、Trial Dimensions 与 Replicate Dimensions；
2. 应用允许的参数/replicate 子集选择；
3. 验证所有选择均位于 Version 允许集合内；
4. 生成参数组合笛卡尔积；
5. 与 seed/replicate dimensions 组合；
6. 按 Summary Group 验证 `minimum_planned_replicates_per_group`；
7. 使用 Canonicalization V1 编码 resolved dimensions；
8. 计算每个 `trial_condition_hash`；
9. 在 Batch 内去重，发现重复时拒绝创建；
10. 按稳定排序创建 Trial；
11. 保存 expected trial count；
12. 在单一事务中创建 Batch 与全部 Trial。

当前每批最多 20 个 Trial，因此 V1 采用一次性展开。未来大矩阵可分片，但不能改变 Trial Condition 身份语义。

## 9.4 Formal Batch 重复要求

Experiment Version 必须固定：

```text
allowed_trial_dimensions
replicate_dimensions
minimum_planned_replicates_per_group
minimum_eligible_replicates_per_group
```

P0 只允许 `batch_purpose = formal`：

- 每个 Summary Group 的 planned replicate 数必须达到最低要求；
- 运行结束后 eligible replicate 数不足时可以生成带警告的不完整 Summary，但不能宣称满足正式统计要求；
- Smoke Test 属于受限 Tool Execution，不创建 Run Batch；
- 诊断性小 Batch 作为 P1 扩展，必须使用 `summary_eligible = false`，不得混入正式 Summary。

## 9.5 Batch 状态

Batch 的状态是派生投影，不是独立科研事实。保留两个正交维度：

### execution_status

```text
planned
queued
running
completed
cancelled
failed_to_coordinate
```

### validation_summary_status

```text
not_started
partial
complete_with_valid_results
complete_without_valid_results
```

状态计算依据：

- `execution_status` 来自 Trial/Attempt 的物理进展和 Coordinator 终态；
- `validation_summary_status` 来自 Trial eligibility；
- UI、Agent 和 Executor 不得直接写这些字段；
- 数据库可保存缓存投影，但必须能从权威对象重新计算。

## 9.6 Batch 取消

取消 Batch 表示请求停止尚未终止的 Trial/Attempt，不删除已产生事实。

- 尚未提交的 Trial：`execution_state = cancelled`，并记录 `cancellation_reason = batch_cancelled_before_execution`；
- running Attempt：向 Coordinator 发出 cancellation request；
- 已自然完成的 Attempt：保留真实终态；
- 已完成且 eligible 的 Trial：仍保留结果；
- Summary 明确显示取消造成的不完整性。

# 10. Trial

## 10.1 Trial 实体与 Trial Condition 身份

必须区分：

```text
trial_id
```

表示某个 Batch 内的 Trial 领域实体；

```text
trial_condition_hash
```

表示代码、数据、环境、参数和 replicate 条件的稳定内容身份。

相同 `trial_condition_hash` 可以出现在不同 Batch 中，V1 不自动跨 Batch 复用结果；同一 Batch 内必须唯一：

```text
UNIQUE(batch_id, trial_condition_hash)
```

## 10.2 Canonicalization V1

身份载荷必须包含：

```text
identity_schema_version = 1
canonicalization_version = 1
experiment_version_identity
resolved_trial_dimensions
```

其中 `resolved_trial_dimensions` 包含 parameters、seed 和 Version 中声明的其他 Trial 维度。

Canonicalization V1 固定规则：

- UTF-8；
- string 使用 JSON string；
- integer 使用规范十进制整数；
- boolean 使用 `true/false`；
- null 使用 `null`；
- decimal 从原始 YAML/JSON token 或字符串构造 Decimal，去除无意义尾零，禁止 NaN、Infinity 和负零歧义；
- `0.1`、`0.10`、`1e-1` 规范化为同一个 Decimal 值；
- list 保持顺序；
- object 按 UTF-8 key 排序；
- 路径使用逻辑相对路径或稳定 URI，不使用临时 Workspace 绝对路径；
- 参数类型属于身份，字符串 `"1"` 与整数 `1` 不同。

不得先把十进制参数转换为二进制 float，再尝试恢复身份表达。

Hash：

```text
trial_condition_hash = sha256(
  "labpilot-trial-condition:v1\n"
  + canonical_json
)
```

## 10.3 Trial 派生状态

Trial 不使用一个混合 `status`。`execution_state` 与 `validation_state` 是由权威 Attempt、Validation Report 和取消事实生成的 **cached domain projection**，不得由 Coordinator、UI 或 Agent 直接指定。

### execution_state

```text
not_started
queued
executing
execution_exhausted
execution_finished
cancelled
needs_attention
```

确定性派生规则按以下优先级求值：

```text
needs_attention
    存在 active Attempt = unknown

executing
    存在 Attempt = running

queued
    存在 Attempt = created / preparing / submitted

execution_finished
    不存在 active Attempt，且至少一个 Attempt = succeeded

execution_exhausted
    不存在 active Attempt、没有 succeeded Attempt，
    且失败不可重试或重试预算已经耗尽

cancelled
    Trial 已取消，且不存在 active Attempt

not_started
    尚无 Attempt，且 Trial 未取消
```

优先级固定为：

```text
needs_attention > executing > queued > execution_finished
> execution_exhausted > cancelled > not_started
```

### validation_state

```text
not_validated
validating
eligible
invalid
inconclusive
```

确定性派生规则：

```text
validating
    存在未完成的 Validation Run

eligible
    selected Validation Report 的 eligibility_decision = eligible

invalid
    最新完成的权威 Validation Report 判定 invalid，
    且不存在 selected eligible Report

inconclusive
    最新权威 Validation 结果为 error / inconclusive，
    或相关外部执行身份仍无法确认

not_validated
    尚无完成的 Validation Report
```

优先级固定为：

```text
validating > eligible > invalid > inconclusive > not_validated
```

Trial 直接保存：

```text
selected_attempt_id nullable
selected_validation_report_id nullable
selection_revision
execution_state_projection
validation_state_projection
```

这些投影用于查询和 UI，但领域命令做正确性判断时必须读取 Attempt、Validation Report、取消事实和授权等权威记录，不能只依赖可能过期的投影。

不使用 `result_selection_state = superseded`。历史变化通过 Runtime Event；未来如需复杂科研治理，可增加 `TrialResultSelectionHistory`。

## 10.4 Selected Attempt 与结果来源

P0 使用保守的 first-eligible 规则：

1. 只有终态执行事实且 Validation `eligible` 的 Attempt 才可成为结果来源；
2. 第一个通过 Validation 的 Attempt 成为 `selected_attempt_id`；
3. 同时写入属于该 Attempt 的 `selected_validation_report_id`；
4. Trial 成为 eligible 后，Coordinator 不再自动创建新 Attempt；
5. 不得依据 Metric 表现自动选择或替换结果；
6. 后续 Validation 重评估可更新 selected Validation Report，但不能静默替换 selected Attempt；
7. 若 selected Validation Report 后续判定 invalid/inconclusive，Trial 失去 eligible 状态，不再进入新 Summary；
8. 需要重新物理运行时必须显式发起并记录原因；P0 不自动替换已选择结果。

P1 可提供显式命令：

```text
SelectTrialAttemptResult(
  trial_id,
  attempt_id,
  validation_report_id,
  reason_code,
  requested_by
)
```

该命令不得以“指标更好”为自动选择依据，必须记录理由和审计事件。

## 10.5 Eligible Trial 重跑授权

P0 自动链路不得重跑已经 eligible 的 Trial。P1 允许用户通过显式命令创建一次性授权：

```text
AuthorizeTrialRerun(
  trial_id,
  reason_code,
  human_note,
  authorized_by,
  authorized_at
)
```

产生：

```text
TrialRerunAuthorization
- authorization_id
- trial_id
- status = active | consumed | revoked
- reason_code
- human_note
- authorized_by
- authorized_at
- consumed_by_attempt_id nullable
- consumed_at nullable
```

规则：

- 每份授权最多消费一次；
- `CreateAttemptForTrial` 为 eligible Trial 创建 Attempt 时必须引用 active 授权；
- 授权消费与 Attempt 创建必须位于同一事务；
- 新 Attempt 不自动替换 selected Attempt；
- Agent 不得根据 Metric 表现自动创建授权；
- `AuthorizeTrialRerun` 与 `SelectTrialAttemptResult` 是两个独立动作。

## 10.6 何时创建新 Trial

同一 Version 下，只在 Version 已声明的维度值发生变化时创建新 Trial：

- resolved parameter；
- seed；
- fold/split/replicate 等预声明 Trial Dimension。

Source Snapshot、DatasetSnapshot、Environment Expectation 或 Version 语义变化必须先创建新 Experiment Version；新 Version 下的 Trial 自然具有新的 condition identity。

不创建新 Trial：

- 同一条件的瞬时启动失败；
- Runner 重启；
- 节点抢占；
- 日志/Artifact 收集重试；
- 在满足同一 Environment Expectation 的等价 Executor 上重新执行。

# 11. Attempt

## 11.1 Attempt 创建

Attempt 只能由 Coordinator 请求、由 Attempt Domain Service 通过领域命令创建：

```text
CreateAttemptForTrial
```

创建条件：

- Trial 尚未 eligible；若 Trial 已 eligible，则必须引用尚未消费的 `TrialRerunAuthorization`；
- Trial 未被取消；
- Attempt 数未超过 Version 上限；
- 不存在同 Trial 的 active Attempt；
- Retry policy 允许；
- `unknown` 状态已完成 Reconciliation；
- 对 `target_started` 的旧 Attempt 不复用 token，而创建新 Attempt。

## 11.2 Attempt Number 与 Launch Record

同一 Trial 内：

```text
attempt_number = max(existing attempt_number) + 1
UNIQUE(trial_id, attempt_number)
```

一个 Attempt 对应一个 AttemptLaunch：

```text
AttemptLaunch
- attempt_launch_id
- attempt_id
- submission_token
- launch_phase
- wrapper_identity
- target_identity
- created_at
- updated_at
```

约束：

```text
UNIQUE(attempt_id)
UNIQUE(submission_token)
```

P0 不支持同一 Attempt 多代 Launch。`target_started` 后若需重新执行，必须创建新 Attempt 和新 token。

## 11.3 Attempt 执行状态

领域层只记录目标进程的物理执行事实：

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

说明：

- `succeeded`：目标进程已确认以成功退出码结束；
- `failed`：目标进程或 Executor 执行阶段已确认失败；
- `unknown`：无法可靠确定外部进程身份或终态；
- `cancelled`：已确认取消生效且进程未自然完成；
- Validation、Collection 和 Metric Parsing 不覆盖 Attempt execution status。

Launch Phase：

```text
requested
→ wrapper_started
→ target_started
→ target_exited
```

Launch Phase 只能单调推进，`target_started` 不得回退到 `wrapper_started`。

## 11.4 Attempt 状态迁移表

主要路径：

```text
created
  → preparing
  → submitted
  → running
  → succeeded | failed | cancelled | unknown
```

允许的提前终止：

```text
created   → cancelled
preparing → failed | cancelled | unknown
submitted → running | failed | cancelled | unknown
running   → succeeded | failed | cancelled | unknown
unknown   → running | succeeded | failed | cancelled
```

不变量：

- `succeeded/failed/cancelled` 是不可逆终态；
- `unknown` 只能通过 Reconciliation 结果退出；
- terminal state 不得被后到的普通命令覆盖；
- 不允许 `created → succeeded`、`running → submitted` 等非法跳转；
- 状态迁移与关键 Event enqueue 必须在同一事务。

## 11.5 Active Attempt

Active Attempt 集合：

```text
created | preparing | submitted | running | unknown
```

同一 Trial 最多一个 active Attempt。`unknown` 期间禁止自动创建新 Attempt，必须先 Reconcile 或人工处置。

PostgreSQL 使用部分唯一索引作为并发兜底：

```sql
CREATE UNIQUE INDEX uq_attempt_one_active_per_trial
ON attempts (trial_id)
WHERE execution_status IN (
  'created', 'preparing', 'submitted', 'running', 'unknown'
);
```

创建 Attempt 时，Attempt Domain Service 先锁定 Trial 行并执行领域检查；部分唯一索引负责处理并发竞争的最终防线。
## 11.6 Execution Failure Classification

Attempt execution failure 仅包含物理执行阶段问题：

```text
executor_start_failure
external_termination
preemption
temporary_filesystem_failure
resource_exhaustion
user_cancelled
command_failure
unknown_executor_state
```

默认 retry：

| failure | 默认重试 |
|---|---|
| executor_start_failure | 可重试 |
| preemption | 可重试 |
| temporary_filesystem_failure | 可重试 |
| external_termination | 需按原因判断 |
| resource_exhaustion | 不自动重试 |
| user_cancelled | 不重试 |
| command_failure | 不自动重试 |
| unknown_executor_state | 禁止自动重提 |

以下不是 Attempt execution failure：

```text
collection_failure
log_access_failure
artifact_registration_failure
metric_parse_failure
```

它们属于 Workflow/Collection/Validation Step。目标进程成功后，即使结果收集失败，Attempt 仍保持 `succeeded`，由 Coordinator 重试收集或进入 `needs_attention`。

Environment mismatch 分为：

- 提交前发现环境不满足：preflight rejection，不启动 Attempt；
- 运行后发现实际环境不匹配：Attempt 保留物理终态，由 Validation 记录 `ENVIRONMENT_MISMATCH`。

修改 batch size、precision 或模型配置解决 OOM 属于新 Trial 或新 Version，不是 Attempt retry。

## 11.7 Cancel 竞争

Attempt 保存：

- `cancellation_requested_at`；
- `cancellation_signal_sent_at`；
- `cancellation_confirmed_at`。

规则：

- 取消请求先发生，但进程在信号生效前自然完成：保存真实 `succeeded/failed`；
- 已确认终止且无自然完成：`cancelled`；
- 无法确认：`unknown`；
- 不能因为后到的取消事务覆盖已确认自然终态。

# 12. Validation Protocol 与 Validation Report

## 12.1 Validation Protocol

Validation Protocol 属于 Experiment Version 的不可变部分，至少包含：

```text
protocol_version
command_completion_rule
expected_progress_rule
required_artifact_rules
metric_extraction_rules
metric_validity_rules
snapshot_match_rules
domain_rules
eligibility_rule
```

## 12.2 Validation 执行顺序

```text
1. Execution Check
2. Source/Dataset/Environment Snapshot Check
3. Expected Progress Check
4. Required Artifact Check
5. Metric Parse and Validity Check
6. Domain Rule Check
7. Eligibility Decision
```

后续步骤可以在前置失败后继续收集诊断，但最终 Report 必须保留全部已执行检查结果。

## 12.3 Validation Report 状态

```text
pending
running
passed
failed
error
```

- `passed`：协议完整执行，Trial 可 eligible；
- `failed`：协议完整执行，发现不满足规则；
- `error`：验证器自身错误或输入不可读取，Trial 不可 eligible；
- Validation `error` 可以重跑 Validation，不要求重跑 Attempt。

## 12.4 reason codes

P0 固定 reason code 分类：

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
VALIDATOR_INTERNAL_ERROR
```

一个 Report 可以包含多个 reason codes。

## 12.5 Eligibility

```text
eligible
invalid
inconclusive
```

- `eligible`：所有阻塞规则通过；
- `invalid`：存在明确阻塞失败；
- `inconclusive`：验证输入不足、外部状态 unknown 或 Validator error。

只有 `eligible` Trial 可进入 Batch Summary。

## 12.6 Validation Report 版本与重评估

同一 Attempt 可以产生多个不可变 Validation Report，例如修复 Metric parser 后重新验证。

最小结构：

```text
validation_report_id
attempt_id
validation_sequence
validation_run_key
protocol_identity
validator_version
status
eligibility_decision
reason_codes
created_at
```

约束：

```text
UNIQUE(attempt_id, validation_sequence)
UNIQUE(validation_run_key)
```

规则：

- Report 不覆盖；
- `validation_sequence` 在 Attempt 内递增；
- Trial 的 `selected_validation_report_id` 必须属于 `selected_attempt_id`；
- 若 Validation Protocol 的科研语义改变，必须创建新 Experiment Version；
- 仅修复 parser/validator bug 且不改变规则含义，可使用新 validator version 对原 Attempt 重评估；
- 重评估使 selected Report 变为 invalid/inconclusive 时，Trial 必须失去 eligible 状态并触发新 Summary revision。

---

# 13. Metric、Artifact 与来源关系

## 13.1 Artifact

Artifact 属于 Attempt 输出事实，不属于 Experiment Version。

最小字段：

```text
artifact_id
project_id
batch_id
trial_id
attempt_id
artifact_type
path_or_uri
filename
size
integrity_type
integrity_value
availability
created_at
```

`integrity_type`：

```text
sha256
size_mtime
external_digest
unverified
```

`unverified` Artifact 不得满足严格完整性规则。

## 13.2 Metric

Metric 必须有来源：

```text
metric_id
trial_id
attempt_id
name
value
step_or_epoch
extractor_name
extractor_version
source_artifact_id_or_log_ref
observed_at
```

禁止保存无法追溯来源的“手工汇总值”作为原始 Metric。

## 13.3 Artifact/Metric 不可覆盖

相同 Attempt 重新收集时：

- 内容相同可幂等复用；
- 内容不同必须创建新记录或新 revision；
- 不静默覆盖旧内容；
- selected Validation Report 明确绑定使用的 Artifact/Metric 集合。

---

# 14. Batch Summary

## 14.1 Summary 输入

Batch Summary 只读取：

- Batch 中的 Trial；
- Trial 当前 selected Attempt 和 selected Validation Report；
- selected Validation Report；
- eligibility = eligible 的 Trial；
- Version 固定 Summary Policy 与 Replicate Dimensions。

## 14.2 Summary Group

不同参数配置不得混合计算统一均值。Version 必须声明：

```text
replicate_dimensions:
- seed
```

P0 默认只支持 `seed` 作为 replicate dimension，但模型允许后续扩展 fold、split、bootstrap replicate。

```text
summary_group_key = sha256(
  "labpilot-summary-group:v1\n"
  + canonical(
      experiment_version_identity
      + resolved_trial_dimensions excluding replicate_dimensions
    )
)
```

每组至少输出：

```text
group_key
group_dimensions
eligible_replicate_count
included_trial_ids
excluded_trial_ids_and_reasons
metric_aggregates:
  count
  mean
  std
  min
  max
```

不同 Group 之间默认不计算总体均值，除非 Version 的 Summary Policy 明确声明了有科研意义的跨组聚合规则。

## 14.3 Metric 聚合规则

P0 使用固定、可复核的统计定义：

```text
count
    该 Group 内实际具有该 Metric 有限数值的 eligible Trial 数

mean
    仅对有限数值计算

std
    sample standard deviation, ddof = 1

count < 2
    std = null
    warning = INSUFFICIENT_REPLICATES_FOR_STD
```

其他规则：

- NaN/Infinity 必须在 Validation 阶段拦截，不得进入 Summary；
- 缺失 required Metric：Trial invalid，不进入正式 Summary；
- 缺失 optional Metric：Trial 仍可 eligible，但该 Metric 独立记录 `count` 与 `missing_count`；
- 数值从原始字符串构造 Decimal，不先转换为二进制 float；
- P0 使用 Decimal precision = 34、rounding = `ROUND_HALF_EVEN`；
- `minimum_eligible_replicates_per_group` 未满足时保留实际统计，但必须产生明确警告，不能宣称满足正式重复要求。

Version 的 Summary Policy 必须固定：

```text
aggregation_schema_version
numeric_precision
rounding_mode
std_definition
minimum_eligible_replicates_per_group
missing_required_metric_policy
missing_optional_metric_policy
```

## 14.4 Batch-Level Summary

Batch 总层输出：

```text
total_trials
eligible_trials
invalid_trials
inconclusive_trials
failed_or_exhausted_trials
cancelled_trials
attempt_count
retry_count
total_runtime
summary_groups
reproducibility_warnings
experiment_version_identity
summary_rule_version
generated_at
```

## 14.5 Summary 版本化

每次纳入 Trial 集合、selected Validation Report 或 Summary Rule 结果变化，创建新 Summary revision。

- 旧 Summary 不覆盖；
- Summary 记录 included/excluded Trial IDs；
- Summary 可重算；
- P0 Summary 为确定性结果；
- P1 LLM Result Interpretation 只能读取指定 Summary revision 和 eligible Trial。

## 14.6 不完整 Batch

Batch 即使存在 invalid、cancelled 或 execution exhausted Trial，也可以生成 Summary，但必须：

- 显示完整纳入/排除数量；
- 不把不完整 Batch 表述为全量完成；
- 按 Group 显示最小有效 replicate 数是否满足规则；
- 不静默删除失败 Trial。

# 15. 领域命令

## 15.1 命令清单

P0 至少定义以下显式领域命令：

```text
CreateExperiment
CreateProposal
ReviseProposal
SubmitProposalForVerification
RecordVerifierResult
RequestDesignApproval
ApproveProposalRevision
RejectProposalRevision
FreezeApprovedProposal
CreateRunBatch
RequestBatchCancellation
CreateAttemptForTrial
RecordAttemptPrepared
RecordAttemptSubmitted
RecordAttemptRunning
RecordAttemptTerminalState
RecordExecutorUnknown
RecordAttemptReconciled
RequestAttemptCancellation
RecordCancellationOutcome
CreateValidationReport
CompleteValidationReport
GenerateBatchSummary
ArchiveExperiment
```

P1 在科研治理需要明确后再增加：

```text
AuthorizeTrialRerun
SelectTrialAttemptResult
```

## 15.2 禁止通用 update

核心对象不得暴露：

```text
PATCH /experiment-version/{id} arbitrary payload
PATCH /trial/{id} status=...
PATCH /attempt/{id} status=...
```

状态和语义只能通过命令推进。普通 CRUD 只允许非语义 metadata。

## 15.3 命令调用权限

| 调用者 | 允许命令 |
|---|---|
| User/UI | 创建/编辑 Proposal、批准/拒绝、创建/取消 Batch、归档；P1 可授权 Trial 重跑和显式选择结果 |
| Agent Harness | 创建 Proposal、提交验证、提出命令请求；不能直接批准 |
| Verifier | 记录 Verifier Result |
| Coordinator | 创建 Attempt、推进协调相关命令、请求 Validation |
| Executor Adapter | 返回 Observation；不得直接改 Trial/Batch |
| Validation Engine | 创建并完成 Validation Report |
| Summary Service | 生成 Summary revision |

---

# 16. 领域事件

## 16.1 事件原则

事件用于审计、Timeline、异步投影和后续动作，不是领域表的替代来源。

关键 Event enqueue 与领域状态修改必须在同一 PostgreSQL 事务中完成。

## 16.2 关键事件

```text
experiment_created
proposal_created
proposal_revised
proposal_verification_completed
proposal_approved
proposal_rejected
experiment_version_created
run_batch_created
trial_created
batch_cancellation_requested
attempt_created
attempt_prepared
attempt_submitted
attempt_running
attempt_succeeded
attempt_failed
attempt_cancelled
attempt_state_unknown
attempt_reconciled
validation_started
validation_completed
trial_became_eligible
trial_became_invalid
trial_result_selected
trial_result_selection_changed
trial_rerun_authorized
trial_rerun_authorization_consumed
batch_summary_generated
experiment_archived
```

## 16.3 correlation 与 causation

事件至少包含：

```text
project_id
aggregate_type
aggregate_id
event_type
sequence
payload
occurred_at
correlation_id
causation_id
agent_run_id (optional)
workflow_id (optional)
```

一次 Batch 执行使用同一 correlation ID；每个事件的 causation ID 指向触发它的命令或前置事件。

同一 Aggregate 的 sequence 必须唯一且单调递增：

```text
UNIQUE(aggregate_type, aggregate_id, sequence)
```

sequence 分配、领域状态更新和 Event enqueue 必须位于同一事务。

---

# 17. 状态所有权矩阵

状态所有权必须区分“持久化写入服务”和“授权调用者”。授权调用者只能发出领域命令，不能直接更新表。

| 事实 | 权威对象 | 持久化写入服务 | 授权调用者/来源 |
|---|---|---|---|
| Proposal Root/Revision | Proposal | Proposal Domain Service | User/UI、Agent Harness |
| Design Approval | Approval | Approval Service | User |
| Experiment Version | Version | Experiment Domain Service | approved freeze command flow |
| Batch membership | Batch | Batch Domain Service | User/UI command |
| Batch derived status | Batch Projection | Domain Projection Service | Trial/Attempt/Validation events |
| Trial condition identity | Trial | Trial Domain Service | Batch creation command |
| Trial execution projection | Trial Projection | Trial Projection Service | Attempt/Cancel events |
| Trial validation projection | Trial Projection | Validation/Trial Domain Service | Validation Reports |
| Trial eligibility/result selection | Trial + Validation | Validation/Trial Domain Service | Validation Engine、P1 explicit user command |
| Trial rerun authorization | TrialRerunAuthorization | Trial Domain Service | P1 explicit user command |
| Attempt execution status | Attempt | Attempt Domain Service | Coordinator based on verified Observation |
| Attempt Launch phase | AttemptLaunch | Attempt Domain Service | Coordinator/Runner verified observation |
| Workflow step | Workflow | Coordinator Workflow Store | Coordinator |
| External process/job state | Executor Observation | Executor Adapter/Runner | external system observation |
| Validation result | Validation Report | Validation Domain Service | Validation Engine |
| Summary | Batch Summary | Summary Domain Service | Summary Worker/Coordinator trigger |

Agent、UI、Executor Adapter 和 Coordinator 均不得绕过相应 Domain Service 直接修改领域表。

# 18. 并发控制与数据库约束

## 18.1 必须的唯一约束

```text
UNIQUE(experiment_id, version_number)
UNIQUE(experiment_versions.proposal_revision_id)
UNIQUE(proposal_revisions.proposal_id, revision_number)
UNIQUE(batch_id, trial_condition_hash)
UNIQUE(trial_id, attempt_number)
PARTIAL UNIQUE(attempts.trial_id) WHERE execution_status IN (created, preparing, submitted, running, unknown)
UNIQUE(attempt_launches.attempt_id)
UNIQUE(attempt_launches.submission_token)
UNIQUE(validation_reports.attempt_id, validation_sequence)
UNIQUE(validation_reports.validation_run_key)
PARTIAL UNIQUE(attempts.rerun_authorization_id) WHERE rerun_authorization_id IS NOT NULL
UNIQUE(batch_id, summary_revision)
UNIQUE(aggregate_type, aggregate_id, event_sequence)
```

不设置 `UNIQUE(proposal_id, payload_hash)`；相同内容可在不同 Revision 中再次出现并保留独立审计意义。

## 18.2 乐观锁

Proposal Root、Batch、Trial、Attempt 建议增加 `row_version`，命令更新时使用乐观锁，防止：

- 用户编辑与审批竞争；
- Cancel 与自然完成竞争；
- 两个 Coordinator 同时推进 Attempt；
- Validation 重评估与 Summary 生成竞争；
- P1 显式结果选择与自动状态推进竞争。

## 18.3 悲观锁使用范围

只在短事务中使用行锁：

- 冻结 Proposal Revision；
- 创建 Batch 与 Trial；
- 分配 Attempt number；
- 选择/更新 selected Validation Report；
- 生成 Summary revision；
- 分配 Aggregate Event sequence。

禁止持锁等待 Executor、模型或文件系统。

# 19. 错误模型

## 19.1 Domain Error

```text
ProposalNotVerified
ApprovalStale
RepositoryNotClean
VersionImmutable
InvalidExperimentTypeRule
DuplicateTrialCondition
BatchNotRunnable
InsufficientPlannedReplicates
TrialAlreadyHasActiveAttempt
TrialRerunAuthorizationRequired
TrialRerunAuthorizationConsumed
AttemptLimitExceeded
RetryNotAllowed
AttemptStateConflict
IllegalAttemptTransition
ExecutorStateUnknown
ReconciliationRequired
ValidationNotEligible
ValidationReportSelectionConflict
SummaryInputChanged
SnapshotMismatch
UnsupportedSourceDependency
SourceMaterializationIncomplete
```

## 19.2 错误响应原则

领域错误必须返回：

```text
error_code
human_message
aggregate_id
current_state
expected_state
retryable
```

不得只返回通用 HTTP 500 或 LLM 文本。

---

# 20. LG-CausalDiffAE 示例

## 20.1 Experiment

```text
Experiment:
“验证 circuit 缺失边是否可通过图发现参数调整恢复”
```

## 20.2 Experiment Version v1

```text
experiment_type: parameter_sweep
frozen_commit: abc123
source_snapshot: tree-abc123
DatasetSnapshot: circuit-v1 manifest digest
base config:
  warmup: 10000
  causal_update_freq: 4000
  total_steps: 40000
parameter matrix:
  graph_threshold: [0.20, 0.25, 0.30]
seeds: [1, 2, 3]
required metrics:
  reconstruction_mae
  graph_shd
required artifacts:
  ema_checkpoint.pt
  learned_graph.json
validation:
  training_steps == 40000
  checkpoint exists
  graph json parses
  metrics finite
```

Batch 展开为 9 个 Trial。`seed` 被声明为 replicate dimension，因此 Summary 形成 3 个参数 Group，每个 Group 聚合 3 个 seed，而不是对全部 9 个 Trial 直接求总体均值。

## 20.3 Retry 示例

Trial：

```text
graph_threshold=0.25
seed=2
```

Attempt #1：Runner 启动时临时文件系统错误，`failed/executor_start_failure`。

Attempt #2：相同 Trial，成功运行并通过 Validation。

`trial_condition_hash` 不变。Attempt #1 未通过执行，Attempt #2 是第一个通过 Validation 的 Attempt，因此成为 selected Attempt。

## 20.4 OOM 示例

若 Attempt #1 OOM：

```text
failure_classification = resource_exhaustion
retryable = false
```

将 batch size 从 16 改为 8 会改变 resolved parameters，因此需要新 Trial；若修改全局设计，则创建 Experiment Version v2。

## 20.5 Validation failure 示例

Attempt exit code = 0，但 `learned_graph.json` 缺失：

```text
Attempt = succeeded
Validation = failed
reason = REQUIRED_ARTIFACT_MISSING
Trial = invalid
Batch Summary excludes Trial
```

---

# 21. 测试策略

## 21.1 单元测试

- Proposal Root/Proposal Revision 与 canonical hash；
- revision 与 Approval 失效；
- FreezeApprovedProposal 幂等；
- experiment_type 固定规则；
- Version 不可变；
- DatasetSnapshot reproducibility 判定；
- Experiment Version stable identity；
- Trial Condition Hash 与 Canonicalization V1；
- Decimal 等价表达和参数类型差异；
- Attempt 状态迁移与非法边拒绝；
- Attempt execution failure 与 collection issue 分类；
- Attempt retry 分类；
- Cancel 竞争规则；
- Validation reason code；
- Trial execution/validation projection 的确定性派生与优先级；
- Summary 只纳入 eligible Trial；
- Summary 按非 replicate dimensions 分组；
- Summary 使用 sample std、低样本 null 和缺失 Metric 策略；
- submodule/Git LFS 非支持边界。

## 21.2 数据库约束测试

- 同 Batch 重复 trial_condition_hash 被拒绝；
- 同 Trial 重复 attempt number 被拒绝；
- 部分唯一索引阻止同一 Trial 同时存在多个 active Attempt；
- 并发创建 Attempt 只成功一个；
- 并发冻结同一 Proposal 只创建一个 Version；
- 同一 Attempt 只能有一个 Launch，submission token 全局唯一；
- Validation sequence/run key 唯一；
- selected Validation Report 更新使用乐观锁；
- TrialRerunAuthorization 只能消费一次；
- Event enqueue 与领域状态原子提交。

## 21.3 集成测试

- Approved Proposal → Version → Batch → Trials；
- formal Batch 子集选择不能绕过最低 planned replicate 要求；
- 物化 frozen commit 后主仓库切换不影响执行源；
- gitlink/submodule 或 LFS pointer 被明确拒绝；
- Source/Dataset/Environment 改变必须创建新 Version；
- 瞬时基础设施失败产生新 Attempt；
- exit 0 + Artifact 缺失形成 invalid Trial；
- Validation parser 修复后重评估，不重跑 Attempt；
- selected Report 失效后 Trial 从 eligible 退出并生成新 Summary revision；
- Batch Summary 使用固定 Decimal/样本标准差规则并可追溯到 included Trial、Attempt、Metric 和 Artifact；
- Cancel 与自然完成竞争保留真实终态。

## 21.4 必须覆盖的范围合同不变量

本规格必须覆盖冻结范围中的：

```text
INV-001, INV-002, INV-003, INV-004, INV-005,
INV-007, INV-008, INV-011, INV-012, INV-016,
INV-017, INV-019, INV-020, INV-021
```

`INV-006`、`INV-015` 的进程与 Reconciliation 细节由 `06-workflow-and-executors.md` 进一步定义。

## 21.5 Experiment Domain 局部不变量

以下不变量是冻结范围在 Experiment Domain 中的细化契约：

```text
ED-INV-001
Trial execution_state 与 validation_state 只能由权威 Attempt、Validation 和取消事实派生。

ED-INV-002
每个 Trial 同一时刻最多存在一个 active Attempt；unknown Attempt 阻止创建新 Attempt。

ED-INV-003
eligible Trial 的物理重跑必须消费一次性 TrialRerunAuthorization。

ED-INV-004
formal Batch 的 planned replicate 数不得低于 Experiment Version 规定的最低值。

ED-INV-005
Experiment Version Identity、Source Reference Manifest 和 Trial Condition Hash 必须使用固定版本的 canonicalization。

ED-INV-006
Batch Summary 必须使用 Version 冻结的统计规则，前端、Agent 和临时请求不得改变统计口径。
```

---

# 22. 与其他架构子规格的接口

## 22.1 Agent Harness

Agent Harness 可以：

- 读取 Experiment/Version/Batch 摘要；
- 创建或修订 Proposal；
- 请求 Verifier；
- 请求用户审批；
- 在 P1 读取 eligible Trial 和 Summary 生成解释提案。

Agent Harness 不可以：

- 直接创建 Version；
- 直接写 Trial/Attempt 状态；
- 自动批准 Proposal；
- 绕过 Coordinator 启动正式执行；
- 把 LLM 判断写成 Validation Result。

## 22.2 Context Compiler

Context Compiler 必须优先读取：

- 当前 Version 的不可变 payload；
- Batch/Trial/Attempt 权威状态；
- selected Validation Report；
- 明确版本的 Summary；

不得把历史聊天摘要作为上述事实的替代来源。

## 22.3 Workflow 与 Executor

`06-workflow-and-executors.md` 必须实现本文契约：

- Attempt 创建后才允许产生执行意图；
- Executor Observation 通过 Attempt Domain Service 的领域命令推进 Attempt；
- unknown 退出必须使用 `RecordAttemptReconciled`；
- `target_started` 后重跑必须新 Attempt；
- unknown 状态禁止自动重提；
- Coordinator 不写 Trial execution/validation projection 或 eligibility；
- eligible Trial 的重跑必须引用并消费一次性授权；
- Retry policy 遵守 execution failure classification；
- Collection failure 不得覆盖 Attempt execution state；
- Coordinator 不得直接写 Attempt 表。

## 22.4 Result/AgentOps

Result 模块保存 Artifact/Metric；Validation Engine 生成 Report；Runtime Event 投影 Timeline。它们不能覆盖领域对象的权威状态。

---

# 23. V1 简化与后续演进

## 23.1 V1 简化

- 单用户；
- 一个主 Repository；
- 一个 Experiment Version 绑定一个 Source Snapshot；
- 参数矩阵只支持有限 JSON 标量/列表；
- 每个 Trial 最多一个 active Attempt，并由 PostgreSQL 部分唯一索引兜底；
- Trial execution/validation 状态是权威事实驱动的缓存投影；
- 默认第一个 eligible Attempt 为 selected；
- P0 不自动替换 selected Attempt；
- replicate_dimensions 默认仅支持 seed；
- P0 只支持满足最低 planned replicate 要求的 formal Batch；
- Batch Summary 按 Summary Group 聚合并使用固定统计口径；
- 未物化 Git submodule/Git LFS 内容不受 P0 支持；
- 不支持跨 Project Trial 复用；
- 不支持人工 post-hoc 排除有效 Trial；
- 不支持复杂统计检验工作流。

## 23.2 后续演进

未来可扩展但不得破坏现有身份语义：

- Worktree/patch-based Source Snapshot；
- DVC/对象存储 Dataset Adapter；
- MLflow Adapter；
- Evaluation Run 独立对象；
- 预注册 exclusion rule 与 post-hoc exclusion 审计；
- 多种统计汇总和置信区间；
- Follow-up Experiment 关系；
- Observation/Evidence/Claim 层；
- 团队 Reviewer 与科研治理。

---

# 24. 架构决策摘要

| ID | 决策 | 原因 | 代价 |
|---|---|---|---|
| ED-01 | Proposal 与 Version 分离 | 草案可修改，正式设计需批准和冻结 | 对象与流程增加 |
| ED-02 | Version 不可变 | 保证运行语义和来源稳定 | 修改需新版本 |
| ED-03 | Batch 创建时一次性展开 Trial | 避免部分展开和重复身份 | 大矩阵未来需分片 |
| ED-04 | Version identity 与 Trial Condition Hash 使用带 namespace 的 Canonicalization V1 | 稳定识别内容身份并支持迁移 | 需严格 Decimal/JSON 规范 |
| ED-05 | Trial 与 Attempt 分离 | 区分科研条件与基础设施重试 | 状态模型更复杂 |
| ED-06 | Attempt execution 与 Validation 分离 | exit 0 不等于结果有效 | 多一个验证阶段 |
| ED-07 | DatasetSnapshot 绑定稳定身份 | 防止路径内容静默变化 | 快照生成有成本 |
| ED-08 | P0 使用 git ls-tree 冻结 Source Reference，并用安全 git archive 物化执行源码 | 避免主工作树变化影响执行 | 需要参考清单与物化清单校验 |
| ED-09 | Batch 状态是派生投影 | 防止多模块竞争写状态 | 需要重算逻辑 |
| ED-10 | Summary 仅使用 eligible Trial 并按 replicate dimensions 分组 | 避免混合不同参数配置 | 需要 Version 声明汇总维度 |
| ED-11 | 领域命令代替任意 PATCH | 保护生命周期不变量 | API 数量增加 |
| ED-12 | 领域表是真相，Event 是审计/投影 | 避免完整 Event Sourcing 复杂度 | 需要原子 event enqueue 与 sequence 约束 |
| ED-13 | 第一个 eligible Attempt 默认成为结果来源 | 避免自动挑选更好结果 | 显式替换需后续治理 |
| ED-14 | Attempt execution 与 Collection Step 分离 | 保留真实物理终态 | Workflow 状态更细 |
| ED-15 | Proposal Root 与 Revision 分离 | 审批和冻结精确绑定不可变内容 | 多一个领域对象 |
| ED-16 | Trial 状态采用权威事实驱动的缓存投影 | 避免重试时手工状态来回写造成失真 | 需要 Projection Service 与重算逻辑 |
| ED-17 | active Attempt 使用领域锁定与部分唯一索引双保险 | 防止并发重复执行 | PostgreSQL 约束与错误映射更复杂 |
| ED-18 | eligible Trial 重跑使用一次性授权 | 区分重新执行与结果来源替换 | P1 增加授权对象 |
| ED-19 | P0 只支持满足最低重复要求的 formal Batch | 防止子集绕过获批设计 | 诊断 Batch 后移 |
| ED-20 | Summary 固定 Decimal 与 sample std 规则 | 保证跨实现统计一致 | 需要精度与缺失值策略 |

---

# 25. 完成定义

本规格可以标记 Approved/Frozen 的条件：

1. Experiment、Version、Batch、Trial、Attempt 和 Validation 的定义无重叠；
2. 所有影响身份的变化均有明确“新 Version / 新 Trial / 新 Attempt”规则；
3. 状态所有权矩阵与范围合同一致；
4. Experiment Version identity、Source Reference Manifest、Trial Condition Hash 与 Canonicalization V1 足以指导实现；
5. Source Materialization、submodule/LFS 边界与 DatasetSnapshot 复现语义闭合；
6. execution、collection、validation、eligibility 和 summary 纳入规则没有混用；
7. Trial 派生状态、active Attempt 约束、Attempt 状态机、Launch 一对一、Reconciliation 命令和写入所有权足以供 `06-workflow-and-executors.md` 继续设计；
8. LG-CausalDiffAE 示例可以覆盖参数展开、重试、OOM 和 Validation failure；
9. Formal Batch 最低重复要求与 Summary 统计口径已经冻结；
10. 没有引入 P2 能力；
11. 用户完成文档评审并明确批准。

