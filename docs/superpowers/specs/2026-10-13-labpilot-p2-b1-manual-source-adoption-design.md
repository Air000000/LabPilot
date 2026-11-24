# P2-B1：人工源码采纳与现有实验审批闭环

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

**2026-10-15 实施记录。** 窄实施已集成本地 main，interview 0016 部署验收通过，该次集成未推送。原本地集成、部署验收和工程收口保留于开发档案。见[公开人工采纳摘要](../evidence/research-runtime-preview.md#source-adoption-and-cross-source-follow-up)。
以下保留初稿的规划范围、权限时点与NOT RUN验收计划；实际结果以收口记录为准。

- 2026-10-13（Asia/Shanghai）；PROPOSED / AUTHOR SELF-REVIEW / IMPLEMENTATION NOT STARTED。
- 基线：本地main 7b63ca53e076dd254961a6664a859e6196defa6d；P2-A静态候选已集成，演示start/doctor已通过。
- 本次“继续”推进独立规划；不授权实施、迁移、业务写入、科研仓库修改、候选执行或付费调用。
- 规划分支planning/p2-b1-source-adoption；main、服务和既有demo保持。

## 1. 首个需求与交付

P2-A能给出确切候选和静态检查，却不能把该候选与正式S2 Snapshot关联。
本切片解决“人选了哪份candidate，正式代码到底是不是它，以及哪版实验用了S2”。
不以此解决模型解释PARTIAL，也不证明梯度裁剪能提高accuracy。

最小交付是一个人调用的adopt命令和一条不可静默改写的SourceAdoption事实，
将精确package、base S1和既有新Snapshot S2关联。人已通过正常Git流程提交S2，
Snapshot捕获、人工ProposalRevision、Verification与Approval继续使用已有Core服务。
P2-B1不替人写Git工作树、创建commit、捕获Snapshot、生成Proposal、批准Version或启动Batch。

原P2-B设想混合Agent写补丁、新源码采纳及跨源码跟进；先拆出人工B1证明来源闭环。
Agent生成及S1结果→S2目标源码的双源上下文留给B2；动态执行隔离/可信judge另设gate。

## 2. 代码事实及对方案的约束

| 当前实现（一手代码） | 设计后果 |
|---|---|
| [register_local_repository](../../../src/labpilot/context/repository_service.py)拒绝同Project另一个primary repository | 不能声称“另建隔离repo并直接注册为同Project”已有支持；不扩展多仓库模型或换primary |
| [CaptureRepositorySnapshot](../../../src/labpilot/context/repository_service.py)捕获当前注册root，支持同registration的多Snapshot与identity复用 | 人在原科研primary repo提交新commit，再用现有命令捕获clean S2；不是改LabPilot产品main |
| [materialize_exact_source](../../../src/labpilot/context/repository_source.py)按绑定Git对象物化S1/S2 | 原commit保留，旧V1可继续物化；不拿当前HEAD冒充旧源码 |
| [create_manual_revision](../../../src/labpilot/experiments/proposal_service.py)接受同Experiment中另一绑定Snapshot；不是幂等接口 | 后续R2可以人工指向S2；adopt不调用它，不能承诺重复adopt只产生一份自动Proposal |
| [approve_revision](../../../src/labpilot/experiments/proposal_service.py)重查live source readiness及精确verification绑定 | 采纳事实不代替实验批准；当前root必须对齐S2才能走新Approval |
| [Result-aware adapter](../../../src/labpilot/api/result_aware_agent_adapter.py)要求Run source与结果Version source一致；[Agent service](../../../src/labpilot/agent_runtime/service.py)保留proposal snapshot guard | 不把绑定S1结果的Follow-up Run偷偷重绑S2；本切片只走人工提案 |
| [execute_idempotent_command](../../../src/labpilot/db/idempotency.py)先查已提交receipt，再只读prepare，operation/receipt同事务 | 只读核对+一条PG事实可直接复用，不新造持久化workflow或外部副作用恢复平台 |

## 3. 严格的采纳含义

SourceAdoption表示人明确接受候选与S2的内容匹配关系，不证明commit由工具生成或AI写出。
Git parent仅说明历史祖先，不能单独证明采用了哪份candidate及哪个checker报告。
一个模糊commit message或RuntimeEvent不是本切片的权威来源关系；采用一个最小PG事实表。

正式RepositorySnapshot本身是源码事实，不是执行批准。P2-A的passed表示指定静态检查完成，
runtime_smoke/performance/scientific_validation/human_approval仍not_run；SourceAdoption不改写这些字段。
这是可信单用户、人审且人工提交代码的流程，不把人确认当作恶意代码sandbox。
有权执行新代码的ExperimentVersion仍必须经Core verifier及显式Human Approval。

## 4. 人工端到端流程

```text
精确package（base S1 + candidate manifest + diff + checker）
→ 人inspect并确认修改意图
→ 人检查科研primary repo仍clean且与S1绑定一致
→ 人在该科研repo用既有Git流程提交S2（保留S1）
→ 人通过现有capture命令取得clean S2及其确切identity
→ 人调用adopt(package_ref, target_snapshot_id/identity, project_id)
→ 系统只读核对后原子记录SourceAdoption
→ 人用现有create_manual_revision在同Experiment创建R2，绑定S2
→ 现有Verification → 人审批 → immutable V2
```

前两步的live状态检查可复用check_approval_readiness(S1)，是当时观察，不是全局Git锁。
单操作者窗口下人自行审阅git diff，不使用copy、reset或checkout脚本覆盖未提交修改。
P2-B1实现不自动apply/commit或回滚科研工作树；外部Git动作已经是人的显式操作。

adopt只接受ID/ref/identity等精确绑定，不接受任意host路径、commit脚本或“latest”占位。
新Git状态capture必须使用新idempotency key；复用旧S1 key会回放旧S1，adopt不得替人寻找latest修复。
Snapshot捕获和adopt不是一个跨Git/PG原子事务；capture成功但adopt拒绝时，S2仍是被捕获的事实，
没有SourceAdoption，不自动发Proposal/Approval，也不删除S2掩盖失败。

## 5. 首次采纳必须核对

1. caller为trusted server-configured user actor，Project active；agent不可调用本命令。
2. package/ref/blob/receipt完整，所有P2-A binding与candidate工作区完整性检查通过。
3. 首次采纳只接受当前安装checker身份的报告；旧checker包可读取但需新submit/prepare才能按新语义采纳。
4. S1与S2均属于同Project、同一个当前active primary registration；IDs及identity与请求精确一致。
5. 两Snapshot clean，S2不同于S1；采用普通单parent commit，S2 parent必须为S1 head，保留历史可达性。
6. 使用已有有界SourceService物化S2到新私有目录；比较全部path/type/Git逻辑mode/内容SHA256与candidate inventory。
7. candidate identity与Snapshot identity属于不同命名空间，不能直接相等比较；只读核对实际完整树与各自引用。
8. 首次prepare检查S2的live source ready；检查时若root变化/dirty/revoked则拒绝，不重置Git或静默重绑。

这不是Git与PG之间的原子租约；检查后的外部Git改动不改变已核对S2的不可变内容。
随后Core Approval重新检查live source，实际执行仍物化批准的确切Snapshot，不将后续HEAD当S2。

Git parent查询是新增的有界只读GitAdapter能力，不使用候选提供的命令或hooks。
保持P2-A的64KiB patch、128KiB train.py、8MiB/128 entries和regular-only profile限制；
使用可信checker的有界解析/compile，code object不执行、不保存candidate pyc。

scope仍是train_model函数体直接改动，不能证明函数行为或accuracy计算诚实；没有安全/性能认证。
现有Core也仍允许人按正常流程审查普通Snapshot，此表不是篡改成所有实验的全局审批前置条件。

## 6. 最小持久化与重放

拟议一个context-owned source_adoptions表、一个对应迁移；本规划不创建它们。
最小事实：adoption ID、Project、base/target Snapshot ID、package_ref、namespaced identity、
user actor、created_at。Snapshot identity在规范化command中精确绑定并核对；package冻结引用
已经包含request/manifest/report，不复制全部技术JSON进科学事实库。

base/target FK采用RESTRICT，base!=target；target唯一；同target另一package/不同base明确冲突。
Project与同primary归属由事务中domain核对，不能绕过SourceService或用结果表伪造来源。
application不更新旧事实；控制面所有者直接改写DB/store仍超出此单用户MVP防御范围。

复用prepare_project_scope、canonical command identity、execute_idempotent_command与UOW。
prepare只读核对；operation重读并锁定Project/registration及必要事实，写adoption、RuntimeEvent、
CommandReceipt同事务。没有模型/物理执行副作用，不新增prepared/running/unknown adoption状态机。

同key同payload返回已记录事实；同key不同payload冲突。新key仍须通过当前checker、归属及live source等全部prepare核对，
通过后同target同package复用原事实；不同package不能覆盖。只有已提交的原key重放可保留旧checker语义，
不能换key绕过当前核对。已提交receipt重放保留旧checker含义，不宣称重跑了新检查或再次授予执行权限。
读取证据缺失需明确提示，不从新ref猜测补齐，不撤销真实历史；未经提交的采纳可在同输入下重试。

## 7. 提案、旧实验与比较边界

R2/V2保持同Experiment，Payload中显式S2 ID/identity；沿用当前Verifier、精确verification及Approval。
adopt命令只产生采纳事实，既有manual revision接口不是幂等，所以不包装一个自动多对象闭环命令。
数据、environment及validation协议须重新核对并明确冻结；不能把“只改train.py”当作它们已满足。

旧V1/S1、Trial/Attempt、Artifact/Metric/Validation/Summary不重写；旧源码物化与结果pinning回归必须保持。
HEAD转为S2后，旧S1草稿的live approval readiness可能不通过，这是现有Core契约，不等于撤销旧V1。
源代码不同属于实验语义差异；保留每版Snapshot身份及已有历史设计字段，不自动合并科研条件或作统计结论。
没有跨源码Agent跟进：两个既有snapshot mismatch guards不放宽，ResultContext仍绑定原来源版本。

## 8. 边界与实施顺序（另行批准后）

允许增量：上述一条采纳事实、context command/service、只读parent查询、本地CLI adopt及最小测试。
不添加AgentDecision/写工具/审批等待、Web/API新入口、多primary、多repo registry、后台worker、
sandbox/容器/特权helper、Memory/MCP/Plugin/Compaction/Projection或distributed scheduler。
不允许本命令改科研工作树、捕获Snapshot、产Proposal/Version/Batch或替人审批。

1. 批准B1的记录/命令/迁移范围及仅隔离测试科研对象，不把本次planning当实施批准。
2. 先验FK/unique、归属、身份与幂等；复用现有PG命令，不搭新框架。
3. 核对P2-A package、S2 inventory和单parent；本地CLI只录入事实。
4. 隔离repo/DB中人工Git→既有capture→adopt→manual R2→verify→human V2。
5. focused+full CI，旧P1/P1-C与运行/结果边界保持；独立host acceptance只用隔离配置。
6. 不改当前演示科研仓库；真实demo采纳、真实Approval或训练需再明确对象及成本范围。

## 9. 验收条件（全部NOT RUN）

| 场景 | 必须观察 |
|---|---|
| 正例局部梯度裁剪 | S2完整树匹配candidate；一个adoption；同Experiment人工R2/V2精确绑定S2 |
| 人未审批R2 | 没有V2，更没有自动Batch/Trial/Attempt；静态通过不升级权限 |
| 错Project/registration/primary/base/target identity、dirty/revoked | 拒绝，无adoption，不换primary |
| candidate/package/report或S2内容/type/mode被改变 | 对照预期ref/全树拒绝；不认证自报passed |
| namespace或只readonly chmod差异 | 不误比candidate/Snapshot hash；逻辑mode规则与P2-A一致 |
| 旧checker首次采纳、旧fact重放 | 前者要求新prepare；后者保留事实及原checker含义，不静默重评分 |
| parent非S1/merge commit、部分文件之外变动 | 拒绝并保留Git/Snapshot事实，不回滚别人修改 |
| 同key重复/不同payload、两个并发命令 | 一条事实及一致receipt，冲突明确，不自动发新Proposal |
| DB提交前/后中断、capture复用旧key、capture后adopt失败 | 事务原子或精确重放；残留Snapshot不等于已采纳 |
| S1旧版本物化、旧结果与pending人工输入 | 既有身份/pins不变；需要live ready的旧草稿依当前规则处理 |
| S1结果强绑S2 Follow-up Run/Proposal | 两个既有guard继续拒绝；没有跨源码权限增量 |
| secrets/Agent调用/当前demo保护 | fixed安全诊断；无agent写权限、模型调用、训练、当前demo改动 |

host验收在专用接受repo/隔离DB配置中完成；不增加当前interview allowed roots，不动现有demo数据。
需要DB的新迁移仅随未来实现范围批准后执行；不运行真实训练，不用假指标冒充科学或live acceptance。

## 10. 设计阶段的作者自审结论

单primary已通过实际register代码核对；同Experiment不同Snapshot的人工revision已有接口；
Approval精确source check及command原子receipt均已核对；没有由这些事实推断新模块测试已通过。
不假装同项目第二repo、自动Git/PG两阶段事务或跨源码Agent能力已存在。

这是窄的人工来源核对与PG事实计划，不是完整受控编码Agent。
若需要自动改代码，应在B1验证后独立评审B2的tool authority、双源上下文及执行隔离，不能加一个写工具就发布。
本次只有代码/文档复核，无新实现、迁移、业务对象、Git科研改动、服务重启或paid调用；main保持7b63ca5。
