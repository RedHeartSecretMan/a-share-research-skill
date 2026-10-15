# 完成交易日市场基础 Module

`market_foundation.py` 消费已接受的六 owner 资格，通过内部
`MarketFoundationOperationAdapter.execute(OperationRequest, timeout)` 和
`fold_market_foundation(...)` Interface 提供日报规范化与交易所分区折叠。
该 Module 尚未接入公共 workflow。六个默认 owner 都具有有界 HTTP 获取与原始响应解析路径；
尚未证明的来源语义会产生带实际尝试次数的明确状态，不形成合法 observation 或总计。

`market-foundation-owner-qualification.json` 是已接受 #114 六条记录的精确内容副本。
装配仍调用 `operation_release_matrix.load_operation_qualification_manifest` 校验资格权威，
副本没有增加 owner、改变 selector、单位或适用性规则。

`OperationRequest` 接收消费者确定的完成交易日 `report_date` 和冻结的
`information_cutoff`。本 Module 验证日报日期一致、来源发布时间不早于报告日且不晚于
cutoff、获取时间不早于发布时间；它不自行推导三交易所共同完成交易日。
查询控制回显、获取时间以及来源记录创建／更新时间均不能代替发布时间。

注入的 report transport 接收资格记录中的 selector、请求和 timeout，并通过已有
`ProviderInvocation.call` 执行每次 leaf 请求。初次请求、分页、重试和 redirect 共同消耗
资格规定的 request budget，同组串行、timeout 和 quiescence 由已有 `ProviderLanes` 拥有。
默认 HTTP 路径复用 `UrlLibTransport` 的实际 leaf 计数；GET 和 POST 都传递 owner timeout。
注入的 HTTP transport 必须是一次原子请求，不得隐藏重试。

report stage 是内部解析结果，不是供应商 wire schema。它声明已证明字段、单位、范围、日期、
发布时间和完整性。`source_scope_qualification`、`source_date_qualification` 和
`unproved_fields` 明确保留纯 A 股范围、独立报告日期以及混合融券数量的缺口。
任何缺口都不能改写为 `not_applicable` 或合法零值。

规范化按字段消费资格合同的 source units，使用 `exact_normalization.exact_scaled_sum`，
不受进程 Decimal Context 影响。SZSE 日报和两融金额使用亿元，BSE 日报原始金额使用人民币元；
BSE 两融 Round 金额使用万元，Round 数量使用万股。逻辑 quantity 字段只有已证明 canonical
shares 时才能进入规范化，SSE 的股／份／手和 SZSE 的亿股／亿份不会被统一换算成股。

owner 输出直接由 #116 的 `OperationOutcome` 脱离调用者后递归封存，投影使用其 `to_dict()`。
fold 只接收当前装配的 owner 实际签发的对象。公共结果只包含规范化字段、固定安全错误状态、
资格中的来源定位以及由执行获得的调用次数；调用者提供的 locator、异常文本、原始响应、
诊断或 attempts 不成为权威。

来源状态调用已有发布 Module 的 canonical helper，保持 `complete`、`observed_empty`、
`partial`、`credential_blocked` 和 `unavailable` 与执行事实分离。报告事实由规范化 observation
派生，不能由诊断中的状态文字获得资格。未证明的来源结果保留有界 `unadopted_report`，
其中包含规范化候选字段及明确缺口；该报告不进入合法 observation、claim 或计算。

SSE、SZSE、BSE 成交额分区都是必需分母。只有三所分区日期、范围和 CNY 单位符合合同，
才形成总计。两融逐字段折叠，保留各所分区。`complete_fields` 只能列出当前 source fields
中已证明完整的字段；其 proof 由规范化封存。partial 分区中某个字段具有该 proof 时，
它可以与另两所同字段相加；其他字段的缺口不能使该字段失效，也不能缩小任一字段的分母。
缺少显式字段完整性证明的 partial 分区不参与求和。
六条已接受 owner 合同对全部字段禁止 `not_applicable`，因此缺失或调用者文字不能排除交易所。
空两融结果不会被填成六个零值。

`market_foundation_sources.py` 的路径消费已核验 H/I/L/N/Q 来源形状：

| owner | 默认获取与解析 | 明确保留的来源缺口 |
| --- | --- | --- |
| SSE 成交额 | GET `commonQuery.do`，固定官方 origin Referer、已绑定 sqlId、精确 `SEARCH_DATE`；静态 JSONP 读取 01 与 03 的亿元字段，拒绝错日期与重复分类 | 01 与 03 的纯 A 股/CDR 排除及发布时间未证明；不使用 17 或 B 股、回购总计 |
| SZSE 成交额 | GET `ShowReport/data`，已绑定 catalog、controls 与 tab；识别亿元指标及千分位字符串 | gp/xz/cy 明确包含 CDR，不映射为纯 A 股字段；控制日期回显不是独立业务日期；发布时间未知 |
| BSE 成交额 | POST `dailyReport.do`，精确 `HQJSRQ`；静态 JSONP 只选 `xxzrlx=2`，逐行核对 `rq`，按已接受合同保留原始 CNY | 发布时间未知；不添加 XJX 或其他类别金额 |
| SSE 两融 | GET `commonSoaQuery.do`，固定官方 origin Referer、`RZRQ_HZ_INFO`、精确同日 begin/end、完整 pageHelp 检查；`opDate` 独立核对日期 | 金额字段为元；股／份／手数量不具备 canonical shares 资格；发布时间未知 |
| SZSE 两融 | GET `ShowReport/data`，`1837_xxpl`、精确 `txtDate`；只选 tab1 交易总量、校验六个单位标签和 unpaged metadata | Q 正行六字段为字符串，subname 绑定报告日，四金额为亿元；control echo 不能代替 subname，亿股／亿份不转换为股，发布时间未知 |
| BSE 两融 | GET `summaryInfoResult.do`，精确 `transDate`、`page=0`；静态 `null([rows, date])` 允许已观察的单引号日期；六个 Round 字符串与显示精度保留 | JavaDate 创建／更新与 sxrq 只作为候选时点；不由其推导公开时间，不以 13 日替代 14 日 |

所有 JSONP 都只解析静态 JSON，不执行 JavaScript。JSON 对象重复字段、非有限数值、异常 frame、
过大响应和未经证明的 schema 失败关闭。固定 Referer 是已选择并验证的请求路径，不宣称它
对响应成功具有必要性、充分性或因果关系。

这些本地 parser、注入 transport 与安装副本测试证明实现和契约消费，不证明来源实际成功。
普通测试保持离线；最终同候选 live capability 与 release attestation 仍由 V4-13 负责。
