# LabPilot

> **一个 AI 辅助实验运行时：把研究目标、实验版本、物理执行与结果组织成可追溯的实验谱系。**

[![CI](https://github.com/Air000000/LabPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Air000000/LabPilot/actions/workflows/ci.yml)

LabPilot 从一个实际科研问题出发：随着实验不断积累，研究者往往会面对大量提案版本、参数/随机种子分支、检查点、日志和指标，而“**为什么提出这个实验、最终批准的是哪个版本、实际执行了什么、最终信任的是哪个结果**”之间的关系会越来越难以还原。

LabPilot 的目标，是把这条研究轨迹显式化并持久保存。系统会记录 ProposalRevision、审批、不可变的 ExperimentVersion、执行身份、物理 Attempt、Artifact、Metric、Validation 以及正式的 Result Selection，使一次实验可以从最初的研究目标一直追溯到最终结果。

Agent 运行在这条实验谱系之上：它可以读取受限的仓库上下文，结合之前的工具结果和人工反馈；当一个 Batch 结束后，还可以基于精确持久化的结果摘要，辅助提出下一轮实验方案。Runtime 与恢复机制的作用，是让这种 AI 辅助过程保持安全、可复现，而不是让模型成为不受约束的执行事实来源。

~~~text
研究目标
  -> ProposalRevision
  -> 确定性 Verification
  -> 人工审批
  -> 不可变 ExperimentVersion
  -> Batch
  -> Trial（参数 / 随机种子分支）
  -> Attempt（一次物理执行意图）
  -> 真实训练
  -> Artifact / Metric / Validation
  -> Result Selection
  -> Batch Summary
  -> 精确 ResultContext
  -> Follow-up AgentRun
  -> Stop | 同一 Experiment 中的下一条 ProposalRevision
~~~

## 为什么需要 LabPilot

在迭代式科研项目里，困难往往不是“再启动一次训练命令”，而是事后仍然能够回答这些问题：

- 当初为什么要做这个实验？
- 它来自哪一个 ProposalRevision 和哪一个仓库状态？
- 实际批准的是哪份代码、数据、环境预期、参数配置和验证规则？
- 当前 Trial 对应的是哪个参数/随机种子分支？
- 后续 Attempt 是新的科学实验，还是一次基础设施重试？
- 哪个物理进程真正产出了某个 checkpoint 或 metric？
- 哪个结果通过了既定验证协议，并最终成为正式结果？
- 哪些证据应该进入下一轮实验修订？

如果缺少显式的实验谱系，这些信息通常会散落在 shell 历史、目录、checkpoint、日志、表格以及人的记忆里。LabPilot V1 将 **提案目标 -> 已批准版本 -> 逻辑 Trial -> 物理 Attempt -> 已验证结果** 建模为持久化状态。

这解决的是产品层面的核心问题。下面的可靠性机制解决的是第二层问题：一旦允许 AI Agent 参与这条科研工作流，概率性的模型决策就不能反过来降低实验记录本身的可信度。

> **当前证据边界：** LabPilot 目前不宣称已经量化降低研究者时间成本，也不宣称已经验证生产规模下的效率提升。V1 当前更窄的主张是：建立一条可追溯、可恢复、可验证的实验谱系，为后续分析和迭代提供可靠基础。

## 架构

~~~mermaid
flowchart LR
    Goal[研究目标] --> Ctx[Context Compiler]
    Repo[Repository Snapshot] --> Ctx
    Tool[只读 Repository Tools] --> Ctx
    Human[人工反馈] --> Ctx

    Ctx --> Agent[Bounded Agent Runtime]
    Agent --> Decision[结构化决策]
    Decision --> ToolCall[Tool Call]
    Decision --> HumanInput[Request Human Input]
    Decision --> Proposal[ProposalRevision]
    Decision --> Stop[Stop]

    Proposal --> Verify[确定性 Verification]
    Verify --> Approval[人工审批]
    Approval --> Version[不可变 ExperimentVersion]

    Version --> Batch[Batch]
    Batch --> Trial[Trial]
    Trial --> Attempt[Attempt]
    Attempt --> Coordinator[Coordinator]
    Coordinator --> Runner[Supervised Runner]
    Runner --> Training[真实训练进程]

    Attempt --> Artifact[Artifact]
    Artifact --> Metric[Metric]
    Metric --> Validation[确定性 Validation]
    Validation --> Selection[Result Selection]
    Selection --> Summary[Batch Summary]
    Summary --> ResultCtx[精确 ResultContext]
    ResultCtx --> FollowAgent[Follow-up AgentRun]
    FollowAgent --> FollowDecision{Stop / Human Input / Proposal}
    FollowDecision --> FollowStop[Stop]
    FollowDecision --> FollowHuman[Request Human Input]
    FollowDecision --> Proposal
~~~

## 核心能力

### 1. 实验谱系与实验治理

LabPilot 将实验历史视为系统的一等公民，而不是一堆输出目录。

ProposalRevision 会持久化实验 `goal`，并同时绑定仓库快照以及执行/验证语义。随后，提案依次经过：

~~~text
ProposalRevision
  -> Verification
  -> Human Approval
  -> Immutable ExperimentVersion
~~~

批准后的 `ExperimentVersion` 会冻结后续解释和复现实验所需要的语义，包括仓库身份、数据集引用、环境预期、参数配置、声明的 Metric/Artifact，以及 Validation Protocol。

参数矩阵与随机种子会被展开为稳定的 **Batch / Trial / Attempt** 身份：

- **Batch**：一次基于已批准 ExperimentVersion 的执行展开；
- **Trial**：一个逻辑上的参数/随机种子分支；
- **Attempt**：该 Trial 的一次物理执行尝试。

这种拆分让研究意图、已批准设计、逻辑实验分支、物理执行以及后续结果保持明确关联，而不会被压缩成一个含义模糊的 “run” 记录。

### 2. Agent 辅助的实验迭代

Agent 的目标是帮助研究者理解实验、修订实验，而不是取代权威的实验记录。

LabPilot 使用 **Bounded Agent Loop（受限 Agent 循环）**。Runtime 会把仓库状态、之前的工具结果和人工反馈编译为受控上下文，并通过以下机制约束每一次模型步骤：

- 结构化的 Decision Schema；
- 带版本的 Tool Contract 与 JSON Schema 校验；
- 有边界的仓库路径和只读 Repository Tools；
- 单步 / 单决策语义；
- Step、Model Call、Token 和 Wall-clock Budget；
- **persist-before-execute**：先持久化通过校验的 Decision，再执行工具调用或物化 Proposal。

当前 Agent 的决策面刻意保持很小：

~~~text
Tool Call | Request Human Input | Proposal | Stop
~~~

这样，Agent 可以读取证据并辅助形成下一份 Proposal，同时模型权限边界保持显式，每个状态迁移也都可以审计。

当 Batch Summary 已经持久化后，LabPilot 可以创建一个绑定到该精确 `BatchSummaryRevision` 的 **Follow-up AgentRun**。模型获得的是一个有边界、确定性的 `ResultContext`，其中包含源 Experiment/Version 身份、Trial 条件、被选中的有效证据、排除项、警告和聚合结果。

Follow-up Proposal 会成为**同一个 Experiment** 中的下一条 `ProposalRevision`，但它仍然必须经过已有的确定性 Verification 和显式人工审批，之后才能产生新的 `ExperimentVersion`。Agent 不会自动批准 Proposal，也不会自动创建或启动下一批 Batch。

### 3. 可靠的长时间物理执行

当已批准实验真正进入训练阶段后，即使发生崩溃或外部结果不确定，LabPilot 也必须保持实验谱系不被破坏。

长时间训练本质上会产生一个控制面问题：Coordinator 崩溃或网络故障后，系统可能无法立即知道物理启动是否已经发生。如果此时直接重试，就可能启动第二个训练进程，从而破坏数据库 Attempt 与真实物理执行之间的一一对应关系。

因此，LabPilot 使用三个核心机制：

1. **Persistent Launch Intent（持久化启动意图）** —— 在跨越外部 Runner 边界之前，先保存冻结后的 Launch Spec 与 Launch Intent。
2. **Stable Execution Identity（稳定执行身份）** —— Runner 接收稳定的物理执行身份，而不是在每条恢复路径上重新生成一个身份。
3. **Coordinator Reconciliation（协调器对账）** —— 启动扫描与周期扫描读取真实物理状态，并与持久化控制面状态进行对账，而不是盲目重新启动。

Retry 与 Cancellation 都是显式状态机操作，而不是普通异常重试。

在故障注入验收中，Coordinator 重启后能够重新对账到原来的训练 Attempt，**没有产生第二次物理启动**。执行路径也覆盖了已经运行任务与尚未启动任务的取消场景。

> LabPilot **不宣称**提供全局意义上的 “exactly-once” 执行保证。当前更准确的约束是：副作用发生前先持久化意图，使用稳定身份，并在结果不确定时通过对账恢复，而不是盲目重放。

### 4. 结果验证与实验闭环

只有 checkpoint 和 metric 文件，还不能构成可用的实验历史。LabPilot 通过一条确定性结果链关闭实验谱系：

~~~text
Artifact
  -> Metric
  -> Validation
  -> Result Selection
  -> Batch Summary
~~~

Result Plane 会记录 Artifact 与 Metric Observation，执行确定性的验证规则，例如 Artifact 是否存在、Metric 是否有限，以及阈值规则；随后为 Trial 选择正式结果，并生成带版本的 Batch Summary。

这里刻意区分两类能力：

- **概率性的 Agent 推理**：负责读取上下文、解释结果并提出下一轮实验；
- **确定性的 Result Validation**：负责判断科学输出是否满足已声明的验证协议。

Runtime Event 与基于 SSE 的读取路径提供执行状态和结果状态的可追踪、可重放视图。因此，最终结果可以一直追溯回生成它的 Trial 以及对应的已批准 `ExperimentVersion`，而不是成为一个孤立指标。

## 关键设计决策

| 设计决策 | 原因 |
|---|---|
| 持久化 ProposalRevision，并使用不可变 ExperimentVersion | 保留实验为什么变化，以及真正获准执行的是哪个精确版本。 |
| 拆分 Batch / Trial / Attempt 身份 | 区分参数/随机种子分支、基础设施重试与真实物理执行。 |
| 使用 Bounded Agent Runtime，而不是不受限自治 | Agent 可以帮助修订实验，但不能成为执行事实或结果有效性的最终来源。 |
| 单步 / 单决策模型循环 | 让每一次模型状态迁移都可观察、可预算、可恢复。 |
| Decision 先持久化、后执行 | 避免外部副作用已经发生，但权威决策记录却丢失。 |
| 创建不可变 ExperimentVersion 前必须人工审批 | 将模型建议与真正获准执行的实验明确分开。 |
| Runner 启动前持久化 Launch Intent | 在崩溃或外部结果不确定时，为恢复逻辑保留可对账的持久事实。 |
| Stable Execution Identity | 恢复时仍指向同一次物理执行，避免误创建新的执行实例。 |
| Deterministic Result Validation | 正式科学结果应当可复现、可审计，而不是依赖自由文本形式的 LLM 判断。 |
| 精确 ResultContext 绑定 | Follow-up AgentRun 固定读取某一个精确持久化的 BatchSummaryRevision，而不是可变的 latest/raw result state。 |

## 运行时组件

- **FastAPI Control Plane** —— 提供 Experiment、Proposal、Execution、Query 和 Event API。
- **PostgreSQL** —— 保存权威实验谱系、身份、Command Receipt、Runtime Event 和 Result 记录。
- **Bounded Agent Runtime** —— 在显式 Tool、Budget、Provenance 与 Approval 约束下，执行仓库感知和结果感知的 Proposal 迭代。
- **Coordinator** —— 负责 Preparation、Admission、Execution Control、Recovery、Cancellation 和 Reconciliation。
- **Runner** —— 面向真实训练 workload 的隔离物理进程边界。
- **Repository Observation Layer** —— 为 Agent Harness 提供有边界、只读的仓库访问。
- **Result Plane** —— 负责 Artifact、Metric、确定性 Validation、Result Selection 和 Batch Summary。
- **Runtime Events / SSE** —— 提供状态追踪与重放接口。

## 快速开始

### 环境要求

- Python **3.12**
- Docker / Docker Compose
- PostgreSQL **16**（本地开发由 Compose 提供）
- 使用 `uv` 进行仓库开发与依赖管理
- 如需跑完整真实训练链路，需要 Linux/systemd Runner Host

### 启动本地 API Control Plane

~~~bash
docker compose up --build postgres migrate api
~~~

随后检查就绪状态：

~~~bash
curl http://127.0.0.1:8000/health/ready
~~~

完整训练链路还需要常驻 Coordinator 与专用 Runner Host。相关入口：

- [`compose.yaml`](compose.yaml)
- [`scripts/install_labpilot_runner_host.sh`](scripts/install_labpilot_runner_host.sh)
- [`deploy/systemd/labpilot-runner.service`](deploy/systemd/labpilot-runner.service)

## 开发与验证

本地等价 CI Gate：

~~~bash
./scripts/check_ci.sh
~~~

它会校验锁定依赖与历史依赖清单，然后执行：

- Ruff；
- strict mypy；
- unit / architecture tests；
- PostgreSQL integration tests；
- dependency-guard shell tests。

GitHub Actions 执行同一套 Gate，并额外验证生产 Docker Image 与 Runtime 假设。

## 范围与边界

LabPilot V1 优先保证 **可追溯实验谱系、正确性、恢复能力、可复现性、已验收恢复路径中的无重复物理启动，以及确定性结果闭环**，而不是追求功能数量。

当前核心系统并不试图成为：

- 通用 Multi-Agent Framework；
- 不受限制的自主科研 Agent；
- 通用 Workflow Engine；
- 完整的实验追踪 UI 或科学 Notebook 替代品；
- 绕过 Verification 与 Human Approval、自动无限运行下一轮实验的自治闭环；
- 生产规模横向调度能力或全局 exactly-once 语义的证明。

这些属于独立的产品与扩展问题。V1 首先建立可信的实验谱系，以及让 AI Agent 参与这条谱系时仍然能够保持可靠性所需要的系统不变量。

## 文档

详细架构、已验收不变量、实施计划、ADR 与 Acceptance Evidence 均保存在 [`docs/`](docs/) 下。

建议从以下入口开始：

- [`DOCUMENT-STATUS.md`](DOCUMENT-STATUS.md) —— 文档权威关系与状态索引；
- [`docs/labpilot/`](docs/labpilot/) —— 架构与实现文档；
- [`docs/superpowers/specs/`](docs/superpowers/specs/) —— 聚焦设计规格与 ADR；
- [`docs/superpowers/evidence/`](docs/superpowers/evidence/) —— 验收与故障注入证据。

顶层 README 有意保持为产品与架构总览；历史冻结计划和完整性清单仍保留在文档树中，以便追溯。

## 技术栈

**Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy · Pydantic · Alembic · Docker Compose · systemd · Pytest · Ruff · mypy · SSE**
