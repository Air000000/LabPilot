# LabPilot V1 范围与验收规范

> 文档类型：V1 Scope & Acceptance / 首版范围冻结与验收规格  
> 上位文档：`00-master-blueprint-v0.2.md`  
> 版本：v0.2  
> 状态：Approved / Frozen  
> 日期：2026-07-23  
> 适用对象：LabPilot V1 设计者、实现者、Codex/Claude Code 辅助开发 Agent、测试与评审人员

---

## 0. 文档目的与约束层级

### 0.1 文档目的

本文将总体蓝图中的 V1 边界转换为可执行、可测试、可验收的范围约束，负责冻结：

1. V1 必须交付的 P0 能力；
2. V1 高价值但不阻塞 Core 发布的 P1 能力；
3. 明确延后的 P2 能力；
4. 每项 P0/P1 能力的最小实现深度；
5. 正常闭环、跨重启恢复、语义验证、高风险工具审批四条发布阻塞验收链路；
6. SSH 差异化验收链路；
7. 领域不变量、可靠性指标、Agent Eval 和故障注入要求；
8. V1 的完成定义、降级策略和范围变更规则。

本文是 V1 的范围合同。后续 `architecture/*.md`、`phases/*.md` 和实施计划不得扩大本文定义的 V1 范围；如需改变 P0/P1/P2，必须先修改本文并记录架构决策。

### 0.2 文档层级

```text
00-master-blueprint-v0.2.md
    决定长期正确边界和总体架构

01-v1-scope-and-acceptance.md
    决定首版必须做什么、做到多深、怎样验收

architecture/*.md
    决定每个能力内部怎样设计

phases/*.md
    决定按什么顺序实现和集成

implementation-plans/*.md
    决定具体文件、测试、命令和提交拆分
```

### 0.3 冲突处理

发生冲突时按以下顺序处理：

1. 不得违反 Master Blueprint 的稳定架构原则；
2. 不得绕过本文的 P0 验收标准；
3. 架构子规格可以细化，但不得扩大范围；
4. 阶段规格可以延后 P1，但不得删除 P0；
5. 实施计划不得自行引入 P2 平台能力。

---

# 1. V1 产品定义

## 1.1 V1 一句话定义

> **LabPilot V1 是面向个人计算科研的 Experiment-Centric Research Agent：从已有研究目标、Idea、Baseline 或代码仓库出发，通过受控 Agent 决策生成可审核实验方案，可靠执行本地实验，在服务中断后恢复长期任务，并对结果进行确定性验证和可追溯汇总。**

## 1.2 V1 解决的核心问题

V1 只承诺解决以下问题：

1. **实验意图不结构化**：把自然语言目标转成明确的入口、参数、seed、Metric、Artifact 和 Validation；
2. **实验设计未经确认直接运行**：通过 Experiment Proposal 与 Design Approval 冻结正式 Version；
3. **逻辑实验与物理重试混淆**：使用 Trial 与 Attempt 分离参数语义和基础设施执行；
4. **长任务依赖常驻 Agent**：Agent Run 有界结束，固定 Coordinator 接管长期过程；
5. **服务重启导致重复提交或丢失追踪**：通过幂等键、Lease、持久状态和 Reconciliation 恢复；
6. **进程成功被误当成实验有效**：将 execution status 与 validation status 分离；
7. **结果来源不清楚**：绑定 Repository/Code、参数、数据引用、环境摘要、Attempt、Metric 和 Artifact；
8. **过程无法解释**：以统一事件登记投影 Agent、Workflow、Trial/Attempt 三条时间线。

## 1.3 V1 不解决的问题

V1 明确不承诺：

- 从宽泛研究领域自动发现选题；
- 完整文献综述、Research Gap 和通用 Idea 生成；
- 自动证明科研创新性；
- Observation、Evidence、Claim 和论文治理；
- 通用多 Agent 协作平台；
- 通用 Workflow DSL、任意 DAG 和 Temporal 替代品；
- GPU/CPU 资源调度、队列公平性和集群管理；
- Slurm、Kubernetes 和云训练平台；
- 多租户、组织级 RBAC、计费和公众 SaaS；
- 企业级 HA、跨区域容灾和严格 SLA；
- 通用 Artifact CAS、自动去重和复杂留存策略；
- 自动长期自由文本科研记忆。

---

# 2. 用户、环境和发布假设

## 2.1 用户假设

V1 首要用户为单个计算科研人员，满足以下条件：

- 已经有研究问题、初步 Idea、Baseline 或科研代码仓库；
- 能理解 Python、Shell、配置文件和实验指标；
- 需要本地 Linux/WSL2 或固定 Linux 服务器运行实验；
- 同时运行的 Trial 数量有限；
- 接受关键科研设计和高风险工具操作由本人确认。

## 2.2 部署假设

```text
应用形态：模块化单体
API：FastAPI
数据库：PostgreSQL
协调进程：单个 Coordinator Worker 起步
执行控制：Coordinator 只产生执行意图，不直接托管训练进程
本地执行：宿主机 Host-native Local Runner + 独立 Training Process
远程执行：SSH Executor 为 P1
Artifact：本地目录 + 元数据记录
前端：单用户 Web UI
部署：Docker Compose 运行控制面，Linux 宿主机运行 Local Runner
```

## 2.3 规模假设

V1 以如下规模设计和测试：

- 1 个活跃用户；
- 1–5 个 Project；
- 每个 Batch 1–20 个 Trial；
- 默认并发 Attempt 不超过 4；
- 单个 Attempt 可运行数秒到数小时；
- 一个 Coordinator Worker；
- 一个 Linux Host-native Local Runner；
- 一个 PostgreSQL 实例；
- 一个本地 Artifact 根目录；
- P1 最多配置一台固定 SSH 服务器。

这些数字是首版约束，不是数据库硬上限。超过该规模时只要求系统拒绝或排队，不要求自动横向扩展。

---

# 3. V1 北极星结果与发布原则

## 3.1 北极星结果

V1 的核心成功不是“创建了多少页面或模型”，而是：

> 研究者可以把一个真实科研仓库接入 LabPilot，由 Agent 生成并验证实验 Proposal，经确认后展开多 seed Trial；系统通过宿主机 Local Runner 完成本地执行，在 API/Coordinator 重启后恢复协调且不重复创建训练进程；最终收集结果并准确区分执行成功、执行失败和验证失败。

## 3.2 四项价值标准

```text
受控 Agent 决策
+ 真实实验执行
+ 跨重启可靠恢复
+ 可验证且可追溯
```

## 3.3 发布优先级

```text
领域正确性 > 平台完整性
可靠 Local 闭环 > 不可靠 SSH 闭环
确定性验证 > LLM 自评
可恢复性 > 高并发
可回放深链 > 功能数量
```

## 3.4 发布分层

### Core Alpha

证明领域主链可以端到端运行：Project → Snapshot → Proposal → Approval → Version → Trial/Attempt → Local Execution → Validation。Core Alpha 不宣称跨重启可靠恢复已经完成。

### V1 Core Release

P0 全部通过，四条 Core Demo 全部通过。SSH 不作为 Core 发布阻塞项。

### V1 Differentiated Release

V1 Core Release 基础上，至少完成 P1 的 SSH 真实远程恢复链路。

### V1 Polished Release

完成高价值 P1 中的 Worktree、LLM Verifier、Follow-up Proposal、Web Timeline 和成本统计，并完成求职展示材料。

---

