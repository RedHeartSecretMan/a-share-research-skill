# 三所当前名单默认采集装配

`universe_sources.acquire_default_current_sources` 固定组合 SSE 主板与科创板当前目录、SZSE `1110/tab1` 和 BSE 当前 POST 名录。它直接执行已经核验的 production parser，不接收调用者预填的完整 Universe，也不引入 SDK、供应商选择或 fallback。

调用者传入已冻结的 `UniverseRequest`、aware `research_now`、同一 `ProviderLanes` 和三个显式有限的 `ProviderLanePolicy`。每组的 page ceiling 取其 actual-attempt ceiling；retry 和 redirect 继续消耗 #119 的同一预算，没有附加的隐式配额。返回值是已有 SSE/SZSE/BSE acquisition 类型的固定顺序 tuple，没有第二个 Universe 或公共研究结果模型。

`CurrentSourceTransports` 是外部传输 seam。测试为三个来源全部注入同步单次 fake；`None` 选择对应来源拥有并绑定 invocation 的 urllib。传输配置和请求在任何采集前统一验证，不能先执行前两个来源，再因第三个 unowned urllib 被拒而留下配置错误的部分操作。

## SSE 同组预算

`acquire_sse_current_lists` 在一次 `sse` lane execution 内调用已证明的 `active_mainboard` 和 `active_star`。两者共用 actual attempt 与逻辑页地址上限。逻辑页地址只限制 source traversal，不冒充 HTTP attempt 计数；所有 initial/retry/redirect 仍以 #119 签发的实际证据为准。

一部分 schema/count 失败不会删除另一部分已成立的观察。任一部分元数据未知时，组合 `reported_count/pages_expected` 保持 `null`，不能把另一部分的数量当成完整 SSE 分母。两个部分完整取得、数量稳定和无重复只证明当前来源范围的分页闭合；它仍包含需正向区分的类别，不等于 cutoff 合格的普通 A 股 Universe。

## Partial 与隔离

三个 provider groups 使用同一 lane Module；当前装配按固定顺序执行，不增加并发框架。一个来源超时或失败后，其他 group 的合法记录保留。同组未 quiescent 的 worker 不能重叠启动后继请求，返回的 source snapshots 不受晚完成 worker 修改。

当前方法明确只采集 current source scopes。历史请求不能通过重用这些当前目录变成历史名单，未知数量保持 `null`。它没有把 Baostock basic 或任何其他未实测字段接入历史路径。

## #110 仍缺的输入

默认 `ExchangeListAdapter → #110` 装配已接入下述诚实 partial 投影，仍不具备正向时点成员记录。当前已核验来源行的 `membership_effective_at/available_at` 仍未知，来源声明总数与普通 A 股时点分母也未必相同。不能为调用 #110 而填入 retrieval、当前 basic 名称、零或当前声明数量。

后续正向投影需要被选定方法实际字段、日期与范围依据，使 production normalizer 可以从来源生成合法 `ExchangeListOutcome`；随后由同一默认路径证明公开对账、正向排除和 unproven→unranked。已核验的 Baostock given-day 声明是逐日名录方向的正面依据，但 AG 已观察真实字段、分页/session 和特定日期的 provider 枚举；普通 A 股类别、名字版本与 BSE 范围仍未闭合。这里不要求全历史都先实测、泛化 PIT 保证书或提前完成 #98。

copied-installed 当前默认采集测试使用合成原始 wire responses，实际执行所有 parser，验证 `[2,1,1]` 条记录与 `[2,1,1]` 次真实 lane attempts，以及失败、预算和冻结日期行为。它不以 `Proven*ListFixture` 代替默认生产分母，也不冒充完整 #122 验收或最终 live proof。

## 诚实投影到 #110

`create_default_current_universe_adapters` 不执行 I/O，返回固定三所 list 和 identity owners。#110 通过其公共 `ExchangeListAdapter.acquire` seam 驱动上述三个真实 parser，接收真实 attempted、source-result 和页数。已有 SSE 点身份、SZSE 和 BSE 批量身份 Adapters 同时装配；每个 list/identity execution 使用显式有限的 #119 policy，共享同一 provider group 的串行与 quiescence 规则。若将两个 execution 均运行，每组最多消耗两倍其 policy actual-attempt ceiling；不把 list 和 identity 的独立预算说成一次共同预算。

`acquire_default_current_universe` 使用这些 owners 调用 #110，并返回不可变 Universe 和原有安全 `source_acquisitions`。当前来源还未证明 membership effective/available 时点，因此 canonical list 不填造记录，expected 与 failed 通过 #110 的未知总量契约保留 `null`。#110 对未闭合 list 不启动 identity，不制造无依据的身份请求。source-reported count 和安全观察继续保留；该路径不宣称 #122 的有用时点化名单已完成。历史请求仍保留这三个当前来源的明确不支持状态。

AH 的 BSE quotation query 与 H 的本名录是不同来源形状。AH 两页 40 行、声明总量 350/18 页及 `hqjsrq/hqgxsj` 不能覆盖 H 的字段或数量；其已证明的第一行“数据更新时间”展示也不能赋予 membership effective/available 或普通 A 股类别。指定 `effective_date=today` 仍不能补齐这些字段。

独立的 [Baostock 日期名册采集](baostock-dated-universe-source.md) 已实现给定日期查询与有限 SDK 分页。provider 名册含指数，当前仍不能为三所 canonical expected 分母补齐类别与公开时间证明，也不会作为未获资格的品牌 fallback。

[SZSE 正式上市公告书事实采集](szse-formal-listing-material.md) 已支持真实 PDF decoder、具名正式批准条款和匹配版本的公开元数据；完整市场目录仍未闭合。

后续 `annList` 补证已找到该 exact updated 文书的公开日期。`acquire_default_universe` 接收调用者提供的 `SzseListingEventTarget(source_code, document_url, max_metadata_pages)`，以有限真实 metadata/PDF acquisition 生成该证券正式上市日的一条正向 canonical partial；不会固化样例代码/UUID，调用者也不提供已证明名单。SSE/BSE 继续使用其现有真实 owners，历史日期不支持时保留 unavailable 和未知总量，BSE 缺失始终阻断 full。具体版本绑定、consumer conservative availability bound 与范围限制见上述文书说明。`acquire_default_current_universe` 保持当前来源模式，不自动把一个事件扩展成市场清单。

同一泛化装配也接收 `SseListingNoticeTarget(document_url)` 与 `sse_listing_transport`，执行[具名 SSE 科创板上市公告](sse-formal-listing-notice.md)的真实 HTML parser。该 owner 仅为文书正式上市日生成正向 partial，article header day 的 consumer conservative bound 不迁移到 SSE 当前名录。SSE/SZSE 两类有限事件均不能替代尚未闭合的市场目录；没有 target 时不会自动使用研究样例。
