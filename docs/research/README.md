# 研究与发布资料

本目录以 v0.3.0 为当前已发布版本；其发布证据采用 `v0.3.0-<主题>.<扩展名>` 命名，只有需要时间锚点的审计文件追加 `-YYYY-MM-DD`。v0.4.0 DAG 开发期的静态资格权威也保留在本目录，但不构成 v0.4.0 发布或联网验证证明。带日期的历史现场记录采用 `v<版本>-<主题>-YYYY-MM-DD.md`，并集中放在 [`history/`](history/) 中。仓库级阅读顺序见 [`../README.md`](../README.md)。

## v0.3.0 当前资料

| 文件 | 用途 | 修改方式 |
| --- | --- | --- |
| [`v0.3.0-release-audit-2026-08-18.md`](v0.3.0-release-audit-2026-08-18.md) | 当前发布状态、范围与验证结论 | 人工维护，所有状态必须有仓库证据 |
| [`v0.3.0-operation-qualification.json`](v0.3.0-operation-qualification.json) | 44 个正式来源操作的逐操作资格清单 | 只随资格审查更新 |
| [`v0.3.0-release-probe-evidence.json`](v0.3.0-release-probe-evidence.json) | 冻结候选的最终脱敏联网验证证据 | 只由维护者验证工具生成 |
| [`v0.3.0-operation-release-matrix.json`](v0.3.0-operation-release-matrix.json) | 由资格清单和联网证据派生的发布矩阵 | 只由生成器写入 |
| [`v0.3.0-operation-release-matrix.md`](v0.3.0-operation-release-matrix.md) | 证据口径、解释边界和维护者复验命令 | 人工维护 |

## v0.4.0 候选资料（待 #98 live attestation）

| 文件 | 用途 | 修改方式 |
| --- | --- | --- |
| [`v0.4.0-operation-qualification.json`](v0.4.0-operation-qualification.json) | #114 的历史资格快照，保留六项待集成报告 owner 的原始契约 | 保留历史身份，不回写为当前发布证明 |
| [`v0.4.0-overview-v2-operation-qualification.json`](v0.4.0-overview-v2-operation-qualification.json) | 市场总览 v2 当前公开计划派生的 46-owner 静态资格：原有 44 项加会话和四指数；六项日报 owner 仅保留内部契约 | 只随对应集成与资格审查更新 |

市场总览 v2 文件用于让维护者探测按实际公开计划义务闭合资格集合；这两份静态记录都不是 v0.4.0 发布矩阵、Release、冻结候选或联网验证证明。

`v0.3.0-*` JSON 证据是版本化发布产物；`v0.4.0-operation-qualification.json` 是开发期静态审阅权威，不是发布产物。两者都不是使用案例，也不得手工调整字段、计数或摘要。运行普通研究请求请从 [`examples/README.md`](../../examples/README.md) 选择请求文件。

正式 GitHub Release 为 [`v0.3.0`](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.3.0)。发布提交、annotated tag 对象、冻结运行时候选和联网证据时点分别记录在发布审计中，不以同一个 SHA 代替四种事实。

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

v0.0.1、v0.1.0、v0.1.1 和 v0.2.0 均已有对应标签与 GitHub Release。历史文件保留当时的候选、现场响应和发布门禁，只用于追溯，不代表 v0.3.0 当前来源状态或安装方式。

| 版本 | 历史资料 |
| --- | --- |
| v0.0.1 | [免费来源可行性](history/v0.0.1-free-source-viability-2026-08-01.md) · [Adapter 资格矩阵](history/v0.0.1-adapter-qualification-matrix-2026-08-01.md) · [RQData/iFinD 验证清单](history/v0.0.1-rqdata-ifind-validation-checklist-2026-08-01.md) |
| v0.1.0 | [发布审计](history/v0.1.0-release-audit-2026-08-03.md) · [市场信号联网验证](history/v0.1.0-market-signals-live-check-2026-08-02.md) · [ETF 期权联网验证](history/v0.1.0-etf-options-live-check-2026-08-02.md) |
| v0.1.1 | [发布审计](history/v0.1.1-release-audit-2026-08-03.md) |
| v0.2.0 | [发布审计](history/v0.2.0-release-audit-2026-08-03.md) |

历史文件中的测试数量、提交 SHA、来源状态和研究日期均属于其记录时点。阅读当前能力时，以根目录 README、安装产物中的 `SKILL.md` / `references/`、Spec 0008 Amendment D/E 与当前静态资格为准；v0.3.0 发布事实仍以 Spec 0007 Amendment A 和本目录的 v0.3.0 资料为准。
