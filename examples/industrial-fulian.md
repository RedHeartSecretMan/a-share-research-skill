# 工业富联：单标的综合研究

本案例展示 v0.3.0 的两个综合单标的方案。示例日期固定为 `2026-08-18`，用于复现请求契约，不代表阅读本文时的最新行情或财务版本。

## 用户问题

> 使用 `$a-share-research` 对工业富联做一轮完整研究：先确认身份，再研究走势、估值、F10 与研究材料、资金和公司事件、未来解禁及板块归属。每一步保留证据、来源状态和限制；最后给出标为 Agent 推断的估值解释、主要风险和可复算的条件触发位。

## 在仓库中运行

通用单标的方案：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-industrial-fulian-single-security.json
```

新标的首次研究方案：

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-industrial-fulian-new-security.json
```

两者都通过唯一公共入口 `entrypoint.py run --request` 执行。`single_security_research@1.0` 面向通用综合研究；`new_security_research@1.0` 固定编排身份、机构覆盖、估值、板块归属、资金流、龙虎榜、未来解禁和两融八步。它们复用现有窄任务，不新增取数捷径。

## 如何阅读结果

- 身份是唯一依赖门禁。身份被阻断时，依赖步骤会明确标为 `skipped_research_dependency`。
- 非身份步骤局部失败不会抹掉独立兄弟步骤；每一步都保留自己的 leaf plan、operation coverage、事实记录和 brief 引用。
- `supported`、`limited`、`blocked` 是证据状态，不是置信度百分比，也不是看多或看空评级。
- 财务报表镜像、机构一致预期、供应商板块分类和市场事件属于不同证据角色，不能混成单一“公司事实”。
- 未来解禁使用独立的最长 90 日窗口；空响应或不可判定覆盖不能写成“没有解禁”。

## 研究判断（Agent 推断）

实际回答应先呈现当次结果，再按以下结构解释：

- **估值解释**：列出 CLI 已复算的总市值、PE TTM、PB MRQ、前向 PE、预测增长、PEG 和 PE 消化时间，并明确价格日、股本时点、财务期、预测年度与用户给定目标 PE。
- **增长假设**：把一致预期和研报观点标为署名外部观点；不得写成公司已实现业绩或法定披露事实。
- **主要风险**：逐项引用当次结果中的财务原件缺口、股本生效边界、预测修订风险、来源失败、分页截断和事件覆盖状态。
- **条件触发位**：可以从 `price / target_pe` 复算使目标 PE 关系失效的 EPS 阈值，也可以定义价格或预测修订条件。该阈值不是买入点、卖出点或目标价。
- **失效条件**：当价格日期、复权口径、股本、财务版本、预测机构数或 EPS 符号发生变化时，必须废弃旧判断并重新运行。

若某个关键领域为 `blocked`，回答只说明该领域缺什么证据以及如何补齐；不得用其他领域的成功结果替代它。
