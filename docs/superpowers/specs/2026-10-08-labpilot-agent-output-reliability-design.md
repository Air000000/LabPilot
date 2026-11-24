# LabPilot — Agent 输出可靠性维护设计与验收计划

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

- 日期：2026-10-08（Asia/Shanghai）
- 状态：**INTEGRATED LOCALLY / OFFLINE GATE PASS / INTERVIEW 0015 DEPLOYMENT PASS / BOUNDED LIVE MIXED**
- 后续本地集成和部署：[独立验收记录](../evidence/research-runtime-preview.md)。
- 代码基线：`95153934b0312739d98b8978bc4809a49d11173e`（clean local main）
- 工作分支：`maintenance/agent-output-reliability`
- 范围：模型输出契约、已知响应处理、展示字段隔离、一次有界纠错和回归验收。
- 后续实施：用户回复“继续继续”；实际提交和验收见[维护收口](../evidence/research-runtime-preview.md)。
- 后续四类固定live：[2 PASS / 2 PARTIAL](../evidence/research-runtime-preview.md)，12 calls / 121,944 tokens，未触发纠错。
- 以下正文保留设计阶段标准与历史检查，实际执行状态以收口记录为准。

用户“好的，开始”承接的是制定窄设计和验收清单。本文件是可评审方案，
不代表实现、独立审查或模型质量验收已完成。它服从
[V1 Scope v0.3](../../labpilot/01-v1-scope-and-acceptance-v0.3.md)、
[Architecture 10](../../labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md)
及已接受的 P1 / P1-C 边界；不重写原 acceptance。
2026-10-08 对初稿 `0d95f31` 的[深度复审记录](../evidence/research-runtime-preview.md)
保存具体发现、依据和修订；本文为修订后方案。未请求或使用其他 agent，复审仍为作者复审。

## 1. 事实、诊断边界与目标

最新[真实案例](../evidence/research-runtime-preview.md)
在第 4 次调用失败，累计 41,289 tokens，安全日志为 `propose.summary / value_error`。
没有耗尽预算，也没有生成 Proposal；准确原因是超长还是非法 UTF-8，未知。
运行时要求 summary ≤1000 UTF-8 字节，但生成的 schema 和编译策略未披露这个限制。
这是已确认的契约缺口，不是“Agent 不会设计实验”或“只要缩短 summary 就一定成功”的证据。

当前代码还存在三个独立缺口：

| 代码 | 已核对的行为 | 本轮改进 |
|---|---|---|
| `agent_runtime/types.py` / `context_compiler.py` | 自定义字节约束未进入模型契约 | 同源约束、schema 注解、关系规则说明 |
| `agent_runtime/deepseek.py` | JSON mode；不检查 finish_reason；payload 解析后才提取 usage；未显式设置输出 cap | 先解析响应信封，再判定完整性和 payload；保留已知 usage |
| `agent_runtime/service.py` | 所有 Decision ValidationError 都终止 Run；失败反馈只在日志；history 只有工具结果和人工输入 | 展示隔离、持久化安全反馈、最多一次纠错 |

2026-10-08 离线探针使用现有 `_proposal()` 测试 fixture：外层 Decision、Proposal、
Dataset、Environment、Metric、Artifact、ValidationRule 的未知键均被当前 TypeAdapter
拒绝。不能把“嵌套字段一定被忽略”当作缺陷；保留并测试已有严格行为。

目标是减少接口引起的失败，并保持权限、事实和预算边界。结构有效、Verifier 通过和
实际科研效果是三个不同验收层；本维护不保证 accuracy ≥0.95，也不接受自主科研能力。

## 2. 成熟方案的采用范围

