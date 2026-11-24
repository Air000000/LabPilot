# 独立受控编码 CLI

[公开能力与证据](../../docs/superpowers/evidence/research-runtime-preview.md) · [总体架构](../../docs/interview/research-runtime-preview.md)

独立 worker 面向单操作者和预装依赖的 Python CPU 项目。它复用 mini-swe-agent 2.4.6 编辑隔离工作区，通过独立环境复核候选，再交给 LabPilot 记录和人工采纳。核心 API 与 Coordinator 无需获得 Docker 权限。容器禁网；允许远程生成时，宿主进程可以发送当前任务读到的源码及工具反馈，须取得独立授权。

## 1. 准备环境及镜像

使用 Linux 或 WSL、可用的 Docker 和当前主机的 Unix endpoint。需要单独准备可信验收测试和镜像，配置使用本机实际的 `sha256:<image-id>`。仓库中的 NOTEARS profile 记录原案例镜像身份，可作字段参考。

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

Docker endpoint 按主机实际配置填写。绘图案例使用同 Dockerfile 的 `plot` / `plot-acceptance`。独立验收镜像的测试放在 `/opt/labpilot-acceptance`。profile 仅允许 `image`、`editable_paths`、`ignored_paths`、`validators` 四类字段；validator 冻结独立镜像和 argv。候选运行阶段禁止联网安装依赖。

交接命令使用 Core 的数据库、Actor、允许的仓库根和内容库配置，见[配置示例](../../.env.example)。配置来自可信环境或 LabPilot 仓库的配置文件。选择隔离的业务实例，避免加载科研项目自己的配置或修改已有部署。

## 2. 冻结并登记基准

从干净科研仓库生成冻结基准。以下 `SOURCE`、`FROZEN`、`GOAL`、`PROFILE`、`TASK` 均由操作者指定，任务和输出应存放在私有目录。

```sh
uv run python tools/coding_worker/baseline.py --repository "$SOURCE" --output "$FROZEN"
uv run python scripts/source_candidate.py create-project --name "$PROJECT_NAME"   --artifact-root-ref "$ARTIFACT_ROOT_REF" --idempotency-key "$CREATE_KEY"
uv run python scripts/source_candidate.py register-repository --project-id "$PROJECT_ID"   --repository "$FROZEN/source" --expected-project-row-version 1 --idempotency-key "$REGISTER_KEY"
uv run python scripts/source_candidate.py capture-snapshot --registration-id "$REGISTRATION_ID"   --idempotency-key "$CAPTURE_KEY"
```

已有登记可复用原 Project 和 registration。每个参数与幂等键都须匹配本次操作。把确认的 Snapshot ID 和 identity 用于准备任务。

## 3. 准备、运行和交接

```sh
uv run python scripts/coding_task.py prepare --baseline "$FROZEN" --goal "$GOAL"   --profile "$PROFILE" --task "$TASK"   --project-id "$PROJECT_ID" --snapshot-id "$SNAPSHOT_ID" --snapshot-identity "$SNAPSHOT_IDENTITY"   --model "$CODING_MODEL" --api-base "$CODING_API_BASE"   --calls "$CODING_CALLS" --tokens "$CODING_TOKENS" --wall-seconds "$CODING_WALL_SECONDS"   --output-cap "$CODING_OUTPUT_CAP"
uv run python scripts/coding_task.py inspect --task "$TASK"
```

`prepare` 和 `inspect` 无模型请求。实际 `run --task "$TASK" --allow-model` 可能收费及发送源码。先核对冻结任务和费用、出境范围，从可信环境提供所选模型配置和凭据。远程模型验收与可重复的 scripted Docker 测试是两类证据。

独立验收通过后，导入并检查候选。

```sh
uv run python scripts/source_candidate.py --profile project-python-v1 import-task --task "$TASK"
uv run python scripts/source_candidate.py --profile project-python-v1 inspect --package-ref "$PACKAGE_REF"
```

操作者审阅确切候选，在已登记研究副本提交直接子版本并捕获目标 Snapshot，再人工发出采纳命令。

```sh
uv run python scripts/source_candidate.py capture-snapshot --registration-id "$REGISTRATION_ID"   --idempotency-key "$TARGET_CAPTURE_KEY"
uv run python scripts/source_candidate.py --profile project-python-v1 adopt --project-id "$PROJECT_ID"   --package-ref "$PACKAGE_REF" --target-snapshot-id "$TARGET_SNAPSHOT_ID"   --target-snapshot-identity "$TARGET_SNAPSHOT_IDENTITY" --idempotency-key "$ADOPTION_KEY"
```

采纳建立源码关系。后续实验需要新的提案验证与人工批准。实例入口、回执格式和独立测试见 [候选服务](../../src/labpilot/context/project_source_candidates.py)、[验收合同](acceptance)及[公开证据](../../docs/superpowers/evidence/research-runtime-preview.md#notears-coding)。