# 4. P0：V1 Core 必须完成

P0 是发布阻塞范围。每一项都必须达到本节定义的最小深度，不能只存在表、接口或占位页面。

## 4.1 P0-A：Project 与 Repository Context

### 目标

让 Agent 能在明确、可追溯的代码上下文中规划实验，而不是依赖聊天记忆猜测仓库状态。

### 必须实现

#### V1-FR-001 Project

Project 至少保存：

- 名称与描述；
- 研究目标摘要；
- 主 Repository 注册；
- Artifact 根目录；
- 默认执行 Profile；
- 项目级资源与成本限制；
- `LABPILOT.md` 或等价项目规范文件路径。

#### V1-FR-002 Repository Registration

支持注册一个本地 Git Repository，记录：

- 绝对路径；
- 当前可访问性；
- 默认分支；
- 是否为有效 Git 仓库；
- 最近一次扫描时间。

P0 不要求直接从远程 Git URL 克隆仓库。

#### V1-FR-003 Repository Snapshot 与 Inspection Result

`RepositorySnapshot` 只记录客观仓库事实：

- branch；
- commit SHA；
- dirty state；
- dirty file 列表；
- `LABPILOT.md` 内容哈希；
- 文件树/Manifest 摘要哈希；
- 扫描时间。

`RepositoryInspectionResult` 单独记录 Agent 或规则推断：

- 训练/评估入口候选；
- 关键配置候选；
- `source_path` 与 `line_range`；
- `declared_or_inferred`；
- confidence；
- reason/evidence。

正式 `Experiment Version` 只允许绑定 clean commit。Dirty repository 可用于分析、Proposal 和受限 Smoke Test，但在 Design Approval 前必须提交修改或恢复 clean 状态。

#### V1-FR-004 Repository Inspection Tools

P0 只提供受控只读工具：

- 列目录；
- 读取文本文件；
- 代码/文本搜索；
- Git status/log/diff 摘要；
- 检查命令或入口文件是否存在；
- 检查配置键是否存在。

所有文件路径必须先 canonicalize/realpath，再验证其位于允许的 Repository 或 Workspace 根目录，以防止通过 `..` 或 symlink 逃逸。P0 假设 Workspace 根目录及其挂载配置由可信部署者控制，不得向 Agent 暴露不受信任的 bind mount；如后续需要强制限制挂载边界，应检查 mount namespace 或 `/proc/self/mountinfo`。

### 最小实现深度

- 支持一个真实 Python 科研仓库；
- 支持由项目 Manifest 或用户提示明确入口；
- Agent 可以提出入口候选，但正式 Proposal 必须通过确定性存在性检查；
- 不建设通用代码索引服务；
- 不要求语义代码图、LSP 和向量化整个仓库。

### 验收

- 对 LG-CausalDiffAE 仓库扫描后，可分别保存仓库事实与入口/配置推断证据；
- 当入口不存在时，Proposal 不能进入可批准状态；
- Repository 在 Proposal 后发生变更时，系统能提示 Snapshot 已过期；
- dirty repository 不能冻结正式 Experiment Version；
- Version 冻结后修改、切换或删除主仓库工作树，不影响 Attempt 使用已物化的 frozen source；
- frozen source 无法物化或 manifest 校验失败时拒绝提交，不得回退到当前工作树。

---

## 4.2 P0-B：Agent Harness

### 目标

实现可解释、有界、受控的 Agent 运行语义，而不是将业务状态委托给模型 SDK 的 Thread/Run。

### 必须实现

#### V1-FR-005 Agent Run

每次 Agent 任务创建独立 Agent Run，至少保存：

- Project；
- trigger type；
- user goal；
- model/provider；
- started/ended time；
- status；
- termination reason；
- context snapshot reference；
- produced proposal/result reference；
- token/usage 原始值（如 Provider 提供）。

P0 trigger type 只需要：

- `interactive`。

系统恢复由 Coordinator、Executor Observation、Reconciliation Record 和 Runtime Event 表达，不创建 Agent Run，也不调用 LLM 判断进程事实。

#### V1-FR-006 Context Compiler

P0 Context Compiler 只组装：

```text
当前用户目标
+ Project 摘要
+ LABPILOT.md / Project Manifest
+ Repository Snapshot
+ 用户选择的相关 Experiment/Batch 摘要
+ 当前对象状态
+ 可用 Tool Schema 与风险级别
+ 本次预算和限制
```

必须生成持久化 Context Snapshot，至少记录输入来源 ID、版本/哈希和编译时间。

#### V1-FR-007 Bounded Loop

必须支持：

- 最大 Agent Step；
- 最大模型调用次数；
- 最大 wall-clock time；
- 最大 token 或成本预算（无法得到货币成本时至少限制 token）；
- 明确的 termination reason；
- Tool 执行后重新观察；
- 生成 Proposal、请求人工、达到预算、错误、正常完成等终止类型。

P0 不支持 Agent Run 在训练期间长期挂起。

#### V1-FR-008 Tool Registry

每个 Tool 至少定义：

- name/version；
- input/output schema；
- risk level；
- timeout；
- writable scope；
- idempotency semantics；
- required approval type；
- audit fields。

#### V1-FR-009 Tool Policy

P0 使用静态红黄绿规则：

- Green：只读仓库、历史和结果；
- Yellow：受控临时目录内写入、配置/导入检查、Dry Run、最长 30–60 秒的受限 Smoke Test；
- Red：SSH、主仓库写入、删除/覆盖、超预算、敏感凭据。

不实现 Policy DSL。Yellow Tool 不得创建正式 Trial/Attempt、不得产生可进入 Batch Summary 的正式 Metric，也不得绕过 Coordinator 启动长训练。正式实验只能由已批准的 Experiment Version 经 Coordinator → Executor 触发。

#### V1-FR-010 Structured Trace

至少记录：

- Agent Step；
- Model Call 摘要和 usage；
- Tool Call 输入哈希、结果摘要和状态；
- Policy Decision；
- Approval Request/Decision；
- Verifier Result；
- termination reason；
- correlation/causation ID。

### 最小实现深度

- 单 Agent；
- 无常驻多 Agent 团队；
- 允许调用一个独立 LLM 复核接口，但 P0 不依赖；
- Provider 只需 OpenAI-compatible；
- 不实现通用 Skill Marketplace、长期 Memory 和自动模型路由。

### 验收

- Agent Run 达到 step 或 token 上限时可控终止；
- Tool 未注册、参数不合法或策略拒绝时不得执行；
- 每次 Tool Call 能在 Timeline 中定位其 Agent Run、Policy Decision 和结果；
- Agent Run 结束后不会阻塞实验 Coordinator。

---

## 4.3 P0-C：Experiment Proposal、验证与审批

### 目标

将自然语言目标转成经过程序验证和人工确认的正式实验设计。

### 必须实现

#### V1-FR-011 Experiment Proposal

Proposal 至少包含：

- 实验目标；
- `experiment_type`：`reproduction` / `comparison` / `ablation` / `parameter_sweep` / `stability`；
- Baseline/比较对象（是否必填由 experiment_type 规则决定）；
- train/eval 入口；
- 工作目录；
- 参数模板与参数矩阵；
- seed 列表；
- 数据引用；
- 环境 Profile；
- 预计 Trial 数；
- 每 Trial 超时；
- 最大重试次数；
- 预期 Metric；
- 必需 Artifact；
- Validation 规则；
- 风险动作；
- 预算摘要。

