# Spec 0008 Amendment B：任务修订与依赖

基线：`codex/v0.4.0-dag` / `b564f9e3c64477d3c95277cfb9ebb2f58f278a23`，Spec 0008 与 `.dag/definition-github-spec-0008.json`。
本文件是[主修订](0008-amendment-b-delivery-plan.md)的责任映射；[来源与时间契约](0008-amendment-b-source-time.md)、[结果包恢复协议](0008-amendment-b-artifact-protocol.md)与本文须一同审查。维护者已采用 B1–B3 推荐方案；本地修订尚未应用到 Tracker 或绑定为 Definition Checkpoint，不表示 Ticket 已接受或 DAG 已启动。V4-U、V4-I、V4-F 是逻辑 ID，不是 GitHub Issue 编号。

## 1. 最小结构决定

- 新增 `V4-U`（可执行时点化三交易所证券池来源）、`V4-I`（盘中三交易所成交额和证券去重广度来源）与 `V4-F`（有界来源可行性探索）。
- 冻结筛选上下文 `C` 由已有 #117 拥有；#120、#69 消费，不新增上下文 Ticket。
- #108 保持原票，按结果包协议修订；不按修复轮次新增 Ticket，不将受限平台测试当作跨平台生产证明。
- #110 保留纯 Universe 归一化、冻结输入与分母对账；`V4-U` 填补其明确排除的 provider-specific acquisition/parser。
- #112 保留完成交易日成交额及已公开两融语义；盘中 owner 另由 `V4-I` 提供，禁止将日报伪装成当前快照。
- #93 直接消费 #117/#118/`V4-I`，不再把 #112 当作盘中输入前置；#96 显式依赖 #112 与 #93。
- `V4-U` 不直接依赖 #116：现有 seam 是 `ExchangeListOutcome` / `IdentityBatchOutcome` → #110 prepared snapshots，未使用 `OperationOutcome`。
- `V4-I` 依赖 `V4-U` 的当前适用会话 membership；筛选使用的前一个共同完成交易日 universe 不得代理当前盘中分母。
- `V4-F` 为 #112/#117/#118/`V4-U`/`V4-I`/#120 提供实现前来源证据，不阻塞 #116/#119/#108；探索记录不替代 #98 的冻结候选最终 attestation。

## 2. 已批准的硬依赖与无环证明

下表保留所有未列为变更的基线 hard edges；既有直接消费依赖予以保留；新增直接消费关系显式列出，纯排序关系不加 hard edge。

| Ticket | 修订后的直接 hard predecessors | 原因 |
| --- | --- | --- |
| #101 | 无 | 保持基线 |
| #105 | #101 | 保持基线 |
| #110 | 无 | 已接受输入基础在 captured integration tip 中，保持基线 |
| #119 | 无 | 保持基线；不是 universe、文件系统或排名 Ticket |
| V4-F | 无 | 独立有界来源探索，不依赖生产实现 |
| #114 | #105 | 保持基线 |
| #115 | #101 | 保持已接受 prepared-input 契约，不自动重开 |
| #116 | #105 | 保持基线 |
| #108 | #115 | 保持基线；不直接消费来源或日历实现 |
| #117 | #105、#116、V4-F | 扩大已拥有的 session/context 输出，先消费日历可行性证据，不依赖筛选 workflow |
| #118 | #105、#116、#117、V4-F | 保留基线，增加固定四指数可行性证据 |
| #112 | #114、#116、V4-F | 保留日报/两融独立输出，先消费来源可行性证据 |
| V4-U | #110、#119、V4-F | 消费现有 universe seam、实际请求计数 lane 与清单/身份可行性证据 |
| V4-I | #116、#117、#119、V4-U、V4-F | immutable outcome、当前会话、请求预算、当前 membership 与盘中可行性证据 |
| #120 | #110、#119、#117、V4-U、V4-F | 保留原输入/调用边界，增加 C、生产来源及批量因子可行性证据的直接消费 |
| #68 | #108、#120 | 保持排名和持久化组合边界 |
| #69 | #68、#117 | 保留排名前置，显式消费上下文准备与恢复接口 |
| #93 | #117、#118、V4-I | 删除 #112；盘中输入确有独立生产者 |
| #96 | #93、#112 | overview 同时集成盘中与完成日/两融能力 |
| #97 | #69、#96 | 保持两条完整产品链汇合 |
| #98 | #97 | 保持冻结后最终 live attestation |

