# SZSE 当前证券名录采集检查点

`szse_universe_source.acquire_szse_current_list` 是 #122 的来源采集增量。它复用 #110 的请求准备工厂、现有 SZSE 身份字段合同及 #119 的实际调用计数。当前输出是待资格闭合的来源观察，不是新的证券池产品，也未注册公共 workflow 或正式 production owner。

## 已有证据与解析范围

#121 已接受的 A 阶段于 2026-10-14 分别读取 `1110/tab1` 的第 1、2 页。脱敏证据保留了 JSON 形状、每页 20 条样本，以及响应声明的 `pagesize=20`、`pagecount=146`、`recordcount=2905`；schema 摘要为 `a7dce66b5ae881292296a6b5c6cb0625c9ea6b3dfbafe6d63d760e941c410019`。这不是全量、去重、历史适用性或 production 资格证明。G 阶段另证实 UI 首屏 20 行、146 页和显示日期 `2026-10-14`；该显示日期不表示公开时间或完整名单生效时间。D 阶段继续保留相同缺口；Tushare 文档不能代替账户权限或 qualified owner。

来源定位为 [SZSE 名录页](https://www.szse.cn/market/product/stock/list/)；请求使用已有 owner 的 `/api/report/ShowReport/data`，固定 `SHOWTYPE=JSON`、`CATALOGID=1110`、`TABKEY=tab1`，逐页传入 `PAGENO`，明确 `tab1PAGESIZE=20`；没有历史日期参数。其他交易所及证券类别不使用该 owner。

解析只选择唯一匹配的 report，保留 `agdm`、`agjc`、`agssrq` 对应的代码、简称和上市日期。字段范围沿用现有 `SzseStockListOperation.field_contract`；额外字段、非法代码、日期、控制字符、路径或未证明的 HTML 名称形状均不能形成有效记录。G 阶段已独立观察到 A 股代码/简称/上市日期三个中文表头、A 股独立页签，以及简称的 `<a href="http://www.szse.cn/certificate/individual/index.html?code=…" target="_blank"><u>…</u></a>` 形状。解析接受此结构并核对同一证券代码，删除标签和 URL，只保留文本简称；非官方链接、额外事件属性、未闭合或其他标签拒绝。I 阶段实际 JSON 的 `metadata.cols` 已绑定 `agdm=A股代码`、`agjc=A股简称`、`agssrq=A股<br>上市日期`，并显示 `name=A股列表`；parser 按这些原始标签核验语义，错标签不能形成合格记录。名称中的布局空格沿用既有身份来源的处理去除，全角字符保留。证券代码只校验六位数字，不用于推断交易所或证券类别。

## 时间与计数

输入的 `screen_date` 在这个采集 Interface 内明确映射为 `membership_effective_date`，并通过 #110 的工厂与调用者冻结的 `research_now`、`information_cutoff` 一起准备。此当前来源不会查询或回填过去的完成交易日。I 的 `metadata.subname` 是带空白的日期标签，规范为 `source_list_date_label`，语义继续为 `unproved`；分页之间标签漂移阻止闭合，不更新 cutoff 或 public/effective time。每页的实际 `retrieved_at` 按 Asia/Shanghai 归一；跨日获取拒绝当前日期适用性。该时间不被填成首次公开时间或 membership 生效时间，冻结的 cutoff 也不因获取而推进。

输出中的 `reported_count` 是来源分页声明，不是已证明的历史 expected universe；未得到有效分页元数据时为 `null`，不能表示为零。只有页号、分页大小、总页数、总行数一致，取得全部页面且记录无缺失、拒绝或重复时，`pages_complete` 才为 true。短页、坏记录、失败的后续页和预算耗尽保留此前成立的记录与最初的报告总数；计数漂移拒绝新的分母。

当前尚无 membership 生效时间和首次可用时间依据，故每条观察保留 `membership_effective_at=null`、`available_at=null`，采集投影明确 `membership_proven=false`。即便分页闭合，`source_result` 仍为 `partial`。访问拒绝和没有成立页面分别保留 `credential_blocked`、`unavailable`；实际 `attempted` 由 lane 的真实调用证据独立确定。

## 有界获取与安全投影

调用者必须传入有限 `max_pages` 和 `ProviderLanePolicy.actual_attempt_budget`。这些值是本次工作的工程上限，不冒充已证明的 provider 容量或整个市场的运行 SLA。所有页面、retry 和 redirect 共享同一 `ProviderLanes` 实例；timeout 后仍在执行的同组请求保持 poisoned，后继采集不重叠启动。

测试必须显式注入同步、单次且无内部 retry/redirect 的 transport。显式 `transport=None` 才选择现有 `UrlLibTransport`，其每个真实 HTTP leaf 与 redirect 由同一 invocation 计数；不接受调用者另建的未绑定 urllib transport。默认装配、正式资格和最终 live 证明仍待闭合。

公共投影只含规范观察、固定原因、opaque 来源引用及 lane 签发的 attempt/quiescence 快照。它不保存 response body、URL 参数、headers、凭据、异常文本、绝对路径或板块等无关 payload。返回后的记录、页数与 lane 证据不会因超时 worker 后续退出而变化。

## 尚未闭合的交付条件

完整 #122 仍需第一方材料与合法 production 形状证明：SSE/BSE 清单和批量身份、三所历史名单或状态事件、历史查询范围、证券类别、退市及标识符变化、生效区间、首次公开/可用时刻、完整分页和数量证明、许可、共享 provider 的全量 workload。当前观察缺少 #110 的必需时间字段，不能通过填入获取时刻构造 `ExchangeListRecord`。

本检查点未修改 #110 的已接受记录/对账合同、#119 的计数权威或 operation qualification。复制安装的确定性测试只证明模块可加载并实际执行 parser；它不满足三所默认生产装配，也不证明 live 成功或 #98 最终发布资格。
