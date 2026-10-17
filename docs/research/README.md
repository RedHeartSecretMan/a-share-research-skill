# 研究与发布资料

本目录以 v0.4.0 为当前发布版本，其资料采用 `v0.4.0-<主题>.<扩展名>` 命名，需要时间锚点的审计文件追加 `-YYYY-MM-DD`。v0.3.0 的历史证据保持原始字节与路径。v0.4.0 来源审计中的候选与发布状态描述记录了正式发布前的冻结时刻；当前发布状态以对应 GitHub Release 和标签为准。带日期的历史现场记录采用 `v<版本>-<主题>-YYYY-MM-DD.md`，并集中放在 [`history/`](history/) 中。仓库级阅读顺序见 [`../README.md`](../README.md)。

## v0.3.0 历史发布资料

| 文件 | 用途 | 修改方式 |
| --- | --- | --- |
| [`v0.3.0-release-audit-2026-08-18.md`](v0.3.0-release-audit-2026-08-18.md) | 该版本发布状态、范围与验证结论 | 人工维护，所有状态必须有仓库证据 |
| [`v0.3.0-operation-qualification.json`](v0.3.0-operation-qualification.json) | 44 个正式来源操作的逐操作资格清单 | 只随资格审查更新 |
| [`v0.3.0-release-probe-evidence.json`](v0.3.0-release-probe-evidence.json) | 冻结候选的最终脱敏联网验证证据 | 只由维护者验证工具生成 |
| [`v0.3.0-operation-release-matrix.json`](v0.3.0-operation-release-matrix.json) | 由资格清单和联网证据派生的发布矩阵 | 只由生成器写入 |
| [`v0.3.0-operation-release-matrix.md`](v0.3.0-operation-release-matrix.md) | 证据口径、解释边界和维护者复验命令 | 人工维护 |

## v0.4.0 当前资料

| 文件 | 用途 | 修改方式 |
| --- | --- | --- |
| [`v0.4.0-operation-qualification.json`](v0.4.0-operation-qualification.json) | #114 的历史资格快照，保留六项待集成报告 owner 的原始契约 | 保留历史身份，不回写为当前发布证明 |
| [`v0.4.0-overview-v2-operation-qualification.json`](v0.4.0-overview-v2-operation-qualification.json) | 市场总览 v2 当前公开计划派生的 46-owner 静态资格：原有 44 项加会话和四指数；六项日报 owner 仅保留内部契约 | 只随对应集成与资格审查更新 |
| [`v0.4.0-release-probe-evidence.json`](v0.4.0-release-probe-evidence.json) | 同一冻结候选的最终脱敏来源证据及 E 离线／安装记录 | 只由维护者工具派生 |
| [`v0.4.0-operation-release-matrix.json`](v0.4.0-operation-release-matrix.json) | 完整 46-owner／61 应用与适用联网 44-owner／59 应用的确定性矩阵 | 只由生成器写入 |
| [`v0.4.0-operation-release-matrix.md`](v0.4.0-operation-release-matrix.md) | 证据口径、未实时验证项和离线复验命令 | 人工维护 |
| [`v0.4.0-release-audit-2026-10-17.md`](v0.4.0-release-audit-2026-10-17.md) | 实际来源身份、三所／四指数 E2E 与最终证明适用边界 | 人工维护，结论须有证据 |

两份静态资格用于按各自版本公开计划闭合资格集合，本身不构成 v0.4.0 发布或联网验证证明；最终证据和矩阵保留真实来源状态，正式发布记录由 GitHub Release 和标签提供。

`v0.3.0-*` 与本版 evidence/matrix JSON 均为版本化证明产物；`v0.4.0-operation-qualification.json` 是开发期静态审阅权威，不是发布产物。两者都不是使用案例，也不得手工调整字段、计数或摘要。运行普通研究请求请从 [`examples/README.md`](../../examples/README.md) 选择请求文件。

当前正式 GitHub Release 为 [`v0.4.0`](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.4.0)。发布提交与 annotated tag 对象由标签确定；冻结运行时候选、来源日期和验证豁免保留在来源审计中，不能用发布提交替换来源证明的原始 SHA。

## 命令约定

所有文档都使用以下占位符，避免把某台机器的解释器路径或安装目录写进仓库：

- `<python>`：已经确认版本不低于 3.12 的 Python 命令；Windows 通常为 `py -3.12`，macOS/Linux 优先为 `python3.12`。
- `<skill-root>`：安装后的 `a-share-research` Skill 根目录。
- 仓库案例和维护者命令都从仓库根目录运行，并使用仓库相对路径。

从仓库运行案例：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/<request>.json
```

从安装后的 Skill 运行自备请求：

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json> [--output <file>]
```

维护者运行仓库工具：

```text
<python> tests/<maintainer-tool>.py <arguments>
```

`run --request` 是唯一公共研究入口。`--output` 只在调用者明确要求持久化时使用；普通运行仍从标准输出读取唯一 JSON 结果。

## 历史资料

v0.0.1、v0.1.0、v0.1.1 和 v0.2.0 均已有对应标签与 GitHub Release。历史文件保留当时的候选、现场响应和发布门禁，只用于追溯，不代表当前来源状态或安装方式。

| 版本 | 历史资料 |
| --- | --- |
| v0.0.1 | [免费来源可行性](history/v0.0.1-free-source-viability-2026-08-01.md) · [Adapter 资格矩阵](history/v0.0.1-adapter-qualification-matrix-2026-08-01.md) · [RQData/iFinD 验证清单](history/v0.0.1-rqdata-ifind-validation-checklist-2026-08-01.md) |
| v0.1.0 | [发布审计](history/v0.1.0-release-audit-2026-08-03.md) · [市场信号联网验证](history/v0.1.0-market-signals-live-check-2026-08-02.md) · [ETF 期权联网验证](history/v0.1.0-etf-options-live-check-2026-08-02.md) |
| v0.1.1 | [发布审计](history/v0.1.1-release-audit-2026-08-03.md) |
| v0.2.0 | [发布审计](history/v0.2.0-release-audit-2026-08-03.md) |

历史文件中的测试数量、提交 SHA、来源状态和研究日期均属于其记录时点。阅读当前能力时，以根目录 README、安装产物中的 `SKILL.md` / `references/`、Spec 0008 Amendment D/E/F、当前静态资格及本版来源验证审计为准；v0.3.0 发布事实仍以 Spec 0007 Amendment A 和本目录的 v0.3.0 资料为准。