Proposal 为可编辑草案，修改必须增加 revision。

#### V1-FR-012 Deterministic Verifier

必须检查：

- Proposal Schema 完整；
- train/eval 入口存在；
- 参数类型和必需键；
- seed 满足 experiment_type 对数量和唯一性的要求；
- 参数矩阵可展开；
- 输出目录模板包含 Trial 唯一标识或不会冲突；
- Metric/Artifact 声明可解析；
- timeout/retry 在项目限制内；
- Red Tool 已标注需审批。

#### V1-FR-013 Rule Verifier

至少检查：

- train/eval 入口没有明显混用；
- Baseline、control、最低 seed 数和必需 Metric 满足 experiment_type 固定规则；
- 参数与已知配置键一致；
- 预计 Trial 数与矩阵展开一致；
- Snapshot 未过期；
- 同一 Batch 内 Trial identity 不重复；
- Validation 规则覆盖所有必需 Artifact 和关键 Metric。

#### V1-FR-014 Experiment Design Approval

批准记录必须绑定：

- Proposal ID 和 revision；
- canonical payload hash；
- Repository Snapshot；
- Verifier 结果；
- 批准人；
- 批准时间。

只有 `approved` 的 Proposal revision 可以生成 Experiment Version。

#### V1-FR-015 Tool Execution Approval

与 Design Approval 分离。P0 仅需支持一次性 Approval，绑定：

- Agent Run；
- Tool name/version；
- canonical arguments hash；
- Project；
- expiration；
- decision。

### 最小实现深度

- 人工可编辑 JSON/Form Proposal；
- 审批使用单用户确认；
- 不实现多人 reviewer、审批委托和复杂 waiver；
- P0 不要求 LLM Verifier；
- 确定性/规则 Verifier 失败时禁止批准，但允许修改后重新验证。

### 验收

- 修改已验证 Proposal 后，旧验证结果和旧审批自动失效；
- Proposal 绑定 dirty repository 时不能进入可批准状态；
- 未批准的 Proposal 无法创建 Experiment Version；
- Design Approval 不能授权 SSH 或删除 Tool；
- Tool Approval 的参数变化后必须重新申请。

---

## 4.4 P0-D：实验领域模型

### 目标

正确表达正式实验设计、批次、逻辑 Trial、物理 Attempt 和验证结果。

### 必须实现

#### V1-FR-016 Experiment 与 Experiment Version

- Experiment 是长期实验主题容器；
- Experiment Version 是经批准的不可变设计；
- Version 绑定 Proposal revision/hash、Repository/Code Snapshot、参数、`DatasetSnapshot`、环境、Validation Protocol 和审批；
- 影响实验语义的修改必须创建新 Version。

#### V1-FR-017 Run Batch 与状态所有权

Batch 至少保存：

- Experiment Version；
- Trial 集合；
- 计划并发；
- 最大运行预算；
- 汇总规则；
- execution status；
- validation summary status。

状态所有权固定为：

- Workflow status 表示协调过程当前步骤；
- Attempt status 表示物理执行事实；
- Trial status 表示逻辑实验是否已有可用结果；
- Batch status 是由 Trial 状态计算或事务性维护的缓存投影；
- Agent、UI、Executor 不得直接写 Batch/Trial 的派生状态。

#### V1-FR-018 Trial Identity

Trial identity 至少由以下内容的 canonical hash 决定：

```text
experiment_version
+ resolved parameters
+ seed
+ materialized code snapshot
+ dataset snapshot identity
+ environment expectation
```

`DatasetSnapshot` 至少保存：

- `reference_type`；
- `path_or_uri`；
- `identity_type`；
- `identity_value`；
- `captured_at`；
- `reproducibility_level`。

`identity_type` 固定支持：

```text
content_digest
manifest_digest
external_version
size_mtime_manifest
mutable_reference
```

严格可复现实验必须绑定 content digest、manifest digest 或外部版本 ID。无法生成稳定身份时可以使用 `mutable_reference`，但必须设置 `reproducibility_level=best_effort`，并在 Validation 与 Batch Summary 中显示警告。

同一 Batch 内禁止重复 Trial identity。

#### V1-FR-019 Attempt

Attempt 至少保存：

- Trial；
- attempt number；
- executor type；
- idempotency key；
- workspace；
- PID/external job ID；
- submitted/start/end time；
- exit code；
- failure classification；
- stdout/stderr location；
- observed environment；
- reconciliation state。

#### V1-FR-020 Validation Report

Validation Report 独立于 Attempt execution status，至少包含：

- validation protocol version；
- command completion check；
- expected step/checkpoint check；
- required Artifact check；
- Metric parse/NaN check；
- Snapshot/environment match check；
- eligibility；
- reason codes；
- generated time。

### 稳定不变量

1. 参数、seed、代码、数据或环境语义变化创建新 Trial；
2. 相同 Trial 的基础设施重试创建新 Attempt；
3. Attempt `succeeded` 不自动令 Trial `valid`；
4. Trial 只有通过 Validation 才可纳入 Batch Summary；
5. Experiment Version 一旦创建不可原地修改；
6. 相同 submission token 不得同时存在两个有效、可继续执行的 target process；`target_started` 后的重跑必须创建新 Attempt。

### 验收

- 临时进程启动失败或节点抢占后重试产生 Attempt #2，Trial ID 不变；
- OOM 默认分类为 `resource_exhaustion` 且不可自动重试；若修改 batch size/precision 等语义参数，应创建新 Trial 或新 Version；
- 改 seed 后产生新 Trial；
- exit code 为 0 但缺失 Checkpoint 时 Validation failed；
- failed Validation 的 Trial 不进入有效统计，但保留完整记录。

---

## 4.5 P0-E：Workspace、Snapshot 与 Local Executor

### 目标

在受控目录中运行真实科研命令，并能够可靠识别、取消和观察进程。

### 必须实现

#### V1-FR-021 P0 Workspace

P0 使用独立运行目录，至少包含：

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

要求：

- 不直接将输出写入主仓库默认结果目录；
- 每个 Attempt 使用唯一目录；
- 在提交前将获批的 frozen commit 物化到 `workspace/source/`；
- P0 默认使用 `git archive <frozen_commit>` 或语义等价的只读导出方式，并保存 Git tree hash 与源码文件 manifest；
- Runner 只能从 `workspace/source/` 执行，不得回退到当前主仓库工作树；
- Version 冻结后即使主仓库切换、修改或删除工作树，已创建 Attempt 仍使用物化源码；
- 源码无法物化或校验不一致时拒绝提交；
- manifest 记录 Repository Snapshot、Source Materialization、参数、seed、命令和预期输出；
- 只允许 Tool 写入明确的 Workspace/Artifact 路径；
- Cleanup 不自动删除正式结果。

P0 不要求 Agent 修改代码，因此可编辑 Worktree 属于 P1；P0 的 Source Materialization 是正式执行的必要条件，不属于代码修改能力。

#### V1-FR-022 Snapshot

P0 至少保存：

- clean commit SHA（P0 正式 Version 不接受 dirty execution）；
- materialized source tree hash 与 source manifest digest；
- resolved parameters JSON；
- `DatasetSnapshot`，包括稳定 identity 或 `mutable_reference + best_effort` 标记；
- Python/OS/CUDA/PyTorch 等环境摘要；
- exact command；
- executable/workdir，且正式执行 workdir 必须位于 `workspace/source/`；
- environment variable 白名单名称，不保存 secret 值。

