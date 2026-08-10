# Spec 0007 Implementation Amendment A：核心 v0.3.0 实施修订

## 状态

**Accepted by maintainer; tracker applied**，2026-08-10。

本修订案在维护者批准前不授权修改现有 Issue 状态、原生依赖边、生产代码、发布标签或 Release。批准后，它在与父 Spec #36 冲突的实施范围、实施架构、Ticket 处置和 v0.3.0 release gate 上具有优先权；未被本修订明确修改的产品承诺继续由父 Spec #36 管理。

## Parent 与修订原因

- Parent：GitHub Issue #36，`Spec 0007：v0.3.0 计划驱动的完整数据研究运行时`。
- 历史 Ticket：#37–#72。
- 历史候选实现：`codex/issue-37-unified-channel`。
- 本修订只调整 v0.3.0，不撤销延期能力的产品决定；开放式选股、投资者适当性、投资行动建议、仓位和杠杆进入后续 v0.4.0 规格。

父 Spec 同时要求统一研究运行时、全部 47 个候选端点处置与有用端点接入、综合研究、开放式选股、适当性和投资行动建议。这使 release gate 同时受数据通道、来源扩展、批量筛选和个性化建议四条大链路约束。前十张已关闭 Ticket 的实现进一步表明，原 DAG 没有把单一结果模型、operation-local ownership 和局部状态语义放在可独立证明的纵向切片中。

截至本修订起草时的本地证据为：

- `origin/main` 为 `95cf19c`。
- #37–#46 的已提交候选终点为 `907e824`，相对 `origin/main` 修改 77 个文件，增加 39,584 行、删除 569 行。
- #46 从 `953d5cb` 到 `907e824` 修改 43 个文件，增加 28,166 行、删除 1,218 行，并经历 20 个后续提交。
- #47 已提交候选为 `72889b5`，相对 `907e824` 增加 7,241 行、删除 194 行；其 worktree 另有 7 个已修改文件、1 个未跟踪文件及 1,516 行未提交增加。
- `ResearchRuntime.research()` 仍通过可变 `_trace_sink` 捕获 `UnifiedResearchResult`，而 `_dispatch_validated()` 同时构造并返回另一套 private payload；各领域 `trace_*` module 再将其投影到公共结果。
- 历史 #45 的“删除旧结果投影和过渡代码”尚未满足。
- 历史 #46 的 operation-local ownership、严格请求拒绝和有效 observation 下的 `limited` 语义曾出现可复现缺口；后续补丁虽增加检查，仍未消除双结果路径及其重复真相。

测试总数和通过次数只证明被测行为，不证明上述产品不变量已经由架构保证。因此，v0.3.0 不继续在历史候选上叠加 projector、fallback、catch 或兼容层。

## 修订后的产品目标

核心 v0.3.0 交付一个计划驱动、证据优先、默认无状态的 A 股研究运行时。自主研究者通过唯一公共 `run --request` ResearchTask Interface 获得统一研究结果信封，并能够：

1. 对明确证券执行综合单标的研究；
2. 对市场状态执行综合市场研究；
3. 继续执行身份、行情走势、ETF 与 ETF 期权、估值与比较、盘中快照、完整交易日分时复盘、研究材料、资金事件、市场信号和预置研究方案等窄 ResearchTask；
4. 看到每个计划要求、operation 尝试、来源观测、字段去向、coverage、来源错误、冲突、限制、事实主张、计算和研究简报之间的可审计关系；
5. 在局部来源失败时继续获得不依赖该来源的有效来源观测、计算和研究判断；
6. 显式启用研究证据存储，并在默认情况下保持无隐藏缓存和无持久化状态；
7. 通过 `standard` 获取核心获取和标准增强，或在明确速度意图下通过 `fast` 只保证核心获取并看到未请求增强。

## 保留的 v0.3.0 产品承诺

下列父 Spec 承诺继续作为 v0.3.0 release gate：

