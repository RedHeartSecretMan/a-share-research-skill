# 真实运行案例：重点监控市场信号

本案例由 v0.3.0 Skill 的唯一公共入口在 `2026-08-19T00:04:24.932423+08:00` 实际运行生成。它不是离线夹具，也不是预写结论；请求和完整 JSON 结果作为同一份带日期快照保存在仓库中。该快照不会自动更新，不能当作阅读本文时的当前市场状态。

## 用户问题

> 截至 2026-08-19，供应商重点监控名单返回了什么？请说明覆盖范围、证据角色和不能据此推断的内容。

## 运行材料

- 请求：[market-focus-monitoring-2026-08-19.request.json](market-focus-monitoring-2026-08-19.request.json)
- 完整结果：[market-focus-monitoring-2026-08-19.result.json](market-focus-monitoring-2026-08-19.result.json)
- 结果 SHA-256：`37b30eca9d6058f32378afb7671b89e5e0e20b67ecf4d0e9e80450259c06c34e`
- 公共命令：

  ```text
  <python> skill/a-share-research/scripts/entrypoint.py run \
    --request examples/real-runs/market-focus-monitoring-2026-08-19.request.json \
    --output examples/real-runs/market-focus-monitoring-2026-08-19.result.json
  ```

请求关闭凭据使用；结果中没有凭据、Cookie、Authorization、localhost 或私有网络地址。

## 直接结论

Skill 合法返回退出码 0 和整体状态 `limited`。`eastmoney_focus_monitoring@1` 当次执行状态为 `succeeded`、coverage 为 `complete`，保留并采纳了 22 条 `focus_monitoring` 观测；其他 7 个市场信号 operation 因本请求只选择 `focus_monitoring` 而保持 `not_requested`。

项目计算 `calculation-2491dd0988ae61956d9ec7de` 记录：供应商报告总数 22、采集 1/1 页、采用 22 条，coverage 为 `observed_nonempty`。结果没有 `conflicts` 或 `source_errors`。

## 来源事实样例

下表只展示完整结果中的前 5 条。代码和名称均保持供应商字段，不能在缺少交易所验证时改写成规范证券身份。

| 供应商代码 | 供应商名称 | 监控状态 | 供应商期间 | 观测 ID |
| --- | --- | --- | --- | --- |
| `300684` | 中石科技 | `active` | 2026-08-19 至 2026-09-01 | `observation-b8ca517652a4582e0b85a2cf` |
| `603221` | 爱丽家居 | `active` | 2026-08-18 至 2026-08-31 | `observation-c6474fa317a231944f76ea65` |
| `920575` | `*ST康乐` | `active` | 2026-08-17 至 2026-08-21 | `observation-f3a656cf6e8a18765c7ab99d` |
| `920090` | `*ST同辉` | `active` | 2026-08-17 至 2026-08-21 | `observation-4dc1da3d4fee8a5cc1809678` |
| `920023` | `*ST田野` | `active` | 2026-08-17 至 2026-08-21 | `observation-f2be2747fcf98b1d1e88cc47` |

## 限制与 Agent 推断

完整结果保留 7 项限制：`availability_time_unknown`、`experimental_market_signal_sources`、`monitoring_reason_unverified`、`provider_watchlist_not_official`、`security_exchange_unverified`、`security_type_unverified` 和 `no_qualified_independent_fallback`。

**Agent 推断：** 这份结果只支持“该供应商在本次检索中返回了 22 条重点监控名单记录”。它不支持把名单称为交易所或监管机构官方名单，也不支持推断入选原因、证券风险等级、公司基本面、后续涨跌或因果关系。若要研究某一条记录，必须先单独解析其规范证券身份，再运行与问题匹配的窄任务。

这是 Agent 基于返回证据和限制形成的研究解释，不是评级、目标价或投资行动建议；最终判断由研究者负责。
