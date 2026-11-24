# LabPilot 文档状态与阅读导航

从 [中文 README](README.md) 或 [English README](README.en.md) 进入。判断当前能力时，结合公开基线上的实现、测试和对应场景的验收结果。设计与路线图规定其适用范围内的约束和实施顺序，功能状态以实际实现和验收为准。

## 当前公开项目

- [总体架构](docs/interview/research-runtime-preview.md) 展示决策、源码、执行和结果的现行权限与控制流。
- [公开能力与验收证据](docs/superpowers/evidence/research-runtime-preview.md) 区分已验收场景、受限能力和未完成事项。[正式公开基线及 CI](docs/superpowers/evidence/research-runtime-preview.md#public-release-acceptance) 对应 [PR #11](https://github.com/Air000000/LabPilot/pull/11) 和 2026-11-24 合入的工程预览。
- 核心闭环采用单 Coordinator 和单机 Runner。Slice 2 生产加固与 Slice 3 完整 native 验收仍未关闭。

## 领域与实现契约

| 阅读入口 | 适用范围 |
| --- | --- |
| [CONTEXT](CONTEXT.md) | 领域词汇，涵盖迭代、源码候选、人工采纳及不同证据的含义。 |
| [V1 Scope v0.3](docs/labpilot/01-v1-scope-and-acceptance-v0.3.md) · [Architecture 10 rebaseline](docs/labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md) | V1 范围、权威关系与架构约束。以各文件所列适用与继承范围理解较早的冻结架构。 |
| [Roadmap v0.3](docs/labpilot/implementation/01-master-implementation-roadmap-v0.3.md) · [Slice 2 → Slice 3 sequencing amendment](docs/labpilot/implementation/01a-slice2-to-slice3-sequencing-amendment-v0.3.1.md) | 实施顺序与已批准的顺序修订。进入后续阶段并不等于补齐前一阶段的全部验收。 |
| [同 Experiment Follow-up ADR](docs/adr/0001-keep-follow-up-proposals-in-same-experiment.md) · [Runner supervision ADR](docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md) · [终态回执修订](docs/superpowers/specs/2026-08-24-labpilot-v1-slice2-terminal-receipt-adr-amendment.md) · [专项设计](docs/superpowers/specs) | 在各自版本、场景和后续修订约束下阅读。设计日期、实施日期和验收日期各有其含义。 |
| [源码](src/labpilot) · [自动化测试](tests) · [CI 门禁](scripts/check_ci.sh) | 核对当前实现和可执行合同。测试结论按运行环境和覆盖范围解释。 |

## 操作指南

- [控制面启动](README.md#启动与演示) 使用独立 Compose 项目验证迁移与 API readiness。
- 完整执行环境另需 Linux/systemd Runner、身份和策略。见 [宿主安装入口](scripts/install_labpilot_runner_host.sh)、[Runner 单元](deploy/systemd/labpilot-runner.service) 和[配置示例](.env.example)。
- [预配置 Operator 演示](README.md#启动与演示) 使用固定项目与快照，适用于单操作者和本地可信访问。
- [独立编码 worker](tools/coding_worker/README.md) 覆盖基准冻结、任务运行、候选验收、导入与人工采纳。
- [固定 NOTEARS native 案例](docs/superpowers/evidence/research-runtime-preview.md#notears-native) 给出已验收环境和复现所缺的科研材料。

## 历史工程档案

- [v0.2 Research Agent OS 蓝图](docs/labpilot/00-master-blueprint-v0.2.md)、[冻结架构](docs/labpilot/architecture) 和[早期实施计划](docs/labpilot/implementation) 保留原时点的背景、合同与门禁。
- [Phase 0 验收](docs/labpilot/implementation/phase-0-acceptance.md)、[WP1-A 计划](docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md)、[Slice 1 计划](docs/superpowers/plans/2026-08-10-labpilot-v1-slice1-proposal-loop.md)、[Slice 2 MVP](docs/superpowers/evidence/2026-08-31-labpilot-slice2-mvp-smoke.md)、[Slice 3 收口](docs/labpilot/implementation/05-slice3-result-plane-closeout.md) 和 [P1 迭代收口](docs/superpowers/evidence/2026-09-22-labpilot-p1-result-aware-iteration-closeout.md) 按各记录的验收时点和运行环境引用。
- [比赛档案](docs/competition/README.md)、[历史验收](docs/superpowers/evidence) 和 [Runner PoC](tests/poc/s2_runner/README.md) 保留当时的实证、限制和失败记录。
- [Handoff 与历史 prompt](docs/superpowers/handoffs) 记录当时的交接背景和后续窗口建议。它们不构成当前开发或运行授权。

现行 CI 使用 [WP0 清单](docs/labpilot/implementation/phase-0-v0.3.4.dependencies.sha256) 与 [WP1-A 清单](docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256)。各版本的冻结设计与依赖清单均保留原文件。

根目录 [FINAL-DOCUMENTS.sha256](FINAL-DOCUMENTS.sha256) 保存早期交付的哈希。其 README 和文档状态条目对应当时的版本，并非当前导航或 README 的校验基准。[WP1 设计记录](docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md)也记录了该历史清单的版本差异。
