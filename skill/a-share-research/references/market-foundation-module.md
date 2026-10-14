# 完成交易日市场基础 Module

`market_foundation.py` 消费已接受的六 owner 资格，通过内部
`MarketFoundationOperationAdapter.execute(OperationRequest, timeout)` 和
`fold_market_foundation(...)` Interface 提供日报规范化与交易所分区折叠。
该 Module 尚未接入公共 workflow。BSE 两融的真实 GET、日期查询和原始响应解析已有
默认装配，其余五个 owner 的真实来源路径尚未闭合；全部 owner 的采纳条件仍需逐项证明。

`market-foundation-owner-qualification.json` 是已接受 #114 六条记录的精确内容副本。
装配仍调用原 `operation_release_matrix.load_operation_qualification_manifest`，由已有逐
owner 摘要校验资格权威；副本没有增加 owner、改变 selector、单位或适用性规则。

`OperationRequest` 接收消费者确定的完成交易日 `report_date` 和冻结的
`information_cutoff`。本 Module 验证日报日期一致、来源发布时间不早于报告日且不晚于
cutoff、获取时间不早于发布时间；它不自行推导三交易所共同完成交易日。

注入的 report transport 接收资格记录中的原始 selector、请求和 timeout，并通过已有
`ProviderInvocation.call` 执行每次 leaf 请求。所有初次请求、分页及重试共同消耗资格规定的
request budget，同组串行、timeout 和 quiescence 由已有 `ProviderLanes` 拥有。
transport 返回包含已证明字段、单位、范围、日期、发布时间和完整性的解析后 report stage。
它必须自己依据真实契约实现原始响应解析；这里的 report stage 是内部数据结构，不能作为
供应商 wire schema 或真实来源可用性证明。

规范化使用已有 `exact_normalization.exact_scaled_sum`，不受进程 Decimal Context 影响。
owner 输出直接由 #116 的 `OperationOutcome` 脱离调用者后递归封存，投影使用其
`to_dict()`。fold 只接收当前装配的 owner 实际签发的对象，公共结果只包含规范化字段、
固定的安全错误状态、资格中的来源定位以及由执行获得的调用次数；调用者提供的诊断、
locator、异常文本和 attempts 不成为权威。

来源状态统一调用已有发布 Module 的 canonical helper。六 owner 的有界 report facts 从
规范化 observation 和引用关系派生，包含已验证的范围、日期、发布时间、行数、字段和单位，
不会采信诊断中的状态文字。明确零成交额和零条两融记录可以保留报告证据与
`observed_empty` 来源状态；有 observation 时的运行时 coverage 仍按 #116 原规则为
`complete`。发布采集保留同一份有界事实并调用同一 helper，旧 owner 的行为不变。

通用 operation obligation、公共 direct result 和脱敏发布计划均可保留 BSE metadata。
这仅表达全市场分区的交易所归属；旧单证券身份请求及来源的交易所支持规则仍由其原有
Interface 校验，不由通用 metadata 推导新的证券支持范围。

SSE、SZSE、BSE 成交额分区都是必需分母。只有三所分区日期、范围和 CNY 单位符合合同，
才形成总计。两融按字段折叠，保留各所分区；目前六条已接受 owner 合同对全部六个字段
明确禁止 `not_applicable`，因此任何缺失或调用者声明都不能缩小某个字段的交易所分母。
完整查询的空集、明确零成交额、partial、credential blocked 和 unavailable 分别保留，
空两融结果不会被填成六个零值。

`market_foundation_sources.py` 的 BSE 两融路径消费已核验 H/I/J 的来源形状：
GET `summaryInfoResult.do`，精确 `transDate`、`page=0`，静态 `null([rows, date])` frame
允许已观察的单引号日期但不执行 JavaScript。六个 Round 字符串按官方万元／万股展示单位
进入既有精确规范化；raw 字段只验证存在性，不套展示单位倍数。保留展示小数位，结果表示
已报告展示值，不宣称还原未经舍入的 raw 值。GET 的每个 leaf 及 redirect 均由已有 invoker
计数，初次请求与其他实际调用共同受一个请求预算约束。

当前返回的 JavaDate 创建／更新时间与 `sxrq` 只保留为未获语义资格的候选时点。
真实公开时间尚未证明时，owner 返回有实际 attempts 的 `unavailable`，并由 #116 封存有界
未采纳规范化报告；该报告不进入合法 observation、claim 或总计。空数组只证明这次响应为空，
不能形成六个零值或无业务结论；错日期不作 fallback。其他五个缺失真实 transport 的 owner
仍明确拒绝执行。完成六个默认生产 owner 还需各自 selector、字段、发布时间、范围与许可
证据；BSE 日报及 SZSE 混合范围／单位的合同问题由 #114 负责处理，不能在 parser 中掩盖。