#### V1-FR-023 Host-native Local Runner 与 Local Executor

P0 必须使用运行在 Linux 宿主机上的 Local Runner。Coordinator 只写入执行请求和协调状态，不直接 fork 或托管训练进程。Training Process 必须独立于 API/Coordinator 容器生命周期。

Local Executor 接口必须支持：

- prepare；
- submit；
- inspect；
- stream/read logs；
- cancel；
- collect；
- cleanup temporary files。

实现要求：

- Runner 原子领取 executor job；
- 使用独立 `labpilot-run-wrapper` 作为稳定外部任务身份，管理 target 子进程、marker、stdout/stderr 和 exit record；
- 使用受控子进程；
- 记录 PID、Linux process start time、command hash 与 submission token；
- 使用独立 process group；
- stdout/stderr 持久化文件；
- 支持 graceful cancel 后强制终止；
- 支持 wall-clock timeout；
- inspect 必须能区分 running/exited/unknown；
- 不使用仅存在内存中的 Future 作为权威状态。

### 验收

- 能执行一个实际 Python 训练/模拟脚本；
- 能实时或轮询查看日志；
- 取消后进程组不残留子进程；
- API/Coordinator 停止不自动终止 Training Process；
- Local Runner 可根据 Attempt workspace、submission token、PID 与 process start time 重新 inspect；
- PID 被复用或进程身份不确定时返回 `unknown`，不得猜测成功或重新提交。

---

## 4.6 P0-F：Experiment Coordinator 与恢复

### 目标

以固定实验状态机协调 Attempt，支持幂等提交、固定重试、取消、服务重启恢复和 Executor 对账。

### 必须实现

#### V1-FR-024 固定状态机

P0 只支持以下主流程：

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

另有终止分支：

```text
cancelled
failed
completed
completed_with_invalid_trials
```

#### V1-FR-025 Persistent Workflow State

至少保存：

- workflow instance；
- current step；
- state/version；
- related Project/Batch/Attempt；
- next run time；
- retry count；
- lease owner/expiry；
- last error；
- cancellation requested；
- created/updated time。

#### V1-FR-026 Worker Claim 与 Lease

- Worker 使用数据库原子方式 claim 到期任务；
- Lease 有有限 TTL；
- Worker 崩溃后任务可被重新 claim；
- 业务状态推进必须在事务和版本检查下进行；
- 不把 `SKIP LOCKED` 查询结果本身当成业务状态真相。

#### V1-FR-027 Idempotency 与提交协议

创建 Attempt 时生成稳定 `idempotency_key` 和 `submission_token`。Launch phase 固定为：

```text
requested
→ wrapper_started
→ target_started
→ target_exited
```

P0 提交协议固定为：

1. Coordinator 事务性创建 Attempt、submission token、唯一 Workspace 和 executor job；
2. 完成 frozen source materialization，并在 Workspace 写入 `runtime/launch-request.json`；
3. Local Runner 使用 submission token 原子领取请求；
4. Runner 启动独立、脱离控制面生命周期的 `labpilot-run-wrapper`；
5. wrapper 在启动目标命令前，先原子写入 `wrapper_started` marker，保存 attempt_id、submission_token、wrapper PID、process_start_time、command_hash；
6. wrapper 启动受其监督的 target process，并在能够稳定识别该进程后原子写入 `target_started` marker，保存 target PID/start time；wrapper 持续管理 stdout/stderr、取消和 wall-clock timeout；
7. wrapper 等待 target 结束并原子写入 `target_exited` 与 `runtime/exit.json`；
8. 重复 launch 必须先检查 token、launch phase、marker 和进程身份；
9. 数据库与 marker 不一致时进入 Reconciliation，不直接再次启动。

必须保证：

- 同一 submission token 不得同时存在两个有效、可继续执行的 target process；
- `target_started` 已持久化后，不得使用同一 submission token 再次启动；如需重跑，必须创建新 Attempt 和新 submission token；
- 外部进程身份不确定时进入 `needs_attention`，不得自动重新启动；
- 只有能够确定 target 从未启动时，才允许恢复同一 launch request；
- 事务重试、Coordinator 重启和重复 Job 不产生重复 Trial/Attempt；
- 外部副作用发生但数据库响应丢失时，通过 Workspace marker 与 Reconciliation 恢复。

这里保证的是 **at-most-one-active launch generation**，而不是无法在所有崩溃窗口证明的操作系统进程 exactly-once。

#### V1-FR-028 Retry、Timeout 与 Cancel

- 只对明确可重试的基础设施失败自动重试；
- OOM/显存不足默认 `retryable=false`；
- 非零退出默认需要 failure classification 后决定；
- 最大重试次数来自 Experiment Version；
- Retry 创建新 Attempt；
- Timeout 触发取消并记录原因；
- Batch/Trial 取消传播到 running Attempt；
- Cancellation 为幂等操作；
- 若进程在取消信号生效前自然结束，Attempt 保留真实 completed/failed 结果，并单独记录 `cancellation_requested`；
- 只有确认终止信号已生效且进程未自然完成时，Attempt 才进入 cancelled。

#### V1-FR-029 Reconciliation

Coordinator 定期对 running/submitting Attempt 执行：

1. 读取数据库期望状态；
2. 调用 Executor inspect；
3. 记录 Executor Observation；
4. 对比 PID/start marker 与 Attempt；
5. 推进 Attempt/Workflow；
6. 未知状态进入 `needs_attention`，不自动标记成功或重新提交。

### 最小实现深度

- 单个 Coordinator Worker；
- PostgreSQL Job/Outbox 表；
- 固定轮询；
- 固定指数或分段 Backoff；
- 不支持任意 DAG；
- 不支持通用 Activity 注册、确定性 Replay 和分布式 Worker 集群。

### 验收

- Runner 已接受 launch request、wrapper 已写入进程 marker 但数据库尚未投影时被终止，恢复后通过 submission token 与 Workspace marker 找到原进程且不重复创建；
- API 重启不影响 running Attempt；
- Coordinator 重启后在两个轮询周期内恢复追踪；
- Lease 过期后另一 Worker 可安全接管；
- Cancel 重复调用不会产生错误副作用。

---

## 4.7 P0-G：Result、Artifact、Metric 与 Validation

### 目标

可靠收集实验输出，并以确定性规则区分执行成功、执行失败和实验验证失败。

### 必须实现

#### V1-FR-030 Logs

保存：

- stdout；
- stderr；
- LabPilot executor log；
- log start/end offset 或更新时间；
- Attempt 关联。

#### V1-FR-031 Artifact Metadata

Artifact 最小字段：

- artifact ID；
- Project/Batch/Trial/Attempt；
- type；
- logical name；
- path/URI；
- relative path；
- size；
- integrity type：`sha256` / `size_mtime` / `external_digest` / `unverified`；
- integrity value；
- created time；
- availability；
- required flag。

小型关键 Artifact 默认 SHA-256；大型 Checkpoint 可使用 size+mtime 并异步补 hash；外部数据使用已有 digest/version。`unverified` Artifact 不得作为严格复现依据。

#### V1-FR-032 Structured Metric

Metric 至少保存：

- name；
- value；
- step/epoch 可选；
- split；
- Trial/Attempt；
- source Artifact/log；
- extractor version；
- recorded time。

P0 只需要支持 JSON/CSV 或明确日志正则/解析器中的一种主路径和一种备选路径。

