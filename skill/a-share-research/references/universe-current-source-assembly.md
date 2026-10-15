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

这是默认**来源采集**装配，尚不是已闭合的默认 `ExchangeListAdapter → #110` 装配。当前已核验来源行的 `membership_effective_at/available_at` 仍未知，来源声明总数与普通 A 股时点分母也未必相同。不能为调用 #110 而填入 retrieval、当前 basic 名称、零或当前声明数量。

后续正向投影需要被选定方法实际字段、日期与范围依据，使 production normalizer 可以从来源生成合法 `ExchangeListOutcome`；随后由同一默认路径证明公开对账、正向排除和 unproven→unranked。已核验的 Baostock given-day 声明是逐日名录方向的正面依据，但实际字段、分页/session 和市场范围尚待 #121 的有界验证。这里不要求全历史都先实测、泛化 PIT 保证书或提前完成 #98。

copied-installed 当前默认采集测试使用合成原始 wire responses，实际执行所有 parser，验证 `[2,1,1]` 条记录与 `[2,1,1]` 次真实 lane attempts，以及失败、预算和冻结日期行为。它不以 `Proven*ListFixture` 代替默认生产分母，也不冒充完整 #122 验收或最终 live proof。