- 唯一公共 `run --request` ResearchTask Interface；
- 唯一、严格校验的 `schema_version: "1.0"` 请求和研究结果信封；
- 泛化单股请求进入版本化 `single_security_research`；
- 泛化市场请求进入版本化 `market_overview_research`；
- 明确的窄问题继续进入窄 ResearchTask；
- 来源观测保留与证据采纳分离；
- 证据采纳按事实主张、计算和研究判断的具体用途裁决；
- 缺失、冲突和失败只沿显式研究依赖传播；
- 互补来源共同尝试，可替代来源才允许受控 fallback；
- 重复来源观测只做可逆归组，不破坏性删除；
- 每个主动请求字段具有规范化观测、仅交叉核验、仅谱系或附理由排除之一的字段去向；
- 安装完整性、来源能力 coverage 和证据采纳资格分别表达；
- `not_requested`、`skipped_policy`、`skipped_research_dependency`、`failed`、`observed_empty`、`partial`、`complete` 七种来源能力 coverage；
- 独立来源执行隔离、同供应商共享节流和有限重试；
- `standard` 为默认，`fast` 只由结构化请求显式选择；
- 默认 ResearchTask 无状态，研究证据存储必须显式启用；
- 每个 v0.3.0 发布声明的来源 operation 具有 operation-specific 资格、离线契约测试和当次 release probe；
- 所有有效 JSON 研究结果使用退出码 0，协议、I/O 或内部错误使用非零退出码；
- stdout 只输出 JSON，stderr 只输出脱敏诊断；
- 不承诺收益、不生成自动交易指令、不收集券商、银行或交易凭据。

## 延期到 v0.4.0 的产品能力

下列能力不属于 v0.3.0 release gate，并在新的 v0.4.0 Spec 建立前保持未实施或待重新 triage：

- 开放式选股；
- SSE、SZSE、BSE 全 A 股批量扫描和证券池分区排名；
- 确定性筛选因子、权重、可比性门槛、排名和未排名候选；
- 两阶段候选深研和深研候选数模式；
- 大型筛选结果的不可变分区 JSONL 研究结果包；
- 投资者适当性资料和独立加密适当性档案；
- `buy`、`sell`、`hold` 或 `insufficient_evidence` 投资行动计划；
- 基准仓位、仓位区间和杠杆上限；
- 投资行动建议有效边界。

ADR-0035–0041 与 ADR-0051–0055 记录的产品决定没有被否定，但其 release applicability 改为 v0.4.0。ADR-0047、ADR-0049 和 ADR-0050 中“全部 47 端点处置”“所有证明有用端点必须在本版本接入”以及筛选 E2E 属于父 Spec 原扩张范围，本修订不再把它们作为 v0.3.0 完成条件。

现有 BSE 失败关闭行为继续保留；新增 BSE 全市场覆盖只在全 A 股筛选重新进入产品范围时成为 release gate。现有盘中快照和完整交易日分时复盘继续保留；新增五档盘口、逐笔成交及额外微观结构端点不属于核心 v0.3.0 release gate。

## 修订后的完成定义

v0.3.0 的“零静默遗漏”只针对版本化核心计划实际声明和调用的正式来源 operations，不针对固定上游中未调用、未发布的全部候选端点。发布必须证明：

- v0.3.0 计划声明的正式 operations 处置率为 100%；
- 正式 operations 主动请求字段的字段去向覆盖率为 100%；
- 通过自身身份、时点、单位、口径和结构契约的有效来源观测保留率为 100%；
- `standard` 计划中每个应尝试 operation 均有 outcome 或明确跳过状态；
- 任一独立来源失败时，不依赖它的有效 observation 保留率为 100%；
- 每个正式 operation 通过确定性离线契约测试和当次 release candidate 的脱敏 live probe；
- 综合单标的、综合市场、窄 ResearchTask 和显式研究证据存储的 installed-copy E2E 全部通过；
- 同版本旧请求和旧结果形状不被接受、生成或静默转换；
- 仓库内不存在运行时 transition、trace-to-result 或第二结果模型；
- Python 3.12 全量测试、Ruff、strict mypy、Skill validation、复制安装、干净安装、分发验证、双语文档检查和 `git diff --check` 全部通过。