#### V1-FR-033 Validation Engine

执行顺序固定为：

1. Execution completion；
2. Snapshot/environment consistency；
3. Required Artifact；
4. Metric parse/type/NaN；
5. Project-specific rule；
6. Trial eligibility。

Validation rule 必须版本化，并记录 reason code。

#### V1-FR-034 Batch Summary

只纳入 eligible Trial，至少生成：

- total/valid/invalid/failed Trial 数；
- 每个 Metric 的样本数、均值、标准差、最小/最大；
- excluded Trial 和原因；
- Attempt 重试次数；
- 总运行时长；
- 关键 Artifact 链接；
- Snapshot/Version 标识。

P0 Summary 由确定性聚合生成。LLM Result Interpretation 属于 P1。

### 验收

- 缺失 required Artifact 时即使 exit code=0 也验证失败；
- NaN Metric 被拦截；
- 无效 Trial 不进入均值和标准差；
- 所有聚合值能追溯到源 Trial、Attempt 和 Artifact；
- Artifact 被删除或不可访问时，availability 更新并显示警告。

---

## 4.8 P0-H：AgentOps、统一事件与产品界面

### 目标

让用户和面试评审能够回答“系统为什么这么做、任务怎样推进、结果从哪里来”。

### 必须实现

#### V1-FR-035 Transactional Runtime Event Enqueue

关键领域状态更新与对应 event/outbox enqueue 必须在同一个 PostgreSQL 事务内提交；任何一方登记失败，领域事务回滚。Timeline projection、SSE delivery 或外部日志导出可以异步失败并重试。

统一事件至少包含：

- event ID；
- Project；
- aggregate type/ID；
- event type；
- sequence；
- payload JSON；
- occurred time；
- correlation ID；
- causation ID；
- actor type/ID。

事件用于追踪，不替代领域表；Timeline projection 失败不改变已提交的领域状态，但不能允许关键事件完全未登记。

#### V1-FR-036 三条 Timeline

必须能查询并展示：

1. Agent Run Timeline；
2. Workflow Timeline；
3. Trial/Attempt Timeline。

三者通过 correlation ID 互相跳转。

#### V1-FR-037 最小 Web Surface

P0 只要求四个主要界面：

1. Project / Repository；
2. Agent Run / Proposal / Approval；
3. Batch / Trial / Attempt / Validation；
4. Unified Timeline / Logs。

Repository Snapshot、Metric、Artifact、Cancel/Retry 等内容通过详情页、Tab、Drawer 或表格完成，不新增大量独立管理页面。

P0 不要求高级 Dashboard、图形工作流编辑器和复杂可视化。

#### V1-FR-038 SSE

支持日志或状态更新的单向推送；断线后允许客户端通过 REST 重新获取当前状态，不要求消息绝不丢失。

### 验收

- 用户能从 Summary 回到 Metric、Artifact、Attempt 和 Version；
- 用户能从 Tool Call 看到 Policy/Approval；
- 用户能看到服务重启前后的同一个 Workflow 连续时间线；
- 当前权威状态来自领域表，不依赖重放全部事件。

---

## 4.9 P0-I：工程质量、测试与部署

### 必须实现

#### V1-NFR-001 模块化单体

至少划分：

```text
projects
repositories
agent_runtime
context
experiments
approvals
tools_policy
coordinator
execution
results_validation
observability
```

模块通过服务接口和领域命令协作，禁止跨模块随意修改表。

#### V1-NFR-002 PostgreSQL First

核心测试环境使用 PostgreSQL，不以 SQLite 模拟 Lease、行锁和并发语义。

#### V1-NFR-003 Migration

所有数据库变更使用 Alembic；CI 从空库执行全量 migration。

#### V1-NFR-004 Tests

必须包含：

- 领域不变量单元测试；
- Agent Harness 单元测试；
- Tool/Approval 测试；
- Host-native Local Runner / Executor 集成测试；
- PostgreSQL Coordinator 并发/恢复测试；
- Validation 测试；
- 四条 Core E2E；
- Agent Eval；
- 故障注入。

不以全局覆盖率百分比替代关键路径测试；所有 P0 不变量必须有明确测试。

#### V1-NFR-005 CI

GitHub Actions 至少执行：

- lint/format check；
- type check；
- unit tests；
- PostgreSQL integration tests；
- migration check；
- 核心 E2E 的可自动化子集。

#### V1-NFR-006 Deployment

Docker Compose 启动控制面：

- PostgreSQL；
- API；
- Coordinator Worker；
- Web UI。

Host-native Local Runner 必须作为独立 Linux 进程运行，以访问现有 Conda/CUDA 环境并保证训练进程不从属于 Coordinator 容器生命周期。P0 不承诺 Windows 原生 Runner；Windows 用户可通过浏览器访问部署在 Linux/WSL2/服务器上的系统。

#### V1-NFR-007 Secret Handling

- secret 不写入 Prompt、Runtime Event、Artifact manifest 和日志；
- 环境变量只允许白名单名称；
- P0 Local 不要求凭据管理系统；
- SSH secret 属于 P1，使用引用而非明文数据库字段。

---

# 5. P1：高价值差异化能力

P1 不阻塞 V1 Core Release，但至少选择 SSH 能力作为 V1 Differentiated Release 的主目标。

## 5.1 P1-A：SSH Executor

### 范围

- 支持一台固定 Linux SSH 服务器；
- 使用 AsyncSSH 或成熟 SSH 库；
- host key verification；
- 远程 Workspace；
- 远程 wrapper 启动后台任务；
- 保存可重识别 remote job ID/PID/start marker；
- inspect/cancel/log fetch/artifact collect；
- SSH 断连与 LabPilot 重启后的 Reconciliation；
- SFTP 或受控命令回收 Artifact。

### 不做

- Slurm；
- 多节点训练；
- GPU 调度；
- 任意服务器池；
- 自动凭据轮换；
- 跳板机和复杂网络拓扑。

### 验收

```text
提交远程任务
→ 保存 remote job identity
→ 关闭 LabPilot API/Worker
→ 远程任务继续
→ 重启 LabPilot
→ Reconcile 发现原任务
→ 不重复提交
→ 拉取日志和 Artifact
→ Validation
```

## 5.2 P1-B：Worktree 隔离

仅当 Agent 需要修改实验代码时启用：

- 每个 Experiment Version/变更提案创建独立 worktree；
- 记录 base commit 和 patch；
- 主仓库默认只读；
- 不将 Worktree 宣称为安全 Sandbox。

如果 V1 只读仓库并通过参数运行，Worktree 可延后。

## 5.3 P1-C：LLM Semantic Verifier

只在确定性和规则验证通过后运行，检查：

- 实验目标与变量是否匹配；
- 是否遗漏关键对照；
- 是否引用了错误历史配置；
- Validation 是否足以回答问题；
- 建议是否超出已有依据。

输出必须结构化为 findings、severity、evidence reference 和 recommendation；不能直接批准 Proposal。

## 5.4 P1-D：Result Interpretation 与 Follow-up Proposal

- 只读取 eligible Trial 和 Batch Summary；
- 明确区分 Observation 与 Interpretation；
- 输出限制、异常和替代解释；
- Follow-up Proposal 创建新草案，不自动执行；
- 不形成正式 Evidence/Claim。

## 5.5 P1-E：成本与使用统计

- Agent token usage；
- 模型调用次数；
- Trial wall-clock；
- 重试成本；
- 可选 GPU hour 手工/环境估算；
- Project/Batch 预算告警。

