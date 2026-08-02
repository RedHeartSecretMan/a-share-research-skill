# v0.1.0 建设中的真实案例

案例以真实用户问题驱动 v0.1.0 纵向切片。蓝色光标展示 10 日走势与公告新闻研究；工业富联展示自动取得股本、财务三表、一致预期与研报材料后形成的当前研究。

| 案例 | 规范证券标识 | 当前覆盖 | 结果状态 |
| --- | --- | --- | --- |
| [蓝色光标](bluefocus.md) | `SZSE:300058` | 10 日未复权 OHLCV、走势指标与证据结论 | `limited` |
| [工业富联](industrial-fulian.md) | `SSE:601138` | 当前总市值、报告估值、前向估值、预测增长、PEG 与 PE 消化情景 | `limited` |

这里的数字是带日期的案例快照，不是当前行情。实际使用时，Agent 必须把“今天”等相对日期解析成新的北京时间日期，并以当次 CLI 返回的来源、冲突和限制为准。

`requests/` 还包含主题研报、蓝色光标公告新闻、互动易、F10、工业富联研报与一致预期、行业研报、市场快讯，以及资金流、龙虎榜、解禁、两融、大宗、股东户数、分红和北向披露边界的版本化任务。市场信号请求独立覆盖[强势题材](requests/market-strong-stock-themes.json)、[蓝色光标板块归属](requests/bluefocus-board-membership.json)、[行业轮动](requests/market-industry-rotation.json)、[涨跌停生态](requests/market-limit-ecology.json)、[重点监控](requests/market-focus-monitoring.json)、[严重异常波动](requests/market-severe-abnormal-movements.json)、[监控异动交叉](requests/market-monitoring-intersection.json)和[市场热度](requests/market-heat.json)。

这些文件是可执行协议示例，不是固定输出快照。市场信号示例中的固定日期用于展示请求口径；只提供当前快照的来源在其他日期可能返回 `indeterminate` 或 `blocked`。真实运行必须把用户的相对日期解析为明确的北京时间日期，并保留当次返回的来源失败、四态 coverage、分页限制、规则、归因来源、单位、方向和文档验证状态。`observed_empty` 只表示来源已经证明完整空集；`partial` 或 `indeterminate` 不表示市场上没有记录。
