# 仓库交付一个自包含 skill

仓库根目录承载开发文档、测试、质量配置和许可证，唯一安装产物位于 `skill/a-share-research/`。该 skill 以精简 `SKILL.md` 作为平台中立入口，以 `scripts/entrypoint.py` 作为唯一公共运行入口，并将 `scripts/a_share_research/` 下的模块保留为内部实现；详细数据源与估值知识放入按需读取的 `references/`。首版不要求单独安装或发布 PyPI 包；调用者直接执行 skill 内的入口脚本。我们接受仓库结构与安装产物分离的构建复杂度，以保持 skill 自包含、外部 Interface 稳定、上下文精简和运行结果可测试。
