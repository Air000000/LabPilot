# P2-B2：基于人工已采纳源码的跨源码 Follow-up（设计草案）

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

**后续状态：窄实施已于2026-10-16集成本地main，interview 0017部署验收通过，未推送。**
见[主线与部署验收](../evidence/research-runtime-preview.md)。
见[工程收口](../evidence/research-runtime-preview.md)。
以下PROPOSED/NOT RUN及权限描述保留规划时点，实际验收以收口为准。

2026-10-15（Asia/Shanghai）。PROPOSED / AUTHOR SELF-REVIEW / IMPLEMENTATION NOT STARTED。
基线main `71d881076f4a0ea812e2fa4d1f70c194b5dab8f0`；B1已集成、interview 0016已验收。
本次“继续”推进下一阶段独立规划；不实施代码/迁移、不调用provider、不写演示业务对象或重启服务。
规划分支`planning/p2-b2-cross-source-follow-up`；main及现有部署保持。

## 1. 先解决哪一个问题

B1已经支持人工固定补丁→静态候选→人工Git提交S2→Snapshot→SourceAdoption→人工R2/V2。
若需求只是展示可追踪、受控执行和人工源码采纳，现有范围已足够；不必为求职项目搭完整编码Agent。
继续迭代的首个实际缺口是：人已接受S2，但Agent仍只能在S1源码上根据S1结果生成Follow-up。

本次B2先交付“读取S1已验证结果，检查人已采纳的S2，为同Experiment生成S2 Proposal”。
原先B2同时包含的Agent写补丁拆为之后独立切片，动态候选执行/可信judge也不并入。
这是交付顺序的明确收窄，不把跨源码提案说成已实现受控编码Agent。

```text
S1 / V1 / Batch / 确切Summary ── 旧结果证据 ──┐
                                           ├→ 新AgentRun → STOP / 人工输入 / S2的R2
S1 → 人已采纳的SourceAdoption → S2 ─ 目标源码 ┘                       ↓
                                                       现有Verification → 人审批 → V2
```

旧结果始终属于产生它的S1；Repository工具和新Proposal目标始终为S2。
只有人显式选择确切SourceAdoption且服务验证完整绑定后，才允许这两者不同。

## 2. 成熟方案对照（2026-10-15核对一手资料）