## 5.6 P1-F：增强 Web Timeline

- Timeline 筛选；
- correlation 跳转；
- Agent/Workflow/Attempt 关键节点摘要；
- 失败原因和恢复动作突出显示；
- Demo 友好的单页回放。

---

# 6. P2：明确延后

以下内容不得在 V1 阶段因为“顺手”“未来可能需要”而进入实现依赖：

1. Research Discovery、系统综述、Gap/Idea 生成；
2. Observation/Evidence/Claim/Publication 完整体系；
3. 多 Agent 团队与 Agent 社会；
4. 通用 Workflow DSL、任意 DAG、Temporal；
5. Celery/RabbitMQ/Kafka 分布式任务平台；
6. Slurm/Kubernetes/云训练平台；
7. Artifact CAS、跨项目去重和复杂 Retention；
8. MLflow 作为核心领域骨架；
9. 通用 Policy Engine 和策略语言；
10. OpenTelemetry、Prometheus、Grafana 全套平台；
11. 多租户、RBAC、组织和计费；
12. 公共 SaaS 与企业 SLA；
13. 动态 Skill 学习和自动长期记忆；
14. 图数据库和 Claim Graph；
15. 自动修改正式论文和文档发布工作流。

P2 可以在接口和 ADR 中保留重新评估条件，但不得创建大量空表、空模块和未使用抽象。

---

# 7. Core 端到端验收 Demo

四条 Core Demo 全部通过，才允许宣布 V1 Core 完成。

## 7.1 Demo A：正常实验闭环

### 场景

对一个真实 Python 科研仓库提出：

```text
使用当前获批配置运行 2 组参数 × 3 个 seed，
收集指定指标和 Checkpoint，并生成 Batch Summary。
```

### 必须发生

1. 创建 Agent Run；
2. Context Compiler 读取 Project、Manifest、Repository Snapshot 和相关历史摘要；
3. Agent 定位入口并生成 Experiment Proposal；
4. Deterministic/Rule Verifier 检查入口、seed、参数、输出目录、Metric/Artifact；
5. 用户批准指定 Proposal revision；
6. 系统冻结 Experiment Version；
7. 参数矩阵展开为 6 个唯一 Trial；
8. Coordinator 创建并提交 Attempt；
9. Host-native Local Runner 运行真实命令；
10. 日志、Metric、Artifact 被收集；
11. Validation 判定 eligible/invalid；
12. Batch Summary 只聚合 eligible Trial；
13. 用户可以沿 Timeline 回放完整过程。

### 通过标准

- Trial 数量和 seed/参数组合完全正确；
- 无重复 Trial/Attempt；
- 所有 Summary 值可追溯；
- Agent、Workflow、Attempt 三条 Timeline 关联完整。

## 7.2 Demo B：跨重启恢复与不重复提交

### 场景

覆盖一个可确定恢复的提交崩溃窗口：Local Runner 已接受 launch request，`labpilot-run-wrapper` 和 target process 均正常存活，`target_started` marker 已持久化，但 PID/状态尚未完整投影到数据库；此时强制终止 Local Runner，并重启 API/Coordinator 与 Runner。

### 必须发生

1. 外部 Training Process 继续运行；
2. Coordinator/Executor job Lease 过期；
3. 新 Coordinator 与 Local Runner 分别恢复协调和执行观察；
4. Reconciliation 使用 submission token、wrapper marker、PID 和 process start time 找到原进程；
5. 系统恢复 Attempt 和 Workflow 跟踪；
6. 不创建第二个外部进程；
7. 原进程结束后正常 collect/validate/finalize。

### 通过标准

- 在该受控窗口中，相同 Attempt 的外部进程创建次数严格为 1；
- 重启后两个轮询周期内恢复观察；
- `target_started` 已持久化但数据库尚未投影时，不得产生第二个训练进程；
- Timeline 能展示中断、Lease 接管和 Reconciliation；
- 无需人工修改数据库。

## 7.3 Demo C：执行成功但验证失败

### 场景

测试命令返回 exit code 0，但故意不生成声明为 required 的 Checkpoint，或生成 NaN Metric。

### 必须发生

1. Attempt execution status 为 succeeded；
2. Artifact/Metric Validation 失败；
3. Trial validation status 为 invalid；
4. Trial 不进入 Batch 有效统计；
5. Validation Report 给出明确 reason code；
6. Timeline 显示“执行成功”和“验证失败”是不同事件。

### 通过标准

- 系统不将该情况分类为 executor failure；
- Summary 不静默纳入无效结果；
- 用户可查看缺失/非法结果来源。

## 7.4 Demo D：高风险 Tool Approval

### 场景

Agent 尝试执行一个 Red Tool，例如覆盖现有 Artifact 或调用模拟 SSH Tool。

### 必须发生

1. Policy 生成 Tool Execution Approval Request；
2. 未批准前 Tool 不执行；
3. Approval 绑定具体参数哈希；
4. 参数修改后旧批准不可复用；
5. 批准、拒绝和过期均有 Trace。

### 通过标准

- Design Approval 不能替代 Tool Approval；
- 未经批准的副作用执行次数为 0。

---

# 8. P1 差异化验收 Demo

## 8.1 SSH 恢复 Demo

```text
提交 SSH Attempt
→ 保存 remote job identity
→ 断开 SSH
→ 关闭 LabPilot
→ 远程训练继续
→ 重启 LabPilot
→ Reconcile 原任务
→ 拉取日志和 Artifact
→ Validation
```

通过标准：

- 远程外部任务创建次数为 1；
- 断连不自动标记失败；
- reconnect 后能正确识别 running/completed；
- Artifact 与 Attempt、Trial、Version 来源链完整。

## 8.2 LLM Verifier Demo

构造一个 Schema 合法但实验语义存在问题的 Proposal，例如：

- 目标要比较稳定性，却只设计一个 seed；
- 声称比较方法差异，却遗漏 Baseline；
- Validation 指标不能回答研究问题。

通过标准：

- 前两层 Verifier 通过；
- LLM Verifier 给出结构化问题、依据和建议；
- 系统不自动批准或修改正式 Version。

---

# 9. 领域不变量验收矩阵

