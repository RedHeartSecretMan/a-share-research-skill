<div align="center">

# A股研究技能

**让每个研究数字都带着身份、时点、来源和计算谱系。**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Public API: run --request](https://img.shields.io/badge/public%20API-run%20--request-0F766E)](skill/a-share-research/references/cli-contract.md)
[![Release: v0.3.0](https://img.shields.io/badge/release-v0.3.0-0F766E)](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.3.0)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-D22128)](LICENSE)

[English](README_en.md) · [快速开始](#快速开始) · [能力](#它能做什么) · [中文案例](examples/README.md) · [文档](docs/README.md) · [v0.3.0 发布审计](docs/research/v0.3.0-release-audit-2026-08-18.md)

</div>

`a-share-research-skill` 是项目仓库名，可安装的 Skill 名为 `$a-share-research`。它把自然语言问题路由到确定性 Python CLI，以版本化 JSON 返回证券身份、研究时点、来源观测、计算谱系、冲突、覆盖和限制，再由 Agent 形成可核验的中文研究材料。

当前稳定版本是 **v0.3.0**。它提供 12 类窄 ResearchTask、6 个固定研究方案和唯一公共入口 `run --request`；现有 GitHub Release 与标签均固定在 v0.3.0 发布树上。

## 快速开始

### 1. 获取并安装

克隆仓库后，把完整的 `skill/a-share-research/` 目录复制到所用 Agent 支持的 Skills 目录；复制后的目录就是 `<skill-root>`。不同 Agent 的发现路径和启用方式可能不同，应以对应平台文档为准。如果目标目录已存在，请先检查内容并有意识地升级，不要直接覆盖未知文件。

```text
git clone --depth 1 --branch v0.3.0 --single-branch https://github.com/RedHeartSecretMan/a-share-research-skill.git
<python> <skill-root>/scripts/install.py
```

`<python>` 必须是 Python 3.12 或更高版本：macOS/Linux 通常为 `python3.12`，Windows 通常为 `py -3.12`。安装器根据 [`requirements.lock`](skill/a-share-research/requirements.lock) 安装运行依赖，并自动执行 `installation_health.py`。成功安装以退出码 0 和 `status: "complete"` 为准；`installation_incomplete` 表示软件安装问题，不代表某个数据来源当天可用。

也可以把完整的 [`skill/a-share-research`](skill/a-share-research/) 复制到其他兼容 Agent 的 Skills 目录。不要只复制 `SKILL.md`；`agents/`、`assets/`、`references/`、`scripts/` 和锁定依赖共同构成安装产物。来源不可访问时会在研究结果中失败关闭，绝不触发 silent source switch。

### 2. 运行一个请求

仓库中的请求模板已固定到 v0.3.0 发布日期。先复制模板；凡是“当前”“今天”或盘中任务，都要把 `as_of` 改成实际的北京时间日期。

```text
<python> skill/a-share-research/scripts/entrypoint.py run \
  --request examples/requests/security-identity.json
```

安装后唯一受支持的公共调用形式是：

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json> [--output <file>]
```

CLI 不接受自然语言，也不调用模型。`stdout` 只输出一个版本化 JSON；`stderr` 只输出诊断。有效的 `supported`、`limited` 或 `blocked` 研究结果都可能以退出码 0 返回，协议、I/O 或内部错误才使用非零退出码。

## 它能做什么

| 研究问题 | 直接任务 | v0.3.0 边界 |
| --- | --- | --- |
| 证券身份 | `security_identity` | 名称、简称和裸代码只是线索；歧义或交易所冲突失败关闭 |
| 行情走势 | `market_trend` | 2–250 个完整交易日，未复权或前复权，保留双源 OHLCV 与冲突 |
| ETF 与 ETF 期权 | `etf_market`、`etf_options` | 上交所 ETF；期权覆盖 510050、510300、510500、588000 |
| 当前盘中快照 | `intraday_market_signal` | 仅一只规范 SSE/SZSE A 股和当前北京时间交易日；不是连续行情 |
| 完整交易日分时复盘 | `intraday_replay` | 一只规范 A 股、一个明确已完成交易日；保留分钟覆盖与日级边界 |
| 估值与比较 | `security_valuation`、`valuation_compare` | 先证明发行主体证券类别范围，再计算或保留不可计算状态 |
| 研究材料 | `research_content` | 研报、一致预期、F10、新闻、公告、快讯和互动问答 |
| 资金与事件 | `capital_events` | 资金流、龙虎榜、解禁、两融、大宗、股东户数和分红 |
| 市场信号 | `market_signals` | 题材、板块、轮动、涨跌停、监控、异常和热度 |
| 固定研究方案 | `research_workflow` | 编排既有窄任务，不开放自定义步骤或依赖图 |

实验来源可以贡献观测并暴露冲突，但不能独自把结论提升为 `supported`。F10 是供应商整理的上市公司资料入口，不等同于交易所法定披露；供应商报告的期权 Greeks/IV 也不是项目本地模型或交易所计算值。

### 预置研究方案

| 方案 ID | 用途 | 关键边界 |
| --- | --- | --- |
| `single_security_research` | 综合单标的研究 | `standard` 执行核心与增强；`fast` 只保证身份和走势；仅支持 `long_history`、`large_materials` 深化 |
| `market_overview_research` | 无标的综合市场研究 | 2 项实际核心、8 项标准增强和 5 项显式不支持能力；不以单证券或单指数代理缺口 |
| `single_security_valuation` | 单票估值 | 复用 `security_valuation` |
| `valuation_comparison` | 多票同口径比较 | 复用 `valuation_compare`，不丢弃受阻标的 |
| `theme_report_research` | 主题研报检索 | 复用 `research_content` |
| `new_security_research` | 新标的系统研究 | 身份是唯一门禁，其他局部失败不停止独立同级步骤 |

`single_security_research` 的 `completeness_mode` 默认为 `standard`；`deep_extensions` 只接受 `long_history` 和 `large_materials`。`market_overview_research` 固定保留 `market_trading_session`、`major_broad_indices`、`whole_market_turnover`、`whole_market_margin` 和 `market_wide_intraday` 五项 v0.3.0 缺口，不伪造 observation、claim 或 calculation。

## 如何读取结果

整体状态只有三种：

- `supported`：关键主张具有适用且已完成用途采纳的证据。
- `limited`：核心问题仍可回答，但非关键缺口、冲突或来源限制必须披露。
- `blocked`：身份或关键证据不足，不能形成实质结论。

请同时查看 `plan`、`records`、`coverage`、`source_errors`、`conflicts`、`limitations`、`claims`、`calculations` 和 `briefs`，不要只读顶层状态。来源状态中的 `complete`、`observed_empty`、`partial`、`credential_blocked` 和 `unavailable` 含义不同；来源失败不能改写为零或“没有记录”。

CLI 负责证据、确定性计算和状态。Agent 可以在结果未整体阻断且问题确实需要解释时给出明确标注的研究判断、风险、失效条件和条件触发位；身份查询等直接证据问题不附加无关观点。详细边界见安装产物中的 [`analysis-boundary.md`](skill/a-share-research/references/analysis-boundary.md)。

## 常见中文问题

> 使用 `$a-share-research`，确认“工业富联（601138）”的交易所和规范证券身份，并说明结果截至哪一天。

> 使用 `$a-share-research`，研究蓝色光标最近 10 个完整交易日的未复权走势，列出证据、冲突、主要风险和失效条件。

> 使用 `$a-share-research`，按同一日期和口径比较工业富联、贵州茅台、宁德时代、美的集团和五粮液的估值，保留每只股票的缺失项与限制。

> 使用 `$a-share-research`，以 `single_security_research@1.0` 的 `standard` 模式完成工业富联综合研究；一个材料来源失败时继续不依赖它的步骤。

> 使用 `$a-share-research`，以 `market_overview_research@1.0` 研究今天的市场，保留行业板块、涨跌停池、题材、资金和快讯的局部状态，并明确列出 v0.3.0 不支持的市场能力。

真实运行案例、中文请求模板与边界说明见 [`examples/README.md`](examples/README.md)。其中 `real-runs/` 保存带日期的实际公共 CLI 结果；`requests/` 是可编辑请求，不是实时行情或当前研究结论。

## 能力边界

- 身份与行情网络路径只覆盖 SSE/SZSE；BSE 不会回退到其他市场。
- 估值必须证明证券类别范围；A/H、A/B 或其他多类别范围不明时阻断发行主体级估值。
- 当前盘中快照不是交易级行情、分钟流、五档盘口或逐笔数据；`intraday_replay` 只复盘已完成交易日。
- ETF 期权没有权威完整合约总数、完整合约单位定义、调整条款或合格独立 fallback，必须保留 coverage 和限制。
- 研报、新闻、F10、资金流、题材和热度中的外部归因保持署名观点或市场信号角色，不自动升级为公司事实。
- 当前不支持开放式选股、持续行情、自动交易、仓位/杠杆建议、项目评级或目标价。
- `market_overview_research` 不支持交易会话、主要宽基指数、全市场成交额、全市场两融和全市场盘中微观结构；这些属于未来规格。

## 文档与案例

- [`docs/README.md`](docs/README.md)：文档入口、版本状态和阅读顺序。
- [`examples/README.md`](examples/README.md)：真实运行案例、中文请求模板和边界说明。
- [`skill/a-share-research/SKILL.md`](skill/a-share-research/SKILL.md)：安装后 Agent 使用的简洁路由。
- [`skill/a-share-research/references/`](skill/a-share-research/references/)：按任务域拆分的精确契约。
- [`CONTEXT.md`](CONTEXT.md)：领域术语与产品边界。
- [`Spec 0007 Amendment A`](docs/specs/0007-implementation-amendment-a.md)：v0.3.0 实施与验收权威。

## 版本与发布证据

v0.3.0 已于 2026-08-18 发布。发布审计记录代码提交、annotated tag、GitHub Release 与冻结运行时 attestation 的对应关系；大型 JSON 证据放在研究文档目录，不占用 README 的主阅读路径。

- [GitHub Release v0.3.0](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.3.0)
- [v0.3.0 发布审计](docs/research/v0.3.0-release-audit-2026-08-18.md)
- [v0.3.0 矩阵口径与复验](docs/research/v0.3.0-operation-release-matrix.md)
- [研究与发布资料索引](docs/research/README.md)
- [v0.2.0 历史发布审计](docs/research/history/v0.2.0-release-audit-2026-08-03.md)

## 开发验证

默认测试完全离线；live probe 只供维护者显式诊断，不属于普通 CI，也不能用单次成功替代 operation 资格审查。

```text
<python> -m unittest discover -s tests -p "test_*.py"
ruff check .
ruff format --check .
mypy --strict skill/a-share-research/scripts
<python> /path/to/skill-creator/scripts/quick_validate.py skill/a-share-research
```

维护者命令、live probe 约束和发布证据生成规则见 [`docs/README.md`](docs/README.md) 与 [`docs/research/README.md`](docs/research/README.md)。

## 许可证与来源

项目使用 [Apache-2.0](LICENSE) 许可证。项目源自 [`simonlin1212/a-stock-data`](https://github.com/simonlin1212/a-stock-data)，保留其版权与署名；当前实现围绕 evidence-first 契约独立重建和维护。
