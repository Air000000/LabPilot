# LabPilot：冻结 Trial 事实视图与来源展示（最小方案）

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

- 日期：2026-10-08（Asia/Shanghai）
- 初始状态（2026-10-08）：**PROPOSED / 作者自审完成 / IMPLEMENTATION NOT STARTED**。
- 2026-10-10 记录：窄实施及离线收口当时已完成，集成、部署及新的真实复验当时尚未完成。原收口保留于开发档案。后续 2026-10-12 复验见[公开 Trial 事实摘要](../evidence/research-runtime-preview.md#history-comparison-and-trial-facts)。
- 基线：clean main `93281b06773e3673bd42475624f4d336fe347f7d`，interview head 0015。
- 分支：`planning/trial-facts-view`。主仓库和已部署运行逻辑保持。
- 用户“好的，继续”承接四案后的窄方案规划；不授予新的付费样本、审批或训练。
- 服从[V1 Scope](../../labpilot/01-v1-scope-and-acceptance-v0.3.md)及
  [Architecture 10](../../labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md)。
- 本文件合并设计、实施范围与验收清单，不再创建通用框架或多个重复计划文件。

## 1. 事实与目标

[公开四案观察摘要](../evidence/research-runtime-preview.md#agent-output-reliability)对应的历史记录中，
12次调用、121,944 tokens：开放探索和STOP通过；显式参数通过但漏读train.py；人工输入
路径正确但历史Trial计数13应为12，并错误声称“无重复测量”。四案均合法结构化输出，
未触发展示降级或纠错。这证明结构校验不等于事实校验，不能归因于预算不足。

需要解决的第一件事是权威事实的生产与呈现，而不是再增加模型行为类别。
数量来自持久化Trial和选定Summary修订；模型负责建议与解释，不能成为数量的权威来源。
先做一个小型TrialFactView和可点击的来源，避免将统计判定、自然语言真假判断一并纳入。

明确三个概念：不同Trial ID是不同科研记录；相同参数/seed可以在不同Trial中再次出现；
独立统计重复需要实验设计支持，不能用Trial数、Attempt数或distinct seed数直接推导。
既有实证同条件在两个Batch各有Trial/Attempt/Metric，这不等于两次独立多seed重复。

## 2. 成熟方案的借鉴范围

| 一手资料（2026-10-08核对） | 借鉴点 | LabPilot取舍 |
|---|---|---|
| [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) | Run元数据记录参数/指标/产物，查询已有记录 | 从权威记录计算数量；不将MLflow Run替换成LabPilot Trial/Attempt，也不导入MLflow |
| [PaperQA2](https://github.com/Future-House/paper-qa) | 有来源的证据与文内引用 | UI事实旁提供真实Summary修订链接；不引入RAG、向量库或LLM引用评审 |
| [OpenHands persistence](https://docs.openhands.dev/sdk/guides/convo-persistence) | 持久化事件区分工具调用/观测，支持回读 | 复用AgentStep/ToolCall，工具返回是已观测证据；不将会话状态变成科学权威 |
| [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output) | schema错误及格式反馈属于结构化输出处理 | 维持现有一次有界纠错；本次合法JSON内的数字错误不冒充schema错误 |

这里是从各项目职责作出的设计推论，不声称它们解决了自然语言事实正确性。
用户早期Claude Code研究总览也区分事实来源等级，且社区恢复源码不是官方逐行实现；
只作历史理解背景。本次不为这个问题重新研究整套Claude/Codex/Hermes扩展机制。
Temporal/STORM与本次小型事实读模型没有新增必要职责，不引入依赖或工作流引擎。

## 3. 第一版做什么

**TrialFactView是读模型，不是新领域实体或LLM Decision。** 仅包含：

- 当前source Batch：batch_id、精确summary_revision_id、Trial总数、selected-valid数、excluded数、distinct seed数。
- 每个实际纳入的历史Batch：相同字段及其精确Summary引用。
- included_history：上述历史范围的联合计数，显式来源列表。
- source_and_included_history：当前与纳入历史的联合计数，显式来源列表。
- 固定profile=`trial-facts-v1`；覆盖范围沿用HistoryContext的coverage_limited/omissions/cutoff。

所有计数按唯一Trial ID；一个Trial有多条Metric或多个Attempt，不增加Trial数量。
同一ID重复或跨Batch出现不是“去重成功”，而是绑定/覆盖错误：拒绝构造，防止掩盖损坏。
不同Trial有相同条件仍分别计数，不自动合并科研记录。
included_history为空时是0，旧Run没有事实视图时是“未冻结”，两者不可混淆。

真实固定证据的预期值：

| 范围 | Trials | selected-valid（各自协议下） | excluded | distinct seeds |
|---|---:|---:|---:|---:|
| 当前Batch | 6 | 2 | 4 | 1 |
| 纳入历史（8+4） | 12 | 9 | 3 | 1 |
| 当前+纳入历史 | 18 | 11 | 7 | 1 |

不增加metric均值/全局最佳排名，不把不同协议的selected-valid升级为同一阈值的结论。
distinct seeds只是观测到的不同seed值数量，不是有效样本量或独立重复次数。
联合范围的distinct seed必须从实际Trial记录取集合并集，不能相加各Batch的seed数量。
逐Batch事实与来源列表必须恰好覆盖最终保留的Batch/精确Summary配对；禁止混入
已omitted的候选、latest Summary或额外范围。新capture必须包含完整事实视图，
只有历史旧capture允许缺省None，不把新捕获失败静默当作旧格式。
不在第一版推断“没有独立测试集”“没有重复测量”或统计显著性：缺少完整资料时仍未知。

## 4. 捕获、编译与回放

1. 新Follow-up的create_bound_run既有UoW加载source Batch/Version/Summary，并已有冻结历史。
   在同一事务内用完整Trial覆盖和Summary入选/排除记录计算事实，再add Run并commit。
   只使用确切Summary ID，不查询latest来填补事实，不加载外部dataset或新增文件权限。
2. 放入现有history_context_json的可选`trial_facts`字段，不增表、列或migration。DTO为
   strict/frozen/extra-forbid；旧字段缺省None且序列化exclude_if=None，旧body/hash保持。
   顶层history-context-v1仅作可选扩展，trial_facts自带固定profile；不回写旧Run。
3. 当前32KiB历史总上限不放宽。每次候选/最终裁剪都基于实际保留的batches重新算事实；
   字节检查必须包含新字段。超过上限仍按既有顺序产生omission，再重算；不能保留删前总数。
   新metadata可能让临界历史更早被裁剪，必须记录为coverage limited，不能伪称完全相同覆盖。
4. 新捕获的experiment.read_history工具版本为v2；旧capture无trial_facts仍为v1。
   工具仍接受{}、green/read_only、不需approval，返回冻结对象。新版本/result hash明确区分。
5. ContextCompiler新增一个可选trial_facts参数，仅新profile生效。首次只把source事实加入
   result_context；历史/联合事实必须实际read_history返回后才随prior_tool_results进入请求。
   不因UI可展示冻结历史，就在第一次模型请求偷偷提前发送历史事实。
6. validate_history_binding核对profile、source IDs、按ResultContext重新算source事实、历史
   Batch事实、联合范围与覆盖字段。损坏时在模型调用前fail closed，不当作缺省或纠错资格。
   新事实进入现有canonical_request_hash和工具契约hash；Decision输出schema不变。
7. 同一Run的纠错继续使用同一capture/profile。旧Run无trial_facts走原编译路径，保持原
   messages/tool v1/hash；不能通过全局修改system policy使旧纠错漂移。reserved/unknown
   仍不重发，现有预算、一次纠错、CAS和invalid-repeat guard保持。
8. Trace已有HistoryContext，直接读取捕获的trial_facts；不增endpoint，不在GET查询latest
   或重建旧事实。旧Trace、旧P1（history_context=None）和普通AgentRun都可回放。

模型策略只增加新profile事实来源的说明，结构化数据才是改进本体；不增加逐错prompt补丁。
编译路径/sourceFact再算只是核验已绑定不可变记录，不是从运行中的全库重新取历史。

## 5. 可核验来源与用户界面

Operator在Follow-up记录中显示“绑定证据中的实验事实”：三个明确范围及每个历史Batch。
来源链接由服务器/页面从已验证UUID构造`/ops/summary-revisions/{revision_id}`，复用现有
修订详情（含Trial来源）；不是让模型输出URL或任意宿主机路径。链接只定位已捕获修订，
不自动选择最新Revision。已有ToolCall ID和步骤仍用于查看实际读了哪些资料。

**不增加模型evidence_refs字段或新引用registry。** 第一版由代码给事实附来源；模型的
summary/question仍原样持久化，页面放在“Agent建议/问题”，保留审计与事实复核。
不覆写旧解释、不将错误13改成12后假称模型原来正确，也不正则扫描数字或强行删句子。
没有trial_facts的旧Run提示未冻结该视图，并保留已有source Summary链接，不填0或重算。

这能保证事实栏的值和来源正确，不能保证自由文本所有断言正确。即使将来增加模型引用，
引用存在也不能证明被引证据支持该断言。LQ-COUNT-001的模型质量部分和
LQ-REPEAT-CLAIM-001不能仅凭UI标签或新字段宣告CLOSED。

## 6. 证据读取规则的边界

本次漏读train.py仍为LQ-EVIDENCE-READ-001 OPEN，不追改原案例标准。
第一版不增加“固定文件必须读”的运行时硬编码，更不解析用户自然语言构造必达工具图。
可信已批准契约、冻结源码和用户明确要求的资料各有作用：Verifier验证可执行契约；
评测记录实际工具读取。后续若确需强制来源声明，须单独规定必需证据而非任意文件名。
单纯打卡read_text不证明读懂或已获得所需事实；对已有可信上下文重复读文件也非必要条件。

## 7. 最小实施范围（待批准）

| 文件 | 具体职责 |
|---|---|
| agent_runtime/trial_facts.py（唯一新增产品模块） | 小型固定DTO、Trial覆盖/范围校验和纯计数函数；无registry/策略插件/新Protocol |
| agent_runtime/result_history.py | optional字段、绑定验证、v1/v2工具分支和旧序列化保持 |
| api/result_aware_agent_adapter.py | 既有UoW捕获；每次裁剪重算，纳入32KiB检查 |
| agent_runtime/context_compiler.py | optional输入与只发送当前可见范围，新profile条件分支 |
| agent_runtime/service.py | 传入已核验capture给两个compile路径；不改状态机 |
| api/operator.html | 小型事实表、现有Summary链接、旧视图缺省；不重塑交互流 |

API schemas若现有HistoryContext自动承载新字段，无须修改。保留Proposal完整Payload；
不顺手增加最小变更DTO、目标解析、引用judge、检索引擎、新执行权限或新模型调用。
建议两阶段本地提交：`feat(agent): freeze scoped trial facts`（含必要回归），
`feat(operator): show pinned experiment facts`（展示/验收/收口）；不预填实现SHA。

## 8. 如何验收

以下均为**待实施验收**，不是当前PASS：

| 检查 | 必须通过的判据 |
|---|---|
| 三个范围/逐Batch | 真实固定fixture为6/12/18，selected为2/9/11，excluded为4/3/7，seed distinct=1 |
| 计数单位 | 多Metric/多Attempt不乘Trial；同条件不同Trial不合并；重复ID/交叉归属拒绝 |
| 空/未知 | 空历史=0；旧未捕获=NULL；全excluded合法；unknown outcome/缺覆盖拒绝 |
| 历史裁剪/排序 | 中途/最终byte omission后重算实际范围；Trial行输入乱序不改规范hash；历史Batch候选优先级仍保留；保留ID/数值精度 |
| source绑定 | 错误Summary/Batch、source与历史重叠、篡改count/profile或非整数拒绝 |
| 事务与freeze | SQL证明创建与capture原子；后来S2/新Batch不改旧Run facts；失败不留下半Run |
| 模型可见性 | 首次请求无历史/联合事实；read_history后才出现；普通/旧Run消息和hash保持 |
| 旧/新回放 | 旧JSON不插入新数字；oldNone可以回放；v2工具有独立契约hash；新事实不回填 |
| 0015恢复 | 一次纠错仍同hash/预算；反馈后恢复/预留后unknown不重发；旧保留反馈Run仍原路径 |
| UI与引用 | 6/12/18来自capture；UUID对应正确summary详情；旧Run不填0；模型13原样保留且与事实分开 |
| 既有科学边界 | P1 two-iteration/P1-C、Verification/Approval、invalid-repeat、Result Plane仍通过 |
| 全量门禁 | ./scripts/check_ci.sh；逐项记录Ruff/mypy/unit+architecture/integration/dependency guard与实际数 |

用现有pytest/SQL集成、纯函数单元与一次真实browser/host只读检查；不建评测平台。
SQL只使用隔离测试库；host部署独立授权后停止写入/确认无在途，再重载服务，保持0015
和既有演示数据。旧Runtime不支持新optional JSON字段的前向读取，可能extra-forbid失败；
不删除新capture去回退，不能把新未完成Run切回旧Runtime，降级路径需先明确。

模型质量另需一次新的固定案例和预算/出境授权：自然语言仍可能说13；必须如实保留。
只有事实栏正确不能关闭整体语义可靠性，不能把四案预算剩余当成新样本授权。

## 9. 本轮实际检查与自审

只读外置probe验证上述真实6/12/18与2/9/11、4/3/7；多Metric不增Trial，重复ID拒绝，
空/全excluded集合与遗漏范围正确，不产生independent_replicate_count；Run count前后36。
这是公式可行性probe，**不是DTO持久化、新hash、SQL原子性或UI已实现验收**。
现有ResultContext/ResultHistory/ContextCompiler unit：**62 passed / 1 warning / 0.47s**；
warning为既有负向模型序列化提示。零Provider、Run/Proposal/Verification/训练增量，
无DB migration、服务重启、main merge或push。probe在
`local-workspace/labpilot-trial-facts-planning-20261008/probe.py`，非产品模块。
作者自审原始记录保留于开发档案，未使用其他agent，
不冒充独立APPROVE。已有full CI仍绑定68bc994；本轮文档/公式没有重跑全量gate。

明确延后：通用claims/LLM引用schema、自然语言真假检测、必达读取workflow、统计显著性、
Proposal变更DTO、Compaction/Projection、Multi-Agent/Memory/MCP/Plugin/distributed scheduler。
Slice2 production hardening仍deferred；不声称global exactly-once或自主科研。
运行契约变更（新capture字段/tool版本/compiler profile）需要按本具体方案批准后实施。
