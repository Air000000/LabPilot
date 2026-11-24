# B3 维护：显式草稿补丁规范化复审与方案

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

**2026-10-17 实施记录。** 窄实施已集成本地 main，主仓库完整 gate 与 CLI 验收通过，该次集成未推送。原主线集成验收及实施收口保留于开发档案。见[公开补丁派生与交接摘要](../evidence/research-runtime-preview.md#static-candidates-and-patch-drafts)。以下保留复审时点。

2026-10-17（Asia/Shanghai）。PROPOSED / AUTHOR SELF-REVIEW / IMPLEMENTATION NOT STARTED。
基线main a463386dc31a6b8f03fd8b4072b0a7df22c1f19e；规划分支planning/b3-patch-normalization。
本次授权为失败后的边界复审与零调用实验；不是新的付费复验、格式转换产品或部署验收。

## 结论与第一性原理

首次固定真实观察中的 patch 存在 hunk 计数错误。原始记录保留于开发档案，见[公开草稿失败摘要](../evidence/research-runtime-preview.md#static-candidates-and-patch-drafts)。
它没有证明模型理解错了目标，也没有证明所有模型/任务的失败率；原始可应用性仍FAIL。
代码修改内容需要模型判断；冗余行数、canonical diff和内容身份适合由确定性工具计算。
当前拒绝机制保护了正式链路，但要求模型同时正确生成修改和Git序列化细节，存在可避免的格式负担。

建议先增加一个**人显式触发、可追溯、静态的规范化导出**，暂不更换B3决策协议或引入编辑器框架。
它把原始草稿转换为独立派生产物，不静默改写AgentStep、不自动submit或追加模型调用。
是否采用结构化search/replace，应在更广格式/模型证据表明有必要时另行决策。

## 一手资料与适用范围

| 来源 | 已核对事实 | 本项目推论 |
|---|---|---|
| [Git apply](https://git-scm.com/docs/git-apply) | --recount从hunk内容推导行数；--check只检查，不应用 | 可复用原生计数逻辑，不自行解析/修补hunk数字 |
| [Aider edit formats](https://aider.chat/docs/more/edit-formats.html) | 支持search/replace、whole及简化udiff，不同模型适用格式不同 | Git unified diff不是唯一成熟输出格式，但单个失败不足以立即替换全协议 |
| [Anthropic text editor](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool) | str_replace要求精确旧文本，且建议唯一匹配；无/多匹配返回错误 | 结构化编辑也有匹配歧义和校验成本，不能视作天然可靠或直接照搬完整文件工具 |

以上是工具事实和本项目取舍，不称为行业强制规范或真实质量benchmark。

## 备选方案自审

| 方案 | 成本/问题 | 取舍 |
|---|---|---|
| 提示模型再数一次、增预算/重采样 | 保留冗余计数负担，不能提供确定性保证，新增成本 | 不作为主要修复 |
| 在P2-A现有prepare直接加--recount | 代码最少，但改变所有人工Request的应用语义及checker指纹，容易静默重解释历史草稿 | 本轮不选 |
| 增加显式规范化导出、复用Git和P2-A | 多一个局部CLI入口；原始草稿与派生patch分开，现有检查契约保持 | **建议首选** |
| 全量切换到old_text/new_text或函数体输出 | 消除hunk计数，但需新schema/纠错/回放/导出兼容、匹配与重叠规则 | 暂缓，不因一个计数错误扩大切片 |

## 最小拟议契约

现有CLI增加`normalize-agent-patch --agent-run-id <UUID>`，名字以实际实施时核对为准。
现有`export-agent-patch`继续输出原始精确字节，旧NULL/普通/Follow-up/等待态行为均保持。

1. 复用现有导出资格：草稿模式、completed/drafted、确切source_patch终态Step及Snapshot/Project身份。
2. 复用只读source loader重新核对同一pinned context与完整train.py；不得回退worktree/latest。
   撤销/身份变化/源码缺失明确失败；只读原始export仍可用于历史审阅，不因此放宽normalize资格。
3. 先执行既有patch路径/模式/二进制拒绝；不为复用而修改source_candidates.py及其checker指纹。
4. 当前用户拥有的0700临时目录仅写绑定train.py副本；固定argv先`git apply --check --recount -`，
   再`git apply --recount -`。不使用--unsafe-paths/--reject/--3way/忽略空白，不执行候选或源码命令。
5. 复用protected_parts做parse/compile与受保护prefix/suffix核对，拒绝no-op；不将范围通过称为行为安全。
6. 用stdlib difflib从exact baseline和应用后内容生成canonical unified diff，固定a/train.py/b/train.py头。
   再在新鲜基线上以**不带--recount**的Git check/apply验证canonical输出与目标bytes相同。
   缺失末尾换行/非UTF-8等无法无损表达的情形明确拒绝；不悄悄补行、截断或更改内容。
7. 原始patch沿用32KiB；base沿用32KiB/500行；应用后source不超过P2-A 128KiB，canonical不超过64KiB。
   全部校验完成才写stdout；失败stdout为空，stderr仅固定原因，不泄漏patch/异常上下文/密钥。
8. stdout只输出派生patch；stderr输出最多4096bytes的机器可读receipt：Run/Step ID、Snapshot身份/base hash、
   raw patch hash、canonical hash、固定normalizer版本/代码指纹及Git/Python版本。
   不加表/列/CAS/日志注册表，不写任意用户指定路径；receipt由操作者选择保留，不冒称数字签名或权威PG事实。
9. 人查看派生diff/receipt后，**另行**选择现有P2-A submit/prepare/inspect。
   Request.patch_ref绑定canonical bytes；raw hash与Run/Step另行审计，不能再称“提交的是模型原始精确字节”。
   P2-A现有静态checker/report、Git提交/capture/adopt/实验审批保持独立；不自动串联。

不得修改旧Step/旧Request、在旧失败目录重跑prepare或把原首样本追认为PASS。
即便normalize成功，也只代表产生了可检查的派生patch；仍不证明语义正确或科学改进。
Git会依据上下文匹配，也可能接受偏移；--recount不提供唯一语义定位或“发现所有截断”的保证。
不能把格式转换叫做智能修复；不猜测缺失修改、不补全模型意图。

## 实施顺序与验收

批准本窄契约后，先在独立分支写旧export不变/新normalize正负用例，再实现一个纯静态转换helper和CLI早返回。
不构造SourceCandidateService，不访问正式候选目录；只读DB/source，临时副本由当前用户持有并清理。
不改AgentDecision/ContextCompiler/预算或迁移，不引入包、工具、后台Agent或新框架。

| 必验项 | 通过标准 |
|---|---|
| 正常diff、增删、多hunk、计数多/少 | 确定性canonical输出；严格Git可应用，目标bytes一致 |
| 错上下文/文件/越界/模式/二进制/多文件/超限/语法/评估修改/no-op | 拒绝，stdout空；不发布任何正式候选 |
| 原始draft/Step/export | 原始hash不变；重复规范化相同输出，零模型调用 |
| source身份失效/撤销/绑定错误/损坏context | fail closed，不换来源 |
| 权限/诊断/临时目录 | 0700，固定argv/最小env/超时，候选顶层raise从未执行，异常不回显 |
| P2-A兼容 | source_candidates.py与source_candidate_check.py字节不变；旧包inspect、旧未完成Request原语义保持 |
| 人工handoff | 仅隔离fixture显式submit/prepare，Request.patch_ref等于canonical hash；无自动业务写入 |
| 回归 | focused/P1两轮/B1/B2/B3、full CI；真实host/Provider另行记录，不能由此推断PASS |

## 本轮已执行的零调用实验

本机Git2.43.0，私有原型`local-workspace/p2a-static/b3-patch-policy-probe.py`。
19个合成检查通过：正常/计数多1多7少1/插入/删除/多hunk；拒绝错上下文、错文件、评估修改、语法、
no-op、mode、binary、绝对/遍历路径、超限、额外文件、损坏hunk标记。
原型仅使用Git、stdlib及现有静态函数，在临时副本运行，未import/execute候选。
此19项不覆盖完整产品验收矩阵，尤其CLI、撤销/旧记录、EOF和诊断集成尚NOT RUN。

把原422bytes失败样本复制到该实验流程后，得到严格可应用的派生diff，AST与指定math.fsum修改一致。
原始SHA256242c04819e66c1140a346fc88f2dda64998f8ccced2be89b6b2d08a9f5a3c4e6保持；
派生SHA256d2bfb4bcb2541770acdca02b0a281db0f8ffd827c194f6dec268d1a29c23f351。
这是**反事实离线探针**，不是新的live、正式prepare或成功CandidatePackage，原NOT ACCEPTED保持。

下列私有证据保留在开发档案的 `p2-b3-patch-policy-review-20261017/` 中，原权限为 0700/0600：
probe.json、counterfactual-canonical.patch、protected.json。
所有DB原行、正式候选文件及三服务身份保持，demo clean；Provider/Request/Package/训练增量均0。
原型未进入产品；完整CI/新CLI/host/live验收NOT RUN，原B3工程与0018部署基线不改写。
当前无活动implementation tranche；LQ-PATCH-FORMAT-001仍OPEN，动态执行和自动科研仍不在范围内。
