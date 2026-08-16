# Slice 2 next-window kickoff prompt

把下面整段作为新 Slice 2 窗口的第一条消息使用。

---

继续推进 LabPilot V1 Slice 2。请先读 GitHub/当前仓库，不要直接开始写 production code，也不要重新设计已经完成的 S2.0 supervision 方案。

当前冻结基线：

```text
S2.0 final code baseline:
4109afa9f871ae45cf32c301040bcff6f109939d

Candidate A = REJECT_A
Candidate B = SELECT_B
Selected physical evidence = systemd + persistent journald
```

请按顺序完整阅读：

```text
docs/superpowers/handoffs/2026-08-16-labpilot-v1-slice2-post-s20-handoff.md
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md
docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md
docs/labpilot/01-v1-scope-and-acceptance-v0.3.md
docs/labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md
docs/labpilot/implementation/01-master-implementation-roadmap-v0.3.md
```

然后核对这些实现/测试文件：

```text
tests/poc/s2_runner/contract.py
tests/poc/s2_runner/durable_state.py
tests/poc/s2_runner/runner_protocol.py
tests/poc/s2_runner/runner_daemon.py
tests/poc/s2_runner/systemd_cli.py
tests/poc/s2_runner/scenario_runner.py
tests/poc/s2_runner/host/README.md
tests/unit/s2_runner_poc/**
```

S2.0 native PoC 已完成。不要为了熟悉代码重新运行 P0-P13，不要清理 preserved journal/marker/evidence，不要触碰 physical experiment server。只有后续经过批准的 production change 实际改变了已验证的 physical invariant 时，才考虑新的 native campaign。

必须保持的 supervision 语义：

```text
Attempt.id -> stable X
Frozen execution semantics -> canonical versioned H
same X + same H -> reconcile
same X + different H -> fail closed
NOT_FOUND != NEVER_ACCEPTED_PROVEN
possible prior acceptance -> never blind create
CONTROL_UNKNOWN -> observe X/H, never blind retry
unknown physical state -> no launch
stop/kill requires exact ACTIVE_MATCH X/H
PID/InvocationID are observations only
physical observation does not directly own Attempt state
Runner is not the durable Attempt ledger and owns no retry scheduler
```

注意一个必须保留的 governance gap：repo 中历史 r2 plan 写明 Candidate B daemon 需要 focused plan amendment，但 Git history 在 `0365f79` / `6040630` 前没有单独 committed 的 Candidate-B plan-amendment 文档。不要假装这个历史审批文档存在，也不要回写/倒签。ADR/evidence 记录的是 native campaign 后已经接受的实际决策。Tasks 2-5 不允许重复这个流程缺口。

`DOCUMENT-STATUS.md` 也不要现在更新。当前 Slice 2 plan 明确要求它只在整个 Slice 2 acceptance 时更新，因此 S2.0 interim 状态以 handoff + ADR + evidence report 为准。

你现在的第一个任务不是实现，而是 amend：

```text
docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md
```

保留五个 formal task 的 outer shape：

```text
Task 1 — S2.0 Runner Supervision PoC + ADR                 COMPLETE
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

请基于当前 accepted Slice 1 implementation 和当前 `src/labpilot/**` 实际结构，为 Tasks 2-5 补到 execution precision：exact files、interfaces、ownership boundaries、必要 migrations、tests、acceptance commands、review/stop gates。不要因为 PoC 代码现成就默认让 production runtime 依赖 `tests/poc/s2_runner/**`。

Task 2 必须仍然是 preparation-only，不启动 physical process：

```text
approved ExperimentVersion
-> clean source identity
-> exact materialized Attempt source
-> verified source manifest
-> Trial identity
-> non-launchable Attempt preparation
-> versioned frozen execution spec
-> launch-ready boundary
```

Task 3 才首次进入 production physical side effect；Task 4 才拥有 DB expected state + physical observation 的 authoritative reconciliation；Task 5 做真实 recovery/fault-injection acceptance。

不要引入 generic Runner backend registry、plugin framework、SSH/remote runner、multi-host scheduler、worker pool、Runner DB/receipt ledger、generic shell/exec protocol、Docker/systemd privileged socket exposure，除非上位 authority 新增了明确需求并经过单独 review。

如果文档、代码、Git history 之间有冲突：先列出 exact file/commit/field 和冲突内容，停止实施，不要自己静默重解释架构。

你的第一轮回复只做三件事：

1. 用自己的话复述当前 Slice 2 状态与冻结 invariant；
2. 列出你发现的任何真实冲突/缺口；
3. 如果没有 blocker，给出 Tasks 2-5 plan amendment 的具体修改方案和拟修改文件清单。

在 plan amendment review/approval 前，不修改 production `src/labpilot/**`、migrations、API routes 或 runtime topology。

---