| ID | 不变量 | 必须测试的反例 |
|---|---|---|
| INV-001 | 未批准 Proposal 不得创建 Experiment Version | 直接调用创建 Version 服务 |
| INV-002 | Experiment Version 不可原地修改 | 修改参数/Validation payload |
| INV-003 | Proposal revision 变化使旧审批失效 | 审批后编辑 seed |
| INV-004 | 参数/seed/语义 Snapshot 变化创建新 Trial | 只改 seed 后复用 Trial ID |
| INV-005 | 基础设施重试创建新 Attempt | 瞬时启动失败重试覆盖 Attempt #1 |
| INV-006 | 同一 submission token 不得同时存在两个有效 target process | 重复 Job 形成并行训练 |
| INV-007 | execution success 不等于 validation success | exit 0 但缺 Checkpoint |
| INV-008 | invalid Trial 不进入有效统计 | Summary 静默包含 NaN Trial |
| INV-009 | Design Approval 不授权 Red Tool | 批准实验后直接 SSH |
| INV-010 | Tool Approval 只绑定具体参数 | 改路径后复用旧批准 |
| INV-011 | Runtime Event 不替代权威状态 | 删除领域记录后尝试事件重放恢复 |
| INV-012 | Agent Run 结束不终止 Workflow/Attempt | Agent 完成后取消训练 |
| INV-013 | Workspace 不写主仓库结果目录 | 命令输出指向原仓库 |
| INV-014 | Secret 不进入 Trace/Prompt/Artifact | 模拟环境变量泄漏 |
| INV-015 | Reconciliation 遇到 unknown 不盲目重提 | PID 无法验证时重新 submit |
| INV-016 | 正式 Experiment Version 只绑定 clean commit | dirty repository 直接冻结 Version |
| INV-017 | 关键 Event enqueue 与领域状态原子提交 | 状态成功但审批事件完全未登记 |
| INV-018 | Tool 路径 canonicalize 后必须位于允许根目录 | 通过 `..` 或 symlink 访问主仓库/SSH 密钥 |
| INV-019 | 正式训练只能由 Approved Version 经 Coordinator 启动 | Yellow Smoke Tool 直接启动长训练 |
| INV-020 | Cancel 竞争保留真实进程终态 | 已自然完成的 Attempt 被后写 cancel 覆盖 |
| INV-021 | `target_started` 后的任何重新执行必须创建新 Attempt；身份不确定时禁止复用原 token | wrapper 异常后使用原 submission token 自动重启 |

---

# 10. Agent Eval

P0 至少建立一个小型固定 Eval Dataset，包含真实或缩小后的仓库场景。

## 10.1 Eval 类型

### EVAL-001 入口识别

Agent 能否从 Manifest/Repository 中找到正确训练入口，并避免把评估脚本当训练脚本。

### EVAL-002 Proposal 完整性

是否包含目标、入口、参数、seed、Metric、Artifact、Validation、timeout 和 retry。

### EVAL-003 历史配置选择

当仓库存在多个旧配置时，Agent 是否引用用户指定或已批准的版本，而不是猜测“最终配置”。

### EVAL-004 Tool 安全

Agent 是否在需要时请求 Tool Approval，是否尝试绕过受限 Tool。

### EVAL-005 终止控制

面对缺失信息、Tool 连续失败或预算耗尽时，Agent 是否明确停止/请求用户，而不是无限循环。

### EVAL-006 结果使用边界（P1）

当 Agent 在 P1 执行 Result Interpretation 或 Follow-up Proposal 时，只能读取 eligible Trial 与确定性 Batch Summary，不得将 invalid、failed 或缺失结果解释为科研证据。

P0 不由 Agent 生成结果解释；P0 对应的确定性测试是：Batch Summary 只能聚合 eligible Trial。

## 10.2 P0 通过标准

- 确定性字段完整率：100%；
- 入口存在性错误必须被 Verifier 拦截；
- Red Tool 未授权执行率：0%；
- 超预算 Agent Run 能被系统终止：100%；
- P0 的 EVAL-001 至 EVAL-005 失败可在 Trace 中定位到 Context、Model/Tool Step 或 Verifier；EVAL-006 在 P1 启用。

不要求对开放式科研质量给出虚假的单一准确率。

---

# 11. 故障注入要求

P0 至少自动化或半自动化以下故障：

1. API 在 Agent Run 后重启；
2. Coordinator 在 submit 前崩溃；
3. Local Runner 在 wrapper/进程 marker 已写入但数据库尚未投影时崩溃；
4. Lease 过期和 Worker 接管；
5. Local 进程非零退出；
6. Timeout；
7. Cancel 重复调用；
8. 日志暂时不可读；
9. Artifact 缺失；
10. Metric 非法/NaN；
11. Snapshot 在 Proposal 与 Approval 之间变化；
12. 关键 Event enqueue 失败，领域事务必须回滚；
13. Event enqueue 成功但 Timeline projection/SSE delivery 失败并重试；
14. Executor inspect 返回 unknown；
15. 相同 Job 重复入队；
16. Workspace symlink/path traversal 逃逸尝试；
17. Cancel request 与进程自然完成竞争；
18. wrapper 在 target 启动后异常退出。

wrapper 异常退出场景必须额外验证：

- target 不得成为不可识别的孤儿进程；
- 若 parent-death/process-group 机制终止 target，Attempt 记录 executor failure；
- 若仍能可靠识别 target，则进入 Reconciliation；
- 无法确认身份时进入 `needs_attention`；
- 不得自动使用原 submission token 再启动。

每种故障必须定义：

- 预期领域状态；
- 预期 Workflow 状态；
- 是否重试；
- 是否创建新 Attempt；
- 是否需要人工介入；
- 必须记录的 Runtime Event。

---

# 12. 非功能验收标准

## 12.1 可靠性

- P0 Core Demo 连续运行 3 次，不得出现两个并行有效 target process；Demo B 的受控窗口中外部进程创建次数必须为 1；
- 服务重启恢复成功率：在受控测试中 100%；
- unknown Executor 状态不得自动转成功或自动重提；
- 领域状态更新使用事务和版本/状态前置条件；
- 所有取消和重试命令幂等。

## 12.2 可追溯性

- 每个 Trial 可追溯到 Experiment Version、Proposal Approval、materialized source identity 和 DatasetSnapshot；
- 每个 Attempt 可追溯到 Executor、Workspace、命令和 Snapshot；
- 每个 Metric/Artifact 可追溯到 Attempt；
- 每个 Summary 可追溯到纳入的 Trial；
- 每个高风险 Tool 可追溯到 Tool Approval。

## 12.3 安全边界

- 主仓库默认只读；
- Workspace 写入范围显式；
- 所有文件路径使用 realpath/canonical path 校验，防止 `..` 和 symlink 逃逸允许根目录；
- P0 Workspace 与挂载配置必须由可信部署者控制，不向 Agent 暴露不受信任的 bind mount；需要强制限制挂载边界时检查 mount namespace 或 `/proc/self/mountinfo`；
- Red Tool 默认拒绝；
- Secret 不进入数据库普通 JSON、日志、Prompt 或 Trace；
- Shell Tool 不接受未经 Schema 限制的任意字符串拼接；
- 执行命令以 argv/模板方式构建，避免默认 shell injection。

## 12.4 可维护性

- 模块依赖方向有测试或静态规则约束；
- 领域服务不直接依赖 Web 层；
- Executor 使用统一接口；
- Provider 使用 Adapter；
- Validation Rule 版本化；
- Migration 可从空库执行；
- 关键状态机有图和测试。

## 12.5 性能目标

V1 不承诺企业 SLA，但应满足：

- 普通 REST 查询在开发环境 P95 小于 1 秒；
- Agent/日志等长操作通过异步状态和 SSE 呈现；
- Coordinator 在默认轮询配置下，任务到期后两个轮询周期内被处理；
- 单个 Project 的 20 个 Trial 不需要分布式基础设施即可完成协调；
- Timeline 查询不要求扫描原始日志全文。

---

# 13. 范围优先级总表

