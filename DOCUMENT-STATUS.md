# LabPilot 文档状态

| 文档 | 状态 | 用途 |
|---|---|---|
| Master Blueprint v0.2 | Operationally frozen by downstream hashes | 最高层产品和架构边界；头部状态文字为历史元数据 |
| V1 Scope v0.2 | Approved / Frozen | V1 范围和验收 |
| Architecture 02–07 v0.2 | Approved / Frozen | 核心架构契约 |
| Architecture Index v0.2 | Approved / Frozen | 模块和依赖索引 |
| Master Roadmap v0.2.3 | Approved / Frozen / Current | 当前工作包路线图 |
| Master Roadmap v0.2.1 | Historical frozen dependency | 仅用于 WP0 v0.3.1 哈希校验 |
| WP0 Foundation v0.3.1 | Ready for Execution | 当前唯一代码级实施计划 |

## 发布包格式

- ZIP：包含 Unix `0755` 权限元数据；标准 `unzip` 会恢复执行位。
- TAR.GZ：作为 Unix 权限保真发布格式。
- 跨解压器校验命令：`bash scripts/verify_architecture_dependencies.sh <manifest>`。

## WP1

- `02-wp1-domain-context-overall-design-v0.5.md`: Approved Design Input
- `03-wp1-a-project-repository-implementation-plan-v0.2.md`: Approved Implementation Plan
- `04-wp1-a-task6-canonical-identity-contract-v0.3.1.md`: Approved Implementation Contract
- WP1-A Task 0–7: Completed / independently reviewed / pushed / CI passed
- WP1-A Task 8: Not started
- Current WP1-A branch: `feature/wp1-domain-context`
- Task 7 accepted implementation commit: `2bdeb7747c546fad879b641f5e343810c511f702`