修订图共 21 个节点、40 条 hard edges。一个合法拓扑分层为：`{101,110,119,V4-F}` → `{105,115,V4-U}` → `{114,116,108}` → `{112,117}` → `{118,V4-I,120}` → `{93,68}` → `{96,69}` → `{97}` → `{98}`。
分层仅证明无环，不代表所有同层票应并发；实际 ready 仍需回读 accepted/claim 状态。
不存在 `#69 → #120` 边；C 不在 #69 内生成上游依赖，因此不会形成筛选闭环。
#69 显式依赖 #117 的上下文接口；既有 #68 依赖仍保留。

## 3. V4-U 完整定义

**Objective**：为 SSE、SZSE、BSE 提供可执行、资格驱动、有界的时点化 A 股清单和批量身份 production Adapters，使 #110 的确定性归一化能够消费真实来源形状。

**Interface 与输入**：复用 #110 的 exchange-list / identity Adapter seam、prepared snapshots 和 #119 provider lane；接收明确 effective date、information cutoff 与调用者已冻结的 research clock。
清单和身份 acquisition 的时间参数在内部明确表示 membership effective date；screening façade 将其绑定 `screen_date`，盘中消费者绑定当前适用会话日期，二者不得混名或静默互换。

**输出**：三交易所规范 membership 记录、清单/身份 owner 与安全 provenance、生效区间、公开时间、实际 retrieval time、页数/expected count、实际 attempt evidence、五种 source-result 状态；输出可供 #110 对账并供 V4-I 建立当前分母。

**范围**：仅本 Spec 所需 owner；无代码前缀猜测、当前名单反推历史、证券类别混入、隐式逐证券无限补抓或 owner brand fallback。
历史来源缺失时如实形成 partial/unavailable 或 unproven，不能把未知名单数量当 0；当前 membership 使用同一来源资格和采集代码，不重新实现另一套清单产品。
不负责决定共同完成交易日、排名、artifact、候选深研或公共 workflow 注册。

**依赖与消费者**：hard predecessors 为 #110/#119/V4-F；直接消费者为 #120/V4-I；正式 manifest 由最终实际 workflow obligations 派生。
不为复用名词而增加 #116 依赖；如果实现需要向 UnifiedResearchResult 投影，使用既有 operation qualification/projection 契约，不重写 #110 的 immutable snapshot 权威。

**AC**：

1. 三交易所 production Adapters 从 copied-installed Skill 可加载；通过注入 transport 真正调用 production parser/normalizer，禁止永久占位失败 owner。
2. 每个 owner 明示日期/历史可查询范围、清单范围、身份依据、生效/公开时间、分页终止条件、数量证明与有限 request/page budgets。
3. 所有初次调用和 retry 都由 #119 在真实调用点计数；嵌套调用不能绕过 budget，同组 timeout 后不能重叠执行。
4. 仅存在当前名单、缺历史生效证明、缺/重复页、声明计数漂移、跨所身份冲突或非 A 股污染时失败关闭相应完整结论；其他有效记录保留。
5. #110 的 `expected = resolved + failed`、正向排除与 unproven→unranked 语义不变；BSE 缺失阻断 full-A-share 结论。
6. 当前会话 membership 和完成日筛选 membership 使用各自明确锚点；新增/退市/类别变化当天不得通过复用前一日分母被忽略。
7. 原始 payload、凭据、绝对路径及未脱敏异常不进入公开输出；attempted 与五种 source-result 状态独立保存。
8. 资格文档与生产形状离线样本证明实现假设；不以离线通过声称 live 成功，最终 live 仍归 #98。

**验证**：三交易所日期切换、上市/退市边界、当日/历史不互换、分页数量、缺 BSE、预算耗尽及 mutation/provenance RED→GREEN；调用 #110 公共 Module 对账；copied-install production-parser 回归；保留基线全量质量与独立双审门禁。

## 4. V4-I 完整定义

**Objective**：交付当前有效交易会话内 SSE/SZSE/BSE A 股累计成交额分区与证券粒度去重广度正式 owners，为 #93 提供可裁决新鲜度和完整分母的盘中事实。

