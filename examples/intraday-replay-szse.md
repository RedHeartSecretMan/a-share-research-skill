# 深交所 A 股分时复盘案例：SZSE:000001

这是一个仅用于离线验收的夹具案例，不是供应商响应，也不包含服务器地址、凭据、缓存或用户配置。它通过同一个公共入口演示另一个规范交易所：

```json
{
  "schema_version": "1.0",
  "task_type": "intraday_replay",
  "subjects": [{"security": "SZSE:000001"}],
  "as_of": "2026-08-18",
  "window": {"observed_from": "2026-08-14", "observed_to": "2026-08-14"},
  "parameters": {},
  "source_policy": {
    "allow_experimental": true,
    "allow_credentials": false,
    "allow_fallback": false
  }
}
```

版本化请求见 [intraday-replay-szse.json](requests/intraday-replay-szse.json)。离线验收可让仓库夹具 CLI 使用 `A_SHARE_INTRADAY_REPLAY_SCENARIO=summary_metrics`，生成确定性的部分 coverage 和不可用的日级边界；`complete` 夹具路径则单独证明完整会话分母，并由公共复盘测试使用。

标准 Skill 安装包含锁定的 `mootdx==0.11.7`。缺失、版本不符或无法加载时，安装健康返回 `installation_incomplete`；该软件事实不代表来源可访问或证据可采用，也不会触发来源切换。普通 `security_identity` 任务仍独立运行并报告自己的来源结果。

## 证据形状

夹具原始记录包含来源时间戳、时间语义、交易阶段、成交状态、OHLC、成交量、成交额和证据定位。以下仅为代表性形状：

| 来源时间戳 | 阶段/状态 | 原始 OHLC | 成交量/成交额 | 定位 |
| --- | --- | --- | --- | --- |
| `2026-08-14T09:30:00+08:00` | `continuous_morning` / `traded` | `10.00/10.20/9.90/10.10` | `100 股` / `1000.00 CNY` | `fixture:summary:0930` |
| `2026-08-14T09:32:00+08:00` | `continuous_morning` / `no_trade` | 不可用 | `0 股` / `0.00 CNY` | `fixture:summary:no-trade-0932` |
| `2026-08-14T15:00:00+08:00` | `closing_auction` / `traded` | `10.00/10.00/10.00/10.00` | `50 股` / `500.00 CNY` | `fixture:summary:closing` |

规范化 `records` 保留明确的 `+08:00` 区间、`CNY/share`、股数、CNY、集合竞价分隔和 `evidence_ids`；无成交记录保留不可用 OHLC，不合成平价。该有界夹具路径的 `coverage` 为 `partial`，明确排除午间休市，并暴露缺失区间和可复算比例。`complete` 路径报告 237 个预期连续分钟、237 个覆盖分钟，以及独立的开盘/收盘集合竞价结果。

可复算 `summary` 提供 VWAP、高低点并列、最大回撤、相邻涨跌和上午/下午成交占比，并保留公式、操作数、单位、舍入、范围和 `evidence_ids`。由于没有配置独立日级边界 operation，该夹具明确把开盘缺口、相对收益和相对振幅列入 `unavailable_fields` / `unavailable_metrics`，不会用零替代。

证据谱系标识 `fixture_intraday_replay@1`、请求日期、来源时间戳、来源字段和确定性计算。限制包括仅夹具证据（fixture-only）、实验来源资格、有界部分覆盖和缺少独立日线交叉。这些限制使结果保持 `limited`，不能被引用为当前市场事实。

## 预测与拒绝边界

普通查询或复盘只返回证据。只有明确的未来判断请求，并且已证明规范身份、可用收盘、20 个完整日线交易日、未复权口径一致且没有核心冲突时，才进入 Agent 预测指引。coverage 为 `indeterminate`、缺少收盘或整个上午/下午、日线不完整或确认停牌时返回 `blocked`，并说明需要补什么证据。该夹具的部分覆盖不足以支撑完整预测。

满足证据门槛后，回答分为下一个交易日和未来 5 个交易日两个独立周期。每个周期包含一个延续/震荡/反转主情景，以及上行、下行替代情景，并分别列出依据、反向证据、假设、触发、失效条件和不确定性。事实、计算、复盘分析和预测必须分层，不输出伪精确概率、评级、目标价或投资行动。
