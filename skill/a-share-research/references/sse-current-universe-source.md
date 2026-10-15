# SSE 当前证券身份采集检查点

`SseCurrentIdentityAdapter` 复用已有 `SseStockListOperation.query` 的真实 production parser，将有限点查询接入 #110 的 identity seam。#121 的 P 阶段仅验证一个当前身份查询；该路径不提供 SSE 完整名录或历史分母，也未获得新的 operation 资格。

## 来源形状与用途

既有匿名 GET caller 使用 `https://query.sse.com.cn/commonQuery.do`，固定 `sqlId=COMMON_SSE_ZQPZ_GP_GPLB_C`、`isPagination=false`，通过 `productid` 查询单证券；沿用既有 Accept、Referer、User-Agent。P 的一个真实响应包含非空 `result`，代码、简称、公司名称、公司代码及当前“上市”标签满足现有 parser 的五个必需字段。独立核验只证明此 selector 的有界可行性，不证明批量、完整名录、证券类别或历史适用性。

调用者传入的是 #110 已准备的、具有 A 股范围依据的 expected records。Adapter 对每个有限请求核对相同 SSE 代码和名称；不从代码前缀、`TYPE`、`SCU_TYPE`、`SMALL_CLASS_NAME`、`STATUS` 或缺失 B 股字段推导 A 股类别。同行非空 B 股代码不取消已经由适用清单证明的 A 股证券身份。

P 的 `pageHelp.total=0` 与实际非空 `result` 并存，不能用此数值声称零条或完整名录。P 的另一个明确空 `productid` 候选返回 error 且没有 `result`；这只证明该候选失败。Q 的官网菜单指向真实股票和暂停/终止上市网页，但菜单 URL 不能当作 bulk API caller。这里没有新增批量 selector。

## 时间与有界执行

输入 `screen_date` 明确表示本次 membership effective date，另保留调用者冻结的 `information_cutoff` 和 `research_now`。当前点查询拒绝历史 membership 请求，不将当前名单回填到以前日期；当日新股或名称改变也不能通过保留旧 expected 名称静默匹配。

身份输出保留实际 `retrieved_at`，`observed_at` 表示 owner 实际观察响应的时刻。它不冒充原始事实、首次公开或上市生效时间。`available_at`、`effective_from`、`effective_to` 和 `listing_state` 继续为 `null`；`QIANYI_DATE` 及其他 opaque 日期字段不被赋予这些语义。#110 保留这些 expected 身份，并以 `as_of_unproven`、`listing_state_unproven` 表达未证明状态。

调用者必须明确提供有限 `max_securities` 和 `ProviderLanePolicy.actual_attempt_budget`。前者控制点查询范围，后者在 #119 实际调用点共同限制所有 initial、retry 和 redirect。HTTP 429 在 leaf 内规范为 rate limiting，retry 由 #119 决定；没有私有计数器或隐式逐证券无限补抓。timeout 后，同 provider lane 继续占用到 quiescence，poisoned lane 不重叠启动后继请求。

查询预算耗尽或局部失败保留已经解析的身份与原 expected count，不能通过缩分母形成表面 complete。点查询的 `source_result=complete` 仅表示请求的代码/名称全数解析；投影始终明示 `membership_proven=false`。空结果、缺少 `result`、重复身份、错误代码或名称不能变成 `observed_empty`。原始 body、源 URL、headers、opaque 字段、异常文本及绝对路径不进入安全投影；实际 attempts 与来源结果独立保存。

## R 核验后的主板 bulk 路径

`acquire_sse_mainboard_current_list` 使用 R 已独立核验的真实 GET caller：`https://query.sse.com.cn/sseQuery/commonQuery.do`、`COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L`，固定主板 A 股控件 `STOCK_TYPE=1`、`COMPANY_STATUS=2,4,5,7,8`、每页 25 行。官方 `paginationChange` 同时修改 `pageHelp.pageNo/beginPage/endPage`；cache size 为 1。JSONP helper 的跨域 script transport 实际使用 GET；研究请求的此项核验不冒充 browser wire capture。来源菜单另有主板 B 股和科创板控件，这些范围不被主板 A 股默认页覆盖。

