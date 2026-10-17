# 市场总览 v2：三所基础、层级与缺失边界

这是 v0.4.0 的公共请求阅读指南。以下数字仅来自确定性离线夹具，不是实际行情或 live attestation；本版实际来源证明见[来源验证审计](../docs/research/v0.4.0-release-audit-2026-10-17.md)。原有 v0.3.0 真实运行记录保持其日期与身份。

从仓库根目录运行 [`workflow-market-overview-research.json`](requests/workflow-market-overview-research.json)：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-market-overview-research.json
```

安装副本使用 `assets/requests/workflow-market-overview.json`；替换所有 `REPLACE_*`，使用 `subjects: []`、`schema_version: "1.0"`、`market_overview_research@2.0`。`@1.0` 不再运行，其他已发布方案仍为 `@1.0`。所有来源授权字段保持显式，本版来源验证只使用免费匿名来源。

## 如何阅读

固定清单是 15 项能力，12 项可按层级和适用性执行。`fast` 只执行 4 项 core：行业板块广度、涨跌停池、三所交易会话和四个固定指数；`standard` 再加入原有 8 项市场增强。清单状态表示计划支持范围，实际数据状态仍要逐项读 coverage、observations、source_errors 和 limitations。

三所为 SSE、SZSE、BSE；四指数固定为 `SSE:000001`、`SZSE:399001`、`SZSE:399006`、`BSE:899050`。它们不代替全市场证券去重广度。会话证据建立共同完成日和信息截止时点；周末保留已证明的最近完成交易日，不按本机星期推算。当前生产日历证明仅覆盖 2026-07-06 至 2026-12-31，超界失败关闭。

离线夹具在 `2026-08-03` 研究时点保留四个固定指数的完成日观测；周末 `2026-08-01` 使用已证明的 `2026-07-31`。缺 BSE 指数时，其余三个指数观测保留，错误明确指向缺失分区；缺会话分区则阻断会话并跳过依赖它的指数步骤，既有独立市场增强继续。缺失不能填零或由其他交易所代理。

全市场成交额 `whole_market_turnover` 和全市场两融 `whole_market_margin` 分别返回 `free_anonymous_turnover_unproved` 与 `free_anonymous_margin_unproved`，两个槽位均为 `unsupported`。内部六个日报 owner 保留严格的来源范围、发布时间、单位和失败合同，但没有公开 operation、观测、计算、主张或 coverage；不能用单证券、指数、板块或两所合计代理。原有单证券成交额和融资融券材料保持原边界。

`deep_extensions: ["market_wide_intraday"]` 仍明确返回 `unsupported` 和 `free_anonymous_intraday_unproved`，没有 operation、coverage 或观测。它与既有单证券 `intraday_market_signal`、`intraday_replay` 分开。全市场筛选、排名、候选深研和公共结果包工作流已退出；`market_screen_research` 在任何来源调用或 artifact 写入前拒绝，内部模块回归不等于公共筛选 E2E。

## 证据边界

单证券盘中功能保留原接口、安装加载与离线回归，本次交付未进行盘中实时验证；这不表示功能不支持，也不提供实时来源成功证明。市场总览的会话与固定四指数来源证明仍独立验证。

静态资格当前有 46 个 public producers，来自原有 44 项加会话与四指数；六个日报 owner 只保留内部合同。操作数不是能力槽位数。安装完整性、离线契约、来源实际 attempted 和语义可用 live 结果是独立事实。`limited` 或 `blocked` 可正常以退出码 0 返回；协议拒绝以非零退出码和空 stdout 返回。不要从计划支持、安装成功或合成夹具推断来源当天成功。
