# Spec 0008 Amendment B：来源、研究时间与候选复用

## 决策状态与适用边界

本文基于本地 `codex/v0.4.0-dag` 的 `b564f9e3c64477d3c95277cfb9ebb2f58f278a23`，补充 Spec 0008 中尚未闭合的生产来源责任、时间上下文和候选调用映射。维护者已明确采用 B1–B3 推荐方案；本文记录已批准的本地计划规范，尚未应用到 Tracker 或完成新的 Definition Checkpoint，也不表示实现或来源验证已经完成。该决策不包含 Tracker 写入或发布操作。

v0.3.0 仍是当前稳定版本。已有 accepted commits 只证明各自已验收的接口与行为；确定性 Adapter、资格合同、离线测试通过和安装可加载均不能被表述为真实来源已可用，或新 workflow 已完成交付。本文不改写旧 release、tag、attestation 或 accepted Ticket 的历史记录。

候选 `target_pe` 请求变化、保留旧 BSE/历史候选深研能力边界以及提前进行有界来源可行性探索均已按[主提案](0008-amendment-b-delivery-plan.md)的 B1–B3 推荐方案确定，无需再次选择同一方案。本文中的责任名称是本地计划名称，不是已创建的 GitHub Issue；正式 DAG 仍须完成主提案规定的 Tracker 应用与定义绑定流程。责任映射见[任务修订](0008-amendment-b-tickets.md)，持久化边界见[恢复协议](0008-amendment-b-artifact-protocol.md)。

## 1. 三交易所历史 Universe 的生产来源责任

新增 Universe Production Sources（V4-U）责任，交付 SSE、SZSE、BSE 的实际 exchange-list 和 identity production Adapters、默认装配及逐 operation 资格材料。该责任消费 #110 的 Universe Interface，不重新实现其快照准备、身份对账与不可变投影；#110 的完成不代表这些生产 Adapters 已存在。

生产责任必须拥有以下事项：

- 将规范请求中的 `screen_date` 与 `information_cutoff` 映射为具体来源查询、历史清单或可核验的上市状态事件；不得用当前清单反推历史 expected universe。
- 明确每个来源实际支持的历史日期范围、证券类别、交易所、分页/总量证明、标识符变更和上市状态生效区间。支持范围尚未查明时记录 `unknown`，不得宣称任意历史日期可用。
- 生产获取、原始形状解析、单位/时间归一化、source-result 及实际 attempts 记录由 operation owner 负责；纯 Universe Module 只消费准备后的规范结果。
- BSE 是独立且必需的身份分区，不使用 SSE/SZSE owner、代码前缀或新三板清单代理。旧单标的身份任务的 BSE 限制不因新增 Universe Adapter 而自动解除。
- 同时保留 `effective_at`、`available_at`、实际 `retrieved_at` 和来源定位；更名、代码迁移、暂停/终止上市、停牌等状态须保留对应证据，不猜测生效时间。
- 有正向证据证明不属于 `screen_date` 的记录进入 `excluded_universe`；expected universe 内缺少状态或生效证据的记录继续保留，并进入带原因的 unranked。清单分页或总量未闭合时，不得先删除未获数据的证券再声称分母完整。

来源支持范围的查明是对当前事实的记录，不自行缩小 Spec 已批准的历史研究目标。如果现有候选无法满足目标，需要提交明确的范围选择与证据，不能静默改为“仅当前日”或“SSE/SZSE 全市场”。

公共筛选 workflow 必须同时依赖 #110 和 Universe Production Sources；#120 可继续以 accepted Universe snapshot 为输入，不承担未声明的原始名单获取责任。#97 与 #98 负责验证，不承担补造缺失 production Adapters 的责任。

验收要求：

