# 时点化 A 股 Universe Module

此内部 Module 属于 v0.4.0 已批准设计；不表示生产来源或筛选 workflow 已发布。唯一公共 CLI 仍为 `entrypoint.py run --request`。

`build_as_of_a_share_universe()` 立即准备调用者请求与各 Adapter 返回的 `ExchangeListOutcome` / `IdentityBatchOutcome`，随后只使用不可变快照投影固定的 SSE、SZSE、BSE 分区。Adapter 负责来源获取；本 Module 不解释供应商原始页面，也不证明来源真实可用。

## 总量和局部记录

`ExchangeListOutcome.expected_count` 是适用日期下整个交易所 A 股清单的总量。来源只能证明部分成员、无法证明全交易所总量时传 `None`，不能把已观察记录数或单份材料的成员数充当该总量。`pages_expected` 和 `pages_received` 保留实际查询的分页事实：来源总页数未知时 `pages_expected=None`，`pages_received` 始终是实际收到的非负整数。不能把页预算上限或已收页数冒充来源总页数。一份完整的 1/1 文档也可能只证明未知全交易所总量中的部分成员。

| 清单事实 | 输入状态 | 分区对账 | 完整性 |
| --- | --- | --- | --- |
| 总量已知且清单、身份均闭合 | `complete` 或正向证明的 `observed_empty` | 非负整数，`expected = resolved + failed` | 仍需满足身份和时点证明 |
| 有效局部记录，全交易所总量未知 | `partial`，`expected_count=None` | `expected=null`、`resolved=0`、`failed=null` | `complete=false` |
| 总量未知且来源不可用或凭据阻断 | `unavailable` 或 `credential_blocked`，`expected_count=None` | `expected=null`、`resolved=0`、`failed=null` | `complete=false` |
| 缺少交易所清单 Adapter | 无来源 outcome | `expected=null`、`resolved=0`、`failed=null` | `complete=false` |

未知证券总量或未知总页数均不能与 `complete` 或 `observed_empty` 并存。已知证券总量、未知总页数的局部查询同样使用 `partial`，保留已知整数分母并标记 `incomplete_pagination`。`observed_empty` 必须由完整查询正向证明零条；缺失来源和未知总量不提供这个证明。已知总量仍按原整数约束检查记录数、分页与来源状态。`IdentityBatchOutcome.expected_count` 保持已知的整数批量分母。

清单不完整时继续保留按规范代码排序的已观察证券及其安全 provenance，跳过身份获取，保留 `listing_state_unproven`、`as_of_unproven` 和 `source_failed` 原因。来源 outcome 中 `observed_count` 是实际保留的清单记录数；分区的 `resolved` 是完成身份解析的记录数，两者不能互换。

总量未知的来源分区使用 `exchange_list_total_unknown` limitation code，实际分页事实保留在 `source_outcomes.exchange_list`，未知的 `pages_expected` 明确输出 `null`；identity outcome 没有分页字段。`to_public_dict()` 返回独立的 plain JSON 形状；未知数明确保留为 `null`，不省略、不填零，也不做虚构闭合。只有三个固定交易所的适用清单和身份都完整时，`full_a_share_universe` 才可能为 `true`。

允许未知总量不会放宽类别、身份冲突、重复、时间、分页、operation identity、安全名称与 provenance 的校验，也不改变一次准备后不再回读调用者数据的约束。
