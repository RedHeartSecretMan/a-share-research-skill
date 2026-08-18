# 蓝色光标：走势、事件与披露的交叉研究

本案例是多个窄任务的组合与呈现指南，不保存任何一次 CLI 结果。示例日期固定为 v0.3.0 基线 `2026-08-18`；它不是阅读本文时的当前行情，方括号内容必须由当次实际结果替换。

## 用户问题

> 使用 `$a-share-research`，研究蓝色光标截至 2026-08-18 的最近 10 个完整交易日走势，并交叉检查龙虎榜、未来 90 日解禁、板块归属、公告与新闻。请逐项披露日期、来源、覆盖和限制，不要把时间接近写成因果；最后给出标为 Agent 推断的研究判断、主要风险和可复算的条件触发位。

## 在仓库中运行

从仓库根目录执行：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-10-day-trend.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-dragon-tiger.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-lockup-90-day.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-board-membership.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-announcements-news.json
```

`<python>` 表示已经确认版本不低于 3.12 的 Python 解释器。每条命令都独立返回统一的 14 字段研究结果；不要把一个任务的成功状态套用到另一个任务。

## 证据合并顺序

1. 先从每个结果的 `research_anchor` 和正式身份观测确认标的是 `SZSE:300058`。
2. 只引用 `observations`、`claims` 和 `calculations` 中能够由稳定 ID 追溯到证据的内容。
3. 分别检查走势、龙虎榜、解禁、板块归属、公告和新闻的 coverage。四种 coverage 状态与来源失败的含义不同。
4. 对照 `conflicts`、`limitations` 和 `source_errors`；公告缺失不能由新闻材料替代，供应商板块标签也不能升级为公司事实。
5. 最后才形成跨任务解释，并明确哪些内容是 Agent 推断。

## 研究判断（Agent 推断）

以下是呈现模板，不是预写的市场结论。实际回答必须用当次 CLI 返回值替换方括号内容。

- **走势判断**：最近 10 个完整交易日的累计涨跌为 `[return]`，最大回撤为 `[drawdown]`，成交量变化为 `[volume_change]`。这些指标最多支持对该窗口价格行为的描述，不能单独证明后续方向。
- **交叉解释**：龙虎榜、板块标签或新闻与价格变化同日出现，只形成共时性线索。只有带正式证据引用的公告事实才能进入公司事件判断。
- **主要风险**：列出当次结果中的身份冲突、来源失败、分页截断、时点不一致和材料缺口；不得把 `indeterminate` 改写成“没有发生”。
- **条件触发位**：可以用窗口内已验证高点、低点或收盘序列定义重新评估条件，并写明价格口径、连续交易日数量和失效条件。该阈值不是买入点、卖出点、止损位或未来价格预测。
- **后续研究**：优先补齐阻断项的原始披露、来源总量或身份交叉证据，再重新运行对应窄任务。

整体状态必须由各任务的实际结果决定。只要关键领域仍为 `blocked`，就停止该领域的实质结论；`limited` 也必须连同限制一起呈现。
