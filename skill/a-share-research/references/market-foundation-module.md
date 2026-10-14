# 完成交易日市场基础 Module

`market_foundation.py` 消费已接受的六 owner 资格，通过内部
`MarketFoundationOperationAdapter.execute(OperationRequest, timeout)` 和
`fold_market_foundation(...)` Interface 提供日报规范化与交易所分区折叠。
该 Module 尚未接入公共 workflow；真实来源的原始响应解析和日期查询装配尚未闭合。

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

SSE、SZSE、BSE 成交额分区都是必需分母。只有三所分区日期、范围和 CNY 单位符合合同，
才形成总计。两融按字段折叠，保留各所分区；目前六条已接受 owner 合同对全部六个字段
明确禁止 `not_applicable`，因此任何缺失或调用者声明都不能缩小某个字段的交易所分母。
完整查询的空集、明确零成交额、partial、credential blocked 和 unavailable 分别保留，
空两融结果不会被填成六个零值。

当前可验证范围是资格消费、解析后 stage 的规范化、有界注入采集、不可变投影和 fold。
缺失真实 transport 时，`execute` 明确拒绝执行；它不会创建永久的 `failed/attempts: 0`
占位来源结果。完成六个默认生产 owner 仍需逐 owner 的实际日期 selector、原始字段映射、
发布时间及必要的范围和许可证据。真实来源成功与最终发布证明仍由其独立验收责任持有。
