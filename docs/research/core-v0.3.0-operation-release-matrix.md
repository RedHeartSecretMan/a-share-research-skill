# core-v0.3.0 正式来源操作 release matrix

状态：**A-08a 资格基线与 attestation 契约；final release evidence 由 A-08b（#88）对冻结候选生成**

适用规格：[Spec 0007 Amendment A 的 A-08a / A-08b](../specs/0007-implementation-amendment-a.md)

## 两阶段交付边界

A-08a 只交付确定性的 qualification manifest、计划/字段去向/安装加载闭包、typed matrix builder 和唯一 maintainer attempt harness。它是 #58、#59、#61–#64 的开发基线；来源组通过 deterministic operation contracts 与 installed-copy public E2E 关闭，不等待交易时段或临时 provider 状态。缺少 final live evidence 和 durable matrix 时 generator 必须保持失败关闭。

A-08b（#88）在 #58、#59、#61–#66 的全部生产变更完成后冻结一个 clean candidate commit，运行本文件定义的 copied-installed public attempts，并只在同候选 evidence union 精确闭合最终分母后生成 durable artifacts。#88 是 #72 的发布前置；它不把 mock、历史成功、其他候选或 skipped application 转换成 live evidence。

## 分母与解释边界

本矩阵的唯一分母是实际发布的版本化计划在公共结果中声明的 `operation_obligations[].producer_operation_id`。同一正式来源操作即使出现在多个任务、工作流步骤或估值比较成员中，也只计一个 operation；这些出现位置作为 applications 保留，不扩大分母。每个 application 同时保留其 task-local `field_dispositions`；同一 producer 在不同任务中可以有不同的合法目的路径，矩阵不得把它们压成一份虚假的全局 contract。默认 producer builders 和 owner-local contracts 只用于验证已发布计划是否漏掉正式来源，不是第二份 operation registry。

矩阵中的 `experimental` 表示该来源操作仍缺对应 #58、#59、#61–#64 的领域资格验证；`integrated` 只表示其 gap route 已由对应领域 Ticket 以确定性 owner contract 和 copied-installed public E2E 闭合。manifest disposition 不改写 ResearchTask 中 operation 的 `experimental`、`allow_experimental`、coverage 或 evidence-adoption 语义：例如 #58 闭合的身份和盘中快照 operations 仍按现有产品契约显式请求实验来源并保持受限结果。`rate_limit_policy.qualification: qualified_by_issue_58` 记录 #58 已审阅该 owner 的有界请求策略；共享的 `mootdx_intraday_replay@1` 仍保留 #59 gap 和 `experimental` disposition，等待日线/市场序列边界完成。

`probe_status: passed` 只证明显式 release probe 经由唯一公共 `run --request` 入口完成了安全探测并形成可解析证据；`attempted: true` 必须由至少一个 application 的公开 `execution_state` 为 `succeeded` 或 `failed` 推导，`not_requested`、`skipped_policy` 和 `skipped_research_dependency` 都不能充当 live evidence。`source_result` 单独表达已尝试来源的取得结果，可以是 `unavailable`、`credential_blocked` 或其他受支持状态。这些事实不得合并解释成来源已成功或已获生产准入。

一个 release probe attempt 绑定精确候选 Git SHA、显式 `verified_at`、request profile 和 canonical attempt digest。单个 attempt 可以保留未尝试 producer 的 `attempted: false`，但只能作为 append-only partial artifact，不能写成 final。最终 evidence 可以合并最多 16 个同候选 attempts；所有输入必须不晚于本次 assembly、距本次 assembly 不超过 7×24 小时，并各自闭合其 public plan denominator、installation loads 和 samples。跨候选、过期、未来、重复、篡改、未知 owner 或 contract/load 漂移都在新 copied process 前失败关闭。最终仍须精确闭合 reviewed 且 plan-derived 的 44-owner 分母，且每个 owner 至少有一次真实 `succeeded|failed`；这取消的是不必要的“同一个进程必须同时触达 44/44”约束，不是 per-operation live truth gate。

## 三份版本化证据

