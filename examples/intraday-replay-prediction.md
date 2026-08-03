# 分时复盘情景预测边界案例

本案例只说明安装后 Skill 的 Agent 路由与回答结构，不保存来源响应，也不把预测加入 `intraday_replay` 的确定性 JSON。`intraday_replay` 仍只返回来源事实、覆盖裁决和计算；历史查询或 replay 请求只有在用户明确要求未来判断时才进入本案例。This is an explicit request boundary.

## 请求触发与证据门槛

普通身份查询、收盘查询、走势研究或 `intraday_replay` 历史复盘不附加预测。明确提出“预测下一交易日和未来 5 个交易日”后，先取得同一规范证券和复盘日的确定性结果，再核验：

- canonical identity；
- 非 `blocked` 且有 usable close 或 closing stage 的 replay；
- 截至 replay date 的 20 complete daily trading sessions；
- security、date、unadjusted price semantics 与 close 一致；
- 没有 unresolved core source conflict。

## 五类路径

| 路径 | 结果边界 | 回答要求 |
| --- | --- | --- |
| 正常预测 | `supported` | 两个固定 horizon 各给一个主情景和上下行替代情景，并保留全部证据字段。 |
| 证据不足拒绝 | `blocked` | coverage is indeterminate、缺 close、whole morning、whole afternoon、日线不完整或 confirmed suspension 时拒绝，并列出需要补齐的证据。 |
| 部分证据 | `limited` | close 可用且缺口有界时最多形成 limited prediction；逐项列出缺口和受影响的判断。 |
| 可选上下文缺失 | 不自动阻断 | 缺少公告、新闻、资金流、资本事件、估值、行业或大盘材料时，披露 optional context 缺口，只把结论限制在 price behavior，不虚构原因或 main force intent。 |
| 禁止投资行动 | 始终拒绝该部分 | 不输出 rating、target price、buy/sell/hold、position sizing、stop-loss、execution、alert、order 或 automatic trading。 |

## 回答模板

先分层展示 `facts`、`calculations`、`replay analysis` 和 `prediction`。prediction 只在明确请求且满足证据门槛后出现；没有真实可复算输入和样本外验证的模型，不写 exact probabilities 或伪统计 confidence。

### Horizon: next trading day

- Primary scenario: 从 continuation、range、reversal 中选择一个；说明 evidence basis、supporting evidence、opposing evidence、assumptions、observable triggers、invalidation conditions 和 uncertainty。
- upside alternative：同样列出 evidence basis、opposing evidence、assumptions、triggers、invalidation 和 uncertainty。
- downside alternative：同样列出 evidence basis、opposing evidence、assumptions、triggers、invalidation 和 uncertainty。

### Horizon: next 5 trading days

独立重复上述三情景结构；不能用一个 horizon 的主情景代替另一个 horizon，也不能把任一情景写成确定结果。

本案例中的 `supported` 只表示回答结构满足证据门槛，不表示来源真实资格或未来结果已被证明；`limited` / `blocked` 必须原样披露，最终投资决定留给研究者。
