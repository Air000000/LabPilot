# P2-B3：Agent 补丁草稿生成（独立规划）

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

**2026-10-17 实施记录。** 窄实现已集成本地 main，interview 0018 部署验收通过，该次集成未推送。原主线与部署验收及实施收口保留于开发档案。见[公开补丁草稿摘要](../evidence/research-runtime-preview.md#static-candidates-and-patch-drafts)。
下方PROPOSED/IMPLEMENTATION NOT STARTED及NOT RUN保留原规划时点，不代替后续实际证据。


2026-10-16（Asia/Shanghai）。PROPOSED / AUTHOR SELF-REVIEW / IMPLEMENTATION NOT STARTED。
基线 main `e25edcac3b5b3f345729409f840f961d59313c3b`，interview 0017 已部署。
本次“继续”仅推进下一窄切片规划；不实施新决策、迁移或部署，不创建演示业务对象或调用 Provider。
规划分支 `planning/p2-b3-agent-source-patch`；main 与已验收历史保持。

## 1. 为什么先停在草稿

B1 已支持人选择静态候选并采纳 S2，B2 已支持 S1 结果 + 已采纳 S2 → S2 实验提案。
若目的只是展示可追溯实验与人工源码采纳，这些已足够；求职项目不必继续搭通用编码 Agent。
若确实要让模型参与改代码，当前缺口是模型没有一种被持久化、明确不具批准权限的源码变更输出。
首版只补“提出并导出未检查的补丁草稿”，复用现有 AgentRun/Step，不直接操作候选工作目录。

```text
人显式选择草稿模式 + exact Snapshot（可选同源码结果）
→ 既有有界 AgentRun：只读工具 / 人工输入 / STOP / source_patch
→ 持久化 SourcePatchDraft，Run completed，DRAFT_UNCHECKED
→ 人审阅并只读导出精确 patch 字节
→ 人显式调用现有 P2-A submit / prepare / inspect
→ 人提交 Git / capture S2 / B1 adopt
→ B2 Follow-up → Verification → 人审批 → V2
```

后半段是已有人工衔接路径，本切片不把它们自动串成后台工作流。
SourcePatchDraft ≠ SourceChangeRequest ≠ CandidateSource ≠ SourceAdoption ≠ Experiment Approval。
“生成成功”仅指输出契约校验与持久化完成，不代表补丁可应用、静态通过、运行安全或科研改进。

## 2. 成熟方案与本项目取舍

以下为 2026-10-16 核对的一手资料；项目取舍是本仓库设计推论，不是行业强制标准。

| 来源 | 已确认做法 | 取舍 |
|---|---|---|
| [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 优先简单方案，固定子任务可以使用可预测工作流 | 复用一个 Runtime，不增加第二 Agent、自动修复循环或新框架；该文为基础设计原则，不视为当前工具版本规范 |
| [Aider edit formats](https://aider.chat/docs/more/edit-formats.html) | 支持 whole、search/replace、简化 unified diff，不同模型适配不同格式 | 首版使用现有 P2-A 已接受的标准 Git patch 文本，不实现多格式编辑器；Aider 的 udiff 不是本项目可直接接受的标准 Git patch |
| [Aider chat modes](https://aider.chat/docs/usage/modes.html) | ask 与 code 有明确行为区别，architect/editor 会增加调用 | 人显式选择草稿模式，不能靠 goal 关键字开放能力；不照搬双模型架构 |
| [OpenHands security](https://docs.openhands.dev/sdk/guides/security) | 动作确认与风险分析有不同职责，动作检查不替代 sandbox | 草稿没有执行入口，后续静态通过与人采纳仍不证明行为安全；不依赖模型自报风险放行 |

第一版直接要求 Git patch 可能有 hunk 格式错误，这是已接受的试验风险，不宣称该格式质量最优。
先用固定案例测量；只有实测格式问题阻碍收益时，再独立比较结构化编辑块或函数体替换。
本规划不实现格式 fallback，不自动重采样，也不把格式失败归咎于实验参数。

## 3. 已核对的实现约束

| 当前代码 | 约束 |
|---|---|
| `agent_runtime/types.py` | PROPOSE 载荷是 ProposalPayload，不能用 summary 或 Proposal 字段偷塞源码补丁 |
| `context_compiler.py` / `service.py` | 编译 schema、运行时 TypeAdapter、纠错和安全字段位置目前围绕同一旧决策集合，新增模式必须一起核对 |
| `service.py` finalize_model_step | 决策与 Run 状态在既有落库路径完成；响应预算判断先于成功终态，不能收到补丁就绕过 max_tokens |
| `main.py` tool factory | 四个 Snapshot 仓库工具，Follow-up 可加冻结 history；草稿生成不需要新增写工具 |
| `context/repository_source.py` | read_text 最多 500 行/次、按完整 blob byte limit 拒绝过大文件；首个 slice 不等于整文件 |
| `source_candidates.py` / `scripts/source_candidate.py` | 现有 stdin patch、64KiB 上限、独立静态 prepare/inspect，可由人继续使用 |
| `source_candidate_check.py` | 只允许 train_model 函数体范围，静态 compile 不执行；范围通过不保证函数行为诚实或安全 |
| `source_binding.py` | B2 diff 上限32KiB、总绑定48KiB；草稿大小合规不保证以后 canonical diff 必然合规 |

只读测量当前 demo worktree train.py 为 7873 bytes / 237 lines，仅说明 profile 实用性；
它不是冻结 Snapshot 的验收证据。正式创建必须从绑定 Snapshot 读取，禁止回退当前 worktree。

## 4. 最小拟议契约

### 4.1 人选择，服务绑定

在现有普通 / Follow-up 创建请求增加 `source_patch: bool = false`（严格布尔）。
只能由已识别的人 actor 显式选择；goal、仓库文本和工具结果不能修改这个选择。
首版不允许同时选择 source_adoption_id；从 exact S1 生成候选，跨源码实验提案继续由 B2 承担。
可选结果仍必须与生成补丁的 Snapshot 同源码；保留现有 Summary/history 精确绑定和资格检查。

只拟新增一个 nullable JSONB `agent_runs.source_patch_context`，旧行 NULL、无默认回填。
服务构造严格版本化记录：profile、base snapshot identity、train.py SHA256；Snapshot ID/Project 复用 Run。
客户端不能提交任意 context JSON。profile 固定为一个 demo 范围，不新增策略 registry。
迁移号实施前重核，当前 head0017；存在非NULL行时拒绝删除该列的 downgrade，不清空旧行换取降级。

创建前核对 active Project/primary registration、clean source、同 Project 与 live source 身份。
首版 train.py 完整内容最多32KiB/500行，UTF-8，且符合现有 demo 的训练入口布局。
读取501行探针或等价完整性核对，拒绝超限，不截断冒充全文。
每次新模型调用前读取 exact source、复核 hash/资格，将完整 train.py 纳入新模式上下文及请求 hash。
继续遵守总消息256KiB、已有结果与历史限额；总量超限明确失败，不自动压缩/省略证据。

### 4.2 新模式与旧模式隔离

新模式输出集合：`tool_call | request_human_input | source_patch | stop`。
其中 `source_patch` 只含 `kind`、`patch` 与既有可选 summary，patch 为非空合法UTF-8、无NUL、最多32KiB。
不接受模型选择宿主路径、Snapshot、checker、命令、写权限或 Approval。
请求 schema 明确标准 `diff --git a/train.py b/train.py` 及目标仅 train_model 函数体。
此处只是**草稿意图**；实际路径、hunk、受保护代码与可应用性由后续 P2-A submit/prepare 验证。
错误路径/语法的草稿可以被保存为未检查文本，不可因此显示 static passed。

普通/旧 NULL Run 继续只接受原四类决策，旧编译输入和 schema hash 不增新分支。
草稿模式不接受实验 PROPOSE；普通模式不接受 source_patch。两者都不能默默切换模式。
复用原四个只读仓库工具及符合资格的冻结 history；不新增 apply_patch、shell、文件写入或训练工具。

ContextCompiler、runtime validation、summary 降级、纠错反馈的固定安全字段词汇使用同一模式契约。
不把新字段名误脱敏成未知后再猜测纠错；不得暴露原始 patch、异常输入或 secret 于诊断日志。
沿用一次有界结构纠错、已知完成/usage 资格、Run 总预算；不为不可应用或静态拒绝自动追加模型调用。

### 4.3 落库、终态与导出

source_patch 只在既有 finalize_model_step 中保存 decision_json，并原子完成 Run，
termination_reason=`source_patch_drafted`，produced_proposal_revision_id 保持 NULL。
补丁本身保存在 Step，不额外建立候选表、CAS写入或第二结果指针；hash 由保存的精确 UTF-8 字节派生。
响应预算先判断；超预算仍保留真实输出/usage，但 Run 为 budget_exhausted，不追认为已完成草稿。
STOP/人工输入照常允许，不强迫模型生成补丁，也不自动代答。

reserved step 恢复仍 outcome unknown，不补调；已完成草稿只读回放，不调用模型或静态 checker。
DB finalize 失败不在内存中宣布完成；状态不明确时按原恢复协议处理，不盲目重新采样。

拟在现有 CLI 增加只读 `export-agent-patch --agent-run-id <UUID>`。
只从 PG 查询已完成的草稿模式 Run 及其确切终态 Step，核对载荷/绑定，向 stdout 写精确 patch bytes。
资格必须同时核对终态 Step 的 decision_kind=source_patch；仅凭 termination_reason 不够，STOP可自行使用同名reason。
不使用最新 Run、当前 worktree 或另一个 Step；不进行 shell 解释，不写任意用户指定路径。
失败时 stderr 仅固定原因码，不输出 partial patch；失败/预算耗尽 Run 不作为合格草稿导出。
Trace 保留原始已持久化输出用于审计，看到该文本本身不等于获得任何执行权。

人审阅后可另行选择现有 submit/prepare；现有 SourceChangeRequest 的 patch_ref 与草稿 bytes hash 可核对。
这证明内容一致，不证明唯一模型作者或一份不可伪造的 Agent→Request 因果授权；首版不承诺该完整自动血缘。
不改变 P2-A 记录 schema/checker 指纹、不偷偷以人工 actor 代替 Agent 创建候选。
静态拒绝时保留原草稿和失败，不能反向标记为已通过；未来如果需要正式 origin_step 关系再独立评审。

## 5. 作者自审与明确取舍

| 风险 / 不合理做法 | 本版处理 |
|---|---|
| 直接给现有 Agent 加写工具，普通 Run 权限随发布升级 | 人显式 opt-in + 持久化 mode，旧NULL不变 |
| 把源码建议塞入实验 Proposal 或 summary | 新模式专用 source_patch；summary仍只作展示 |
| 模型生成后立即 prepare，再多次修复直到绿 | 只持久化草稿，P2-A由人显式触发，无隐藏调用预算 |
| 为一份草稿造新workflow/表/CAS多阶段提交 | 复用Step和同一DB finalize，无草稿文件副作用 |
| 模型读到文件局部却被称为完整源码检查 | 新profile完整有界注入并核对hash；超限拒绝 |
| 在schema里写train.py就声称已限制实际变更 | 草稿未检查；真正限制由P2-A执行，错误草稿不得显示通过 |
| 静态检查过就自动训练候选 | 无动态执行、Git提交、capture、adopt、Approval或Batch入口 |
| 补丁文本hash等于作者签名/正式因果关系 | 明确只证明bytes相同；不声称认证或唯一来源 |
| 输出cap对齐B2就保证全链都能接受 | P2-A canonical diff和B2总上下文仍独立检查，不承诺任意草稿可衔接 |

这只是作者复审，不是独立审计或已批准实施。最大未验证假设是固定模型能稳定给出有用、可应用的标准patch；
不得用离线 scripted 输出通过替代 live 质量观察，更不能用训练结果 fixture 证明科学改进。

## 6. 实施顺序与验收清单（新能力均 NOT RUN）

1. 批准本窄模式/一列JSONB/只读导出范围后，先写旧NULL、模式隔离与迁移回归。
2. 完整源码/context/profile冻结；复用运行时预算/纠错/unknown；加入终态草稿和只读导出。
3. 固定ScriptedProvider案例在隔离库执行：导出→人显式P2-A submit/prepare/inspect；不执行候选。
4. focused + `./scripts/check_ci.sh`，再独立做主线集成/部署验收；禁止自动修改真实demo。
5. 如需 live，另定固定案例、egress、calls/tokens/时限；不沿用旧案例已耗尽授权。

| 必验项 | 验收标准 |
|---|---|
| 旧NULL普通/P1/B2、旧人工等待与纠错 | 原schema/prompt/hash语义、权限和回放保持；B2仍可原样工作 |
| 普通Run收到source_patch / 草稿Run收到PROPOSE | 明确拒绝，无Proposal/候选/科研执行增量 |
| 目标绑定错/dirty/revoked/源码缺失/内容hash变化/超限 | 创建前拒绝或存量Run新调用前固定失败，不换Snapshot或省略源码 |
| 合法训练体patch | Run保存精确payload，completed/drafted；导出字节hash一致；不自动submit |
| STOP自报source_patch_drafted | 仍按STOP处理，没有source_patch终态Step就不能导出或显示草稿已生成 |
| 多字节、NUL、空白、超32KiB、额外参数 | 严格输入校验，安全诊断不回显原文；已知资格下最多一次结构纠错 |
| 无法应用、改评估代码、错文件/模式/二进制patch | 草稿仍标unchecked；人显式P2-A入口拒绝，不标成候选成功 |
| 超预算、STOP、人输入及恢复、调用期间死亡 | 沿用原状态/usage/unknown，无自动补调、无草稿冒充完成 |
| DB finalize前/后中断、重复导出 | 未提交不宣布完成；已提交可恢复/只读导出，零新provider请求 |
| 恶意源码/patch包含命令或策略文本 | 仅数据，不解释执行；工具白名单保持，不能直接接Runner或shell |
| 集成边界 | export只读；人工submit形成未批准候选；无Git/capture/adopt/Version/Batch/Trial/Attempt增量 |
| 迁移与旧记录 | NULL不回填，旧字段保持；非NULL时拒绝破坏性downgrade，不删数据“恢复” |

未来 live 最小单案建议：依据精确demo代码生成 train_model 内一个指定的数值稳定性变更，
不改评估函数/接口；记录首样本是否可应用、P2-A是否通过、目标语义是否满足，三项分别评分。
这只是待授权案例建议，预算和实际 Provider/模型仍须当时核对，本轮没有调用。

## 7. 本轮实际结果

只读核对main/已接受B2、现有CLI/Step/schema/读取与静态check边界；复用干净副工作树建立规划分支。
既有静态候选与source-binding单测 **40 passed in 6.80s**；这是旧实现回归，不是B3验收。
没有新产品实现/迁移/DB业务写入/候选执行/Provider请求/服务重启/main merge/push。
Core/B1/B2接受基线保持，当前没有活动implementation tranche；新模式等待本方案明确实施授权。
Slice2 production hardening仍deferred，不声称global exactly-once、自主科研或production规模调度。
