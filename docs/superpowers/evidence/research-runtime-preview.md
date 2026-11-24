# Research Agent Runtime 公开能力与验收证据

[中文 README](../../../README.md) · [English](../../../README.en.md) · [当前架构](../../interview/research-runtime-preview.md)

本页是开发者整理的历史验收摘要，也是两个 README 的共同依据。历史门禁、真实模型运行和 native 实验各自绑定运行版本及环境。读者可以从本版本复核源码、测试和标明公开的历史记录。

| 证据来源 | 对外可访问性 |
| --- | --- |
| LabPilot 公开历史 | [Core Demo c92bf45](https://github.com/Air000000/LabPilot/commit/c92bf45b63053b1ae00209660ed351cf309f91c8)、[两轮闭环 e8caaf6](https://github.com/Air000000/LabPilot/commit/e8caaf668fed2b5e5878393fa0c47c001ba218e9)、[原公开基线 0291997](https://github.com/Air000000/LabPilot/commit/0291997933e08b0d5de9f9cde809097c841ecce1) 均在公开祖先链中。 |
| LabPilot 私有开发历史 | `405a4c1`、`ca3e5a7`、`88d2ff9`、`3238c49`、`b432db0`、`466750b`、`a2965bd`、`9270642`、`0ddf78f`、`108d612` 为内部工程或验收标识，无公开提交链接。 |
| 隔离 NOTEARS 研究副本 | `f113119`、`dd55cb3`、`0444d2d` 属于独立科研 Git 仓库。其上游许可可核对，隔离版本未通过此版本发布。 |
| 当前快照与开发者摘要 | 相对链接指向当前候选的实现、测试和验收合同。本文对私有运行的记载为开发者摘要；原始日志、模型轨迹和操作回执由项目留存。 |

## 能力与验证范围

| 能力 | 状态和实现入口 | 验收场景、基线及可核验证据 | 保证范围 |
| --- | --- | --- | --- |
| 结构化决策、上下文、工具、预算和出境 | 核心已验收。[Runtime](../../../src/labpilot/agent_runtime/service.py)、[ContextCompiler](../../../src/labpilot/agent_runtime/context_compiler.py)、[Provider 策略](../../../src/labpilot/agent_runtime/provider.py)。 | Core Demo `c92bf45`，真实模型和浏览器通过。预留、调用结果未知、预算和并发有[运行时单测](../../../tests/unit/agent_runtime/test_agent_service.py)及[数据库集成测试](../../../tests/integration/agent_runtime/test_agent_service.py)。 | 默认 `local_only`。普通 Run 有四个快照绑定只读工具。已知完成且可计账的指定格式错误最多纠错一次，未知调用不补调。整段 Proposal 容器纠错仅有离线证据。 |
| 提案、确定性验证、人工批准、冻结版本 | 核心已验收。[ProposalService](../../../src/labpilot/experiments/proposal_service.py)、[Verifier](../../../src/labpilot/experiments/proposal_verifier.py)。 | `c92bf45` 的 Core Demo 和 2026-10-28 NOTEARS 人工提案。验证、并发和幂等见[集成测试](../../../tests/integration/experiments/test_proposal_service.py)。 | 批准绑定确切提案、校验、源码及执行契约。由人批准实验。 |
| Batch / Trial / Attempt 与物化 | 核心已验收。[批次服务](../../../src/labpilot/execution/batch_service.py)、[物化与规格](../../../src/labpilot/execution/preparation_service.py)。 | Core Demo `c92bf45` 与 [NOTEARS native](#notears-native)。确切快照和取消竞争见[物化集成测试](../../../tests/integration/test_attempt_preparation.py)。 | 基础设施重试增加物理 Attempt。每个 Trial 保留其参数和 seed。 |
| 稳定身份、恢复对账、取消 | 指定场景已验收。[Coordinator](../../../src/labpilot/coordinator/service.py)、[执行服务](../../../src/labpilot/execution/execution_service.py)、[Runner](../../../src/labpilot/execution/runner_daemon.py)。 | [2026-08-31 MVP](2026-08-31-labpilot-slice2-mvp-smoke.md) 有 F01、F17 执行及取消证据，记录未给确切运行 SHA。[Core Demo](2026-09-19-labpilot-competition-core-demo-closeout.md) `c92bf45` 实测一次运行中重启 Coordinator 后仍为同一 Attempt 和原物理进程。另有[对账测试](../../../tests/integration/test_coordinator_reconciliation.py)和[权限边界测试](../../../tests/architecture/test_execution_privilege_boundary.py)。 | 单协调器、单机 Linux/systemd。未知观测保守处理。未验收训练 checkpoint 恢复、任意故障组合和全局 exactly-once。 |
| Artifact、MetricObservation、ValidationReport、Selection、Summary | 核心已验收。[结果模型](../../../src/labpilot/results_validation/models.py)、[摘要服务](../../../src/labpilot/results_validation/summary_service.py)。 | `c92bf45` 四个真实 Trial 有一个入选，入选 accuracy 为 0.941。NOTEARS `108d612` 记录两项入选，另有[结果处理测试](../../../tests/integration/test_result_processing_service.py)及[摘要测试](../../../tests/integration/test_batch_summary_service.py)。 | 按声明协议判定资格。限于所记录的条件、版本和入选集合。 |
| 结果驱动迭代、冻结历史和 Trial 事实 | 基础闭环已验收。新增事实有离线和指定场景验收。[绑定入口](../../../src/labpilot/api/result_aware_agent_adapter.py)、[历史工具](../../../src/labpilot/agent_runtime/result_history.py)、[Trial 事实](../../../src/labpilot/agent_runtime/trial_facts.py)。 | [两轮闭环](../../../tests/integration/p1/test_result_aware_iteration.py)及[历史收口](2026-09-22-labpilot-p1-result-aware-iteration-closeout.md) `e8caaf6` 使用 ScriptedProvider。10-06 interview 捕获同实验历史，通过限定范围的真实读取。10-12 真实复验计数与人工路径通过，整体 PARTIAL。见[历史绑定测试](../../../tests/integration/agent_runtime/test_result_history_binding.py)。 | 精确绑定摘要、同实验有限历史、固定 Trial 范围。解释质量、重复测量推断与证据读取仍有开放问题。不会自动批准或启动下一批。 |
| 静态候选、核心 Agent 补丁草稿 | 静态候选已验收。核心草稿属实验性能力。[候选服务](../../../src/labpilot/context/source_candidates.py)、[草稿输入](../../../src/labpilot/agent_runtime/source_patch.py)、[显式规范化](../../../src/labpilot/context/source_patch_normalization.py)。 | `405a4c1` / `ca3e5a7` / `88d2ff9` 实现。10-17 原始真实草稿静态应用失败。操作者另行规范化，派生补丁通过静态交接并人工采纳，后续人工 S2 四 Trial 回归与原基线逐条件一致。见[草稿测试](../../../tests/integration/p1/test_agent_source_patch.py)和[规范化测试](../../../tests/integration/p1/test_agent_patch_normalization.py)。 | 草稿是未检查文本，静态候选只具备静态检查证据。人工派生结果保留原样本的失败结论。 |
| 独立编码、独立验收、人工采纳 | 两个固定编码任务已验收。[编码 CLI](../../../tools/coding_worker/coding_task.py)、[导入](../../../src/labpilot/context/project_source_candidates.py)、[采纳](../../../src/labpilot/context/source_adoption.py)。 | [下方 NOTEARS 编码记录](#notears-coding)。[多文件导入、采纳及真实 Docker 交接测试](../../../tests/integration/context/test_project_source_candidates.py)和[独立验收合同](../../../tools/coding_worker/acceptance)。 | 准备好的 Linux/Docker、冻结范围和可信验收镜像。人工采纳独立于编码和实验批准。通用编码与任意仓库接入均未验收。 |
| 跨源码 Follow-up | 已实现并通过离线验收，真实提案验收未通过。[SourceBinding](../../../src/labpilot/agent_runtime/source_binding.py)、[采纳证据适配](../../../src/labpilot/api/source_adoption_evidence.py)。 | `3238c49` 实现，[数据库闭环测试](../../../tests/integration/p1/test_adopted_source_follow_up.py)。2026-10-20 两个固定真实提案样本分别因缺失 Proposal 容器及非法 JSON 终止，均无 Proposal 或 Verification。 | 人显式选定有效采纳关系，新 Run 固定旧结果与目标 Snapshot。真实提案质量尚未建立。人工 S2 基准跑有自身证据。 |
| NOTEARS native 准入与实验 | 指定场景已验收。[离线构造器](../../../scripts/build_notears_proposal.py)、[构造器测试](../../../tests/unit/scripts/test_build_notears_proposal.py)、[适配验收合同](../../../tools/coding_worker/acceptance/test_notears_experiment.py)。 | [下方 NOTEARS native 记录](#notears-native)。 | 受保护的专用依赖、精确数据根、固定合成数据和单 seed。 |
| Operator | 固定演示已验收。[页面](../../../src/labpilot/api/operator.html)、[回放](../../../src/labpilot/api/routers/ops.py)。 | `c92bf45` 真实浏览器 Golden Path，后续 interview 验收覆盖旧 Trace 和冻结 Trial 事实展示。 | 固定项目入口、单操作者。取消、跨源码选择、补丁、采纳及 NOTEARS 专用流程使用其他入口。 |
| 分布式调度、自动采纳及审批、通用自主科研 | 未实现或超出现有系统职责。 | 以 [执行权限测试](../../../tests/architecture/test_execution_privilege_boundary.py) 和 [Runner 接口](../../../src/labpilot/execution/runner_protocol.py) 为边界。 | 无长期真实使用、研究效率或提案质量的可推广量化结论。 |

## NOTEARS coding

| 日期与运行基线 | 实际任务与验收 | 采纳 |
| --- | --- | --- |
| 2026-10-25，LabPilot `b432db0836b197fa0eb884ddd6cfaa89aac6a269` | 001 R6 在冻结的 15 文件基准上做 headless 导入、求解参数和显式绘图改造。真实模型 14 次已知响应，自主提交 8 文件变化。独立 CPU 7 项及绘图 1 项、候选重放和包导入通过。首次样本及 R1–R5 共六次失败保留。 | R6 未采纳。较早 R4 的真实草稿通过人工辅助交接及检查后采纳，源码 `f113119`，形成 19 文件基准。 |
| 2026-10-26，LabPilot `466750bc7cfdac18c303c6d3dafa885539f4783b` | 002 基于 `f113119` 自主补齐输入和参数校验。真实模型 9 次已知响应，自主提交 3 文件变化。独立 CPU 49 项及绘图 1 项、候选重放和包导入通过。 | 在 LabPilot `a2965bd` 操作基线上经人工批准，采纳为 `dd55cb3`，直接父提交为 `f113119`，形成 20 文件 Snapshot。 |

运行环境为准备好的 WSL Linux、Docker 隔离工作区和独立验收镜像，使用真实远程模型。独立合同见 [001](../../../tools/coding_worker/acceptance/test_notears_cpu_001.py) 和 [002](../../../tools/coding_worker/acceptance/test_notears_cpu_002.py)。领域交接与采纳可通过[集成测试](../../../tests/integration/context/test_project_source_candidates.py)核对。原始轨迹、完整模型输入与操作者回执由项目留存。

## NOTEARS native

- 2026-10-27，人工编写的实验适配经独立 CPU 79 项、绘图 1 项验证后采纳为 `0444d2d`，父提交为 `dd55cb3`。23 文件清单及 mode 与候选一致。历史准入收口 `9270642`，正式提案验证记录 `0ddf78f`。
- 2026-10-28，人工提案、正式 Verifier 和人工批准后，通过既有 Linux/systemd Runner 实际执行 `notears_linear`。实验记录提交 `108d612b9d5a55f73643c7a146f3859134288d16`。运行环境为 Python 3.12.3、NumPy 2.5.4、SciPy 1.18.1、igraph 1.0.0 和已准入的 `notears-cpu-v1` profile。
- 固定 `200×5` Gaussian SEM，seed `7`，`lambda1` 为 `0.05` 和 `0.1`。一个 Version、一个 Batch、两个 Trial，每个一次 Attempt。零模型调用、零重试。两组均 exit 0、valid、selected。共 6 份产物、10 项指标、2 份验证报告、2 项选择和一份 complete Summary。
- 两组的 SHD、FDR、TPR、FPR、NNZ 均为 `0 / 0 / 1 / 0 / 5`，估计权重不同。对现成矩阵复算评价指标并检查 DAG，沿用冻结评价器。结果协议验证产物和数值合同，没有 SHD 质量门槛。样本不支持因果能力、超参优劣或研究效率的普遍结论。
- 权限和运行接口见 [Runner policy](../../../src/labpilot/execution/runner_policy.py)、[Proposal 构造器](../../../scripts/build_notears_proposal.py) 和[实验适配验收合同](../../../tools/coding_worker/acceptance/test_notears_experiment.py)。复现需要有许可的科研源码、相同数据清单和可用的本机准入配置。此仓库没有随快照分发完整改版科研源码、实验数据或原始产物。上述历史摘要和测试入口不足以独立复现完整 native 实验。

## 门禁与历史边界

历史工程基线 `108d612b9d5a55f73643c7a146f3859134288d16` 于 2026-10-28 在隔离 PostgreSQL 中通过 [本地门禁](../../../scripts/check_ci.sh)，含 1442 项 unit/architecture、392 项 integration 通过、1 项外部模型 smoke 跳过、5 项未选择。显式 Docker worker 交接通过，零 Provider 请求。该次未执行 GitHub 的生产镜像额外步骤。公开旧基线 `0291997` 的 [GitHub 门禁](https://github.com/Air000000/LabPilot/actions/runs/35955206282) 有镜像检查记录。

Slice 2 生产加固、完整 native 故障矩阵，以及因验收 supervisor 未能启动子进程而中断的 Slice 3 完整 native 验收仍未关闭。2026-11-24 的封存回执哈希复核只证明所核验文件与各自封存索引一致。

## 第三方来源

| 材料 | 来源和范围 |
| --- | --- |
| mini-swe-agent | [上游组件](https://github.com/SWE-agent/mini-swe-agent)，独立 worker 依赖固定为 `2.4.6`。LabPilot 提供任务冻结、工作区约束、独立验收、包导入和人工采纳。 |
| NOTEARS | [上游源码及许可](https://github.com/xunzheng/notears/blob/master/LICENSE)。隔离科研副本保留上游 Apache-2.0 许可文件。公开候选仅包含 LabPilot 构造器、测试合同、合成小样本参考值、示例任务和结果摘要。 |
| 静态候选补丁 | [梯度裁剪测试补丁](../../../tests/fixtures/source_candidates/gradient_clipping.patch) 针对测试用训练入口，随测试保留。 |
| 运行依赖及镜像 | [Core 依赖](../../../pyproject.toml)、[worker 依赖](../../../tools/coding_worker/pyproject.toml) 及同目录锁文件记录来源。公开候选未打包其镜像、wheel 或第三方源码。 |
