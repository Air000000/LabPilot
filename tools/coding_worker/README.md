# 独立受控编码 CLI

[公开能力与证据](../../docs/superpowers/evidence/research-runtime-preview.md) · [总体架构](../../docs/interview/research-runtime-preview.md)

独立 worker 面向单操作者和预装依赖的 Python CPU 项目。它复用 mini-swe-agent 2.4.6 修改隔离工作区，将通过检查的结果交给 Core 导入和人工采纳。核心科研 Agent、API 与 Coordinator 无需获得 Docker 权限。

## 信任边界

| 阶段 | 实际约束 |
| --- | --- |
| 容器运行 | worker 在隔离、非 root、禁网的 Docker 容器中运行。容器有可写工作区，宿主 worker 单独处理模型请求。 |
| 任务合同 | 基准、目标、工具环境、预算和验收命令在 prepare 时冻结。`editable_paths` 同时作为模型指令。 |
| 候选范围 | `editable_paths` 约束导出的最终候选，通过差异检查、重放和 Core 导入执行。它不逐次拦截容器内写入。候选范围外的变化会阻止准入。 |
| 独立验收 | worker 将候选树载入新的验证容器并运行可信冻结测试，检查退出码和候选源码一致性。编码 Agent 的开发测试只作开发反馈。 |
| Core 导入 | [ProjectSourceCandidateService](../../src/labpilot/context/project_source_candidates.py) 核对任务与候选身份、验收合同、回执、日志哈希和内容一致性，并重放差异。动态 Docker 检查由独立 worker 执行。 |
| 人工采纳 | 验收回执是可信本地操作者提供的证明，内容哈希可核对。内容哈希无法证明回执不可伪造。回执不充当第三方签名或远程可信执行证明。导入后仍需独立审阅、提交、捕获 Snapshot 和显式采纳。 |

## 1. 准备环境及镜像

使用 Linux 或 WSL、可用的本机 Docker Unix endpoint 和 Python 3.12。Core 与 worker 各自安装锁定依赖。以下命令从 LabPilot 仓库根目录运行，展示 CPU 镜像和 NOTEARS 验收镜像的构建入口。

```sh
uv sync --locked --all-groups
uv sync --project tools/coding_worker --locked
unset DOCKER_CONTEXT
export DOCKER_HOST=unix:///var/run/docker.sock

docker build --target cpu -t labpilot-coding-cpu:local -f tools/coding_worker/Dockerfile tools/coding_worker
docker build --target cpu-acceptance -t labpilot-coding-cpu-acceptance:local -f tools/coding_worker/Dockerfile tools/coding_worker
docker image inspect --format '{{.Id}}' labpilot-coding-cpu:local
docker image inspect --format '{{.Id}}' labpilot-coding-cpu-acceptance:local
```

将 `DOCKER_HOST` 改成当前主机实际的 Unix socket。绘图场景使用 `plot` / `plot-acceptance`。为自己的任务准备可信验收镜像，将测试装入候选工作区之外，并在 profile 中使用本机实际的 `sha256:<image-id>`。仓库镜像的测试位于 `/opt/labpilot-acceptance`。历史 NOTEARS profile 的镜像 ID 和完整研究副本未随版本分发，可参考其[字段结构](notears-profile.json)。

Profile 只含 `image`、`editable_paths`、`ignored_paths`、`validators`。验收项使用冻结的镜像和 `argv`，忽略模式不能匹配基准中已跟踪的路径。运行时容器禁网，所需依赖应在镜像中准备。

Core 交接命令从 LabPilot 的 [Settings](../../src/labpilot/settings.py) 读取可信环境或仓库根目录 `.env`，见[配置示例](../../.env.example)。使用明确的隔离数据库、Actor、允许的仓库根和内容库。科研项目自身的 `.env` 不应作为 Core 源码候选 CLI的配置来源。

## 2. 冻结并登记基准

`SOURCE` 是已有 Git 仓库。`FROZEN` 必须是源仓库之外尚未存在的输出目录。冻结工具复制 Git 跟踪的工作区文件，可能纳入已跟踪文件的未提交变更。它记录缺失的跟踪路径，原 Git 元数据和未跟踪文件不随副本复制。输出含 `baseline.json` 和拥有独立 Git 历史的 `$FROZEN/source`。先审阅参与冻结的内容，特别是已跟踪的配置和数据。

```sh
uv run python tools/coding_worker/baseline.py --repository "$SOURCE" --output "$FROZEN"
uv run python scripts/source_candidate.py create-project --name "$PROJECT_NAME" \
  --artifact-root-ref "$ARTIFACT_ROOT_REF" --idempotency-key "$CREATE_KEY"
uv run python scripts/source_candidate.py register-repository --project-id "$PROJECT_ID" \
  --repository "$FROZEN/source" --expected-project-row-version "$PROJECT_ROW_VERSION" \
  --idempotency-key "$REGISTER_KEY"
uv run python scripts/source_candidate.py capture-snapshot --registration-id "$REGISTRATION_ID" \
  --idempotency-key "$CAPTURE_KEY"
```