1. installed-copy 默认装配可实际调用三个交易所的 list/identity production Adapters；离线测试替换外部传输边界，不能以调用方预填完整 Universe 代替生产装配验收。
2. 生产解析测试覆盖完整历史清单、新上市、已退市、状态未证明、标识符变化、重复、错误证券类别、分页缺口、时间缺口、BSE 缺失及安全来源定位。
3. 每个分区保留可证明的 expected、resolved 和 failed 对账；只有三个 expected 分母都由适用清单证明并完整闭合，才可形成全 A 股 Universe 结论。
4. 每个生产 owner 的历史范围、必须字段、预算、许可和资格状态进入来源可行性表及 operation qualification；未查明项不得以空字符串、默认成功或一次当前快照代替。
5. 最终冻结候选仍按原发布门禁证明真实三交易所 Universe；本条不把离线成功升级为 live 成功。

## 2. 当前盘中成交额与去重市场广度

#112 的 SSE/SZSE/BSE 成交额 owner 消费已完成交易日日报合同。这些 owner 保持原有用途，不因被 #93 组合而获得当前盘中语义。

新增 Current Market Observations（V4-I）责任，为 #93 提供当前会话的三交易所 A 股累计成交额和去重市场广度 production owners。可以复用已证明适用的来源与规范化实现，但每项 operation 必须单独证明资产范围、交易所、会话、来源观测时间、单位、完整性、限流与许可；不得把日报获取时间当成盘中观测时间。

V4-I 的 expected membership 来自 V4-U 对当前适用会话日期的清单与身份获取。筛选使用此前最近共同完成交易日的 Universe，二者必须保留不同时间锚点；不能以昨日名单代理今日新上市、退市或类别变化后的盘中分母。

三交易所累计成交额只包含适用 A 股普通股票成交额，排除基金、ETF、债券、B 股、期权和指数成交额。来源直接提供交易所总计时，必须证明其资产定义及统计范围；逐证券汇总时，必须证明 canonical identity 去重、expected universe、记录完整性和共同时间窗口。口径不可比或缺少分区时保留已成立的分区值，不形成全 A 股总计。

`market_wide_intraday` 的市场广度指同一有效会话内、来源时点兼容的三交易所 A 股证券状态分布，不是行业板块上涨数/下跌数之和。

- 每只证券按 canonical security identity 在其所属交易所计入一次；跨板块重复归属不增加证券家数。
- 上涨、下跌、平盘必须基于可比的当前成交价与明确前收/除权参考基准；基准不明或不兼容时，不得推断方向。
- 停牌、尚无成交以及状态不可判定须单独保留。缺失、来源失败和未成交均不得填入平盘或零成交。
- expected universe、可分类证券数及未知数量分别公开；多个原因可以同时关联同一证券，但家数不能重复相加。
- 如果来源直接发布交易所级广度而非逐证券记录，必须证明 A 股范围、去重规则、状态分类、分母完整性及发布时间语义，才能采用该汇总。不得为了使用汇总结果而省略范围证明。
- 只有三交易所范围及分母完整、分类可对账且观测时间兼容时，才可形成完整全 A 股广度；否则保留分区或 partial 状态。总量本身无法证明时，未知总量不得记为零。

现有 `industry_board_breadth` 继续保持板块粒度，既不改名为去重广度，也不作为其缺失时的代理。

#93 消费 #117 的会话证据、#118 的指数证据、已获资格的 Current Market Observations。其生产依赖必须包含 Current Market Observations，不能只凭 #112 的日报结果闭合。

验收要求：

1. 活动会话内，各必要市场观测相对本次真实获取/检查时刻的年龄及彼此时差均不超过既定 60 秒上限；不能用重新获取日报、更新 `retrieved_at` 或缩小字段集合制造合格快照。
2. 会话外 explicit deep 请求按原契约跳过；午间休市等状态按已批准盘中规则处理，不能把活动会话的 60 秒规则扩展为其他场景的通用陈旧阈值。
3. 一项来源失败保留其他成立 observation，但整体状态和完整性不得由幸存的分区替代。
4. 离线测试覆盖日报冒充盘中、交易所/资产范围混入、板块重复计数、停牌/无成交/缺失混淆、跨源时差、部分记录、总量未知和 wrong-session。
5. 来源可行性表必须列出上述盘中 owner 的具体候选与证据状态。最终发布仍需满足一次当前会话全市场 E2E；弱化范围、时差或将该能力延后必须另行批准。

## 3. 研究时间上下文及跨重启恢复