**Interface 与输入**：#117 当前 session context；V4-U 对该会话日期及 cutoff 取得的 canonical membership；#119 有界 provider lane；通过现有 Market Foundation operation Adapter seam 返回 #116 immutable outcomes。

**输出**：三交易所累计成交额分区及其 asset scope/unit/source observation time；按 canonical security 去重的上涨/下跌/平盘与停牌、无可比前收、缺失/失败等显式计数和分母对账；安全来源谱系与 actual-attempt evidence。
广度必须公开已分类与未分类/缺失部分及其语义；涨跌判断的前收/除权基准、盘中价格类型不明时不能强行归类。无成交/停牌不是平盘的默认代理。

**范围**：只交付当前会话累计/横截面观测，不交付逐笔、分钟流、盘口或订阅；已有行业板块广度仍保留原能力语义，不得累加板块涨跌家数产生证券去重广度。
不能把 #112 的完成日成交额、单指数/ETF 成交额或 stale 值当盘中字段。

**依赖与消费者**：hard predecessors 为 #116/#117/#119/V4-U/V4-F；直接消费者 #93，之后 #96/#97/#98。
V4-U 是实质数据依赖：用于当前 session expected membership；不得用“名称同为 universe”复用上一筛选日分母。

**AC**：

1. 三交易所成交额与证券广度 production owners 可从 installed copy 加载，经注入 transport 执行真实 parser；字段、源时点、币种/单位、A 股资产范围和限定用途明确。
2. 每个交易所的 expected membership 来自同一适用会话；canonical IDs 去重、跨所冲突和分母变化有显式裁决，非 A 股永不进入。
3. 分类条件有来源语义证明；停牌、无可比基准、缺失、冲突与失败不冒充上涨/下跌/平盘或成功空集；全部计数非负且对账闭合。
4. 活动会话 observation age 与跨来源 skew 必须能按原始时间证明不超过 60 秒；retrieved_at 不替代 observation time。
5. 缺交易所、来源未闭页、超时或不可比字段保留有效分区和局部失败；没有完整证券分母不得声称全 A 股广度或全市场同步完整快照。
6. 每次 actual invocation/retry 受 #119 约束；同组串行至 quiescence，独立组不超过三个；高覆盖采集不得偷偷绕过总页/请求限制。
7. 产出由 #116 封存并生成 detached plain JSON；来源事实与 #93 跨 owner fold 分工明确。
8. 当前会话外不生成伪当前值；#93 保存 auditable skip，其他 overview 能力可继续。最终 live freshness/coverage 证明归 #98。

**验证**：三个交易所同日/跨日、新股/退市切换、duplicate canonical ID、板块重复成员反例、停牌/除权/缺前收、零/空/失败区分、60 秒边界与预算/超时；生产形状 fixture→真实 parser→immutable outcome→#93 fold；保留全量质量和独立双审。

## 5. 受影响既有 Ticket 的责任增量

以下 delta 与原 AC 取并集；未明确修订的原义务继续有效。accepted 表示历史权威，不在本文件重新宣称任何 Ticket 已接受。

