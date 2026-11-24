# LabPilot — Research Agent Runtime / Harness

[English](README.en.md) · [总体架构](docs/interview/research-runtime-preview.md) · [能力与验收证据](docs/superpowers/evidence/research-runtime-preview.md)

LabPilot 是面向科研实验与算法研发的 Agent Runtime / Harness，解决 AI Agent 参与科研迭代时的决策约束、源码治理、长任务可靠执行与结果可信性问题。

系统将受约束的模型决策、独立源码验收、确定性方案校验、人工审批、可靠实验执行及结果验证纳入同一条可追溯治理链路，明确关联研究意图、源码版本、执行过程与实验结果。

当前处于 **Engineering Preview（阶段性工程预览）**。

## 当前能力

| 治理范围 | 机制 |
| --- | --- |
| 决策 | ContextCompiler 按绑定的源码快照、工具契约和实验事实构建有界模型上下文。Runtime 校验结构化决策、预算和数据出境策略，先记录调用身份，再调用模型。调用结果不确定时保守终止。 |
| 源码 | 核心 Agent 可在显式模式下生成并持久化未经验证的补丁草稿。独立编码 worker 按预定义任务合同修改隔离工作区源码，并由单独的容器验收候选。操作者审阅、提交、捕获并采纳新 Snapshot。 |
| 执行 | Verifier 确定性校验提案，人工批准后生成 ExperimentVersion。Batch 按参数和 seed 展开 Trial。Attempt 物化确切源码，由 Coordinator 和 Linux Runner 使用稳定执行身份对账。 |
| 结果 | Artifact、MetricObservation、ValidationReport、TrialResultSelection 和 BatchSummaryRevision 分层保存。后续 AgentRun 绑定精确摘要，可使用冻结历史和 Trial 事实。 |

## 架构与权限

科研实验链为 **研究目标 → ProposalRevision → Verification → 人工批准 → ExperimentVersion → 创建 Batch / 展开 Trial → 显式 StartBatch → Attempt → 产物、验证、选择、摘要 → 后续 AgentRun**。PostgreSQL 保存权威领域状态。进程正常退出和结果符合声明协议分别记录。确定性结果验证以声明协议为准，科研假设仍须独立评估。基础设施重试增加 Attempt，Trial 的科研条件保持原身份。

普通科研 Agent 的基础工具是绑定快照的四个只读仓库工具。符合条件的新 Follow-up 可额外读取冻结的同实验历史。普通 Run 无代码写入、shell、审批或物理执行权限。模型调用前会预留持久化步骤。已持久化的模型提案决策支持重入式生成 ProposalRevision。一次有界纠错仅适用于已完成响应中指定的格式错误，且其 Token 用量须可用于预算记账。远程模型可能接收仓库内容，使用前须明确授权出境。

独立编码 CLI 复用 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)，在非 root、无网络的 Docker 工作区执行编辑和开发测试。任务冻结基准、候选变更范围、预算与验收合同。工作区可写，`editable_paths` 在最终候选的差异检查、重放和准入时执行范围约束。宿主 worker 的远程模型调用需要单独授权出境。独立容器运行可信冻结测试，Core 核对本地操作者提供的验收回执及候选内容。验收通过后仍须人工采纳。核心 Agent 草稿须单独交接和检查。已采纳 Snapshot 可用于人工提案或显式跨源码 Follow-up，新实验仍须确定性校验、人工批准和显式启动。跨源码机制已通过离线验收，两次指定的真实提案均未通过验收。

## 关键工程机制

- **Bounded Agent Loop**　每步校验一个明确决策。工具契约、运行预算、绑定上下文和出境策略限定模型可以读取什么、做什么。
- **Persist-before-execute**　调模型前保存步骤预留和请求身份。物理启动前另行保存启动意图和执行身份。前者避免未知响应后盲目补调，后者为进程对账提供依据。未知状态保守处置，系统无全局 exactly-once 保证。
- **Stable Execution Identity**　协调器重启后用稳定身份及可用的 Runner 观测核对原物理尝试，避免仅因失去响应就重新启动。
- **Immutable ExperimentVersion**　人工批准冻结确切提案及其源码、数据与执行语义，后续更改需要重新校验和批准。
- **Independent Candidate Verification**　候选在独立环境中重放，使用可信冻结测试和范围检查。编码 Agent 自行生成的测试用于开发反馈。
- **Trial / Attempt Separation**　Trial 在所属实验设计下记录参数和 seed，基础设施重试增加同一 Trial 的物理尝试。跨源码、数据或环境时，相同参数和 seed 不足以证明条件等价。
- **Exact Revision Binding**　执行依赖批准的源码快照。后续 AgentRun 固定读取绑定的摘要修订，后来生成的摘要不会改写其依据。

## 已验收案例

- **Core Demo**　使用真实模型调用与真实 CPU 逻辑回归训练。四个 Trial 中一个达到声明阈值，入选 accuracy 为 0.941。
- **确定性闭环与恢复**　ScriptedProvider 的两轮结果驱动闭环有独立验收。另有真实运行中重启 Coordinator 的对账验收，同一 Attempt 保持一次物理启动。运行中真实取消另见 MVP 记录。
- **受控编码**　NOTEARS-CPU-001 的 R6 自主候选通过独立验收与交接，未采纳。实际采纳的是人工辅助交接的 R4 模型草稿 `f113119`。NOTEARS-CPU-002 自主候选基于它完成修改，经独立验收和人工确认采纳为 `dd55cb3`。
- **NOTEARS native 实验**　人工编写并采纳实验适配 `0444d2d`，再人工提案、校验、批准和执行。固定合成数据、单 seed、两个 lambda，共两个 Trial，各一次 Attempt。实验零模型调用、零重试。两组 SHD 均为 0。结果仅描述固定工作负载上的观测。本仓库未分发完整科研副本、数据及原始实验产物。

