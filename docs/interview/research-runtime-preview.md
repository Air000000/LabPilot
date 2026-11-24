# Research Agent Runtime 总体架构

[公开验收范围](../superpowers/evidence/research-runtime-preview.md) · [中文 README](../../README.md) · [English](../../README.en.md)

```mermaid
flowchart TD
    Goal["研究目标 · Research goal"] --> Context
    Repo["绑定的 RepositorySnapshot"] --> Context
    subgraph Core["科研 Agent · 只读工具与结构化决策"]
        Context["ContextCompiler"] --> Run["AgentRun / AgentStep<br/>预算 · 出境策略 · 先持久化"]
        Run -->|读取快照或冻结历史| Context
        Run --> Human["停止或请求人工输入"]
        Human -->|人工答复| Context
        Run --> Draft["显式模式 · 未检查补丁草稿"]
    end
    subgraph Coding["独立编码 Executor · Docker 工作区"]
        Task["冻结基准 · 范围 · 目标 · 预算"] --> Worker["mini-swe-agent 编辑和开发测试"]
        Worker --> Check["新容器重放 · 冻结测试"]
        Check --> Package["通过验收的候选包"]
    end
    Repo -->|冻结编码基准| Task
    Draft -.->|操作者可审阅后另行交接| Static["人工静态候选检查"]
    Static --> Review["人工审阅 · 提交 · 捕获"]
    Package --> Review
    Review --> Adoption["SourceAdoption"]
    Adoption --> NextSource["已采纳 Snapshot"]
    NextSource -->|人工提案| Proposal
    NextSource -.->|显式绑定跨源码 Follow-up| Context
    Run -->|普通提案直接进入校验链| Proposal["ProposalRevision"]
    Proposal --> Verify["确定性 Verification"]
    Verify --> Approval["人工批准"]
    Approval --> Version["ExperimentVersion"]
    Version --> Batch["Batch → Trial → Attempt"]
    Batch --> Execute["源码物化 · Coordinator → Runner / systemd"]
    Execute --> Artifacts["Artifact / MetricObservation"]
    Artifacts --> Validation["ValidationReport → TrialResultSelection"]
    Validation --> Summary["BatchSummaryRevision"]
    Summary --> Follow["Follow-up AgentRun<br/>精确摘要 · 冻结历史 · Trial 事实"]
    Follow --> Context
```

科研 Agent 的补丁草稿保留在 AgentStep。独立编码工作区由操作者另行启动。普通科研提案可直接使用已绑定的源码快照，无需 SourceAdoption。源码变更路径经人工采纳记录候选和新 Snapshot 的关系。后续实验仍须单独校验、人工审批和启动，审批绑定确切提案、校验、源码、数据和执行语义。物理进程由 Runner 管理，Coordinator 使用稳定身份对账。PostgreSQL 保存权威实验和运行状态。

下列图片对应早期 Core Demo 架构，可用于回顾当时的验收基线 `c92bf45`（2026-09-19）。其状态名称和覆盖范围按[历史验收](../superpowers/evidence/2026-09-19-labpilot-competition-core-demo-closeout.md)理解。当前预览的状态与权限以本页、源码及公开证据索引为准。

- [历史总体架构](architecture-overview.png)
- [历史 Agent Harness](agent-harness.png)
- [历史执行与恢复](execution-recovery.png)
- [历史结果迭代](result-loop.png)