R 的第一批真实主板响应实际有 25 行，声明 1702 条、69 页；保留 20 条身份元数据不表示只有 20 行或全量已取得。生产 parser 仅解析固定 callback 包裹的静态 JSON，不执行 JavaScript；验证页号、每页容量、总页数和数量一致，并拒绝 executable tail、异常 schema、未知字段、错误 `STOCK_TYPE` 与不安全名称。重复记录、短页、后页失败、计数漂移和预算耗尽保留先前成立记录及原声明数量，阻断 `pages_complete`。

官方 caller 将 `LIST_DATE` 显示为上市日期。Parser 将八位日期标签规范为 `displayed_listing_date`，仍不据此建立完整历史生效区间或首次公开时间。`STATE_CODE` 的历史生效语义仍未证明；`PRODUCT_STATUS` 在 U 核验后按下面的完整 C20 规则进行当前类别推导。正向 A/B 组件保留，空或占位字段不被用来证明类别缺席。source scope 明示 `SSE_MAINBOARD_A_SELECTOR`，`membership_proven=false`；即使本次分页闭合仍为 `partial`。R 的暂停上市默认控件 `COMPANY_STATUS=5` 有单独零行证据；它不表示 `COMPANY_STATUS=3` 终止上市或主板默认名单为空，此主板 parser 不复用该 observed-empty 结论。

`SseMainboardCurrentIdentityAdapter` 使用该批量路径核对 #110 已准备的 expected 代码/名称，不退化为逐证券点查询。A 股范围仍须由适用 expected 清单证明，匹配身份的状态、生效和公开时间继续为 `null`；#110 对账与 unranked 语义不变。科创板、完整 SSE 证券类别、历史名单/状态事件及 source version 仍待证明，当前路径不能独自闭合完整 SSE 分母。

R 的可消费 W1–W3 记录由独立核验 `f48abd3da22d1b6623b76b6c967e95e9abc9358011f933f53eba48d52d7bd274` 绑定，真实 source body 摘要为 `bfcd8652d96935dcac30aaf5805487686f3d2b47a22d8fc63f640de411b3e0f1`；这不是最终来源资格或 release attestation。测试使用 expressly synthetic 证券、日期和计数，仅重现已证明形状。

## S 核验后的固定来源分区

`acquire_sse_list_partition` 使用同一 parser 处理已核验的四个固定 UI 来源范围：`active_mainboard`、`active_star`、`terminated_mainboard`、`terminated_star`。当前主板路径仍由 `acquire_sse_mainboard_current_list` 及其 identity Adapter 使用；新分区没有形成新的公共 workflow、registry 或历史证券池产品。

S 的实际当前科创板查询使用 `STOCK_TYPE=8`，声明 618 条、25 页；终止上市主板查询使用 `STOCK_TYPE=1,2`、`COMPANY_STATUS=3` 及 `/commonQuery.do`，声明 156 条、7 页；终止科创板查询使用 `STOCK_TYPE=8`、`COMPANY_STATUS=3`，声明 3 条、1 页。前三批实际取得 25、25、3 行，留存的 5、10、3 条身份材料不等于全量收集。科创板不是纯 A 股类别的自动证明，主板 `1,2` 分区包含 B 股。

Parser 按各分区允许的真实 `STOCK_TYPE` 检查行形状；规范保留正向 A/B 代码组件和显示上市/终止日期，不以 B 组件出现为由删除同一行 A 组件。空或占位组件仍不证明类别缺席。U 已核验正式 C20 定义与官网显示规则，补足了同一第五位置 Y 的存托凭证含义；具体判定见下节。raw D 字母仍不可任意搜索，B 股组件不被改写为规范 A 股身份，历史类别版本继续单独裁决。

终止上市目录可以作为后来取得的历史事实载体，因此历史请求可解析这些日期事实，并保留真实晚于 cutoff 的 `retrieved_at`。这并不证明在旧 cutoff 前已经公开了同一事实版本：`available_at` 和 membership 生效时点继续未知，结果为 `partial`，不进入 as-of expected 分母。当前 active 分区继续拒绝历史 membership 请求。日期的实际生效日/含排边界可由适用官方定义证明，无须索要精确秒数；若官方公布日期与同一版本对应充分，也可按已证明的保守日界裁决可用时间。