#117 在既有会话责任内提供 Screening Research Context 的准备与恢复校验；不新增上下文 Ticket。#69 调用该接口把公共筛选请求编译为一次研究的固定时间上下文。该接口依据三交易所日历/会话证据，在 Universe、factor acquisition、ranking 与 candidate research 之前形成唯一上下文；不得由各下游重复读取当前时钟独立推导。

首次 `create` 至少冻结以下语义：

- 用户请求的 `requested_as_of`；
- 首次研究时刻，使用 aware Asia/Shanghai 时间；
- 由首次研究时刻和 `requested_as_of` 按 Spec 推导的 `information_cutoff`；
- 不晚于请求边界、三个交易所共同完成的 `screen_date`；
- 支持 `screen_date` 的日历/会话 evidence 引用、适用日期范围及版本；
- 规范化上下文摘要，绑定请求语义、plan 与 artifact。

若 `as_of` 是首次运行当日，cutoff 为首次实际 aware 研究时刻；若是历史日期，cutoff 为该日 Asia/Shanghai 23:59:59；未来日期失败关闭。当前交易日尚未结束时，`screen_date` 是此前最近共同完成交易日。周末和节假日不得以简单减一天代替交易日证明。

#117 需提供足够的三交易所日历范围及 latest-completed-session 证据供该推导使用；若现有 Interface 只形成当前市场上下文而无法支持请求日期，须显式补齐该用途。日历缺口或交易所之间会话冲突不产生猜测出的 `screen_date`。

Result Artifact Module 保存并校验上述固定上下文，但不负责重新解释市场日历。#117 的上下文接口负责在 `resume` 时验证并恢复其语义：

1. 恢复先验证请求、producer、plan、operation manifest、上下文摘要和既有分区字节，再进行任何新来源调用。
2. 本次恢复的真实时刻用于执行超时、新获取材料的实际 `retrieved_at` 和未来时间检查，不重新定义原始 cutoff 或 screen date。
3. 首次 create 的“当前日 cutoff 等于首次研究时刻”规则，只在首次冻结时判定。恢复不能要求原始 cutoff 等于恢复时刻，也不能因跨日而把原始日内 cutoff 改为 23:59:59。
4. 未完成 shard 和未执行候选只能补入符合原始 cutoff 的证据。恢复后新公开、修订或替代的内容若不满足原始时点资格，应作为不可采纳/不可取得处理，不能借恢复回填。
5. 任何恢复后获取的材料保留实际 `retrieved_at`；不允许将获取时钟伪装为原始 cutoff。实现可传递冻结的逻辑研究上下文，但必须与真实执行时间区分。
6. 已完成 shard 和排名保持写一次；恢复不能因新数据、交易日切换或候选局部失败重新计算已冻结排名。

公共 ResearchTask 仍保留单一 `as_of`；本节不授权调用方任意传入内部时间字段，也不引入第二结果模型。固定上下文通过统一结果的既定 plan/anchor 和显式 artifact 元数据表达，具体字段必须保持唯一权威与稳定引用。

#117 拥有固定上下文的语义，#108 只持久化和复验相关字节，#69 负责公共请求编排，#120 消费已验证上下文与 Universe snapshot。各项职责不能仅用新增一条依赖代替。

验收要求：

- 首次运行分别覆盖周末、节假日、盘中、盘后及历史日；三交易所日历缺口和冲突失败关闭。
- 同日上午创建、下午恢复；盘中创建、收盘后恢复；跨日/跨周末恢复，均保持原始 cutoff、screen date 和已有分区字节。
- 恢复时先验证持久化上下文，再执行新来源调用；篡改 cutoff、screen date 或日历引用不能通过仅重算局部文件摘要获得授权。
- 在 cutoff 之后新公开的财务或材料不得进入旧排名/旧候选；后来获取但本来已公开且版本可证明的证据仍保留真实 `retrieved_at`。
- #120 通过 #110 已有的 `research_now` 输入传入首次冻结的逻辑研究时刻；来源的 `retrieved_at` 仍记录真实获取时间。先以同日及跨日恢复证明现有接口的适用性；若必须改变已接受接口，单独记录变更与回归责任，不静默重开 #110 或把恢复失败归为来源无数据。

