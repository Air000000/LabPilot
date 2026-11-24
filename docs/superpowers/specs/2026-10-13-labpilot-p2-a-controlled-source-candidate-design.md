# LabPilot P2-A：静态源码候选包与明确的执行边界（r1）

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

> 后续实施记录：复审后批准了静态实施，`405a4c1` 已通过当时的本地工程验收。原验收记录留在开发档案，见[公开静态候选摘要](../evidence/research-runtime-preview.md#static-candidates-and-patch-drafts)。
> 合入前作者复审补齐了请求绑定与有界静态 compile，仅构建 code object。原复审保留于开发档案。
> 下方 PROPOSED / NOT RUN 指 r1 设计复审时点。后续实施当时已集成本地 main，该次集成未推送或重启服务。2026-11-24 的公开状态见[正式发布验收](../evidence/research-runtime-preview.md#public-release-acceptance)。

- 日期：2026-10-13（Asia/Shanghai）。**PROPOSED / AUTHOR SELF-REVIEW / IMPLEMENTATION NOT STARTED**。
- r0初稿提交 `af22824e095a40823c3a6aa6e43c07ea7910fdba`；本次用户明确要求结合成熟方案自审并改进，授权设计修订，不授权实现、候选执行或付费调用。
- 默认实施范围收窄为 **STATIC-ONLY / DYNAMIC_EXECUTION_NOT_AUTHORIZED**。
- Core基线 `ef7d4ec5326c143f5911d7b3de45770aee6f8e5e`，独立分支planning/p2-a-controlled-source；main/演示部署/历史数据不变。
- 原r0设计由Git历史保留；本文件合并成熟方案对照、缺陷处置、修订方案和验收，不再堆叠重复计划/ADR。

## 1. 先检验需求，避免把开发顺序当产品需求

当前Core已经支持参数扫描、冻结源码执行与结果迭代；若研究任务只改lr/epochs/seed，
现有链路足够。受控改代码不解决模型自由文本解释PARTIAL，也不自动建立科研能力。
实施前应确认一个确实需要源码差异的任务和可审阅补丁，例如train_model中引入梯度裁剪，
保持CLI、数据切分、评估及输出协议。该例是工程验收用例，不预言精度或显著性改善。
若没有这个需求，暂停P2-A实施，已接受Core可继续用于求职展示。

本阶段的产品价值是给源码候选绑定真实基准、展示实际差异、生成明确范围的技术报告。
它不是通用编码Agent、独立补丁工作流平台或自动科研优化器。

## 2. 成熟方案对照与真实取舍

| 一手依据（本次核对） | 成熟做法 / 限制 | 本项目取舍 |
|---|---|---|
| [Git apply](https://git-scm.com/docs/git-apply) | 默认不能应用的hunk使整份补丁失败；check和应用可不依赖Git工作树；reject会允许部分应用 | 直接复用Git，禁用reject/3way/unsafe-paths，不写patch引擎；验证真实最终树 |
| [OpenHands security](https://docs.openhands.dev/sdk/guides/security) | 风险分析、确认策略和执行隔离职责不同；直接执行入口可能绕过会话确认；检测不是完整沙箱 | 所有CLI/API/未来工具入口必须受同一能力边界约束；不让LLM risk或一个审批字段代替执行授权 |
| [Docker Sandboxes security](https://docs.docker.com/ai/sandboxes/security/) | 当前Docker Sandboxes用microVM边界，并区分直接共享与private clone | 普通Engine容器不冒充microVM；先不执行候选，不为手工补丁立即引入VM/特权平台 |
| [Temporal Activity](https://docs.temporal.io/activity-definition) | 可重试任务要靠副作用端幂等，持久化不会自动让外部动作只执行一次 | 静态候选可从pristine base确定性重建；动态执行未知不能直接重发，不引入Temporal依赖 |
| [systemd timer源码文档](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml) | timer用于激活单元，AccuracySec默认1min；触发时间不是硬实时执行保证 | 不把一次期限timer写成30s必停证明；动态TTL另行设计并真实验收 |

这是将职责映射到LabPilot的设计推论，不声称这些项目已经实现本文件的科研血缘语义。
不采用OpenHands SDK、Temporal server、microVM或新的registry作为本阶段依赖。

## 3. r0自审发现与r1处置

| ID | r0问题 | r1改进 |
|---|---|---|
| R1 范围与复杂度 | 人工补丁检查起步就要求Docker、特权helper、timer、恢复；收益尚未验证 | 默认只做静态候选包；动态执行独立授权，当前不设计/安装特权期限服务 |
| R2 权限错觉 | 把函数体/AST限制当作保护评估行为的主要措施 | 明确仅是直接变更范围；反例能在train_model中修改accuracy全局，静态通过不证明行为安全 |
| R3 报告可信度 | 固定只读harness仍可能与候选同进程/同权限；stdout或exit0可能伪造通过 | 默认报告只由可信静态checker生成；动态日志归类为不可信观测，未来不得同进程import候选后信任其passed |
| R4 数据与重复建设 | 泛称自行保存immutable JSON，没有复用现有fsync/校验内容存储 | 复用ContentAddressedStore和canonical/hash能力；明确原始字节、语义清单、发布receipt边界 |
| R5 恢复过度保守 | 将静态重建也套用物理训练的unknown不重试 | 只在新临时目录从同一请求/base重建静态候选，最终身份必须相同；不重复应用到半成品 |
| R6 期限机制缺口 | timer触发前后仍可能晚启动，时间精度/stop宽限与daemon失败未定义 | 动态方案仍NOT READY；未来必须由同一可信生命周期所有者持有启动资格和期限，不能只加oneshot timer |
| R7 身份与审批混用 | 内容hash可检测变化，但hash不能认证报告作者或阻止控制面所有者改写所有引用 | 依赖可信预期ref与操作者边界，明确非防篡改审计；未来正式批准必须由权威DB绑定 |
| R8 边界与模式 | 文件只读chmod可能被误算成源码mode改变；base verifier可能拿来验证已修改candidate | Git逻辑mode与运行目录权限分离；base与candidate清单各有自己的身份和验证职责 |
| R9 科研保证 | 同文件评估、技术smoke与scientific valid可能混淆 | 不产生正式科研结果，动态/科学检查明确not_run；独立可信评估仍另设scope |

作者自审，不冒充独立APPROVE。R2/R3/R6等没有靠添加几个关键字黑名单或prompt补丁宣称解决。

## 4. 默认范围与authority增量

服从[V1 Scope](../../labpilot/01-v1-scope-and-acceptance-v0.3.md)、
[Architecture 10](../../labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md)
和DOCUMENT-STATUS的已接受Core边界。本修订仍PROPOSED；批准r1仅能授权下列静态能力。
r0设想的候选执行、Docker/期限helper不能从旧段落继承为当前实施权限。

**允许的候选增量**：人提交补丁；读取同Project clean Snapshot的确切源码；在新专用副本
应用只涉及train.py/train_model函数体的补丁；有界语法/范围解析；生成diff、清单及报告。
**不执行**：候选Python、import候选、pytest/test discovery、候选脚本、模型、训练或安装依赖。
**不新增**：AgentDecision/写工具/审批等待、API执行入口、Web页面、daemon、数据库表/迁移、
正式新RepositorySnapshot/Proposal/Version/Trial/Attempt、Docker/systemd单元或签名基础设施。

P2-B的Agent提交及S1旧结果→S2新源码→新Proposal/Approval血缘需要另行批准。
现有source_snapshot_mismatch、follow_up_proposal_snapshot_mismatch和只读工具契约不放宽。
技术包不能颁发实验执行权限；原R0历史文件和已接受baseline不重写。

## 5. 最小流程与可审阅交付

```text
submit（确切base + patch + rationale）
→ validate input/base binding
→ prepare：在新目录物化base、原生Git check/apply、完整树与函数范围校验
→ publish：CandidateSource manifest + diff + static SourceCheckReport
→ inspect：核对预期ref、呈现实际检查和not_run项
```

建议仅提供submit / prepare / inspect三个本地命令，名字待实施确定；不把check的名称
当作静态/动态混用的理由。不提供默认执行选项、Docker不可用时回退host等隐含路径。

交付必须显示：base Snapshot ID/identity、request ref、patch ref、candidate manifest ref、
实际diff、检查profile/version、checker provenance和static report ref。
状态示例是“静态范围检查通过，候选未执行，等待人工审阅”；不使用无范围的passed/safe/approved。
人审阅此包不等于现有Experiment Approval，P2-A没有采纳/训练按钮。

## 6. 数据身份、存储与可信边界

复用现有ContentAddressedStore存patch/request/manifest/diff/report不可变字节；
其put/read已包含hash校验、临时文件、fsync及原子发布。不是新完整Artifact CAS或科学事实库。
复用canonical JSON及namespaced hash；不另写序列化库/签名服务或持久化状态引擎。

语义request包含schema version、project ID、base Snapshot ID/identity、patch ref、固定
范围profile/version、rationale（<=1000 UTF-8字节）。其canonical ref作为稳定请求身份，
避免CLI里误写“server生成ID”；操作者/时间放在单独提交receipt，不充当源码内容身份。
同一语义请求重复submit返回同一ref，不因timestamp生成另一个修改任务。
patch原始字节<=64KiB，train.py<=128KiB，base总内容<=8MiB/128条文件清单；超过则拒绝，
不截断后继续。CLI接收UUID/hash ref，记录根由可信配置指定，不能接受任意host workspace路径。

candidate manifest绑定request ref/base identity，按规范化相对路径排序，记录文件类型、
Git逻辑mode(100644/100755)及内容SHA256；不把chmod只读/owner/mtime等临时权限当源码差异。
base引用仍是S1；candidate只有自己的manifest，不冒充正式RepositorySnapshot S2。
已知demo profile拒绝symlink/submodule/LFS及特殊文件，不能自动放宽为其他仓库类型。

SourceCheckReport绑定candidate/request/profile/checker identities，逐项标记
passed/failed/not_run并给证据ref。默认仅执行base_binding、patch_applicability、path/type
范围、protected_source、syntax和manifest验证；runtime_smoke、performance、scientific
validation和human approval均not_run。报告只能表示其实际执行过的检查。
log/raw stdout不承载passed资格；future动态report必须隔离候选与判定者并定义观察真实性。

提交者补丁与既有源码均是待检查内容；操作者、安装的Git/Python/checker和记录store是
当前单用户本地可信边界。预期refs由提交receipt/调用者固定；hash只能在预期ref可信时
检测变化，无法认证是谁产生结果。控制面所有者重写整个store/ref超出此MVP防御范围。
未来正式采纳仍需重新核对内容并在PG中绑定Snapshot/Verification/Approval，不信本地passed。

## 7. 基准物化、补丁与范围校验

直接复用RepositorySnapshotSourceService.describe/materialize_exact_source校验project、
clean和expected identity，并取得RepositoryMaterialization。不得伪造Version去走Attempt准备。
该物化方法对已存在destination进入verify-only；所以prepare必须创建新的专用staging路径，
不能预建空destination再期待其写入，也不能用S1 verifier验证已经修改的candidate。
确认base完整清单后才应用补丁；S1的Git/内容store及用户工作树保持不变。

固定可信Git argv + STDIN，cwd为本次private staging根；不共享.git/HOME/.venv，
不启用unsafe-paths/reject/3way/自动补全，不从当前worktree回退取文件。
原生git apply --check及默认原子应用负责补丁算法；本地策略同时验证解析得到的改动
路径/类型及最终全树。不能只在apply之后才检查越界，也不能只信模型changed_files。
只允许既有普通train.py内容变动；新增/删除/rename/mode/binary/.git/绝对或父路径均拒绝。
空变化/净结果与base相同拒绝；空白变化不宣称算法改善。

demo-training-body-v1要求唯一train_model、signature/decorators不变，函数体之外原字节
保持；使用AST定位范围及语法，使用实际字节与清单核验变更，不把AST当Python安全证明。
当前demo布局之外的不支持结构拒绝，不做通用函数权限引擎或每错误追加关键词禁令。
解析只用可信stdlib AST，不import/exec/eval候选；可信parser进程用隔离Python导入路径，
不把candidate目录加入sys.path；输入/输出/CPU/内存有界，解析失败/超限就拒绝。

static判定后固化candidate身份；运行目录可设只读以减少意外修改，但本地owner仍可改文件，
inspect须对照固定manifest重新验证，不能把chmod当不可篡改证明。

## 8. 静态恢复与发布：按副作用分类

submit把请求bytes先存为ref，再发布receipt；prepare在同请求锁下物化到新的staging。
完成前无公开ready包；完成后原子发布只含精确ref的package receipt，文件及父目录fsync。
返回重复prepare时只回放并验证已完成包，不覆盖已发表的request/manifest/report。
checker/profile改变须显式升级请求中的profile/version，生成新请求与报告；不把旧profile验收静默升级。
固定profile必须绑定可信checker语义版本与Git/Python版本；参与身份的静态产物不含
timestamp、临时路径或耗时，这些仅写观察receipt，避免重复prepare被伪装成内容变化。

CLI中断留下的未发表staging不构成通过。由于没有模型/候选运行/正式科研写入，允许在
新的专用目录从同一请求与base重建；静态产物身份必须一致，禁止往半应用目录再apply。
无法验证原始ref或身份不一致时记录integrity_error/attention_required，不猜测成功。
记录部分写入、缺blob、损坏ref和并发publish都要验收；失败目录按明确保留策略处理，
不自动递归删除旧worktree/volume/演示数据，也不把未发表草稿升级为科研事实。

静态确定性重建与外部任务重发不同。未来动态outcome未知仍没有blind rerun，不能从
本节的可重建推导出容器/训练的安全重试。也不宣称global exactly-once或多writer平台。

## 9. 动态检查：保留要求，当前NOT READY / NOT AUTHORIZED

动态执行有实际需求后须独立确定威胁模型、所选sandbox、生命周期所有者、资源预算和
判定可信边界。普通受限Docker只作为较低保证选项，不冒充Docker Sandboxes的microVM。
恶意内核攻击/容器逃逸不是现有普通container可形式保证的防御；不为项目立即安装新VM。

未来执行至少要求无密钥/控制socket/host namespace，固定镜像digest/argv/harness、
网络与挂载最小化、明确CPU/memory/PIDs/tmpfs/output限制；候选进程与可信判定器隔离。
若同Python进程import候选，它能修改测试环境；只读harness文件也不能证明测试运行未被篡改。
raw log/candidate stdout、exit0、自报JSON均只能是观测，不能自动成为可信pass或科学指标。
必须明确哪些检查由可信执行器实际观察，哪些输出仍由候选自报；不能增加签名给不可信判定背书。

r0的单次systemd临时期限单元不作为可实施TTL设计保留：默认精度、kill宽限、daemon
状态、宿主机暂停及“timer先结束、start后到达”的竞争都未解决。仅检查当前时间后start
不足以撤销晚启动。未来须由同一可信生命周期所有者控制create/start/expiry/stop，保存
单调预算与身份，设计迟到启动拒绝、父CLI死亡及stop确认；不在本阶段造特权helper/新服务。

动态扩展必须实际验收迟到启动、CLI crash、无限循环/子进程、输出/存储超限、资源耗尽、
daemon未知、强制终止后无残留、report真实性和幂等观察；静态原子性probe不替代这些场景。
独立可信科学评估与S1→S2 Proposal/Approval血缘仍是另外的研究/产品契约。

## 10. 最小实施顺序及验收（批准r1后）

1. 明确首个源码修改任务、base Snapshot和固定profile；只有超参需求则NO-GO。
2. 复用现有exact source与内容存储，完成submit、static prepare、inspect和精确ref身份。
3. 用人工固定补丁验收正例/失败/中断/篡改；不调用模型，也不执行候选Python。
4. focused + full CI，旧P1/P1-C/TrialFactView与已接受恢复/权限保持；独立静态host验收。
5. 再依据真实需求决定动态检查或P2-B；r1批准不授权它们。

| 静态场景 | 必须观察 |
|---|---|
| 合法局部补丁 | S1和main不变，实际diff和candidate身份匹配，static通过/动态not_run |
| 错Project/base/clean/缺blob或身份不符 | 写候选前拒绝，不fallback worktree |
| ../、绝对路径、symlink、mode/binary、其他文件/目录 | 拒绝，workspace之外不变 |
| 改accuracy/load_dataset/main或signature | 直接范围校验拒绝 |
| 函数体内部globals/eval等动态语义 | 不执行；不因静态范围符合就宣布安全或科学有效，无权限升级 |
| malformed/空/no-op/超长/极深语法 | 有界拒绝，诊断不含任意原始代码/secret |
| 部分不适用hunk | 原生Git原子失败，候选未发表，不写rej或模糊补齐 |
| 中断/并发/重复submit或prepare | 无部分ready包；同请求ref，fresh重建身份一致，不重写已发表记录 |
| request/manifest/report/候选损坏 | 对照可信预期ref失败，不将自洽的新hash当原记录 |
| 权限chmod只读 | Git逻辑mode身份不变，修改内容仍可被ref/清单检测 |
| fake passed stdout/伪造report | 默认不运行；不接收补丁给出的pass状态，runtime/science仍not_run |
| 旧系统 | focused/full gate及演示保护检查保持，不新增科研对象、权限、迁移、服务 |

新实现验收目前全部NOT RUN；本次只有设计探针和文档核对，不能填作产品PASS。

## 11. 本次实际探针与作者复审结论

在已有demo的Git blob上只做AST解析；没有执行候选Python。构造在train_model函数体中
增加globals修改accuracy的字符串，语法/signature/函数外字节均保持：证明函数范围不等于
行为约束，而非证明已构建一个Python攻击防御器。
在一次性合成a.txt/b.txt上验证Git默认原子失败：一个hunk不适用，check/apply都拒绝，
两文件不变，Git版本2.43.0；不是对真实仓库应用补丁。
哈希反例证明改报告并重算自身hash仍可自洽，而与原先固定的ref不符；不执行或伪造真实业务报告。
收据 `probes.json` 和辅助脚本保留于开发档案。

复审修正的是范围/可信边界/可实现性，不把Scope变小包装成动态隔离已经通过。
默认设计可进入单独范围批准与实施计划；动态执行仍有未解决的生命周期与判定问题。
这是作者复审，不是独立APPROVE或production安全审计。没有新依赖、模型、DB、容器、timer、
服务重启、正式源码修改、merge或push；main保持原接受基线。