S 的最终独立核验摘要为 `eac74cce06ff1c2f38648b1acc9ad04d18e5bafcb7f9ea33b433571c2c4d2c5b`。固定源 selector 和形状允许继续真实 parser 实现；普通 A 股/CDR 分类、完整请求日期名录与变更事件、同一版本在 cutoff 前公开的证明仍需补齐。

## U 核验后的完整 C20 类别推导

实际《竞价撮合平台市场数据文件》3.540 的字段 29 明定“产品状态标志”为完整 C20：未定义位置填空格，第五位 Y 表示存托凭证；第四位 D/S/P/T/U 分别具有国内正常、风险警示、退市整理、退市转让、优先股含义；第六位 L/M 属于债券适当性标志。parser 校验所有 20 位及已定义取值，缺失、短字段、占位或非法位置继续为未知。它不把任意一个 D 或缺少展示后缀当作类别判断。

官网真实 caller 的 `PRODUCT_STATUS` 经 `substring(4,5)==Y` 显示 D；2025 年 7 月《证券特殊标识指南》第八条定义存托凭证加 D，第三条明确产品信息文件和官网之间的展示关系。将官网字段与正式 C20 结合，是由字段名、同一位置和官方展示规则支持的推导，记录为 `official_scope_and_C20_inference`，不伪装为新查询的证券级事实。

普通 A 股当前判定要求适用 `STOCK_TYPE=1/8` 来源范围、正向 A 代码组件、完整有效 C20、相应国内股票状态，以及没有第五位 Y、第四位 U 或第六位 L/M 等其他类别约束。正向 Y、优先股或债券标志分别保留明确非 A 类别；仅 B 组件与主板 B 股范围形成正向 B 股事实，不删除同行 A 组件。Mainboard identity Adapter 不把正向非 A 事实重新标成调用者 expected A 身份；已有分母记录进入 failed/unranked 并保留来源审计。

类别的当前语义推导与历史可采纳分开：`classification_as_of_proven=false`，记录的 `available_at` 与 membership 时间仍未知。3.540 的“2026 年 6 月”版本标签不被当成具体公开日；2025 年 7 月指南的 `2025-07-14T00:00:00+08:00` 保守界只属于同名规则版本，不分配给 roster 行。离线正向 CDR 合成例由这些实际定义支持，不额外要求一个未批准的 CDR 定点 live 样本。

## 资格缺口

| 事项 | 当前证明边界 |
| --- | --- |
| 当前点身份 selector | P 有界真实样本和已有 parser；不代表 bulk |
| 完整 current/historical 名录 | R/S 证明当前主板、科创板与终止分区 caller/首批形状；完整分页和历史版本仍 unknown |
| A 股普通股票与 CDR 等类别 | U 支持完整 C20 + scope 的当前推导；缺失/非法字段与历史类别版本仍不猜测 |
| 上市、退市、代码/名称变化 | 历史事件与生效区间 unknown |
| 事实版本与 cutoff | 首次公开及版本对应 unknown；实际 retrieval 不倒填 |
| 空结果语义 | 非空点样本及失败 bulk 候选不证明完整成功空集 |
| 许可与全市场 workload | operation-specific 自动化、缓存、分发权限及全量容量 unknown |

后取到的历史材料可以定位历史事实；只有同一事实版本在 cutoff 前已经公开、事件覆盖与时间边界可证明时，才可采纳。适用官方日期与明确边界可以形成时间依据；不凭空要求或制造精确秒数。

copied-installed 测试只替换 transport 并执行真实 parser，使用独立合成 expected-list 夹具验证 #110 对账和 #119 实际调用；这不是默认名录装配验收、live 来源成功或 #98 最终 release attestation。#122 完整交付仍需三所真实 list/identity 默认装配和全部原 AC。


## 默认当前采集组合

`acquire_sse_current_lists` 把当前主板与科创板放入一次共同的 `sse` lane execution，共享 initial/retry/redirect 的实际预算和有限页地址上限；一部分缺失不抹掉另一部分记录。三所固定装配及其尚未满足的 #110 输入见[当前名单默认采集](universe-current-source-assembly.md)。该装配不增加未核验的历史字段或供应商 fallback。
