# LabPilot

LabPilot V1 当前采用 **v0.3 Core Rebaseline**：一个可控、可恢复、可验证的 Agent-assisted Experiment Runner，而不是缩小版 Research Agent OS。

## 1. 当前 V1 planning authority

对所有尚未实现的 V1 工作，规范性规划输入按以下顺序解释：

```text
docs/labpilot/01-v1-scope-and-acceptance-v0.3.md
> docs/labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md
> docs/labpilot/implementation/01-master-implementation-roadmap-v0.3.md
> current approved Slice-specific detailed plan / ADR
```

`DOCUMENT-STATUS.md` 是当前 authority/status map。

更早的 v0.2 architecture、roadmap、implementation plans 与 Architecture 08/09 对 remaining V1 默认为 historical/informative。它们继续解释在其约束下已经接受的 WP0 与 WP1-A Task 0–10；只有被当前 v0.3 / Slice authority 显式引用的具体 invariant 才重新进入未来实施 tranche。

当前实现状态：

```text
WP0 + WP1-A Tasks 0–10
= Accepted Historical Foundation

WP1-A v0.2 Tasks 11–17
= Superseded / Never Executed

Remaining V1
= Slice 1 → Slice 2 → Slice 3
```

R0 finalization 只授权起草 **Slice 1 Detailed Implementation Plan**，不直接授权 Slice 1 implementation。该 plan 必须绑定 exact R0 authority commit SHA。

## 2. 历史冻结依赖与完整性证据

历史 frozen docs、`*.dependencies.sha256` 与 `FINAL-DOCUMENTS.sha256` 继续保留，用于证明对应历史 implementation tranche / release bundle 的输入字节没有漂移。

例如，`docs/labpilot/implementation/01-master-implementation-roadmap-v0.2.1-frozen.md` 仍被历史 WP0 dependency manifest 绑定，因此不能因为它已经被后续 roadmap 取代而删除。

这些历史 hash **不再自动决定 remaining-V1 的语义 authority**。当前 v0.3 authority 的机器可验证根是完成 R0 finalization 的 Git commit SHA；后续 Slice plan 直接绑定该 SHA，不建立新的级联 architecture hash graph。

## 3. Master Blueprint 状态说明

`docs/labpilot/00-master-blueprint-v0.2.md` 头部仍保留历史状态文字。该文件字节已被历史冻结文档和 implementation tranche 绑定，因此不为了同步元数据而原地修改。

R0 v0.3 之后，它对 remaining V1 属于 historical high-level input，而不是 closed-world authority list 的成员；对已经在其历史 contract 下验收的工作仍保留解释价值。

## 4. 历史实施前置与 dependency verification

历史 WP0/WP1-A dependency verification 继续有效，例如：

```bash
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.4.dependencies.sha256

bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256
```

历史 architecture dependency manifests 也仍可单独验证。它们证明对应 frozen tranche 的完整性，不意味着旧 roadmap 仍拥有未来执行 authority。

## 5. Remaining V1 implementation path

```text
R0 v0.3 authority
→ Slice 1 Detailed Implementation Plan
→ Slice 1: Repository → Proposal → Approval
→ Slice 2: Trial → Real Training → Recovery
→ Slice 3: Artifact → Metric → Validation → Summary
→ V1 Core Demo
```

Core complexity budget 优先用于真实 failure modes：Runner/recovery、duplicate-launch prevention、Approval/path/argv/secret safety、scientific provenance 与 deterministic Validation。

Evidence/Claim Closure、dynamic Tool platform、Projection platform、generic workflow/result-processing platform 不属于 V1 Core 默认实现义务。

## 6. 校验命令

### 跨解压器通用命令

ZIP 标准支持 Unix 权限元数据，但部分解压器不会恢复执行位。统一可以使用：

```bash
bash scripts/verify_architecture_dependencies.sh FINAL-DOCUMENTS.sha256
```

### Unix 直接执行

如果脚本执行位已保留，也可以直接运行：

```bash
./scripts/verify_architecture_dependencies.sh FINAL-DOCUMENTS.sha256
```

如果解压器忽略权限，可执行：

```bash
chmod 755 scripts/verify_architecture_dependencies.sh
```

这些命令验证历史 bundle/tranche integrity，不承担 v0.3 semantic authority selection。

## 7. 当前交付边界

- WP0 已完成；
- WP1-A Task 0–10 已完成、独立评审并 push；
- V1 v0.3 R0 Rebaseline 已通过 finding-specific re-review；
- 完成 R0 finalization 的 commit 将成为 remaining-V1 authority root；
- 旧 WP1-A Task 11–17 已 superseded，不再执行；
- 当前只授权起草 Slice 1 Detailed Implementation Plan；
- Slice 1 implementation 尚未授权；
- Slice 2/3 只在前一 Slice acceptance 后 JIT planning。

## 8. 本地 Repository 观察边界

当前已实现的 Repository 基础继续作为 Accepted Foundation 使用。现有 Compose 将 `${LABPILOT_REPOSITORY_HOST_ROOT:-.}` 只读挂载到 API 容器 `/repositories`，允许根由服务端 `LABPILOT_REPOSITORY_ALLOWED_ROOTS` 配置；content-addressed repository 数据写入独立 content volume。

Repository Git 操作用于有界、只读观察，不使用 shell，不执行网络访问，也不执行 fetch、pull、clone、checkout、reset、push 或其他会修改 Repository 的命令。

v0.3 后续模块应通过 narrow Repository contract/facade 消费该基础，不把 Bundle/manifest/CAS 等内部 identity richness 自动传播到 Agent、Proposal、Experiment 或 Result Plane。