| Ticket | Objective / 输入→输出 delta | AC / 验证 delta |
| --- | --- | --- |
| #105 | 继续统一 source-error/qualification 公共契约；接纳实际计划声明的 U/I owner，无品牌总量目标 | 新 owner 沿用唯一来源状态/attempt authority；不为新票造第二 manifest |
| #110 | 不新增 provider parsing；消费 V4-U 同 seam 输出，继续投影 immutable universe | 既有分母、时点、mutation AC 全保留；使用 C 原冻结时钟，不按 resume 当下重新解释 cutoff |
| #116 | 保留唯一 immutable Operation Evidence；增加 V4-I 为消费者 | 不新增平行包装器；已有所有 operation-owned 字段不可变与 detached projection AC 不变 |
| #117 | 扩展 session authority 为 C：请求日期+一次 aware research_now+三所日历→共同已完成 screen_date、cutoff、calendar proof/digest | 当前日期用原 research_now，历史日期用 23:59:59，未来拒绝；未收盘用前共同完成日；不能用单所日历代理；C 冻结、投影独立且可验证 |
| #118 | 固定四指数继续消费 #117 session context | 保留原四项身份与源时间 AC；与 I 组合时保留可比价格/时间语义，不能被完成日指数值代理当前值 |
| #112 | 明确完成交易日 A 股成交额与已公开两融→日报 partition/fold；消费者由 #93 改为 #96 | 六 owner、exact normalization、applicability、三所必需与源状态 AC 全保留；不得承担 60 秒盘中快照承诺 |
| #115 | 保留已接受的两个 prepared-input 工厂；#108 优先经现有安全记录表达 C，不改工厂权威 | C 日历/时点语义由 #117 验证；Decision A 与 ordinary hostile input AC 全保留；若现有工厂无法表达批准后的结构，先明确新增变更责任与验证，再更新定义，不借历史接受直接覆盖 |
| #108 | 完成原 lifecycle；持久化与复验 C/producer/plan/operation identity；实现批准后的文件系统支持与恢复协议 | 保留 prevalidate-before-mutate、ownership、write-once、manifest-last、tamper/unknown residue 拒绝；按协议变化更新 checkpoint/manifest/receipt 测试，不另拆 repair 票 |
| #119 | 一条 provider lane 管 initial/retry/timeout/quiescence；U/I/120 复用 | actual attempts 与 observable invocation 一致；不扩为通用调度框架；容量测试从 setup 外计时，清除 ignored runtime 污染 |
| #120 | C+V4-U→时点合格 factor partitions；不再假设只有合成 Universe 可用 | 每证券身份/日期/cutoff 与 C 一致；20 个适用成交日、财务 available_at、factor-local unranked、实际预算 AC 保留；增加最大批准规模容量证据 |
| #68 | 合格 factor+C 引用→固定四因子排名与 artifact 分区 | 固定数学/权重/并列/unranked 不变；三所小型纵向案例验证 C、分母与 artifact 引用，缺失不得改分母或重加权 |
| #69 | C+冻结排名→有界候选、receipt；C 由 #117 提供，不由候选链反向供应上游 | 按已批准 B1 同步严格输入、模板与候选映射：standard 显式 target_pe，fast 禁止；缺类别证据不补值，BSE/历史限制显式 blocked；create 捕获一次时钟，resume 复用原 C；按已批准 B2 接入 finalized receipt recovery；失败不改排名 |
| #93 | #117 session+#118 indices+V4-I turnover/breadth→market_wide_intraday fold | 去除日报/行业广度代理；保留 60 秒双边界、失败隔离、会话外 skip；I 的分母不完整不得生成完整市场结论 |
| #96 | #93+#112→十五项 overview @2.0 | completed-session 与 current-session obligations 分别声明；保留已有十能力/tier；新增广度 owner 服务既定盘中 capability，不擅增第十六能力 |
| #97 | 两条完成链→干净 installed-copy E2E、文档和分发 | 三所合成端到端场景、跨日 resume、C 漂移、真实平台持久化和干净复制测试；不复制 .runtime/pycache；全部版本与公开能力一致 |
| #98 | clean candidate→typed attempts→final evidence→durable matrix | 精确 owner 分母与下表 release-critical capability 均闭合；已批准 B3 的 early feasibility 不可代替同候选新鲜 attestation |

### C 的 create / resume 约束

1. create 时在统一 orchestrator 边界只读取一次 aware `research_now`，#117 决定并封存 `requested_as_of`、`information_cutoff`、`screen_date`、原时钟与日历证明引用。
2. #120 向既有 #110 传 C 的原时钟；这利用已有 `research_now` 参数，不要求 #110 引入 #117 的类型或新公共字段。
3. resume 先由 #108 复验请求/producer/plan/operation/C 摘要及已完成字节，再由 #117 复核保存的上下文语义与证明；禁止按恢复当天重新计算 cutoff。
4. #69 对候选复用同一内部 frozen context；仅请求日期相同不等于 cutoff 相同。保持原唯一公共 CLI 和单一 `as_of`；仅 B1 显式批准的筛选 inputs 变化例外。
5. 原日历证明不全或持久化 C 漂移时失败关闭；不通过临时重取新上下文绕过同请求 resume 约束。

## 6. 原验收义务保留矩阵

