# LabPilot 最终文档整合包 v1.1.1

本包是可以直接作为 WP0 实施输入的自包含文档包。它保留当前权威文档，以及当前实施计划通过 SHA-256 明确绑定的历史冻结依赖；不包含 Draft、Superseded 实施计划、diff、Changelog 或旧压缩包。

## 1. 当前权威文档

### 总体蓝图与范围

1. `docs/labpilot/00-master-blueprint-v0.2.md`
2. `docs/labpilot/01-v1-scope-and-acceptance-v0.2-frozen.md`

### 核心架构规格

3. `docs/labpilot/architecture/02-experiment-domain-v0.2-frozen.md`
4. `docs/labpilot/architecture/03-agent-harness-v0.2-frozen.md`
5. `docs/labpilot/architecture/04-context-and-repository-v0.2-frozen.md`
6. `docs/labpilot/architecture/05-tools-policy-and-workspace-v0.2-frozen.md`
7. `docs/labpilot/architecture/06-workflow-and-executors-v0.2-frozen.md`
8. `docs/labpilot/architecture/07-results-validation-and-agentops-v0.2-frozen.md`

### 当前实施治理

9. `docs/labpilot/implementation/00-architecture-index-and-dependency-map-v0.2-frozen.md`
10. `docs/labpilot/implementation/01-master-implementation-roadmap-v0.2.3-frozen.md`
11. `docs/superpowers/plans/2026-07-24-labpilot-phase-0-foundation-v0.3.1-ready-for-execution.md`

## 2. 必须保留的历史冻结依赖

`docs/labpilot/implementation/01-master-implementation-roadmap-v0.2.1-frozen.md` 不是当前 Roadmap，但 WP0 v0.3.1 的不可变依赖清单明确绑定了该文件及其 SHA-256。它必须留在实施仓库中，不能因为已被 v0.2.3 取代而删除。

实施时：

- 当前总体路线以 Roadmap v0.2.3 为准；
- WP0 Task 0 的依赖校验仍按其冻结清单验证 Roadmap v0.2.1；
- 这两者不冲突：前者约束后续 WP2/WP6，后者保证 WP0 计划的历史输入不被改写。

## 3. Master Blueprint 状态说明

`00-master-blueprint-v0.2.md` 头部仍保留历史文字 `状态：待用户评审`。本包不修改该文件，因为它的现有字节已被 04–07、WP0 等下游冻结文档通过 SHA-256 绑定。

当前治理含义是：

- Blueprint 内容已作为下游冻结架构的权威上游输入；
- 头部状态文字属于未同步的历史元数据，不代表架构仍未确认；
- 除非进行一次完整版本升级和级联哈希更新，否则不得直接修改该文件字节。

## 4. 实施前置文件

WP0 开始前必须存在：

- `docs/labpilot/implementation/phase-0-v0.3.1.dependencies.sha256`
- `docs/labpilot/implementation/phase-0-v0.3.1-dependency-verification.txt`
- `scripts/verify_architecture_dependencies.sh`

本包已经包含这些文件。

## 5. 实施顺序

```text
WP0 Foundation
→ WP1 Experiment Domain + Context
→ WP2 Native Agent Harness + Tool/Policy
→ WP3 Workflow + Runner
→ WP4 Result Plane + AgentOps
→ WP5 Web UI + Core Demos
→ WP6 P1 Differentiation
```

WP2 的核心使用 LabPilot 自有 Native Bounded Loop。LangGraph、LangChain Agent、LangSmith Runtime、CrewAI 等第三方 Agent Framework 不得成为核心 Agent Runtime 的权威状态层。

## 6. 校验命令

### 跨解压器通用命令

ZIP 标准支持 Unix 权限元数据，但部分解压器（例如 Python `zipfile.extractall()`）不会恢复执行位。为保证在所有解压器下都可校验，统一使用：

```bash
bash scripts/verify_architecture_dependencies.sh FINAL-DOCUMENTS.sha256
```

### Unix 直接执行

本发布同时提供保留 POSIX 权限的 `tar.gz`。使用标准 `tar` 解压后，也可以直接执行：

```bash
./scripts/verify_architecture_dependencies.sh FINAL-DOCUMENTS.sha256
```

使用标准 Info-ZIP `unzip` 解压本 ZIP 时，脚本同样恢复为 `0755`；若所用解压器忽略权限，可执行一次：

```bash
chmod 755 scripts/verify_architecture_dependencies.sh
```

### 校验 WP0 不可变依赖

```bash
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.1.dependencies.sha256
```

预期 WP0 输出 11 条 `OK`。

### 校验架构文档的局部依赖

```bash
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/04-context-and-repository.dependencies.sha256
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/05-tools-policy-and-workspace.dependencies.sha256
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/06-workflow-and-executors.dependencies.sha256
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/architecture/07-results-validation-and-agentops.dependencies.sha256
```

## 7. 当前交付边界

- `00–07` 核心架构已完成；
- Architecture Index 和当前 Roadmap 已完成；
- WP0 详细计划已完成，可进入真实 Git 仓库实施；
- WP1–WP6 的代码级详细计划将在前置工作包落地后逐阶段生成；
- 本包不包含应用代码，也不表示 WP0 已经实施完成。
