# 市场总览 v2：三所基础、层级与缺失边界

这是 v0.4.0 候选的公共请求阅读指南。以下数字仅来自确定性离线夹具，不是实际行情或 live attestation；最终同候选匿名发布证明由 #98 完成。原有 v0.3.0 真实运行记录保持其日期与身份。

从仓库根目录运行 [`workflow-market-overview-research.json`](requests/workflow-market-overview-research.json)：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-market-overview-research.json
```

安装副本使用 `assets/requests/workflow-market-overview.json`；替换所有 `REPLACE_*`，使用 `subjects: []`、`schema_version: "1.0"`、`market_overview_research@2.0`。`@1.0` 不再运行，其他已发布方案仍为 `@1.0`。所有来源授权字段保持显式，当前候选验证只使用免费匿名来源。

## 如何阅读

固定清单是 15 项能力，14 项可按层级和适用性执行。`fast` 只执行 core：行业板块广度、涨跌停池、三所交易会话、四个固定指数和完成日成交额；`standard` 再加入两融及原有八项市场增强。清单状态表示计划支持范围，实际数据状态仍要逐项读 coverage、observations、source_errors 和 limitations。

三所为 SSE、SZSE、BSE；四指数固定为 `SSE:000001`、`SZSE:399001`、`SZSE:399006`、`BSE:899050`。它们不代替全市场证券去重广度。会话证据建立共同完成日和信息截止时点；周末保留已证明的最近完成交易日，不按本机星期推算。当前生产日历证明仅覆盖 2026-07-06 至 2026-12-31，超界失败关闭。

离线夹具在 `2026-08-03` 研究时点形成三所完成日成交额 `6750005000 CNY`，三所输入 ID 均保留在计算谱系中；周末 `2026-08-01` 使用 `2026-07-31`。这些值只说明可复算契约。缺 BSE 成交额时，SSE/SZSE 观测保留但没有全市场成交额计算；两融和其他独立步骤继续，不能把缺失填零或把两所称为全 A 股。

`deep_extensions: ["market_wide_intraday"]` 仍明确返回 `unsupported` 和 `free_anonymous_intraday_unproved`，没有 operation、coverage 或观测。它与既有单证券 `intraday_market_signal`、`intraday_replay` 分开。全市场筛选、排名、候选深研和公共结果包工作流已退出；`market_screen_research` 在任何来源调用或 artifact 写入前拒绝，内部模块回归不等于公共筛选 E2E。

## 证据边界

静态资格当前有 52 个 public producers，来自原有 44 项加六项完成日报告、会话与四指数；操作数不是能力槽位数。安装完整性、离线契约、来源实际 attempted 和语义可用 live 结果是独立事实。`limited` 或 `blocked` 可正常以退出码 0 返回；协议拒绝以非零退出码和空 stdout 返回。不要从计划支持、安装成功或合成夹具推断来源当天成功。
