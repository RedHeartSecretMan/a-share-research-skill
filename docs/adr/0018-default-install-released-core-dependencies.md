---
status: accepted
supersedes: ADR-0016 对 v0.3.0 正式发布核心 operations 按需安装可选依赖的适用范围
---

# 默认安装正式核心运行依赖

核心版本正式发布的来源 operations 所需软件依赖必须完整精确锁定，并由标准隔离 Skill 安装一次性安装；这里的完整范围是正式 F10、盘中快照和分时复盘 operations 的最小实际运行闭包及平台适用项，不是上游 `mootdx` 未发布或 lazy utility/CLI 的全部 declared closure。候选或未发布 operation 的依赖不进入默认安装，上游包的通用 `pip check` 语义也不替代本 Skill 的正式 operation 安装健康。安装后必须检查 artifact-local 依赖文件、版本、module 加载和正式 operation 加载，缺失、错版或加载失败统一报告 `installation_incomplete`。

安装完整性只表达本地软件状态，不探测或承诺来源可访问性、凭据与付费权限或 evidence adoption 资格；这些事实继续由来源策略和研究结果表达。本决策仅取代 ADR-0016 对 v0.3.0 正式发布核心 operations 的按需安装政策，其他能力范围与历史决定保持不变。