| 来源 | 采用原则 | 不迁移的部分 |
|---|---|---|
| [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/structured-outputs) / [Pydantic AI](https://pydantic.dev/docs/ai/core-concepts/retries/) | 明确输出契约；输出纠错与工具、传输重试分层 | 不引入 SDK、重试框架或多层 retry |
| 用户早期 Codex / Hermes 调研 | 规范化响应；恢复原因和预算作用域明确 | 不复制 Provider fallback、压缩、插件或多 Agent |
| [STORM](https://github.com/stanford-oval/storm) | 材料收集与最终产物分阶段 | 不建多视角讨论系统 |
| [PaperQA2](https://github.com/Future-House/paper-qa) | 决策关联具体来源和证据 | 不建文献检索、向量库或知识本体 |
| [OpenHands](https://github.com/OpenHands/software-agent-sdk) | Agent 和实际执行环境分离 | 不替换 Runner 或实验审批 |
| [MLflow](https://mlflow.org/docs/latest/ml/tracking/) | 参数、源码、指标和 Artifact 关联 | 不部署第二套实验追踪平台 |
| [Temporal](https://temporal.io/blog/idempotency-and-durable-execution) | 持久化编排与外部副作用幂等分离 | 不替换 PostgreSQL / systemd / reconciliation |

早期调研材料是设计输入，不是当前产品行为或实施 authority。
DeepSeek 当前 Chat Completions 的 `json_object` 只保证 JSON 格式；
`finish_reason=length` 可能意味着输出截断。
[官方接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)
原生 schema-constrained output 的 endpoint / model / schema 兼容性另行验证；本轮
保留当前 JSON mode 加本地严格校验，不建立多 Provider 能力协商框架。

## 3. 决策一：同源契约，保持科研语义

继续使用既有四类 Decision 和完整 ProposalPayload。共享 summary=1000、tool_name=100、
question=4000 的字节常量供 validator 和字段说明使用。schema 能表达的必要字符上界、
类型、required、枚举和 pattern 由 Pydantic 生成；UTF-8 字节上界与非空规则明确注解。
JSON Schema maxLength 按字符计数，不能声称它保证字节上界；本地字节校验仍是权威。

为 `_bounded_text` 定义代码固定的错误码（空白、非法 UTF-8、超字节上界），
安全诊断只允许这些已知码和既有原生错误类型，不输出自定义异常文本或 context。
审计四类 Decision 和 Proposal 的自定义约束；已有路径、有限数值、Dataset 身份、
ValidationRule threshold、Artifact/Metric 引用和 argv placeholder 规则仍由本地边界验证。
不为追求一次生成成功而放宽这些约束，也不把所有科研规则重复实现成新的校验引擎。

声明 summary 是可选短展示文字，建议省略或 null；详细事实来自结构化证据和 Proposal。
保留现有 count scope / comparability / trial_evidence 策略，不持续堆叠失败样本特例。
仅文字形式的目标仍由模型理解；本轮不增加自然语言参数解析器，也不硬编码学习率或 epochs。

## 4. 决策二：规范化 Provider 响应与输出 cap

DeepSeek adapter 顺序：bounded HTTP body → JSON envelope → usage → finish_reason → content JSON。
仅完整 `finish_reason=stop` 的单一响应可进入 Decision 验证。缺少/未知终止原因、
content_filter、length、格式错误的 envelope 等分别失败；不从半截 JSON 抢救动作。
无 tools wire 合约时，不接受 tool_calls/function_call 分支。请求数、body 大小和 timeout
继续受现有界限约束；不添加 HTTP / SDK 自动重试或 Provider fallback。

`choices` 必须恰好一个；即使 finish_reason=stop，message 中非空 tool_calls/function_call
也不能通过。envelope/content 均拒绝重复 JSON 键，不能用 last-key-wins 消除歧义。
使用 stdlib json.loads 的 object_pairs_hook 及现有 parse_constant；只发固定错误码，
不记录动态键或文本。重复键、非标准 NaN/Infinity、过深导致的解析异常均 terminal，
不属于可纠错的普通 JSON syntax error；语法错误/非对象的资格还须满足下述已知完成和 usage 条件。

ModelResponse 和 ProviderError 各增加 `response_completed=False` 的事实标记，
ProviderError 另可携带有界安全 usage。只由受信 adapter 设置，不读取模型 payload 同名键。
DeepSeek 完整 stop 响应可设 True；其他失败不能因此获得资格。
OpenCodeGo 继续缺省 False：其旧解析器未验证 finish_reason，本轮不把“已返回 Mapping”
等同于“已明确完成”。旧正常输出照原路径验证；展示降级和纠错仅用于明确 True 的响应。
新增 ScriptedProvider 场景显式设置 True，并提供已知 usage；旧 fixture 的缺省行为保持。
这是一个响应事实字段，不是 Provider capability registry 或新的输出类型体系。

复用既有 ModelRequest.semantic_parameters，不添加第二个请求参数容器。
DeepSeek 服务装配使用固定上界 8192（与本次核对的非思考模式文档默认值一致），
每次实际 cap 为 min(8192, Run 剩余累计 token 额度)，编译为唯一允许的
`{"max_tokens": actual_cap}`，原样进入 canonical request hash 和该 Step 的已持久化参数。
adapter 仅接受该已知整数参数并映射到 wire `max_tokens`；未知键保持拒绝。
不增加用户配置面板，也不自动因截断提高 cap。代表性完整 Proposal 是离线兼容检查的样本。

现有 OpenCodeGo 的请求继续用空 semantic_parameters；不擅自发送新 wire 参数。
其现有非空参数拒绝保持。ScriptedProvider 可接收并断言已编译参数，便于离线验收。
不提取两份 adapter 为通用 Provider 框架。

有效信封中的 usage 即使 payload 非 JSON 或截断也进入失败 Step 和 Run 计数。
只保留代码已知的非负整数计数字段（prompt/completion/total 及已知 cache 计数），
拒绝 bool/负值/错误类型；未知字段和任意字符串不能透传 DB/Trace。信封没有可验证的
usage 时保持未知。输入+输出回退计数沿用既有规则，不擅自扣除 cache hit。
DeepSeek 可用总量定义：合法 total_tokens；或 total 缺省且 prompt/completion 两者都为合法
非负整数。两者都有时 total 必须等于 prompt+completion。主要计数字段出现 bool、负数、
错误类型或不一致则 `provider_usage_invalid`；缺少可用总量则 `provider_usage_unknown`。
这些响应 terminal，无动作、无纠错；独立合法的组件仍可记录为已知计数下界。
不一致的 total 不作为预算增量，保留合法 prompt/completion 组件，不伪造新的 Provider total。
未知扩展字段忽略，非法可选 cache 字段舍弃，不因非关键扩展打断正常 Run。
明确完成但缺少总量也不能据增量 0 自动续跑。网络/timeout 仍未知且不重发。
以上远端规范化限定 DeepSeek；不把旧本地测试的空 usage 当作真实收费为零的证据。
reason_code 仍是固定代码，
不得附带 response body、URL、headers、原始输出或异常上下文。不存在 usage 时保持未知，
不能把空 usage / 计数增量 0 描述成“没有收费”。完整正常响应沿用 ModelResponse。

累计 token 预算仍由 Provider usage 返回后核算；prompt 计费和服务端未知结果不能提前精确
限制。输出 cap 不构成严格的总费用保证。收到已知 usage 时，即使 wall-clock 同时耗尽，
也要先记账，再以预算原因终止；真正 timeout / reserved 的未知状态不能虚构 usage。
收到响应后的处理优先级：提取安全 usage → 验证/分类（不执行动作）→ 计算已用预算
→ finalize 原 Step。wall-clock 已到或累计 token 已到时优先 budget_exhausted；否则再决定
纠错/完成/人工输入/失败。仅达到 max_steps/max_model_calls，仍允许本次合法响应走原有
STOP/Human/Proposal/Tool 路径，但不允许再调用模型。已知无效输出即使累计预算已到也保存
失败诊断，不能发纠错请求。字段 total_model_calls 包括 finalized reserved 的保守尝试计数，
不能把 unknown 的记录断言成一次确实到达 Provider 的调用。
min(8192, remaining) 只限制生成量；很小的 cap 可能导致 length，此时 terminal，
不得自动提高 cap 或以“这是纠错”绕过预算。prompt 总计费仍可能越过 remaining。

token/wall-clock 耗尽时，合法 Decision 仍可与 succeeded Step 一起记录，但不得执行工具或
materialize Proposal；不合法响应以 failed Step 保存反馈。steps/calls 上界仅阻止后续模型
调用，本次合法 STOP/Human/Proposal/Tool 保留既有交接（Tool 后不能再调用模型）。无效
输出若无 reserve 余额则 budget_exhausted，不能留下 ready 等待越预算纠错。

## 5. 决策三：展示隔离，只保留完整有效的动作

summary 不参与权限、执行参数或科学结果准入。采用窄而显式的降级：

1. 原对象严格验证；完全有效则走既有路径。
2. 仅明确完成且可用 usage 已知，错误全部精确位于该 Decision 顶层 summary，
   且属于 string_type/string_too_long/已注册 UTF-8 字节或编码错误时，构造只把 summary 设为 null 的候选。
3. 再对候选做完整严格验证；成功后才可持久化并交给工具/Proposal 路径。
4. 同一 Step 记录 `summary_omitted` 及安全错误位置；不保存被省略文字。

判断使用完整内存错误列表，不能只检查截取到前 8 条的日志。混合错误、嵌套同名字段、
额外 reasoning 字段、未知 key、非法 Proposal、权限/身份失败不属于展示降级。
新的未知 summary 校验码不能仅凭字段位置被忽略。不启用全局 Pydantic strict=True，
保持已有合法值与类型转换语义；“严格”在此指完整调用现有校验、未知字段拒绝及无业务补猜。
任何动作字段不得删除、截断、强行补值或替换。省略 summary 不使它成为科研事实；
保留的模型文字也不作为事实权威。旧成功 Decision 和旧 terminal Run 不回写或复活。

## 6. 决策四：每个 Run 最多一次已知输出纠错

此处“纠错”指同一 Run 内一次新的、收费且计入预算的生成。因不保存失败原始响应，
它根据相同绑定上下文与安全反馈重新生成完整 Decision，而不是对原候选做补丁。
成功率记录必须区分首次输出与纠错后输出。

| 条件 | 处理 |
|---|---|
| 明确完成、usage 总量已知、无歧义的 stop 响应 content 为普通 JSON syntax error / 非对象 | 可使用一次纠错；反馈只有固定错误码 |
| 明确完成、usage 总量已知、Decision 有下面列举的错误 | 可使用一次纠错，需全部错误都属于允许集合 |
| 只有 summary 错误且动作通过完整独立验证 | 省略 summary，不消耗纠错次数 |
| Snapshot/Dataset 身份类错误、未知/额外字段、unsafe path、非有限数值、未知自定义规则错误 | 终止，不能猜值或删字段 |
| 既有 tool policy / Proposal materialization / invalid-repeat guard 拒绝 | 保持原拒绝；不纳入自动纠错 |
| 证据不足或冲突 | 模型 request_human_input / stop；纠错策略不能补造证据 |
| length / content_filter / 缺少或未知 finish_reason / 网络异常 / reserved | 终止；不自动重发 |
| 未证实完成 / 不可用或不一致 usage / 重复 JSON 键 / 非标准常量 / 过深解析失败 | 终止；不自动纠错或执行降级候选 |
| 无预算、或该 Run 已用过一次纠错 | 预算终止 / invalid_agent_decision，不能开启新 Run 绕过 |

纠错资格采用固定白名单，并检查全部错误；超过安全反馈容量、动态位置不明确或任一
不可纠错错误时保守终止。不增加“所有 ValidationError 都重试”的通用分支。
Snapshot/Dataset 保护指既有身份/输入校验，不能借此新增“所有 Follow-up 必须永久沿用
上一轮 Dataset/Environment”的科研限制。新设计的合法改变仍由原 Verification/Approval 治理。

初始纠错白名单固定为以下路径/错误，不使用“普通字段”这种开放判断：

| 路径（均为原始完整 error loc，variant 前缀不可省略） | 允许的错误 |
|---|---|
| 根 discriminator（空 loc） | union_tag_not_found / union_tag_invalid |
| tool_call.tool_name；request_human_input.question | missing / string_type / 已注册空白、UTF-8、字节上界错误 |
| tool_call.arguments（容器本身） | missing / dict_type |
| stop.reason_code | missing / string_type / string_pattern_mismatch |
| propose.proposal.parameter_matrix（容器本身） | missing / dict_type |
| propose.proposal.seeds（容器）及整数索引元素 | missing / tuple_type（容器）；int_type / int_parsing（元素） |
| propose.proposal.timeout_seconds / max_retries | missing / int_type / int_parsing |
| 任一 variant 顶层 summary，与上述可纠错错误同时出现 | 仅 §5 的展示错误码；不是单独删除 summary 后放行 |

其他 loc/code 组合均不允许，包括整个 Proposal 缺失、路径/身份/Dataset/Environment、
参数矩阵动态键和值、额外字段、非有限数值、未知自定义 value_error。
实现前用真实 TypeAdapter 验证这些路径与错误码；只修正名称差异，不自动扩大资格。
首次纠错响应允许四种既有 Decision，不要求强行生成 Proposal；真实语义质量仍需另行验收。

### 最小持久化与 Trace

新增 `0015_agent_output_feedback`，仅在现有 `agent_steps` 增加一个 nullable JSONB
`output_feedback_json`（NULL 或 object 的 DB CHECK），不新增表、Run 状态或业务对象。
使用代码定义的有界 DTO，内容只允许：

- `correction_of_step_id`：当前调用纠正哪个失败 Step；保留 UUID，不接收模型值；
- `category` / `code`：固定 invalid_decision / summary_omitted / provider_invalid_output 等码；
- `error_count` 及最多 8 个已知位置/类型（每个 loc 最多 8 段）；
- `correction_allowed`：系统根据完整错误作出的固定布尔判定。

DTO extra=forbid；UUID/布尔/计数/固定 code 严格校验，errors/loc 使用有界序列，
总 UTF-8 canonical 大小 ≤8 KiB。人类文本、Provider 原文、input/ctx/msg/url 不进入该字段。
correction_allowed 表示当时的输出资格，不表示现在还剩一次调用权。剩余权由已有 Step 链推导。

常规/旧 Step 为 NULL；纠错 Step 在 reserve 时存 correction_of_step_id，之后 finalize
保留该链接。失败 Step decision_kind / decision_json 仍为 NULL。反馈和 Run 计数在同一
现有 finalize UoW 内保存，决不能借用 token_usage 或写不合法 Decision 存反馈。
读取损坏、非有界反馈时 fail closed；不能把它当缺省 NULL 获得新纠错次数。
reserve 的现有 Run 锁/CAS 内检查 correction_of_step_id：指向同一 Run 最新的 failed
model_call Step，有合法且允许纠错的反馈，且全 Run 尚无纠错链接。不接收 API/模型提供的链接。
finalize 必须要求目标 Step 仍 reserved；拒绝重复终结，防止重复计数或覆盖原反馈。
使用现有 conflict 路径；无需新表、JSON 索引、跨 Run FK 或后台计数器。
API Trace 只新增可选 `output_feedback` DTO 字段（对应 DB output_feedback_json），
不新增 endpoint 或后台事件系统。
Operator 最小显示“展示摘要已省略 / 已纠错一次”的事实，不展示原失败文字。
reserved 链接只证明纠错已预留，显示“纠错调用已预留”；unknown 不声称 Provider 已调用或纠错成功。

旧行不 backfill。upgrade 是 additive；反馈存在时 downgrade 拒绝，避免删除审计证据。
实现阶段核对唯一 head，若已有后续 migration，顺序调整而不覆盖历史。
旧 Step DTO/API 新字段默认 None；现有调用者与内存 repo test double 必须同步可选 finalize 参数。
真实部署不得滚动混用新旧代码：短暂停止接受 advance，等待 in-flight 请求/Runner 交接安定，
停止 API/Coordinator 后再复查无 reserved Step/active Attempt，upgrade 后统一启动并验 readiness。
不自动取消业务或强行清理记录。存在非 terminal Run 的新 feedback 时，禁止回退旧 Runtime：
旧代码会忽略资格而继续普通生成。需要保留新代码或经另行授权处置这些 Run，不能 drop 字段。

### 状态与崩溃边界

```text
reserve Step → 调用 Provider → 已知不合格输出
→ 原 Step failed + usage + feedback；Run ready（仅首次可纠错且有预算）
→ 从 Step 链检查尚未预留过纠错；复核输入绑定并编译安全反馈
→ reserve 新 Step（correction_of_step_id；此刻消费唯一纠错机会）→ 新调用
→ 合格：persist Decision，再工具/Proposal；不合格：terminal
```

上限是每个 Run 一次，不因成功工具调用、人工输入、advance 或进程重启重置。
未预留纠错 Step 时重启，可从失败反馈重新进入一次纠错；预留后崩溃，仍沿用
reserved → model_call_outcome_unknown，不能重新调用。反馈持久化失败则不能继续调用。
首次失败 finalize 后在同一次 advance 的既有 while loop 中继续；Run ready 是持久化恢复点，
不是要求 UI 再开一个并发请求。重启后的续进才使用新的 advance。Run ready + last failed
但反馈缺失/不合法时 terminal，不能推断还有资格。全 Run 的任一已有 correction_of_step_id
都消费唯一机会，不因后续工具/人工步骤重置。

CAS/row_version 防止相同版本的双预留，但不是跨请求执行 lease。若另一客户端持最新版本
在 active reserved 期间调用 advance，当前逻辑可能将仍在执行的调用判 unknown，并使原
finalize 遇到版本冲突。这是已复现的既有 fail-closed 可用性边界，不是安全并发支持。
本轮保留单 writer 操作约定及 Operator advancing guard；读状态用 GET，不重复 POST。
不增加进程锁/lease/恢复调度器；测试区分 stale-version CAS 与 fresh-version 干扰，
仅后者的“无第二次调用、失败关闭”可接受，不能宣称原调用正常完成。
pending Proposal 恢复走原路径，
不得重采样或重复 materialize。预算先检查，再 reserve；所有已知调用和 token 计入原 Run。

编译反馈使用 system-controlled 的独立字段，进入 canonical_request_hash；不伪装成工具
结果或人工输入。仅紧接符合资格的 failed Step 时传一次；既有源 Summary、frozen History、
repository/tool contracts 不被修改。不新增 hash family、恢复服务或调度器。

所谓“相同上下文”必须验证，不仅写在提示词里：纠错前检查 Run/失败 Step/当前 Provider
的 provider/model 一致；用失败 Step 原 semantic_parameters、不带反馈重编译基础上下文，
比较其 canonical_request_hash 与失败 Step 的既有 hash。任何代码策略/schema/toolset/证据
变化导致不相等，terminal `correction_context_changed`，不偷偷在新上下文重试。
通过后再编译安全反馈与本次实际 cap，得到新的请求 hash。复用现有 Compiler/hash，
无需存原坏响应、增加上下文投影或新 hash family。此检查也覆盖重启期间的代码版本漂移。

## 7. 实施顺序与文件范围

先在此分支实施，不 merge main / push / 迁移演示库 / 重启服务 / 调用付费 Provider。
不增加依赖。使用现有 pytest、ScriptedProvider、httpx.MockTransport、SQLAlchemy/Alembic。

| 阶段 / 建议提交 | 生产范围 | 最小检查 |
|---|---|---|
| A `fix(agent): align output contracts and provider completion handling` | types.py、context_compiler.py、provider.py 完成事实字段、deepseek.py、service.py 记账、既有 provider_name 分支（main 装配和 OpenCodeGo 代码无须改动） | schema/字节边界；实际 cap hash；finish_reason；known usage |
| B `fix(agent): isolate summaries and bound decision correction` | models.py、repository.py、types.py、service.py、context_compiler.py、0015、api/schemas/agent_runs.py、operator.html | 降级严格性；反馈与 CAS；一次纠错；重启/未知；Trace/迁移 |
| C `test(agent): cover output reliability and close regression baseline` | 扩展现有 tests；如必要仅一个 focused output reliability test 文件；验收后更新 README/status/evidence | 平衡案例、P1/P1-C、完整 CI；原失败保留 |

各阶段先补能失败的最小回归，再实现。若 focused 检查证明范围需要扩大，先记录具体
原因再调整方案，不顺手改训练、Result Plane 或 framework。验收文档引用真实实现提交，
不在同一 commit 自引用，不把前一阶段通过冒充最终合并 gate。
阶段 A/B 都须保持可运行；迁移、读写反馈、纠错和 Trace DTO 在 B 一起交付，不先打开纠错
再补持久化。测试与其对应实现同提交，C 是最终验收/文档收口，不延后必需测试。

## 8. 验收清单

**离线回归设计标准（设计时 NOT RUN；后续实际结果见维护收口）：**

| ID | 场景 | 必须观察到 |
|---|---|---|
| O1 | 四类 schema、ASCII/中文/多字节边界、空白、非法 UTF-8 | 契约披露；合法值仍合法，超界仍拒绝；字节与字符不混淆 |
| O2 | 顶层与嵌套未知字段；dynamic arguments/matrix | 未知字段拒绝；合法动态参数不被错误禁止 |
| O3 | 完整响应、非 JSON、重复键、多 choices、length/filter、完成标记、NaN/过深 | 分类正确；歧义/部分输出无动作；旧 Provider 无新纠错资格 |
| O4 | cap、剩余预算、小 cap、wall-clock、未知/部分/不一致 usage、恰好最后一次调用 | wire/hash/Step 一致；已知 usage 记账；未知费用不续跑；最后合法响应按原语义处理 |
| O5 | 仅 summary 非法；summary+动作非法；超 8 个混合错误 | 仅独立有效动作降级；混合/截取反馈不能绕过验证 |
| O6 | invalid → valid；invalid → invalid；valid tool 后再 invalid | 最多一次纠错，调用/Step/usage 正确；无业务增量直到合格 |
| O7 | finalize 失败/重复；反馈后重启；reserve 后重启；stale/fresh-version 并发；跨代码/模型/证据改变 | 一次纠错和一次记账；unknown 不重发；输入漂移终止；不冒充 lease 并发支持 |
| O8 | 权限/身份/unsafe path/invalid-repeat 拒绝 | 不自动改值、不绕过；既有拒绝语义保持 |
| O9 | 全量目标已满足、证据不足、历史不可比较、显式/开放参数 | STOP/人工输入/合理 Proposal 都可成立；不固定某组超参 |
| O10 | 0014→0015、fresh head、旧 NULL Trace、feedback Trace、downgrade | additive 无 backfill；旧记录回放；存在反馈时拒绝破坏性降级 |
| O11 | 动态键/异常文本/原始坏 JSON/usage 扩展含 secret sentinels | 安全诊断与 feedback 的日志/DB/API/UI 无输入泄露且有界；不宣称合法模型动作文本能自动脱敏 |
| O12 | P1 two-iteration、历史比较、Proposal pending replay | 既有审批、来源绑定、guard 和 materialization 语义保持 |

O9 的脚本化 Provider 用于验证接收/执行行为，不冒充真实模型的决策质量。
参数开放案例用允许集合/边界、证据关联和新条件检查，不能用特定 epochs 值作 golden answer。
新增纠错用的 mock 返回 response_completed=True 和已知 usage；旧 empty-usage/未标记 mock
只验原正常路径，不能证明真实 Provider 已完成或有可靠成本。状态机/CAS/重启/反馈必须有
SQL 集成检查，不能仅由宽松 MemoryAgentRepository 得出持久化验收结论。
沿用现有 tests/unit/agent_runtime/{test_context_compiler,test_deepseek,test_agent_service}.py，
tests/integration/agent_runtime/test_agent_service.py、test_result_history_binding.py、
tests/integration/p1/{test_result_aware_iteration,test_historical_comparison}.py 和 DB/API 测试。

最终执行 `./scripts/check_ci.sh`，逐项记录依赖冻结、lock/sync、Ruff、mypy、
unit/architecture、integration（含 skip/deselection）、dependency guard 的实际结果/数量。
负向约束测试的 PostgreSQL ERROR 不等于 pytest 失败。文档不能预填 PASS。

**真实模型质量（另行授权，NOT RUN）：**

离线通过后，固定案例、模型、输入/证据、总调用及出境预算再申请少量 live 验证：
至少覆盖显式参数、开放 Follow-up、应停止、应请求人工输入四类；不重采样挑成功案例。
记录首次符合率、一次纠错后符合率、正式 Verification、证据依据、调用/token/时间成本。
正确 STOP/人工输入是成功结果；出现 Proposal 不等于实验性能达标。
采用确定性 grader 加人工检查，不增加 LLM judge 或评测平台。
[评测参考](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
所有真实案例都保留；原两个失败仍 NOT ACCEPTED，不追溯改写。
四类各一个样本只报告逐案结果与 n，不推断长期成功率或统计稳定性；先验收明确指定参数
和有限新条件 Proposal，再观察 STOP/人工输入的正反场景。真实费用预算须包括一次纠错。

**真实 host（另行授权，NOT RUN）：**

实施与离线收口不等于 interview 部署。按 §6 停止写入并复查无 reserved Step / active Attempt；
保护已有行与失败观察。授权后才对专用 interview DB upgrade head、加载 API/Coordinator，
检查 readiness/operator/ops、Runner/socket、旧 Trace 及新字段。无须训练。
网络 HEAD reachability 不是付费模型或 credential acceptance；不宣称 global exactly-once。

## 9. 作者自审与规划检查（设计阶段历史记录）

自审不是独立 APPROVE。已处理的具体风险：

- 内存纠错计数无法跨重启：一列有界反馈和既有 CAS/UoW 解决；不新增状态机服务。
- 日志截断可能漏掉第 9 个安全错误：降级/资格检查使用全部错误，过多反馈保守拒绝。
- 不保存坏响应无法 patch 原候选：明确为相同上下文重新生成，失败和成本仍保留。
- 未知调用与格式错误混淆：完成信封才可纠错；reserved/网络/截断不重发。
- 隐含 Provider cap 未进入请求身份：复用现有 semantic_parameters，wire/hash/Step 参数同源。
- 展示错误吞掉动作错误：仅顶层 summary，候选再做完整验证；旧记录不回写。
- 为减少机械抄写而扩展领域协议：本轮仍完整 ProposalPayload；Follow-up intent/确定性组装 deferred。

规划检查：当前 baseline clean；代码/存储/API/Provider 对照完成；离线 schema/未知键探针
完成；文档链接/范围/whitespace 在提交前核对。基线现有
test_context_compiler.py + test_deepseek.py：**30 passed / 0.36s**，使用 main 的现有 .venv、
`pytest -q -p no:cacheprovider`，没有 DB 或 Provider 网络调用。这只是旧实现回归，
不代表本设计已实现。实现、迁移、完整 CI、host、live 均 **NOT RUN**。

深度复审补充：7 个旧实现离线探针确认重复键、多 choices、坏 content 丢 usage、
不一致 total 优先、响应后墙钟丢 usage、最新版本 advance 干扰 active reserved、
Memory repo 重复 finalize（SQL 源码也缺少 reserved predicate）。没有操作真实 DB/服务/Provider。
这些是基线诊断，不是新设计已通过；详见复审记录。修订后实际实施、迁移、全量 CI、host/live 仍 NOT RUN。

本次复审另运行现有 ContextCompiler、DeepSeek、AgentService、ResultContext、ResultHistory
五个 unit 文件：**115 passed / 1 warning / 1.90s**。warning 为已有负向字段测试的 Pydantic
序列化提示；pytest 无失败。这些仍是旧代码离线回归，不是新行为验收。

需要批准的具体变化是：summary 展示降级、每 Run 一次纠错、一个 Step JSONB 反馈及 additive
Trace 字段、DeepSeek 显式输出 cap/完成状态和已知 usage 记账。它们改变运行语义，必须按
这份具体方案评审；设计阶段不先实施。用户已授权的是规划，不重复申请读取或文档编辑权限。

## 10. 明确不做

不添加新 Agent Framework、Multi-Agent、Memory、MCP、插件、Context Compaction/Projection、
分布式调度、自动审批/训练或 native schema endpoint 迁移；不引入依赖和通用 retry 框架。
不改 Result Plane 科学准入、现有 invalid-repeat 算法、Slice 2 reconciliation/Runner 权限。
Slice 2 production hardening 仍 deferred；不声称自主科研或 production-scale scheduling。
Follow-up 最小变更 DTO/确定性 Proposal 组装待本轮效果与成本证据后单独规划。
