# v0.3.0 中文可执行案例

本目录提供 42 份版本化请求和 6 篇中文说明。案例标题、问题和解释均使用中文；JSON 中的 `task_type`、字段名和枚举值属于 v0.3.0 公共协议，必须保留原始英文标识，不能翻译后提交给 CLI。

所有固定日期都用于复现请求口径，不是“当前行情”。实际使用时，先把“今天”“最近”等相对时间解析为明确的北京时间日期，再复制请求并更新 `as_of` 与相应窗口。结果必须以当次 CLI 返回的身份、来源、覆盖、冲突、错误和限制为准。

## 运行方式

从仓库根目录执行：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/<request>.json
```

`<python>` 表示已经确认版本不低于 3.12 的解释器。仓库案例使用仓库相对路径；安装后的 Skill 则使用 `<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>`。两者都只通过 `run --request` 进入公共研究接口。

## 中文说明案例

| 案例 | 规范证券 | 说明重点 |
| --- | --- | --- |
| [蓝色光标多证据交叉](bluefocus.md) | `SZSE:300058` | 走势、交易事件、板块归属和披露材料如何分开取证与解释 |
| [工业富联综合研究](industrial-fulian.md) | `SSE:601138` | 综合单标的与新标的固定方案如何保留局部失败和兄弟步骤 |
| [盘中行情快照](intraday-snapshot.md) | `SSE:600519` | 当前北京时间交易日的一次快照以及会话失败关闭 |
| [分时复盘情景边界](intraday-replay-prediction.md) | `SSE:600519` | 只有用户明确请求未来判断时，Agent 才能在证据门槛内形成情景 |
| [上交所分时复盘](intraday-replay-sse.md) | `SSE:600519` | 离线测试夹具中的分钟记录、覆盖状态、摘要和证据谱系 |
| [深交所分时复盘](intraday-replay-szse.md) | `SZSE:000001` | 同一复盘契约在深交所规范证券上的应用 |

## 固定研究方案（6）

| 中文用途 | 请求文件 |
| --- | --- |
| 工业富联综合单标的研究 | [`workflow-industrial-fulian-single-security.json`](requests/workflow-industrial-fulian-single-security.json) |
| 工业富联单票估值 | [`workflow-single-security-valuation.json`](requests/workflow-single-security-valuation.json) |
| 五只股票同口径估值比较 | [`workflow-valuation-comparison.json`](requests/workflow-valuation-comparison.json) |
| 人形机器人主题研报 | [`workflow-theme-report-research.json`](requests/workflow-theme-report-research.json) |
| 工业富联新标的八步研究 | [`workflow-industrial-fulian-new-security.json`](requests/workflow-industrial-fulian-new-security.json) |
| 综合市场研究 | [`workflow-market-overview-research.json`](requests/workflow-market-overview-research.json) |

综合单标的方案的 `standard` 模式尝试身份、20 个完整交易日走势和全部标准增强；`fast` 只执行身份与走势。综合市场方案固定声明 15 项能力，其中 5 项在 v0.3.0 明确为不支持，不会伪造来源。调用者不能提交自定义步骤或依赖图。

## 行情与估值窄任务（4）

| 中文用途 | 请求文件 |
| --- | --- |
| 50ETF 行情 | [`510050-etf-market.json`](requests/510050-etf-market.json) |
| 蓝色光标最近 10 个完整交易日走势 | [`bluefocus-10-day-trend.json`](requests/bluefocus-10-day-trend.json) |
| 五只股票同口径估值比较 | [`five-stock-valuation-compare.json`](requests/five-stock-valuation-compare.json) |
| 工业富联单票估值 | [`industrial-fulian-valuation.json`](requests/industrial-fulian-valuation.json) |

## 研究材料（8）

| 中文用途 | 请求文件 |
| --- | --- |
| 蓝色光标公告与新闻 | [`bluefocus-announcements-news.json`](requests/bluefocus-announcements-news.json) |
| 蓝色光标 F10 资料 | [`bluefocus-f10.json`](requests/bluefocus-f10.json) |
| 蓝色光标投资者问答 | [`bluefocus-investor-qa.json`](requests/bluefocus-investor-qa.json) |
| 工业富联公告 | [`industrial-fulian-announcements.json`](requests/industrial-fulian-announcements.json) |
| 工业富联研报与一致预期材料 | [`industrial-fulian-research-reports.json`](requests/industrial-fulian-research-reports.json) |
| 行业研报 | [`industry-reports.json`](requests/industry-reports.json) |
| 市场快讯 | [`market-flashes.json`](requests/market-flashes.json) |
| 主题研报检索 | [`theme-report-search.json`](requests/theme-report-search.json) |

## 资金与事件（9）

| 中文用途 | 请求文件 |
| --- | --- |
| 蓝色光标龙虎榜 | [`bluefocus-dragon-tiger.json`](requests/bluefocus-dragon-tiger.json) |
| 蓝色光标未来 90 日解禁 | [`bluefocus-lockup-90-day.json`](requests/bluefocus-lockup-90-day.json) |
| 工业富联 5 日资金流 | [`industrial-fulian-5-day-fund-flow.json`](requests/industrial-fulian-5-day-fund-flow.json) |
| 工业富联两融、大宗、股东户数与分红 | [`industrial-fulian-capital-events.json`](requests/industrial-fulian-capital-events.json) |
| 行业板块当日资金流 | [`industry-board-fund-flow.json`](requests/industry-board-fund-flow.json) |
| 行业板块 5 日资金流 | [`industry-board-5-day-fund-flow.json`](requests/industry-board-5-day-fund-flow.json) |
| 全市场龙虎榜 | [`market-dragon-tiger.json`](requests/market-dragon-tiger.json) |
| 贵州茅台融资融券 | [`moutai-margin-trading.json`](requests/moutai-margin-trading.json) |
| 北向净买额披露边界 | [`northbound-disclosure.json`](requests/northbound-disclosure.json) |

## 市场信号（8）

| 中文用途 | 请求文件 |
| --- | --- |
| 蓝色光标板块归属 | [`bluefocus-board-membership.json`](requests/bluefocus-board-membership.json) |
| 重点监控 | [`market-focus-monitoring.json`](requests/market-focus-monitoring.json) |
| 市场热度 | [`market-heat.json`](requests/market-heat.json) |
| 行业轮动 | [`market-industry-rotation.json`](requests/market-industry-rotation.json) |
| 涨跌停生态 | [`market-limit-ecology.json`](requests/market-limit-ecology.json) |
| 监控与严重异常交集 | [`market-monitoring-intersection.json`](requests/market-monitoring-intersection.json) |
| 严重异常波动 | [`market-severe-abnormal-movements.json`](requests/market-severe-abnormal-movements.json) |
| 强势股题材 | [`market-strong-stock-themes.json`](requests/market-strong-stock-themes.json) |

## ETF 期权与盘中（7）

| 中文用途 | 请求文件 |
| --- | --- |
| 50ETF 最近月份平值期权 | [`510050-atm-options.json`](requests/510050-atm-options.json) |
| 300ETF 最近月份平值期权 | [`510300-atm-options.json`](requests/510300-atm-options.json) |
| 500ETF 最近月份平值期权 | [`510500-atm-options.json`](requests/510500-atm-options.json) |
| 科创 50ETF 最近月份平值期权 | [`588000-atm-options.json`](requests/588000-atm-options.json) |
| 当前交易日盘中快照 | [`intraday-market-snapshot.json`](requests/intraday-market-snapshot.json) |
| 上交所已完成交易日分时复盘 | [`intraday-replay-sse.json`](requests/intraday-replay-sse.json) |
| 深交所已完成交易日分时复盘 | [`intraday-replay-szse.json`](requests/intraday-replay-szse.json) |

ETF 期权中的 Greeks/IV 是供应商报告值，不是项目本地 BSM 或交易所计算。`observed_empty` 只表示来源已经证明完整空集；`partial` 或 `indeterminate` 不能解释为市场没有记录。盘中快照只接受运行当日，分时复盘只接受已完成交易日；运行前必须更新固定日期。
