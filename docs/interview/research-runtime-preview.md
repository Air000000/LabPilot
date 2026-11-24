# Research Agent Runtime 总体架构

[公开验收范围](../superpowers/evidence/research-runtime-preview.md) · [中文 README](../../README.md) · [English](../../README.en.md)

```mermaid
flowchart TD
    Goal["研究目标"] --> Context
    Repo["绑定的 RepositorySnapshot"] --> Context
    subgraph Core["科研 Agent · 只读工具与结构化决策"]
        Context["ContextCompiler"] --> Run["AgentRun / AgentStep<br/>预算 · 出境策略 · 调用前预留"]
        Run -->|读取快照或冻结历史| Context
        Run -->|stop| Stop["completed · 当前 Run 终止"]
        Run -->|request_human_input| Await["awaiting_human · 等待人工输入"]
        Await -->|提交人工回复| Ready["记录人工步骤 · ready"]
        Ready -->|显式 advance| Context
        Run -->|显式源码草稿模式| Draft["未经验证的 SourcePatchDraft · 持久化"]
    end
    subgraph Coding["独立编码 worker · 隔离 Docker 工作区"]
        Task["冻结基准 · 目标 · 候选范围 · 预算"] --> Worker["mini-swe-agent 修改工作区并运行开发测试"]
        Worker --> Check["导出 · 差异检查 · 重放 · 可信冻结测试"]
        Check --> Receipt["候选及本地操作者验收回执"]
    end
    Repo -->|冻结编码基准| Task
    Draft -.->|人单独审阅并提交| Static["静态候选检查"]
    Receipt --> Import["Core 导入 · 内容与回执一致性检查"]
    Static --> Review["人工审阅 · 提交 · 捕获 Snapshot"]
    Import --> Review
    Review --> Adoption["显式人工 SourceAdoption"]
    Adoption --> NextSource["已采纳 Snapshot"]
    NextSource -->|单独创建人工提案| Proposal
    NextSource -.->|显式选择跨源码目标| Follow
    Run -->|普通提案| Proposal["ProposalRevision"]
    Proposal --> Verify["确定性 Verification"]
    Verify --> Approval["人工批准"]
    Approval --> Version["ExperimentVersion"]
    Version -->|显式创建 Batch| Batch["Batch · 按设计展开 Trial"]
    Batch -->|独立 StartBatch 命令| Advance["Coordinator 推进获准执行"]
    Advance --> Attempt["准备 Attempt · 物化确切源码 · 持久化启动意图"]
    Attempt --> Execute["Runner / systemd 物理执行与对账"]
    Execute --> Artifacts["Artifact / MetricObservation"]
    Artifacts --> Validation["ValidationReport → TrialResultSelection"]
    Validation --> Summary["BatchSummaryRevision"]
    Summary -->|创建后续 Run 并绑定精确摘要| Follow["Follow-up AgentRun<br/>冻结历史 · Trial 事实"]
    Follow --> Context
```

[Agent Runtime](../../src/labpilot/agent_runtime/service.py) 将 `stop` 记录为 `completed`。仅 `awaiting_human` 接受匹配版本的人工输入，随后返回 `ready`，由 [advance API](../../src/labpilot/api/routers/agent_runs.py) 继续推进。

普通科研提案使用绑定的源码快照，无需 SourceAdoption。独立 worker 的工作区可写，`editable_paths` 在输出成为候选时约束最终变更范围。独立容器执行冻结的动态检查，Core 核对可信本地操作者提供的回执与候选内容。[独立编码指南](../../tools/coding_worker/README.md#信任边界)说明导入、验收和人工采纳的各自职责。

人工采纳只记录确切候选和新 Snapshot 的关系。采纳后的实验仍须单独提案、校验和人工批准。批准冻结实验版本，[创建 Batch](../../src/labpilot/api/routers/experiments.py) 展开 Trial，[StartBatch](../../src/labpilot/api/routers/execution.py) 单独授权 [Coordinator](../../src/labpilot/coordinator/service.py) 推进 Attempt。Runner 管理物理执行，Coordinator 用稳定身份对账。PostgreSQL 保存权威实验和运行状态。显式绑定已采纳源码的 Follow-up 机制已实现，两次指定真实提案均未通过验收。见[跨源码证据摘要](../superpowers/evidence/research-runtime-preview.md#source-adoption-and-cross-source-follow-up)。

下列图片对应早期 Core Demo 架构，可用于回顾当时的验收基线 `c92bf45`（2026-09-19）。其状态名称和覆盖范围按[历史验收](../superpowers/evidence/2026-09-19-labpilot-competition-core-demo-closeout.md)理解。当前预览的状态与权限以本页、源码及公开证据索引为准。

- [历史总体架构](architecture-overview.png)
- [历史 Agent Harness](agent-harness.png)
- [历史执行与恢复](execution-recovery.png)
- [历史结果迭代](result-loop.png)
