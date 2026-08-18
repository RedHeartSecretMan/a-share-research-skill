# 盘中行情快照案例

本案例展示 v0.3.0 的 `intraday_market_signal` 请求边界。仓库中的[请求样例](requests/intraday-market-snapshot.json)固定记录 v0.3.0 基线日期 `2026-08-18`，用于审查契约，不是永久有效的“当前行情”请求。

## 运行前准备

盘中任务只接受**当前北京时间交易日**。实际运行前复制请求文件，把 `as_of` 改为运行当日明确的 `YYYY-MM-DD`；不要把“今天”写入 JSON，也不要直接沿用历史样例日期。

```text
<python> skill/a-share-research/scripts/install.py
<python> skill/a-share-research/scripts/entrypoint.py run --request <已更新日期的请求.json>
```

`<python>` 必须是 Python 3.12 或更高版本。默认安装会把锁定的 `mootdx==0.11.7` 安装到 Skill 自身 `.runtime`；安装健康与来源可用性是两件不同的事。

## 请求契约

- `task_type` 固定为 `intraday_market_signal`；
- `subjects` 恰好包含一只规范的 `SSE:<code>` 或 `SZSE:<code>` A 股；
- `as_of` 是运行时的当前北京时间交易日，`window` 为 `null`；
- `source_policy.allow_experimental` 为 `true`，且不允许静默切换来源；
- 通达信和腾讯必须对同一证券、交易日、会话和核心价格形成可比较观测。

## 如何理解结果

适用盘中会话内，两路观测兼容时，结果最高仍为 `limited`，因为来源 operation 的资格限制必须保留。结果会记录来源观测时间、获取时间、价格类型、单位、字段血缘、冲突和限制。

历史或未来日期、非交易日、盘前盘后、会话无法确认、依赖缺失、任一核心来源失败、观测陈旧、错证券或核心价格冲突都会返回结构化 `blocked`，不会回退到最近收盘价。有效的 `limited` / `blocked` 领域结果仍以退出码 0 返回；参数、协议、I/O 或内部错误才使用非零退出码。

本案例不保存行情数字、来源原始响应、缓存、凭据或用户级配置。盘中快照是研究级观测，不是交易级实时行情，也不能直接生成买卖、持有或仓位指令。
