# BSE 当前证券名录采集检查点

#121 的 G 阶段已定位 [BSE 上市公司页](https://www.bse.cn/nq/listedcompany.html)及其实际 `companies_name.min.js`；H 阶段实际执行了两个名单 POST。`bse_universe_source.acquire_bse_current_list` 已实现对应的获取、静态 JSONP 解析、有限分页和 #119 leaf 调用计数；它仍保留 membership 与首次可用时间缺口。

## 请求与已验证形状

客户端声明 `POST https://www.bse.cn/nqxxController/nqxxCnzq.do`，初始 `page=0`，无证券过滤时 `xxzqdm=""`，并传入 `typejb="T"`、`xxfcbj=["2"]`、`sortfield="xxzqdm"`、`sorttype="asc"`。H 按 jQuery 默认数组编码推导 `xxfcbj[]=2`，实际正响应验证了所提交的 form。该依据不是浏览器网络抓包。`prepare_bse_current_list_query` 保留不可变语义参数，`encoded_body` 使用此已验证 bracket form。

H 的第 0、1 页响应均为 JSONP 顶层数组，单个 page 对象含 `content`、`number`、`size`、`numberOfElements`、`firstPage`、`lastPage`、`totalElements`、`totalPages`；两次声明 349 条、18 页、每页 20 行，两个样本页证券代码互不重复。这是两页观察，不是稳定全量、历史范围或运行 SLA。

解析静态 callback wrapper 内的 JSON，完全不执行 JavaScript。追加语句、错误 wrapper、页号/数量/首尾标记冲突、未知 page size 和未知身份形状不能形成完整页面。当前固定请求只验证了 20 行 page size，不扩大为任意容量。坏记录、重复或后续失败保留此前成立的观察及最初报告总数；无成立元数据时 `reported_count=null`，不填零。公开输出不保存 callback、原始响应、headers、URL、绝对路径或异常文本。

每个名单行必须有 H 实际观察到的 `xxzqdm`、`xxzqjc`、`fxssrq`、`xxgprq`、`xxfcbj`、`xxzqjb`、`xxjsrq`、`xxgxsj` 字符串；当前 selector 记录核对 `xxfcbj="2"`、`xxzqjb="T"`，不从代码前缀推断市场。其他已知字段不进入输出，未证明字段或非法身份拒绝。零行响应的适用语义尚未查明，不由缺响应或空数组推断零 expected universe。

## 时间与 #110 接线

页面将 `fxssrq` 显示为“上市日期*”，并明确限定：精选层挂牌公司平移为 BSE 上市公司时，所示日期为其在精选层的挂牌日期。因此它只规范化为带限定的 `displayed_listing_date`，不能直接赋给 membership 生效区间。`xxjsrq`、`xxgxsj` 作为日期/更新候选标签保留，语义为 `unproved`；不按名字拼接发布时间或 `available_at`。

请求通过 #110 的准备工厂绑定调用者冻结的 `research_now`、`information_cutoff` 和明确 membership effective date。当前来源不查询或回填历史日期；真实 `retrieved_at` 独立记录，跨日响应不冒充原日适用名单。即使页面对账闭合，当前采集 `membership_proven=false`、`source_result=partial`，每条 `membership_effective_at`、`available_at` 仍为未知。

`BseCurrentIdentityAdapter` 满足 #110 已接受的 identity seam，以一次有界批量采集匹配调用者已准备的 BSE canonical code/name。其分母来自 #110 的已证明名单，不来自 `reported_count`。代码或名称没有匹配的 expected 证券继续保留为 failed；不会缩小 expected。未证明的 `listing_state`、`effective_from/to`、`available_at` 继续为 `None`，由既有 #110 Module 形成 `listing_state_unproven/as_of_unproven` 和不完整分区。

identity outcome 的 `complete` 只表示本批请求的全部 code/name 均已观察到，不表示时间资格或完整 Universe。`observed_at` 记录 owner 取得 response 的实际观察时刻；它不是 `xxjsrq/xxgxsj` 的解释，也不是 source publication/effective time。提供的合成已证明名单及 copied-installed 测试验证此对账路径，不成为真实 production expected 名单的证据。H 的未知时间不足以构造实际 `ExchangeListRecord`，该 list 接线继续关闭。

## 预算与剩余条件

调用者必须提供有限 `max_pages` 和 `ProviderLanePolicy.actual_attempt_budget`；页面、retry 和 redirect 共用 #119 的真实 invocation 入口。timeout 后仍在执行的请求保留 poisoned group，同组后继不重叠启动。测试显式注入单次同步 transport；显式 `None` 才使用绑定同一 leaf invoker 的现有 urllib transport。

另一个 [新旧代码对照表](https://www.bse.cn/code_mapping/200025792.html)有 248 行连续序号与 `2025-09-12` 文档日期。文档日期不表示代码切换生效日，表格也不证明当前证券池或全部历史。禁止以对照表替代名录或从代码前缀造身份。

三所历史名单/状态事件、生效和首次可用时间、完整 source scope、source permission、稳定 terminal/count 与共享 provider 的全量 workload 仍需真实契约证据。当前增量不修改 #110/#119 或资格 authority，不注册公共 workflow；默认三所生产装配、完整 #122 验收和 #98 live attestation 均尚未完成。