## 强制外部 Interface 与 seams

### 唯一外部 Interface

研究运行时继续遵循 ADR-0007：

```text
research(ResearchTask) -> UnifiedResearchResult
```

安装产物只通过以下公共 seam 暴露该 Interface：

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>
```

调用者只需要理解严格 ResearchTask、统一研究结果信封、错误模式和运行成本。计划编译、来源选择、并发执行、evidence adoption、coverage 聚合和简报状态判定隐藏在同一个深 module 内。

### 来源 operation Adapter seam

真实变化发生在外部来源 operation。每个 Adapter 在内部 seam 满足一个小 Interface，概念形态为：

```text
execute(OperationRequest, timeout) -> OperationOutcome
```

`OperationOutcome` 由一个 operation 创建并拥有，至少包含：

- `capability_id`；
- `operation_id`；
- 一个七态 coverage outcome；
- 本 operation 产生的规范化来源观测；
- 本 operation 产生的来源错误；
- 本 operation 实际响应字段对应的字段去向证据；
- 有界、脱敏、JSON-safe 的运行诊断。

observation 和 source error 在 outcome 内按构造归属于 operation。运行时不得先把输出放入全局 ID 池，再允许 operation 通过任意 ID 声称所有权。稳定 ID 由统一结果构造过程分配或验证，不允许引用兄弟 operation 的输出证明自身 coverage。

生产 Adapter 与确定性测试 Adapter 是该 seam 的两个实际 adapters。不得仅为了测试把内部 helper、projector 或 registry 细节提升为新的外部 Interface。

### 统一结果构造

计划、operation outcomes 和确定性领域计算共同折叠为一个 `UnifiedResearchResult`。coverage 和简报状态必须从计划义务、operation outcomes 与显式研究依赖推导；调用方和领域 projector 不得手工声明一个与实际输出矛盾的状态。

任务特有视图只能引用统一结果中的 observation、claim、calculation、conflict、limitation 和 source error。它不能复制一份 task-private 事实并再次决定状态。

## 非协商不变量

1. `ResearchRuntime.research()` 直接返回唯一 `UnifiedResearchResult`；不通过 callback、sink、全局可变状态或副作用捕获结果。
2. 运行时不存在 `_trace_sink`、`TraceSink`、`trace_*_result`、`_unified_*` 过渡字段或 private legacy payload 到公共结果的转换路径。
3. 同一业务概念只有一个权威定义。字段去向、coverage truth table、状态原因和 operation identity 不在 registry、channel、projector 和测试常量中重复维护。
4. operation ownership 由 `OperationOutcome` 构造保证，并在公共序列化前重新校验。
5. `observed_empty` 只表示完整成功查询证明总量为零；失败、分页不全或 coverage 不明不能伪装成空集。
6. 只要有效来源观测仍能回答核心问题，结果通常为 `limited`；只有身份、时点、口径无法确认、核心冲突或必要输入缺失使核心问题无法安全回答时才 `blocked`。
7. `blocked` 仍返回已经取得的有效来源观测和阻断原因。
8. 请求顶层、`source_policy` 和任务特有对象的未知字段结构化拒绝；同版本旧形状不静默容忍。
9. provider raw payload、凭据、请求头和服务器敏感细节不进入公共结果、日志、夹具或研究证据存储。
10. 任何新 module 必须通过删除测试证明其隐藏了实质复杂度；只转发参数或复制结构的浅 module 不进入正式架构。

## 测试 seams

主要验收 seam 是复制安装后的公共 `run --request` subprocess。每个实现 Ticket 必须以该 seam 的一个可观察失败开始，并在相同 seam 证明行为完成。

唯一必要的较低 seam 是来源 operation Adapter：使用最小确定性响应验证规范化 observation、OperationOutcome ownership、字段去向和失败关闭。运行时内部 module 只在纯确定性逻辑通过公共 seam 难以精确诊断时保留少量内部测试。

旧 `trace_*`、projector、private payload 和浅 channel 的单元测试不构成迁移资产。新的 public-seam 或 operation seam 测试覆盖同一行为后，应删除依赖旧实现布局的测试，而不是在其上再叠一套断言。

## 实施迁移策略

### 基线与资产保留

- 不删除、重置或清理当前主 checkout、`codex/issue-37-unified-channel`、其他 branch 或 worktree。
- 新恢复分支在实施阶段从重新读取的干净 `origin/main` 建立。
- 历史候选分支只作为行为、fixture、来源契约和失败样例的资产源；不得整体 merge 或以保留历史代码为理由继续双结果模型。
- 选择性移植前必须在新 public seam 写出 RED，并证明被移植资产服务于当前 Acceptance Criteria。
- 当前 #47 未提交修改保持原位，Amendment 批准前不继续修改，也不丢弃。

### 渐进替换

恢复实现以纵向 ResearchTask 切片推进。中间恢复分支可以在尚未发布时暂时存在未迁移任务，但每个迁移 Ticket 必须删除该任务对应的旧路径；不建立新的兼容层。#45 只有在全部任务都不再通过 legacy/trace 路径后才能再次关闭。

每个 Ticket 只负责当前 Acceptance Criteria 和一个主要领域变化。若实现需要修改多个无关领域、引入第二套 registry/projector，或需要第二轮超出原 blocker 的大范围返工，主协调代理必须停止并拆票。

## 历史 Ticket 处置

以下 tracker 处置已经在维护者批准 Amendment 后应用。

| Ticket | 当前状态 | Amendment A 处置 | v0.3.0 作用 |
|---|---:|---|---|
| #37 | closed | 保持历史关闭；评论标记其 private tracer 路线已被 supersede，行为和 fixtures 由 A-01 选择性复用 | 不再计作架构完成证据 |
| #38 | closed | 保持历史关闭；市场走势、ETF、ETF 期权行为由 A-03 重新通过直接结果路径验收 | 行为资产 |
| #39 | closed | 保持历史关闭；估值与比较行为由 A-03 重新验收 | 行为资产 |
| #40 | closed | 保持历史关闭；盘中快照与复盘由 A-04 重新验收 | 行为资产 |
| #41 | closed | 保持历史关闭；研究材料由 A-05 重新验收 | 行为资产 |
| #42 | closed | 保持历史关闭；资金事件由 A-06 重新验收 | 行为资产 |
| #43 | closed | 保持历史关闭；市场信号由 A-06 重新验收 | 行为资产 |
| #44 | closed | 保持历史关闭；预置方案由 A-07 重新验收 | 行为资产 |
| #45 | closed | 重新打开，作为唯一结果 Interface 和 transition 删除的验收 gate；依赖 A-07 | 核心 gate |
| #46 | closed | 重新打开，作为 ownership、用途裁决、局部传播和零遗漏验收 gate；依赖 A-02、A-07 | 核心 gate |
| #47 | open | 保留但重置实现；现有 committed/uncommitted candidate 只作资产源；新的实现依赖 #45、#46 | 核心 executor |
| #48 | open | 保留；“全部合格依赖”收缩为 v0.3.0 正式 operations 的运行依赖；依赖 #45 | 核心安装健康 |
| #49 | open | 原范围保留；依赖 #47 | 核心显式存储 |
| #50 | open | 从 #36 的 v0.3.0 子图移出，移除 `ready-for-agent`，标记 `needs-triage` 等待 v0.4.0 Spec | 延期 |
| #51–#57 | open | 固定 47 端点探测与完整矩阵从 v0.3.0 子图移出，移除 `ready-for-agent`，等待独立来源扩展或 v0.4.0 规格 | 延期，不阻塞核心发布 |
| #58 | open | 保留编号并修订为仅验证/补齐核心身份与交易会话 operations；移除 BSE 全市场筛选要求；依赖 A-08 | 核心来源组 |
| #59 | open | 保留编号并收缩到核心计划实际发布的日线、指数、ETF 与期权 operations；依赖 A-08 | 核心来源组 |
| #60 | open | 新增分钟、五档和逐笔端点从 v0.3.0 子图移出；现有盘中快照与复盘由 A-04 保留 | 延期扩展 |
| #61 | open | 保留编号并收缩到综合单标的实际使用的财务、F10、股本、估值与一致预期 operations；依赖 A-08 | 核心来源组 |
| #62 | open | 保留编号并收缩到综合研究实际使用的公告、研报、新闻、快讯与互动 operations；依赖 A-08 | 核心来源组 |
| #63 | open | 保留编号并收缩到综合研究实际使用的资金流、龙虎榜、两融和资本事件 operations；依赖 A-08 | 核心来源组 |
| #64 | open | 保留编号并收缩到综合市场实际使用的广度、涨跌停、轮动、热度、监控、异常和北向披露 operations；依赖 A-08 | 核心来源组 |
| #65 | open | 原综合单标的产品范围保留；移除 #60 及完整 47 端点链依赖；依赖 #47、#58、#59、#61–#64 | 核心产品计划 |
| #66 | open | 原综合市场产品范围保留；微观结构只保持显式扩展语义，不依赖新增 #60；依赖 #47、#58、#59、#62–#64 | 核心产品计划 |
| #67–#71 | open | 从 #36 的 v0.3.0 子图移出，移除 `ready-for-agent`，标记 `needs-triage` 等待 v0.4.0 Spec | 延期 |
| #72 | open | 修订为核心 v0.3.0 release gate，删除筛选、适当性、投资动作、仓位和全 47 端点要求；依赖 #48、#49、#65、#66、A-08 | 最终 gate |

延期 Ticket 保持开放以保留产品意图，不以 `wontfix` 关闭，也不删除历史依赖说明。批准后应从 #36 的原生子 Issue/DAG 中移除，并在 v0.4.0 Spec 建立时重新挂接。

## Amendment A 新增恢复 Tickets

以下使用 amendment-local ID。维护者批准后才创建 GitHub Issues、获得正式编号并建立原生依赖边。

### A-01：以证券身份建立直接统一结果纵向切片

通过 installed-copy `security_identity` 请求建立最小端到端路径：严格 ResearchTask、operation Adapter、operation-owned outcome、直接 `UnifiedResearchResult` 和唯一 stdout JSON。`ResearchRuntime.research()` 不再通过 `_trace_sink` 捕获该任务结果；未知顶层和 `source_policy` 字段失败关闭。

验收重点：

- 一个成功、一个受限、一个阻断身份用例通过公共 seam；
- operation 不能引用兄弟输出；
- 同版本旧请求与未知字段被拒绝；
- 不增加第二公共 schema 或 identity 专用公共命令。

### A-02：由 operation outcomes 推导 coverage、用途采纳和简报状态

在 A-01 纵向切片上，以两个确定性 Adapter 覆盖七态 coverage、局部来源失败、未复核有效 observation、用途不适用、核心冲突和已证明空集。coverage 和简报状态由计划义务、outcomes 与显式研究依赖纯折叠产生，不允许调用方手工设置矛盾状态。

验收重点：

- 有效 observation 在兄弟 operation 失败时保留；
- 有效 observation 能回答核心问题时为 `limited`；
- `blocked` 只影响无法安全回答的核心问题及真实下游；
- operation、observation、source error 和字段去向所有权可从公共结果证明；
- 字段去向只有一个权威定义。

### A-03：直接迁移行情走势、ETF、ETF 期权、估值与比较

选择性复用 #38、#39 的领域行为和 fixtures，使相关窄 ResearchTask 直接产生统一结果对象，删除其 task-private payload 和 `trace_*` 投影。保持 Decimal、交易日、复权、ETF/期权合约和估值谱系语义。

### A-04：直接迁移盘中快照与完整交易日分时复盘

选择性复用 #40 行为和 Spec 0005/0006 的公共用例，使盘中快照与复盘直接产生统一结果对象。保持北京时间、会话、新鲜度、单位、覆盖边界、停牌和双源失败关闭语义；删除对应 trace/projector 路径。

### A-05：直接迁移研究材料

选择性复用 #41 行为和 fixtures，使公告、研报、新闻、快讯、互动问答和 F10 通过 operation outcomes 进入统一结果。互补来源分别保留，可逆归组不删除成员；一个材料来源缺失不能把已有有效材料整体变成 `blocked`。

### A-06：直接迁移资金事件与市场信号

选择性复用 #42、#43 行为和 fixtures，使资金流、龙虎榜、两融、资本事件、题材、轮动、涨跌停、监控、异常和热度直接进入统一结果。市场级、板块级和证券级 observation 保持不同身份；局部失败只降低对应 coverage。

### A-07：迁移预置研究方案并删除所有 transition 路径

选择性复用 #44 的方案行为，使方案步骤只组合统一叶子结果和稳定引用。完成后删除 `_trace_sink`、`TraceSink`、所有 task-result `trace_*` adapters、`_unified_*` 过渡字段、private legacy payload 转换以及只验证旧布局的测试。

本票通过后，#45 和 #46 才能执行确认性验收并重新关闭。

### A-08：建立核心 v0.3.0 正式 operation 资格与 release matrix

从 #45、#46、#47、#48 实际发布路径枚举 v0.3.0 核心计划将调用的 operations，复用现有 operation 资格材料、离线契约和 live probe 入口，形成仅针对正式 operations 的 release matrix。不得先建立通用探测框架；只有现有模式不能满足一个已确认来源时才增加最小能力。

每个 operation 记录：适用范围、字段、时点、单位、失败/空结果语义、来源角色、获取角色、依赖、凭据、限流、字段去向、最近 `verified_at` 和结果。矩阵发现的缺口只进入对应 #58、#59、#61–#64，不自动扩大到固定 47 端点。

## 修订后的 DAG

### 依赖边

```text
A-01 -> A-02
A-02 -> A-03, A-04, A-05, A-06
A-03, A-04, A-05, A-06 -> A-07
A-07 -> #45
A-02, A-07 -> #46
#45, #46 -> #47
#45 -> #48
#47 -> #49
#47, #48 -> A-08
A-08 -> #58, #59, #61, #62, #63, #64
#47, #58, #59, #61, #62, #63, #64 -> #65
#47, #58, #59, #62, #63, #64 -> #66
#48, #49, A-08, #65, #66 -> #72
```

### 可并发 frontier

1. 首个 frontier：A-01。
2. A-01 完成后：A-02。
3. A-02 完成后：A-03、A-04、A-05、A-06 可按最多三个独立 worktrees 并发；共享文件冲突高的 ticket 由主协调代理串行化。
4. 四个领域迁移完成后：A-07。
5. #45/#46 确认通过后：#47 与 #48 可并发。
6. #47 完成后 #49 可与 A-08 并发；A-08 还需 #48。
7. A-08 完成后：#58、#59、#61–#64 按真实文件 ownership 和来源独立性并发。
8. 核心来源组完成后：#65 与 #66 可并发。
9. 最终 frontier：#72。

该 DAG 不包含 #50–#57、#60、#67–#71。它们不再阻塞 v0.3.0。

## Ticket 执行与审查约束

- 主协调代理负责父 Spec、Amendment、live DAG、worktree、范围裁决、集成、tracker 证据和最终门禁。
- 每个 numbered implementation Ticket 由一个独立 worker 在独立 worktree/branch 完成；主协调代理不静默代写 Ticket 实现或 reviewer remediation。
- Worker 首先读取当前 Ticket、Amendment、相关 ADR、CONTEXT、公共 seam 和现有实现；不得从历史快照假设 live 状态。
- 每票使用 public-seam TDD；只有外部来源 Adapter 和纯确定性 fold 允许必要的较低测试。
- 默认一次 Standards/Spec 双审、一次 blocker remediation、一次只验证原 blocker 的确认性复审。
- 只有直接违反当前 Acceptance Criteria、可在公开 seam 稳定复现，并造成错误研究结果、证据失效、secret 泄漏或来源隔离失效的问题阻塞当前票。
- 架构美化、理论攻击面、后续票明确负责的能力和延期产品范围只记录并转交，不在当前票扩张实现。
- 不为最后少量非核心边界增加 fallback、catch、兼容层、第二 registry 或第二 projector。
- Ticket 只有在独立复验、集成到协调分支、tracker 记录 commit 与门禁证据后才完成；本地孤立分支通过不等于 Spec 完成。
- 未经另行授权不 push、不创建 PR、不发布 Release、不清理历史 branch/worktree。

## #72 修订后的最终验收

最终 release gate 至少证明：

1. 安装副本对所有保留窄 ResearchTask 输出唯一严格 `schema_version: "1.0"` 信封；
2. `single_security_research` 的 `standard`、`fast`、局部来源失败、冲突和窄任务路由 E2E 通过；
3. `market_overview_research` 的核心市场定义、标准增强、局部失败、已证明空集和不支持增强 E2E 通过；
4. 显式研究证据存储的默认无状态、写入、命中、新鲜度、版本不符、删除和盘中拒绝 E2E 通过；
5. v0.3.0 正式 operations 的资格、字段去向、安装加载和 release probe 全部通过；
6. 有效 observation 保留、operation-local ownership、coverage truth、局部失败传播和零静默遗漏指标全部为 100%；
7. 请求和响应未知字段、同版本旧形状、非有限数字、过大或不安全 JSON 失败关闭；
8. 仓库搜索和运行测试证明没有 `_trace_sink`、task-result `trace_*`、`_unified_*` 或 legacy result conversion；
9. README、SKILL、CONTEXT、ADR applicability、示例和安装文档只声明核心 v0.3.0 能力，并把延期能力标为未来规格；
10. Python 3.12 全量测试、Ruff、strict mypy、Skill validation、复制安装、干净安装、分发验证、双语文档检查和 `git diff --check` 全部通过；
11. Standards 与 Spec 独立审查均无当前 release blocker；
12. 不把测试总数、代码行数、端点数量或单次 live 成功当作上述验收的替代证据。

## 明确非目标

- 不在本修订阶段编写生产代码；
- 不继续当前 #47 WIP；
- 不清理或重写用户未提交的 CONTEXT、ADR、研究审计材料；
- 不把 Amendment 实现为新的运行时 module、feature flag 或兼容模式；
- 不为了保留历史候选而维持双结果模型；
- 不把全部 47 候选端点的探测、资格或接入率作为 v0.3.0 release gate；
- 不新增开放式筛选、全市场排名、适当性、投资动作、仓位、杠杆或自动交易；
- 不新增持续行情、默认五档盘口或默认逐笔抓取；
- 不自动 push、创建 PR、打 tag 或发布 v0.3.0。

## 批准后的 tracker 应用记录

1. 将本修订案发布为 #36 的 amendment 子 Issue，并在 #36 留下权威链接和 scope override 摘要；
2. 创建 A-01–A-08，挂接为 #36 子 Issues；
3. 重新打开 #45、#46，添加历史候选未满足 AC 的证据评论；
4. 更新 #47–#49、#58、#59、#61–#66、#72 的 Amendment 链接、收缩范围和 native `blocked_by`；
5. 从 #36 v0.3.0 子图移出 #50–#57、#60、#67–#71，移除 `ready-for-agent` 并添加 `needs-triage`；
6. 为 #37–#44 添加 superseded implementation-path 评论但保持历史关闭；
7. 逐项读取原生依赖，证明新图无环、只有 A-01 是初始 frontier；
8. 在所有 tracker 写入回读一致后，才把 A-01 标为 `ready-for-agent` 并启动新的持久协调实施任务。

## Review 清单

维护者批准本修订意味着同时确认：

- 核心 v0.3.0 范围及 v0.4.0 延期范围；
- v0.3.0 不再以固定 47 端点完整探测和接入作为 release gate；
- 历史候选分支保留但不整体继承，新恢复实现从干净 `origin/main` 选择性迁移资产；
- #45、#46 重新打开；
- #37–#44 保持历史关闭但不计作修订后的完成证据；
- A-01–A-08 和修订后的 DAG；
- #49 显式研究证据存储仍属于 v0.3.0；
- tracker 变更和生产代码实施分成两个门禁：先应用 tracker，再另行启动实施。
