# 研究级盘中行情快照案例

这个案例展示 v0.2.0 的 `intraday_market_signal` 请求边界，不保存或伪造任何来源成功响应。示例标的是规范证券 `SSE:600519`；仓库中的 [盘中请求](requests/intraday-market-snapshot.json) 保留 `2026-08-03` 发布日，作为可审计的请求契约样本，而不是“当前行情”。

## 运行前准备

盘中任务只接受**当前北京时间交易日**。后续运行时先复制请求文件，把 `as_of` 改成运行当日的明确 `YYYY-MM-DD`；不要把“今天”原样写入 JSON，也不要把历史示例日期当成当前研究时点。

该能力按需依赖 `mootdx==0.11.7`。依赖必须安装在实际执行 Skill 的同一个 Python 3.12+ 环境中；缺少它只会让盘中任务返回 `missing_optional_dependency` / `blocked`，不会阻断其他研究能力。

```text
<python> -m pip install "mootdx==0.11.7"
<python> skill/a-share-research/scripts/entrypoint.py run --request <copied-current-date-request.json>
```

`entrypoint.py run --request` 是唯一公共调用 seam。普通测试与 CI 保持离线；真实来源 probe 只能由维护者显式执行，不能把单次成功升级为生产来源准入。

## 请求契约

- `task_type` 固定为 `intraday_market_signal`；
- `subjects` 恰好包含一只规范 `SSE:<code>` 或 `SZSE:<code>` A 股；
- `as_of` 是运行时的当前北京时间交易日，`window` 为 `null`；
- `source_policy.allow_experimental` 为 `true`，不得静默切换来源；
- 通达信提供同一观测的快照基准，腾讯必须成功完成独立核心价格交叉核验。

## 结果应如何理解

适用盘中会话内，两路观测在身份、交易日、会话、价格类型、时点和核心价格上兼容时，结果最高为 `limited`，因为两个 operation 仍是实验来源。结果必须保留来源观测时间、获取时间、单位、字段血缘、冲突与限制。

以下情况按契约返回结构化 `blocked`，而不是回退到最近收盘价或补猜：历史/未来日期、非交易日、盘前或盘后、会话无法确认、`mootdx` 缺失、任一来源失败、观测陈旧、错证券、价格类型不兼容或核心价格冲突。有效的 `limited` / `blocked` 领域结果仍以退出码 0 返回；只有参数、协议、I/O 或内部错误使用非零退出码和脱敏 stderr。

本案例不包含固定行情数字、来源原始响应、缓存、凭据或 `~/.mootdx` 配置。实际研究只能引用当次 CLI 返回且未被阻断的字段；不能将其称为交易级实时行情，也不能据此生成买卖、持有或仓位指令。