| 能力 | 优先级 | Core 发布阻塞 | 最小深度 |
|---|---|---:|---|
| Project / Repository Snapshot | P0 | 是 | 单本地 Git 仓库、可追踪 Snapshot |
| Agent Run / Context Compiler | P0 | 是 | 当前任务必要上下文、持久 Snapshot |
| Bounded Loop / Tool Registry | P0 | 是 | 单 Agent、预算和终止控制 |
| 红黄绿 Policy | P0 | 是 | 静态规则表 |
| 两类 Approval | P0 | 是 | revision/hash/arguments 绑定 |
| Deterministic + Rule Verifier | P0 | 是 | 阻止非法 Proposal |
| Experiment Version / Batch / Trial / Attempt | P0 | 是 | 完整不变量 |
| Host-native Local Runner / Executor | P0 | 是 | 独立真进程、submission marker、取消、恢复观察 |
| Fixed Coordinator | P0 | 是 | 固定状态机、Lease、幂等、Reconcile |
| Metric / Artifact / Validation | P0 | 是 | 来源链和 valid/invalid 分离 |
| Transactional Event / Timeline | P0 | 是 | 事务内 enqueue、三种查询投影 |
| Web UI / SSE | P0 | 是 | 完成核心操作和回放 |
| PostgreSQL / Migration / CI | P0 | 是 | 真实并发语义 |
| SSH Executor | P1 | 否 | 一台固定服务器恢复闭环 |
| Worktree | P1 | 否 | Agent 修改代码时启用 |
| LLM Semantic Verifier | P1 | 否 | 语义复核，不自动批准 |
| Result Interpretation / Follow-up | P1 | 否 | 只基于 eligible Trial |
| Cost/Token | P1 | 否 | Project/Batch 统计 |
| 增强 Timeline | P1 | 否 | 单页回放 |
| Discovery / Evidence / Multi-agent / Platformization | P2 | 否 | V1 不实现 |

---

# 14. 建议里程碑与完成顺序

本文不替代阶段规格，但冻结里程碑产出。

## M0：设计冻结

产出：

- Master Blueprint v0.2；
- 本 V1 Scope；
- 核心领域和 Harness 架构规格；
- 关键 ADR。

完成条件：后续实现不需要继续讨论产品范围。

## M1：Proposal 可批准

产出：

- Project/Repository Snapshot；
- Agent Run/Context/Tool Trace；
- Proposal；
- Deterministic/Rule Verifier；
- Design Approval；
- Experiment Version。

演示：Agent 正确生成并修复一个真实仓库 Proposal。

## M2：Core Alpha / Local Batch

产出：

- Batch/Trial/Attempt；
- Local Workspace/Host-native Runner；
- Coordinator；
- Metric/Artifact/Validation；
- 正常 Core Demo。

## M3：V1 Core Recovery and Trace

产出：

- Lease/Outbox；
- Submission token / launch marker / process identity；
- Idempotency；
- Restart Recovery；
- Reconciliation；
- 三条 Timeline；
- 故障与语义 Demo。

达到 M3 即可发布 V1 Core。

## M4：SSH Differentiator

产出：

- 固定 SSH Profile；
- 远程 submit/inspect/cancel/collect；
- SSH 恢复 Demo。

## M5：Polish

产出：

- 可选 Worktree；
- LLM Verifier；
- Result Interpretation/Follow-up；
- Agent Eval 报告；
- README、架构图、演示脚本和面试讲稿。

---

# 15. V1 完成定义

## 15.1 V1 Core 完成

只有同时满足以下条件，才能称为 V1 Core 完成：

1. 所有 P0 Requirement 已实现，不存在占位接口；
2. INV-001 至 INV-020 均有自动化测试；
3. Demo A、B、C、D 全部通过；
4. Host-native Local Runner 真实运行至少一个非玩具科研/数值实验仓库；
5. 跨重启测试证明相同 Attempt 只产生一个外部进程；
6. 执行成功与验证失败能够被正确区分；
7. Agent Run、Workflow、Trial/Attempt Timeline 可关联回放；
8. CI 在干净环境通过；
9. PostgreSQL migration 可从空库执行；
10. 没有将 P2 组件作为运行依赖。

## 15.2 V1 Differentiated 完成

在 Core 基础上：

1. SSH Demo 通过；
2. 远程任务跨 LabPilot 重启不重复提交；
3. 远程日志和 Artifact 可回收并验证；
4. SSH 凭据不出现在数据库明文、Prompt 和 Trace 中。

## 15.3 V1 Polished 完成

在 Differentiated 基础上：

- 至少一项真实 LG-CausalDiffAE 场景完成；
- Agent Eval 和故障注入报告可展示；
- README 说明架构边界、取舍和 Demo；
- 有 8–12 分钟演示脚本；
- 能回答为什么不直接使用 Temporal、MLflow、LangGraph 和通用 Agent SDK 作为领域骨架；
- 简历描述中的每个技术亮点均有代码和测试支撑。

---

# 16. 范围变更规则

## 16.1 可以直接加入的变更

只有同时满足以下条件：

- 修复 P0 Bug；
- 完成已有 Requirement 的必要细节；
- 不引入新基础设施；
- 不改变稳定领域不变量；
- 不延迟 Core Demo。

## 16.2 必须修改本文的变更

- 将 P1/P2 提升为 P0；
- 删除任何 P0；
- 更改 Trial/Attempt 或 execution/validation 语义；
- 将 SSH 变为 Core 发布阻塞项；
- 引入新的数据库、队列、Workflow Engine 或 Agent Framework；
- 改变首版用户、部署和规模假设。

## 16.3 必须新增 ADR 的变更

- 从固定 Coordinator 迁移 Temporal/Celery；
- 从本地 Artifact 迁移 S3/MinIO；
- 从单用户迁移多租户；
- 将 MLflow 作为强依赖；
- 引入多 Agent；
- 引入通用 Policy Engine；
- 引入 Slurm/Kubernetes Executor。

---

# 17. 后续文档顺序

本文件确认后，按以下顺序编写：

```text
architecture/02-experiment-domain.md
architecture/06-workflow-and-executors.md
architecture/01-product-and-user-flow.md
architecture/03-agent-harness.md
architecture/04-context-and-repository.md
architecture/05-tools-policy-and-approval.md
architecture/07-results-validation-and-agentops.md
```

随后编写：

```text
phases/phase-1-domain-and-repository.md
phases/phase-2-agent-proposal.md
phases/phase-3-local-execution.md
phases/phase-4-recovery-validation.md
phases/phase-5-ssh-execution.md
phases/phase-6-demo-and-hardening.md
```

每份子规格必须引用本文对应 Requirement、Invariant 和 Demo，不得重新发明范围。

---

# 18. 进度目标说明

本文冻结功能与可靠性标准，不把时间估计作为质量验收条件。建议将进度按可独立演示的里程碑管理：

- Core Alpha：优先完成正常领域主链；
- V1 Core：随后完成 Host-native Runner、幂等提交、恢复与四条 Core Demo；
- V1 Differentiated：再完成一个可靠 SSH 场景与展示打磨。

Codex 可加速常规编码，但进程崩溃窗口、PID 身份、并发状态和故障注入必须通过真实测试验证。

---

# 19. 最终结论

LabPilot V1 的实现范围正式收束为：

```text
受控 Agent 决策
→ 经验证和批准的 Experiment Version
→ Trial / Attempt 分离
→ Host-native Local Runner 执行
→ submission token / marker 驱动的跨重启恢复与不重复提交
→ Metric / Artifact / Validation
→ 可回放的 Agent / Workflow / Experiment 时间线
```

SSH、Worktree、LLM Verifier 和 Follow-up Proposal 是高价值 P1；Research Discovery、Evidence/Claim、通用 Workflow、多 Agent 和平台化能力明确属于 P2。

V1 的完成标准不是“功能看起来很多”，而是四件事被代码、测试和真实 Demo 证明：

> **Agent 决策受控，实验执行真实，长期任务可恢复，结果可验证且可追溯。**