每个案例的版本、环境、失败边界和测试入口见[能力与验收证据](docs/superpowers/evidence/research-runtime-preview.md)。

## 工程结构

Python 3.12、FastAPI、PostgreSQL 16、SQLAlchemy、Pydantic、Alembic、systemd、Docker Compose。

- [运行时与领域服务](src/labpilot) · [数据库迁移](migrations) · [测试](tests)
- [宿主 Runner 服务](deploy/systemd/labpilot-runner.service) · [独立编码 worker](tools/coding_worker)
- [本地等价 CI](scripts/check_ci.sh) · [GitHub CI 配置](.github/workflows/ci.yml)

## 启动与演示

| 用途 | 入口及前提 |
| --- | --- |
| 控制面 readiness | 在本地 Docker 环境启动 PostgreSQL、迁移和 API。可用空工作区检查 readiness，无需模型凭据或 Runner。见下方命令。 |
| 完整执行环境 | 按 [Runner 安装脚本](scripts/install_labpilot_runner_host.sh) 和 [配置示例](.env.example) 准备 Linux/systemd 主机、身份、目录、socket 和准入策略，再启动 Coordinator。CPU 可运行已验收案例，GPU 由工作负载决定。 |
| 预配置 Operator 演示 | [演示脚本](scripts/start_interview_operator_demo.sh) 的 `start`、`doctor` 面向已经配置的开发机。默认用检出目录所有者及其配置目录，可显式设置 `LABPILOT_INTERVIEW_USER`、`LABPILOT_INTERVIEW_CONFIG_DIR` 和 `LABPILOT_REPOSITORY_ALLOWED_ROOTS`。`/operator` 绑定固定演示项目和快照，`/ops` 可读取既有记录。 |
| 独立编码 worker | 需要单独的锁定依赖、Docker、冻结镜像、任务与验收配置。见[编码操作指南](tools/coding_worker/README.md)。 |
| NOTEARS 固定实验 | 需要已登记的 23 文件科研快照、冻结数据、依赖和 native profile。仓库提供 [Proposal 构造器](scripts/build_notears_proposal.py) 和[固定案例证据](docs/superpowers/evidence/research-runtime-preview.md#notears-native)。 |

在 Linux 上用本机 Docker 和支持 `--wait` 的 Compose，从仓库根目录在同一 shell 执行。命令使用独立项目及临时目录，忽略仓库 `.env`，关闭模型凭据和远程出境，只启动控制面。Compose 将 API 发布到 `127.0.0.1` 上的动态端口。

```sh
preview_dir="$(mktemp -d)"
preview_project="labpilot-preview-$(date +%s)-$$"

preview_compose() {
  LABPILOT_ATTEMPT_WORKSPACE_ROOT="${preview_dir:?}" \
  LABPILOT_CONTROLLER_UID="$(id -u)" LABPILOT_CONTROLLER_GID="$(id -g)" \
  LABPILOT_TRAIN_GID="$(id -g)" LABPILOT_API_PORT=0 \
  LABPILOT_REPOSITORY_HOST_ROOT="$PWD" LABPILOT_REPOSITORY_ALLOWED_ROOTS='["/repositories"]' \
  LABPILOT_INSTALLATION_IDENTITY="${preview_project:?}" LABPILOT_LOCAL_ACTOR_ID=preview-operator \
  LABPILOT_DATA_EGRESS_POLICY=local_only LABPILOT_AGENT_PROVIDER_KIND=opencode_go \
  LABPILOT_AGENT_PROVIDER_MODEL= LABPILOT_AGENT_PROVIDER_API_KEY= \
  docker compose --env-file /dev/null -f compose.yaml -p "$preview_project" "$@"
}

preview_compose up --build --wait -d postgres migrate api
preview_addr="$(preview_compose port api 8000)"
curl --noproxy 127.0.0.1 -fsS "http://$preview_addr/health/ready"
```

网页可以在该地址访问。Operator 使用服务端配置的单操作者身份，只为本地可信访问准备。保持 loopback 绑定，避免将它作为共享服务暴露。完整执行环境应使用实际配置的宿主身份。

在同一 shell 运行 `preview_compose down --volumes && rmdir -- "$preview_dir"` 清理本次临时项目及其卷。

## 能力边界

- 当前为单 Coordinator、单机 Linux Runner。系统没有全局 exactly-once 或分布式调度保证。已实测的 Coordinator 恢复是身份与状态对账。训练 checkpoint 恢复及完整生产故障矩阵尚未完成验收。
- Operator 面向单操作者和固定演示。独立编码、源码采纳和正式 NOTEARS 接入使用各自的 CLI、API 或主机配置入口。
- 固定案例验证了工程链路。通用自主科研、普遍的因果发现能力、超参优劣、长期真实用户使用、提案质量与科研效率均无可推广的量化结论。

[文档导航](DOCUMENT-STATUS.md) · [领域词汇](CONTEXT.md) · [正式公开基线及 CI](docs/superpowers/evidence/research-runtime-preview.md#public-release-acceptance)

## 许可

LabPilot 当前没有项目级 LICENSE，也未宣布项目级开源许可。第三方组件沿用其各自的许可条款。