## 4. 候选 workflow 的请求映射与能力边界

`market_screen_research@1.0` 的 `inputs.target_pe` 采用以下严格规则：

- `standard` 模式必须由调用者显式提供正数、有限的 Decimal 字符串 `target_pe`；不设默认值，不从排名分数、provider PE、研报目标或 Agent 判断推导。
- `fast` 模式禁止出现 `target_pe`，包括 `null`；因该模式不执行候选估值，不接受无用途的情景参数。
- `target_pe` 只传给候选 `single_security_research@1.0` 的估值情景，不进入筛选四因子、资格、percentile、权重或排序计算。
- `target_pe` 属于请求语义，参与完整 artifact 请求摘要；对已开始的 artifact 改变它不能以 resume 继续。不同 target PE 的新请求在相同合格 factor 输入下仍必须形成相同 ranking 字节。
- 即使最终无 eligible candidate，standard 请求也必须先通过相同的输入验证，不能让请求是否合法取决于来源返回结果。

该变化针对尚未交付的筛选 workflow，不改变旧单标的 workflow 的 `target_pe` 语义。落实已批准方案时同步更新严格 contract、安装模板、Spec、Tracker AC 和文档；不得先在实现中加入静默默认值。

候选映射必须从 canonical candidate 和适用的发行主体证券类别证据构造旧 workflow 请求。不能把“该记录是 A 股”解释为 `issuer_security_class_count: 1`，也不能复制示例中的类别数。

缺少足够类别证据时，先形成有稳定原因、候选身份、时间边界和证据缺口的 candidate `blocked` 结果；不提交伪造类别数的旧 workflow 请求。该 candidate 结果使用同一 UnifiedResearchResult 及 artifact 表达，不建立第二候选结果协议。

**B1 已批准的能力边界：** 本修订保留现有 `single_security_research@1.0` 的 BSE、历史估值或其他旧来源限制。BSE 候选、历史日期以及旧来源无法满足精确 cutoff 的候选仍可能 blocked/limited；必须逐步披露已知不支持和实际失败，不能仅报告“已执行深研”。三交易所可进入 Universe/排名，不等于三交易所每个候选都已有同等深研能力。

后续若要增加 BSE 或完整历史候选深研，须另行决策并设置明确能力责任、来源资格及验收，不由 #69 默默扩展旧 workflow。保留旧能力边界并透明呈现，以及新增 target PE 规则，均属于本次已批准的 B1 方案。

#69 必须明确并验证以下映射：canonical candidate、类别证据、显式 target PE、固定 `requested_as_of/information_cutoff/screen_date`、原始 source policy → 旧 workflow 请求与内部研究上下文。仅复制日期字符串不足以证明 cutoff 一致；当前快照的获取和财务公开时点仍需逐项裁决。

验收要求：

1. standard 缺失/非法 target PE 失败关闭；fast 出现 target PE 失败关闭；不支持参数不会被丢弃后继续运行。
2. 缺少类别证据产生 candidate blocked，不补 `1`；BSE top candidate 与历史 candidate 保留真实能力限制。
3. fast 不触发候选来源调用；standard 仍默认 5 个、上限 20 个，按既定排序选取，不以跳过失败候选的方式扩大候选请求数量。
4. 单候选及全部候选失败不改变 frozen ranking；结果明确区分“选中候选数”“执行状态”和“形成可用深研结果数”。
5. 候选恢复使用原始 cutoff；来源材料晚于 cutoff 不得因本次恢复发生在以后而获采纳。
6. target PE 变化不影响同一 factor 输入的排名，但改变 artifact 请求语义；旧 artifact 不可改参恢复。

## 5. expected、eligible 与 zero-eligible 的呈现和状态

expected universe 是由三交易所适用清单建立的研究分母；eligible 是 expected universe 内满足全部四因子资格的子集；percentile 的 `n` 是 eligible 数量。两种分母不得互换。

