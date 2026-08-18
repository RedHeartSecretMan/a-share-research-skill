# 文档索引

本仓库以 **v0.3.0** 为当前稳定版本。根目录 README 面向使用者，安装产物中的 `SKILL.md` / `references/` 面向 Agent，`specs/`、`adr/` 和 `research/` 面向维护者。先按目的选择入口，不必从头阅读全部历史文件。

## 使用者

- [`../README.md`](../README.md)：中文产品说明、安装、能力边界和快速开始。
- [`../README_en.md`](../README_en.md)：英文产品说明。
- [`../examples/README.md`](../examples/README.md)：v0.3.0 真实运行案例、中文请求模板与边界说明。
- [`../skill/a-share-research/references/cli-contract.md`](../skill/a-share-research/references/cli-contract.md)：唯一公共命令、进程语义和参考文件路由。

## Agent

- [`../skill/a-share-research/SKILL.md`](../skill/a-share-research/SKILL.md)：安装后加载的简洁入口，只负责触发、路由、执行顺序和完成条件。
- [`../skill/a-share-research/assets/requests/`](../skill/a-share-research/assets/requests/)：12 类 ResearchTask 的可复制请求模板；运行前必须替换全部 `REPLACE_*` 占位符。
- [`../skill/a-share-research/references/`](../skill/a-share-research/references/)：按行情、盘中、研究领域、方案、证据和分析边界拆分的精确契约。

## 维护者

- [`../CONTEXT.md`](../CONTEXT.md)：领域术语、边界和非目标。
- [`specs/0007-implementation-amendment-a.md`](specs/0007-implementation-amendment-a.md)：v0.3.0 实施、DAG 与最终验收权威；状态为已交付。
- [`adr/`](adr/)：架构决策。文件按四位编号加短横线主题命名；带 frontmatter 的 ADR 必须把 frontmatter 放在文件第一行。
- [`research/README.md`](research/README.md)：当前发布证据、命令占位符和历史资料索引。
- [`agents/`](agents/)：Issue tracker、triage 和领域文档规则。

## 版本规则

- 当前发布资料采用 `v0.3.0-<主题>.<扩展名>`；只有现场审计需要追加 `-YYYY-MM-DD`。
- v0.0.1–v0.2.0 的研究记录位于 [`research/history/`](research/history/)，保留其原始日期、候选提交和当时结论。
- 历史 Spec 和 ADR 不因新版本发布而重写；若当前状态发生变化，在文件状态区追加交付说明，并保留批准时语境。
- 机器生成的 JSON evidence/matrix 不手工编辑；其人类解释放在相邻 Markdown 文档。

## 统一命令占位符

所有文档统一使用：

- `<python>`：已确认版本不低于 3.12 的 Python 命令。
- `<skill-root>`：安装后的 `a-share-research` Skill 根目录。
- `<research-task.json>`：符合 `schema_version: "1.0"` 的请求文件。

公共研究调用始终写作：

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json> [--output <file>]
```

仓库内案例从仓库根目录运行；维护者工具使用 `tests/<tool>.py`。内部 `scripts/a_share_research/` 模块不是公共入口。