三个命令各自返回 `primary_result_id`，依次用于 Project、RepositoryRegistration 和 Snapshot。捕获 Snapshot 时另用响应的 `primary_result_identity`。新建 Project 的初始行版本为 1，复用已有 Project 时从 Core 读取当前行版本。已有正确登记时复用对应对象。每个幂等键须标识该次操作。导入要求准备任务时绑定同一 Project 和确切的基准 Snapshot。

## 3. 准备、核查和运行任务

`GOAL` 是目标文件，`PROFILE` 是上述 JSON 合同。`TASK` 必须是一个尚不存在的私有任务目录，位于原科研仓库与冻结输出目录之外。真实模式的 `CODING_MODEL` 使用所配置的 DeepSeek LiteLLM 模型 ID，`CODING_API_BASE` 使用可信 HTTPS 地址。

```sh
uv run python scripts/coding_task.py prepare --baseline "$FROZEN" --goal "$GOAL" \
  --profile "$PROFILE" --task "$TASK" \
  --project-id "$PROJECT_ID" --snapshot-id "$SNAPSHOT_ID" --snapshot-identity "$SNAPSHOT_IDENTITY" \
  --model "$CODING_MODEL" --api-base "$CODING_API_BASE" \
  --calls "$CODING_CALLS" --tokens "$CODING_TOKENS" --wall-seconds "$CODING_WALL_SECONDS" \
  --output-cap "$CODING_OUTPUT_CAP"
uv run python scripts/coding_task.py inspect --task "$TASK"
```

这里的 `prepare` 和 `inspect` 不发模型请求。运行前审阅任务目录中冻结的 `task.json`。真实模式需要操作者明确批准费用和出境范围。宿主进程可能向指定端点发送任务中读取的源码和工具输出。

```sh
# 先从可信环境加载并导出 DEEPSEEK_API_KEY。
uv run python scripts/coding_task.py run --task "$TASK" --allow-model
uv run python scripts/coding_task.py inspect --task "$TASK"
```

[入口包装器](../../scripts/coding_task.py) 转发的模型凭据仅为 `DEEPSEEK_API_KEY`。其余白名单变量为 `PATH`、`HOME`、`LANG`、`LC_ALL`、`SSL_CERT_FILE`、`SSL_CERT_DIR`、`REQUESTS_CA_BUNDLE`、`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 和 `DOCKER_HOST`。Core Provider 的配置变量和其他模型凭据不由此传入，模型密钥也不进入候选容器。

`--calls` 限制模型调用次数，`--tokens` 用已返回且可计账的总 Token 用量推进预算。`--output-cap` 为单次生成的输出上限，`--wall-seconds` 为运行时长预算，外层还有 watchdog。已发生调用的账单受供应商计费和实际用量影响，这些参数不提供严格的货币费用上限。

可在**另一个 prepared 任务**上执行 `uv run python scripts/coding_task.py run --task "$TASK" --actions "$ACTIONS"` 运行受信任的本地脚本。`ACTIONS` 是 JSON 命令字符串数组。它用确定性模型替代远程请求，仍会在 Docker 中运行并验收候选。[Docker scripted 验收用例](../../tests/integration/context/test_project_source_candidates.py)展示该路径。这用于检查交接和验收合同。

## 4. 验收、导入与人工采纳

运行及验收输出留在私有任务目录。通过后 `status.json` 为 `candidate_ready`，验收摘要及日志位于 `validation.json` 和对应的 `validation-*.log`，`candidate.json`、`candidate.diff` 和 `candidate/` 提供可核对的候选。只导入与通过回执对应的候选。定位失败时用 `inspect`，已有 `candidate_ready` 或 `validation_failed` 的任务可用 `validate --task "$TASK"` 重做独立检查。

```sh
uv run python scripts/source_candidate.py --profile project-python-v1 import-task --task "$TASK"
uv run python scripts/source_candidate.py --profile project-python-v1 inspect --package-ref "$PACKAGE_REF"
```

`PACKAGE_REF` 取自导入结果。操作者审阅确切差异，从基准提交在已登记的独立研究副本创建直接子版本，使内容和模式与候选精确一致。保留旧 Snapshot。随后捕获目标 Snapshot，核对其 identity 与审阅过的源码，再显式发出采纳命令。

```sh
uv run python scripts/source_candidate.py capture-snapshot --registration-id "$REGISTRATION_ID" \
  --idempotency-key "$TARGET_CAPTURE_KEY"
uv run python scripts/source_candidate.py --profile project-python-v1 adopt --project-id "$PROJECT_ID" \
  --package-ref "$PACKAGE_REF" --target-snapshot-id "$TARGET_SNAPSHOT_ID" \
  --target-snapshot-identity "$TARGET_SNAPSHOT_IDENTITY" --idempotency-key "$ADOPTION_KEY"
```

验收、导入和采纳均不验证科研结果，也不授权训练。后续实验仍须单独提案、确定性校验、人工批准、创建 Batch 和显式启动。见[固定 NOTEARS 编码证据](../../docs/superpowers/evidence/research-runtime-preview.md#notears-coding)。