最终完成的筛选结果必须同时呈现各交易所及合计的 expected、eligible/ranked、unranked 以及来源失败/资格不满足的原因分布。原因列表可以一只证券对应多个原因，原因计数不能冒充互斥证券家数；排名与未排名集合在完成结果中应互斥并覆盖全部已证明 expected universe。`excluded_universe` 不进入该分母但继续保留审计。未完成 artifact、未知 expected 总量或未闭合 Universe 不得伪造完整对账。

排名标题和解释限定为“本次可排名集合中的研究优先级”。完整三交易所 Universe 只证明证券池覆盖，不能单独证明因子覆盖或排名代表性。保留 Spec 已批准的正 PE/PB、四因子、缺失不补值、权重和 `n=1` 数学，不在本修订中新增行业中性化或未批准的最低排名覆盖率。要求每交易所至少一只 ranked、最低百分比或其他新增 release 门槛均须单独决策，本附件不暗含这些要求。

zero eligible 必须区分：

- **完整数据证明零合格证券：** Universe 和必要来源范围完整、证据可裁决，全部证券因已批准业务资格进入 unranked。此时零排名/零候选是正常研究结果，不仅因为数量为零就升级为 blocked，也不虚构候选。整体 supported/limited 仍由现有来源资格和证据规则决定。
- **来源或时点缺口导致无可排名证券：** 身份分母、财务、成交额或其他关键输入失败/未知，使系统不能证明谁合格。此时必须保留 source failure、coverage 和 unranked 原因；不得表述为“市场没有符合条件的证券”或 successful empty。
- **混合情形：** 一部分证券已证明不合格，另一部分不可判定，两者分别报告。不得把不可判定证券加入已证明不合格数量，也不得以候选列表为空隐去来源问题。

验收包括三交易所完整且全体业务不合格、某交易所财务 owner 全部失败、全部 factor owner 失败、只有一个 eligible、混合缺失与负 PE/PB、以及 Universe 总量不可知。状态由证据事实推导；零值、空列表和 exception 不得共享同一成功空集路径。

## 6. 来源可行性表与工程进入条件

下表是待补齐的来源资格清单，不是可用性结论。`unknown` 表示本文所依据的本地材料尚不能证明，既不表示来源不存在，也不表示可用。既有 operation 合同只能用于其明确用途；没有具体证据的历史范围、首次公开时刻、页数和请求总量禁止填入推测值。