| 来源 | 已确认做法 | 本项目取舍（设计推论） |
|---|---|---|
| [W3C PROV-DM role](https://www.w3.org/TR/2013/REC-prov-dm-20130430/#term-attribute-role) | 同一实体在不同活动中可有不同role；来源关系与使用角色显式区分 | 明确结果来源/提案目标角色和精确身份；不引入RDF、知识图谱库或通用provenance服务 |
| [MLflow data](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.data.html) | 记录dataset身份、source和输入context；实际数据可能经过转换而不同于原source | 保留各版来源和角色，不把旧metrics改标成新源码结果；不采用MLflow依赖或重做现有Result Plane |
| [OpenHands security](https://docs.openhands.dev/sdk/guides/security) | 风险分析与确认策略职责不同，动作检查不是sandbox | 由服务校验SourceAdoption和目标绑定，不靠prompt/模型自报放行；仍不执行候选或新增写工具 |

这些资料支持职责与身份分离，不直接规定LabPilot表结构或科学比较语义；下面的设计由本仓库约束导出。

## 3. 实际代码约束与作者自审

| 已核对的一手代码 | 设计影响 |
|---|---|
| [create_bound_run](../../../src/labpilot/api/result_aware_agent_adapter.py)核对Summary最新revision、同Project、clean及Run源码=源Version源码 | 无adoption的旧请求保持同源码；有adoption仅增加受验证的窄分支，不能删除source_snapshot_mismatch |
| [AgentRunRow](../../../src/labpilot/agent_runtime/models.py)已有target repository_snapshot_id、source_summary和history_context | 拟只新增nullable source_adoption_id FK，不另造Run、workflow或完整JSON事实副本 |
| [History binding](../../../src/labpilot/agent_runtime/result_history.py)按源Summary/Experiment/版本/TrialFacts校验 | 继续以S1来源冻结有界历史，不改为S2的latest、不重算历史或改变计数范围 |
| [Repository tools](../../../src/labpilot/tools_policy/repository_tools.py)闭包绑定一个Snapshot | 保留四个只读源码工具并绑定S2；不给模型任意Snapshot/path选择器 |
| [ContextCompiler](../../../src/labpilot/agent_runtime/context_compiler.py)注入单个snapshot和结果证据，消息有总byte上限 | 单个snapshot明确为提案目标；增量source_binding区分S1/S2，旧null模式不加新prompt字段 |
| [materialize_proposal](../../../src/labpilot/agent_runtime/service.py)要求Proposal Snapshot ID=Run目标，按源Experiment落库 | 保留该guard并核对identity；新提案仍在同Experiment，不把S2当新Experiment |
| 同文件_repeats_only_invalid_trials只比较参数+seed | 跨源码后相同参数不是原样科研条件；只对已验证的S1→S2模式避免误拦，同源码规则保持 |
| [follow-up revision](../../../src/labpilot/experiments/proposal_service.py)以origin AgentStep幂等、再验证payload source | 复用持久化决策/提案重放，不增加新的多阶段事务；Verification/Approval保持独立 |

旧参数guard目前不支持源码差异，并不构成一个已经放行的跨源码产品故障：当前create_bound_run先拒绝这种Run。
纯函数探针证实相同参数/seed在S1或S2都会返回重复；改变参数返回不重复。
因此不能只改入口guard然后宣称整个闭环已支持跨源码，也不能为绕过guard强迫模型修改epochs。

## 4. 最小持久化与请求

拟在既有CreateFollowUpAgentRunRequest增加可选source_adoption_id；现有repository_snapshot_id继续表示目标。
AgentRun持久化同名nullable FK（RESTRICT），旧行默认NULL且不回填；不新增第二份来源Snapshot列，
来源Snapshot从确切Summary→Version取得，目标身份从Run Snapshot与adoption取得。
DB check：adoption非NULL必须同时具有source_summary；跨对象归属由同事务服务校验。
新迁移号在实施时重核head，当前仅预计0017，规划不生成迁移。

无adoption：完整保留当前同源码契约。有adoption：只允许真正不同的S1/S2，
必须满足同Project、active Project/primary registration、clean、精确Snapshot绑定，
adoption.base = 源Version Snapshot，adoption.target = Run Snapshot。
不能只凭“是S1的Git后代”、任意S2 ID、模型提供diff或一段人工自然语言放行。

采用一个已采纳的直接S1→S2关系，不解析多跳S1→S2→S3、不追latest或自动挑另一条adoption。
创建时保留既有“该Batch最新Summary”资格检查；创建后仍pin该revision，后续新Summary/采纳不覆盖Run。
开始新模型调用前如Project不再active、registration被撤销或不再为当前primary，明确失败且不换绑定；
这不会撤销旧采纳/旧版本。已持久化PROPOSE的重放只验证原不可变归属与目标，后续正式Approval仍核对当前source readiness。
该POST沿用现有每次创建新Run的契约，不承诺请求级exactly-once；不顺便重构所有Agent创建幂等。
服务从人请求取得选择；AgentDecision/tool arguments无权新增或改写该字段，旧Run不能就地换目标。

## 5. 上下文与证据读取

在创建Run时及每次模型调用前，解析固定source_adoption_id，核对其Project、base/target与namespaced identity。
SourceAdoption是先前人选择的事实，不重新调用adopt，也不要求旧报告具有当前checker fingerprint。
从B1冻结的package/CAS refs读取并校验canonical package/request/report/diff，生成小型类型化source_binding输入：

- role=result_source：S1 ID/identity及来源Version/Summary IDs；
- role=proposal_target：S2 ID/identity；
- SourceAdoption ID/identity、精确package/diff/report refs、实际diff、原rationale及原checker身份；
- 报告static通过的既有含义，以及runtime/scientific/human approval仍not_run。

不依赖历史candidate工作目录、可变worktree或当前checker重新评估；历史包的CAS校验是证据完整性检查，
不是再次判为静态通过。新读模型可复用现有ContentAddressedStore与Pydantic记录类型，
不为增加只读reader而改动P2-A checker主体/fingerprint，也不新增索引、registry、缓存或有状态worker。
沿用ResultContextSource/SQL适配器的依赖边界接入只读解析，不让Agent Runtime直接查询context表，
不新建一个仅为该实现服务的通用接口体系。
读取的预期ref必须由已绑定PG事实确定；缺blob、canonical/身份错误就拒绝，不能临时submit/prepare修复。

拟定source_binding canonical输入总量<=48KiB，其中diff<=32KiB；超过明确拒绝，不截断后继续。
这是初始有界profile，实施时用固定案例确认大小；不承诺所有合法64KiB patch都能进入本切片。
保留ResultContext与最终消息总byte限额；source_binding计入canonical_request_hash，不能通过多个小字段绕过总上限。
模型继续最多读取当前绑定源码和有界历史；不额外放入S1/S2全仓库、raw logs或新的检索系统。

diff/rationale/仓库文字是数据，不升级成system权限；上下文说明明确来源角色：
S1 accuracy不能证明S2达标，SourceAdoption也不证明变更有收益或评估诚实。
“当前源Summary没有S2测量”不等于“S2从未运行”；有界历史若含S2记录仍保留其确切版本与协议。
TrialFactView仍按已命名的S1/历史范围计数，不把同参数但不同源码的Trial视作同一科研条件或独立重复。

## 6. 提案 guard、恢复与旧数据

所有模式：Proposal目标必须等于Run的确切目标ID/identity；错误目标在生成正式revision前拒绝，
follow_up_proposal_snapshot_mismatch保留，不允许模型把Proposal偷偷换回S1或其他S3。
adoption模式必须再次验证PG绑定，不能用一个布尔开关证明跨源码资格。
源Experiment仍从已pin的S1 ResultContext取得；create_follow_up_agent_revision保持origin_step幂等。

无adoption的同源码Follow-up：现有重复invalid参数guard及prompt原样保持，包括允许包含新参数的方案。
已验证的S1→S2 Follow-up：S1无效参数相同不构成原样重复，允许作为新源码的控制条件，
不能据此称为已改进、重复测量或科学有效。无需在本切片搭通用实验条件相似度/可比性引擎。
新mode的policy须明确说明上述规则，不能一边允许proposal一边继续要求模型不得用旧参数。

沿用step reservation、persisted Decision、CAS/row_version、token/wall-clock及provider unknown fail-closed。
创建时证据错误不插Run；已存在Run在新模型调用前缺证据时持久化明确failure reason、零新provider调用，
不做自动重采样/换adoption/回退源码。已持久化PROPOSE的恢复只需验证不可变PG绑定和目标，
继续原origin_step落库重放，不为重新生成上下文或缺CAS而再调用模型。

旧NULL Run不注入source_binding或新prompt键，保留原请求hash/纠错上下文资格、旧history和等待人工输入。
API/Trace拟增加可选adoption及来源/目标身份以供审阅；加字段不等于旧HTTP JSON字节完全相同，
验收保证旧字段、资格和内容可读，不宣称全量响应字节不变。
API错误仅使用固定reason，不回显CAS原文、源码、secret或任意异常文本。
迁移downgrade若有绑定Run应拒绝破坏性移除；不销毁新Run或回写NULL来获得降级成功。

## 7. 允许与排除范围

允许的拟议增量：一列FK/约束及迁移，现有Follow-up API可选字段、Run DTO/trace、
SQL绑定适配器/只读CAS evidence reader、条件化ContextCompiler与精确提案guard、小型回归。
复用四种Decision、四个仓库工具及既有history工具；不增加decision/tool类别或自动权限。
先通过API/trace完成端到端；Operator新表单/候选列表/采纳按钮不是首版必需，不另造Web模块。

不生成补丁、不写Git/commit/Snapshot/SourceAdoption，不自动approve/start Batch，不引入sandbox、
动态judge、Multi-Agent/Memory/MCP/Plugin/Compaction/Projection或分布式调度，不修复所有旧自由文本PARTIAL。
Slice2 production hardening仍deferred；不声称global exactly-once或自主科研。

## 8. 分阶段实施与验收（新功能全部NOT RUN）

1. 独立审阅本草案的跨源码资格、一个nullable FK与API范围，确认首个固定S1→S2案例。
2. 先写资格/legacy回归与迁移，再贯通CAS reader、source_binding及目标tools；全部ScriptedProvider。
3. 验证STOP、人工输入/恢复、S2 PROPOSE与提案重放；正式Verifier通过后在隔离库中显式人审批。
4. focused + 全量CI；主线旧P1/P1-C/B1、provider unknown/预算/边界/历史记录保持。
5. 如需host acceptance，仅新隔离科研repo/DB；main、interview0016及真实demo不被新实现自动修改。
6. live provider案例另定目标、数据发送、调用/token/时间预算，不能沿用已耗尽的早期授权。

| 必验场景 | 观察标准 |
|---|---|
| 无adoption+跨源码 / 假adoption / 错Project或base/target/identity | 写Run前拒绝；默认source_snapshot_mismatch继续有效 |
| 已采纳S1→S2+确切S1 Summary | Run目标/tools=S2；ResultContext/源TrialFacts=S1；同Experiment |
| S1 invalid参数原样用于S2 | 可生成S2未批准Proposal；同源码重复仍拒绝，含新条件旧规则仍通过 |
| payload改回S1或改为S3/identity不符 | 提案物化前拒绝，无新revision |
| source/result报告/rationale/diff有prompt injection | 不升级权限，Decision/toolset固定；不能据此宣布prompt injection已全面解决 |
| CAS缺失/超限/变造、错误binding | 创建前拒绝或已存Run明确失败；不替换ref/重新调用模型 |
| 历史checker/工作目录已清理 | 依确切PG+CAS可读；不用当前checker追认，不依赖临时目录 |
| 后续新Summary/新adoption/primary状态变化 | 原Run不换pin或target；新Run按当前资格核对；源码不可用不回退worktree |
| STOP / 人工输入 / 同步预算耗尽 / unknown | 原生命周期与persist-before-execute保持，无额外权限或blind retry |
| PROPOSE后进程崩溃/重复advance | 一份origin_step revision，恢复不重新模型调用；人审批仍独立 |
| 源S1已达阈值但目标是检查S2、历史有S2或历史省略 | 不把S1结果改标S2；不把有界缺失当作从未运行，判断与用户目标一致 |
| 旧NULL Run / 旧人工等待 / 旧纠错step / schema迁移 | NULL保持、旧请求语义及字段可读，不回填或自动推进 |
| 完整离线two-iteration跨源码链 | S1可信Summary→adoption→S2 Run→R2→Verify→显式Approval→V2；不训练 |

## 9. 本次实际工作与自审结论

已核对main71d8810及clean状态，B1字段/采纳身份、Follow-up入口/物化、History绑定和工具源码。
现有纯函数探针结果（不是新增模块验收）位于
`/home/air/.local/state/labpilot-evidence/p2-b2-planning-20261015/probes.json`，
helper为`local-workspace/p2a-static/b2-probe.py`；首次写入因父目录权限失败，
只新建本次air拥有的私有子目录后记录成功，没有更改旧证据/服务权限或产品代码。

已按一手资料自审：拒绝把来源和目标混成一个snapshot、拒绝单删guard、避免重复参数误拦，
只选一个adoption FK和有界只读证据，避免对工作目录/最新checker造成恢复依赖。
这是作者设计复审，不能冒充独立APPROVE、实现PASS、模型质量或科学验收。
本次无src/scripts/tests/migrations/dependency修改、DB连接、provider调用、候选执行、
服务重启、科研Git操作、main merge或push；没有活动implementation tranche。