| 基线义务 | 唯一主要 owner | 消费/整体证明 | Amendment B 不得删除的内容 |
| --- | --- | --- | --- |
| 严格公共请求/唯一 CLI | #101 | #69/#96/#97 | per-workflow version、未知形状拒绝、无临时生产入口 |
| 来源状态/实际执行分离 | #105/#119 | U/I/#120/#98 | 五种 source-result 状态、真实 attempts、exact manifest |
| 深 immutable operation proof | #116 | #112/#117/#118/I | mutation 不改存储，plain JSON 不别名 |
| 时点三所 Universe | #110 + U | #120/I/#97/#98 | 非 A 股排除、BSE 必需、缺证据不消失、数量对账 |
| 固定四指数 | #118 | #93/#96/#98 | 精确集合、源时间而非获取时间、局部有效值保留 |
| 完成日 turnover/两融 | #114/#112 | #96/#98 | 六 owner 资格、精确数学、applicability 证据、不兼容不相加 |
| 盘中同步市场事实 | I + #93 | #96/#98 | 当前 session、60 秒年龄与 skew、证券去重而非行业累加 |
| 因子与排名 | #120/#68 | #69/#97/#98 | 固定四因子、20 日、cutoff、Decimal、缺失不补值、不重加权 |
| Result Artifact 安全生命周期 | #115/#108 | #68/#69/#97 | prepared authority、先验证再修改、未知残留不动、write-once、摘要复验 |
| 有界候选深研 | #69 | #97/#98 | fast=0；standard 默认 5/1–20；失败不改排名；相同冻结上下文 |
| 分发/质量 | 各票 + #97 | #98 | 原 full Python 3.12、Ruff、strict mypy、Skill validation、双审等门禁不删 |
| 最终发布 attestation | #98 | Whole-DAG acceptance | 同 clean candidate、新鲜精确分母、全部来源状态、无自动发布 |

## 7. V4-F 有界来源可行性探索

维护者已采用 B3，将 `V4-F` 纳入本地修订图；该决定批准独立、有限预算的 early-live 可行性阶段，不表示探索已执行或生产来源已获资格。该阶段随正式定义应用后执行。
生产来源票实现前先读第一方契约与已授权留存的来源形状，逐 owner 给出日期/字段/单位/许可/历史范围/分页/预算表；找不到证据的项标为未证明，禁止据此伪造 production shape 或 live 成功。
所有普通测试继续离线；阅读官网与 provider live attempt 分开记录。

`V4-F` 不依赖生产实现，不写发布资格、不更新 fixtures、不计入 #98 attestation，不提供通用探针公共 CLI。
开始探索前，执行者先编制可审查的有限预算来源清单，固定候选 owners、访问范围、请求上限、证据保存/脱敏边界及停止条件，并取得维护者对该具体清单的确认；这些具体执行参数尚待形成，不需重复批准 B3 的政策选择。
V4-F 产出“可行/有条件/未证实”的来源语义与覆盖证据。#112/#117/#118/U/I/#120 六张来源实现票均直接依赖 V4-F，消费各自相关证据；不阻断 #116/#119/#108 等纯基础票。
V4-F 不依赖这些消费者，故加入后仍无环。其逻辑 ID 尚未映射真实 GitHub Issue，Tracker 与 Definition Checkpoint 仍待应用。

V4-F Ticket 定义：

- Objective：在实现前确认 release-critical 数据获取是否有可证语义、时点与容量路径，尽早给出不可行/未知结论。
- Hard predecessors：无；它不能依赖 U/I 或其他生产 owner 已实现，也不得临时接管生产实现。
- 输入：已批准的 Spec 修订、capability→候选 owner 表、按现有权限明确的访问/许可与单次探索预算；不得读取或输出无关本地凭据。
- 输出：带时点、来源定位和限定范围的脱敏可行性记录，保留失败、覆盖缺口与尚需批准的语义/预算变更。
- AC：开始前形成上述可审查来源清单并取得维护者确认，只执行已确认清单内有界查询；核验历史清单能力、财务公开时间、20 日批量覆盖、BSE 和盘中时间/广度语义；未知仍为未知。
- AC：达到预算、访问限制或语义矛盾时停止该来源；不得用未批准 fallback、重复重试或修改 product scope 获得表面成功。
- AC：记录不计入正式 operation 资格完成或最终 release evidence，不回写普通测试 fixtures，不改变生产 registry。
- Verification：独立检查范围、次数、脱敏、时点和结论证据匹配；无 full production suite 要求，因为该票不修改生产实现。
- 消费者动作：可行则按既定契约实现；不可行则呈交维护者明确选择替换合格 owner、批准范围修订或保持阻塞，不能自动降低 AC。