| 来源责任/用途 | 必须证明的字段或语义 | 要覆盖的日期/历史范围 | 必需证据 | 页数与请求预算 | 当前本地证据状态 |
| --- | --- | --- | --- | --- | --- |
| SSE 历史 list/identity | exchange、canonical code/name、A 股类别、清单总量与分页、上市状态及生效区间、标识符变化、available_at、retrieved_at、locator | 请求的 screen_date；实际最早/最晚日期与缺口须声明 | 历史清单/状态事件的正式材料、总量证明及 production-shaped 解析样本 | 单页容量、总页数来源、每日期 list/identity calls、重试上限均 unknown | 既有 sse_stock_list@1 只登记 snapshot；历史生产 owner 尚未闭合 |
| SZSE 历史 list/identity | 同上，并保留不同证券类别及上市板块的明确范围 | 同上 | 同上 | unknown | 既有 szse_stock_list@1 只登记 snapshot；历史生产 owner 尚未闭合 |
| BSE 历史 list/identity | 同上；证明 BSE 与新三板区分、适用代码变更及生效时间 | 同上 | BSE 清单/身份正式材料与历史区间证据 | unknown | 纯 Universe Interface 支持 BSE；未证明默认生产 owner 可用 |
| 批量财务与 PE/PB | canonical issuer/security、归母利润 TTM 所需各报告期、当前/上年同期 MRQ 归母权益、价格/股本口径、PE/PB 定义、单位、首次公开时间、版本/更正生效、available_at、retrieved_at、lineage | 不晚于 information_cutoff 的适用版本；须覆盖 TTM 与同期权益所需报告期 | 来源字段合同、首发与更正版本样本、三交易所覆盖证据；只有 report period 或当前 source_update_time 不够 | 分页容量、报告期乘数、覆盖全 Universe 的 calls、重试与权限限制 unknown | 既有 sina_financial_statements@1 证明旧估值用途；批量/PIT 版本与范围尚未闭合 |
| 20 个完整成交日成交额 | canonical security、准确交易日序号、成交额 CNY、会话完成、停牌/无成交/缺失区分、完整性及 lineage | 每个 screen_date 之前含该日的适用 20 个完整成交日；来源可提供的回溯范围须声明 | 交易日历、批量 daily 样本、上市不足 20 日和停牌样本 | 日期 × 分页/证券批次 × retry 的总 calls、速率、耗时上界 unknown | 不得由单证券样例或 owner 有限预算推断整次全市场采集可行 |
| 当前三交易所 A 股累计成交额 | exchange、纯 A 股范围、当前 session/date、source observed_at、CNY、累计口径、完整性、retrieved_at | 当前适用会话；不接受已完成日报替代 | 对应盘中 owner 的来源字段与时间合同、三交易所 production-shaped 样本 | 每次快照 calls、共享节流与最坏耗时能否满足 60 秒 unknown | #112 三个日报合同已登记；盘中用途尚未闭合 |
| 当前去重市场广度 | 分区 expected 分母、canonical 去重或来源明确的去重证明、up/down/flat/suspended/no-trade/unknown 语义、可比前收基准、source observed_at、完整性 | 与会话、指数、累计成交额处于同一允许时差窗口 | 全 A 股范围/分母/分类证明；不能用行业板块家数替代 | 全量或汇总来源 calls、页数、实际观测时间跨度、重试预算 unknown | 既有 industry_board_breadth 不满足全 A 股去重定义 |
| 三交易所日历与会话 | 交易所、session date、阶段、共同完成日、覆盖日期范围、规则/例外来源、版本、available_at、retrieved_at | 请求 screen_date 所需历史范围及当前会话；实际来源范围待证明 | #117 owner 的正式日历、阶段规则、例外和覆盖证据 | 查询年度/日期范围、缓存许可、页数与重试预算 unknown | #117 已声明责任；尚不能据计划文本认定来源可用 |
| 固定四指数 | 四个固定 canonical IDs、点位/涨跌、基准、单位、source session/time、完整性及 lineage | 适用/最近完成会话；盘中用途另须满足 60 秒 | #118 每项 owner 的身份、时间和 production-shaped 样本 | 每组请求和重试、四指数共同时间跨度 unknown | #118 已声明责任；live 可用性仍未证明 |

#119 继续拥有实际 invocation/retry 计数、timeout-quiescence 和共享 provider lane；不得在 factor 层重新计数或私自重试。来源资格还必须给出覆盖已证明 expected universe 的整次 workload 预算：

- 每 owner 单页容量、页数上限、日期/报告期批次数、最大 attempts 及重试如何共用预算；
- 同 provider group 所有 owners 的合计请求、pacing、timeout 和故障后 lane 不可用影响；
- 最多三个 provider groups 的并发约束下，完整工作负载与失败后恢复的耗时、内存及 artifact 规模；
- 超预算时如何保留 partial、尚未获取证券和明确原因，不通过截断后改 expected 分母使计数看似完成。

预算数值必须由具体候选合同和样本推导；暂时无证据时继续标 unknown。决定可接受的产品运行时长、历史支持边界或最低覆盖率属于明确产品决策，不由工程代理静默决定。

本地合同审计、权威资料及已有合法样本可以先用于排除不成立的来源假设。B3 已批准在实现及最终候选冻结之前设置独立的有界真实来源可行性探索阶段，调整原 live 执行顺序，无需再次批准同一政策。探索开始前，计划必须列明具体来源候选、访问范围、请求与重试预算、停止条件及证据记录方式，并取得维护者对该具体清单的确认；只执行已确认清单内的有界查询。本次决策记录不表示探索已经运行。探索结果单独记录为可行性证据，不能进入最终同候选 attestation union，也不能降低 #98 的最终冻结候选门禁。

任何来源仍为 unknown 的能力都不得被文档、Tracker 或 UI 标为已支持。发现核心假设不成立时，应提交已尝试路径、失败证据、可保留的产品范围和待决策选项；不能继续以越来越复杂的离线模拟掩盖生产链路缺口。