- [`core-v0.3.0-operation-qualification.json`](./core-v0.3.0-operation-qualification.json) 是 A-08a 逐 producer 审阅的语义 manifest。每项必须引用实际 owner contract、离线测试和 A-08a，并精确闭合计划分母；缺失、额外或重复 producer 都会失败关闭。
- `core-v0.3.0-release-probe-evidence.json` 由维护者显式运行 repo-only probe 并对同候选 fresh attempts 做 typed closure 后生成。它只保存安装健康、sanitized 计划/coverage、逐 producer probe 状态及其候选 SHA/attempt digest/request-profile provenance，不保存凭据、server detail、完整 provider payload 或 provider stderr。
- `core-v0.3.0-operation-release-matrix.json` 完全由前两份证据通过 typed builder 派生。测试按字节检查其是否陈旧；不得手工修改 operation ID、字段或汇总比率。

## 维护者验证

release probe 不进入普通 CI，也不阻塞 #58、#59、#61–#66 的开发 Ticket。A-08b 发布候选时，维护者为 `verified_at` 提供带时区的显式证据时点，并运行：

开始 probe 前必须先把代码固定为一个 clean commit；CLI 读取并绑定该 `HEAD`。attempt 与 final artifact 不得 amend 回这个候选提交，否则候选 SHA 会改变并使全部 attempt 失效；44/44 闭合后，证据与矩阵应作为后续独立提交交付。

```text
<python-3.12> tests/release_probe_core_operations.py \
  --confirm-live \
  --verified-at <ISO-8601-with-timezone> \
  --as-of <YYYY-MM-DD> \
  --replay-date <completed-YYYY-MM-DD> \
  --attempt-output /private/tmp/core-v0.3.0-release-probe-<candidate>-<attempt-id>.json \
  --failure-output /private/tmp/core-v0.3.0-release-probe-failure-<attempt-id>.json \
  --output docs/research/core-v0.3.0-release-probe-evidence.json
```

`--attempt-output` 必须指向本次 attempt 专用且尚不存在的路径；它与 final、failure 和所有 prior attempt 路径必须不同。本次 44/44 尚未闭合时该 partial attempt 仍会原子保留，而 final artifact 保持原字节不变。后续 attempt 在同一命令中为每个 prior artifact 重复增加 `--attempt-input <path>`；CLI 会先验证全部 prior 的候选 SHA、digest、时间窗口和逐 attempt contract closure，再接触 copied installer/provider。

`--failure-output` 必须指向一个本次 attempt 专用、尚不存在且不同于 final `--output` 的路径。若 copied process 或 typed closure 失败，该独立 JSON 只披露固定阶段、已完成/失败 profile，以及 reviewed 44-owner 分母下的 observed、attempted 与精确 unattempted 集合，不包含 provider stdout/stderr、原始 payload、路由值、路径或异常文本。若连 reviewed qualification manifest 都无法安全读取，报告会明确把 denominator 标为 unavailable，而不会猜测缺失集合。成功时 failure path 保持不存在。

随后生成并检查纯派生矩阵：

```text
<python-3.12> tests/generate_core_operation_release_matrix.py --write
<python-3.12> tests/generate_core_operation_release_matrix.py --check
```

copied installer 和 provider probe 子进程只继承宿主中已经存在、经 URL 结构验证且不含用户名、密码或 fragment 的 HTTP(S) routing 变量，以及有界 `NO_PROXY`；仍不继承凭据、header、token 或其他用户配置。routing 值不会进入 artifact、标准输出或标准错误。session-bound snapshot profile 在安装健康后优先执行，artifact 再按 profile 名稳定排序。每个 profile 的 `harness_timeout_seconds` 只是有限的维护者探测上限，不是 direct/workflow 产品计划的聚合 execution budget。每个 partial attempt 先经 strict shape、bounded JSON、candidate/digest、plan/load/sample closure 后以 create-new 语义写入；final evidence 仅在同候选 fresh union 精确闭合 44/44 后通过同目录临时文件和原子替换写入。矩阵顶层 `release_evidence` 再披露唯一 candidate SHA、实际使用的 attempt digests 与验证时间范围。标准输出只含状态、计数、artifact 路径与摘要，失败只输出固定安全诊断。

## 发布门槛

本矩阵要求以下三项均为 `1.000000`，并由实际 numerator/denominator 计算：

- 正式来源操作 disposition 覆盖率；
- active-request 字段去向覆盖率：每个不同的 active task-local field contract 计一次；相同 producer 的相同 contract 跨 applications 去重，不同 contract 分别计数；
- 默认安装加载覆盖率。

任何 plan/evidence owner 漂移、field contract 漂移、安装健康不闭合、离线 contract 未通过、probe harness 未通过、正式来源操作在全部 applications 中均未实际尝试、隐私形态不安全或派生文件陈旧都会失败关闭。