## 8. 批量容量与 release-critical 证据表

容量证据由 #119/#120/U/I/#108 各自 owning seam 产生，#97 汇总；避免新增通用性能框架。
每个 owner 列有限 pages×attempts、初次/重试共同预算、节流/超时上界；每次批量结果报告实际调用次数及丢失范围，不从“有限”推导可接受耗时。
以批准的最大证券数/页数/记录数/分区数形成合成容量样本，记录请求总量、最大活动 lane、峰值内存/磁盘、checkpoint 与统一结果字节、取消/恢复时延；包括预算刚好耗尽、慢/拒绝取消 owner 和 partial resume。
保留原 64 KiB（65,536 字节）checkpoint 门槛；按恢复协议预检当前及未来合法状态的容量，并在写入前证明固定分区及至多 20 个候选的 file-count envelope。容量不足须先调整合规分区方案或提交明确变更，不能提高或忽略门槛。
资源 SLA、支持文件系统/平台矩阵及新公开参数属于显式设计决定；未定前只报告实测容量和已证明边界，不凭空给数字。

| Release-critical 能力/链路 | 可用 owner/必须证明的结果 | 不足以完成的证据 |
| --- | --- | --- |
| market_trading_session | #117 三所日历/阶段与共同完成会话证据 | 本机时钟、当前 retrieval time、单所日历 |
| major_broad_indices | #118 精确四指数，适用时点/价格语义与谱系闭合 | 单指数成功、缺北证 50、旧值新获取时间 |
| whole_market_turnover | #112 三所 A 股完成日分区和可比总计 | ETF/指数/债券成交额、只覆盖两所 |
| whole_market_margin | #112 各适用所/字段实际可用；不适用有正式规则证明 | unavailable 当空集、任意文字 not_applicable |
| market_wide_intraday | I+#93 三所当日 membership、成交额、去重广度、四指数/session，60 秒 age/skew | 日报、行业家数、上个筛选日分母、部分快照冒充完整 |
| as-of full-A-share universe | U+#110 三所完整清单与身份分母，cutoff 前可证明状态 | 当前名单反推历史、BSE 缺失、缺失数量当 0 |
| fixed-plan factor/ranking | #120+#68 固定四因子、20 日、财务 available_at、按既定资格形成的排名结果与完整 unranked 对账；完整业务证据证明 zero eligible 时允许空排名 | 全部 source_failed 后仅有空排名、插补、缩分母、动态改权重 |
| screen artifact + candidates | #69 copied-installed fast/standard，C 一致，partial/resume/finalize/receipt，局部失败不改排名 | 只有模块 mock、未读回摘要、恢复后新 cutoff |

最终 #98 仍需一次三交易所完整筛选 E2E 与一次合格当前会话盘中 E2E；每个新增 release-critical capability 至少有一个语义可用 owner，`complete`/适用的 `observed_empty`/被能力明确允许的 `partial` 判定沿用 Spec。
未知/缺失核心分母不能靠 partial 豁免；非空证券池或固定四指数也不能以 observed_empty 替代所需数据。
operation attempted 100% 与 capability 可用是两项独立门禁；跨候选、过期或提前可行性 evidence 不参与最终并集。

## 9. 执行与验证说明

- 前置可并行不等于必须并行；按共享文件、实际 integration tip 和双审吞吐限流，不把 provider 最大三组混同 Agent 数量。
- 保留每票原质量门禁；修复测试输入环境和工具复现性，不通过删测试、放宽 timeout 或降低审查范围加速。
- #69/#96 已有 installed-copy E2E；新增的是更早共享的小型三所场景和跨 seam 断言，不声称原计划完全没有 E2E。
- 本地修订不改变 push/tag/Release/Tracker 写入的授权边界；必须另行应用定义、回读依赖和接受状态，再启动实施。
