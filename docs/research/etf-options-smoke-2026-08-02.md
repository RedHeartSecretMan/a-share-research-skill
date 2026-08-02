# ETF 期权真实来源 smoke（2026-08-02）

## 目的与环境

本记录验证 Issue #19 的真实来源边界，不把 `blocked` 结果改写成“无合约”或“无报价”。最终复跑环境为 `Darwin 25.5.0 arm64`、`Python 3.12.8`，研究日为 `2026-08-02`，请求的最近完整交易日为 `2026-07-31`。四个请求均使用 `nearest_unexpired`、`atm`、`latest_completed` 和显式实验来源许可。

运行入口：

```text
python3.12 skill/a-share-research/scripts/entrypoint.py run --request examples/requests/<ETF>-atm-options.json
```

## 结果

第一次四标的探针均取得了 `2026-08` 合约清单：510050 为 22 个、510300 为 26 个、510500 为 52 个、588000 为 46 个。随后批量报价响应因请求中的逗号被百分号编码而触发 `unknown_schema`，没有被误报成空合约集合。该探针暴露并修正了 URL 批量协议，同时把默认批次收紧为每次两个合约。

| 标的 | 第一次结果 | 结果文件 SHA-256 |
| --- | --- | --- |
| 510050 | `blocked / unknown_schema`，合约清单 22 | `b453771ecabee68a5b1d31cf323952667ca81c0bd7cb23111d854775626119be` |
| 510300 | `blocked / unknown_schema`，合约清单 26 | `e41abd0a0efa48f6b153828c16780b785cbd5fc90def131b7142facd38090368` |
| 510500 | `blocked / unknown_schema`，合约清单 52 | `9b064ba823855b6dd738dedfb73fa6357d25b03d0ab360d8dd78d9609a764c16` |
| 588000 | `blocked / unknown_schema`，合约清单 46；未静默回退到 510050 | `89f42d21858a8728f6f9632b44ab8f0f722145b10cf9eb2e596ad895f0ea1cc0` |

修正批量协议后重新运行 510050。来源返回的同一快照存在认购与认沽报价时点冲突；当时的运行时返回 `blocked / quote_time_conflict`，没有猜测收盘时点，也没有拼接跨时点 T 型报价。结果文件 SHA-256 为 `a705a2854ac39f6288cffeab54de16729dcc17ffbb5d04b4b9a661072eeb146e`。

一次中间复跑曾因宿主审批响应流中断而未能覆盖全部标的；没有绕过审批或把该次结果冒充为最终代码复跑。其后已在当前工作树上使用 Python 3.12.8 完成四标的最终复跑。

## 最终代码复跑状态

上述探针之后，代码又修正了合约月份与认购/认沽清单证据、ETF 参考价真实时点与来源、报价与 analytics 分离定位、`partial` 合约覆盖、无最新成交但双边报价可用的语义，以及调整合约身份校验。当前工作树的四标的最终复跑均已完成。

四份最终结果都诚实返回 `blocked / quote_time_conflict`：来源已建立 `2026-08` 的部分合约清单，但同一快照的合约报价时点不一致，运行时没有拼接跨时点 T 型报价。由于来源没有权威合约总量，合约清单 coverage 保持为 `partial`；报价和供应商 analytics coverage 均不可判定。稳定结果如下：

| 标的 | 最终状态与稳定诊断 | 合约清单 coverage | 结果文件 SHA-256 |
| --- | --- | --- | --- |
| 510050 | `blocked / quote_time_conflict` | `partial`，`2026-08`，观测 22 个 | `0541c22fd77c46dbd99f43d81c75efb5dbf3dec7e9c8cd27b7273cc5e1de22b7` |
| 510300 | `blocked / quote_time_conflict` | `partial`，`2026-08`，观测 26 个 | `75371238c16ffcc8df433a4392e595830d665c99514d036fd162124e2f7e5a96` |
| 510500 | `blocked / quote_time_conflict` | `partial`，`2026-08`，观测 52 个 | `db343503b096a5038daa6023b3a8a1327088843ce784be2e0ac5fc2f8e2a5221` |
| 588000 | `blocked / quote_time_conflict` | `partial`，`2026-08`，观测 46 个；身份未回退到 510050 | `bde71ee999ac7074c96dcf3533fd1c880150acee2e00d0c094cc11e938db8008` |

这些结果证明当前工作树能够到达四个标的的外部合约来源，并在报价时点冲突时一致地失败关闭；`blocked` 表示证据不足以形成适用的 T 型报价，不表示没有期权合约。`blocked` 输出仍保留身份 evidence，以及 rejected quote batch 的时点分组、计数和 locator 诊断，但这些被拒报价不被采纳为合约证据。

原始 JSON 仅保存在运行主机的 `/private/tmp`，不提交动态行情或供应商原始载荷。仓库只保存命令、环境、稳定诊断、计数和哈希。
