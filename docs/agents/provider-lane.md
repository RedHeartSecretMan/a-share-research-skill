# Provider lane 内部 Interface

`ProviderLanes` 是内部来源执行 Module，不新增 CLI 或研究结果模型。一次获取流程中的 owners 必须共享同一 `ProviderLanes` 实例；最多三个 provider groups 同时占用，每个 group 共用一个 `SerialRequestGate`，并使用一致的 pacing/backoff 策略。

`run(provider_group, adapter, policy)` 接收有限的 `actual_attempt_budget`、执行 timeout 和有限 cancellation grace，返回 `ProviderLaneResult`。timeout 从进入 `run` 开始，包括等待同组及三组容量的时间；已启动的调用最多额外等待 cancellation grace。这里的 wall clock timeout 独立于可注入的 pacing clock，避免暂停的测试时钟或供应商拒绝取消使公开返回无界。

## 计数 Seam 与 Adapter

`ProductionInvocationAdapter` 接受一个同步、单次的真实供应商调用，例如 `transport.get`，其 `execute` 通过 execution-owned `ProviderInvocation.call` 调用一次 atomic `invoke`。`invoke` 必须拥有整个调用的终止，不得隐藏 retry 或启动未追踪的后台请求。已有 transport 自动 retry 必须禁用；分页、operation 协调或嵌套 retry helper 放在 `execute` 中，并把每次真实请求交给同一个 `invocation.call`。

`ProviderInvocation.call` 是每次 leaf invocation 唯一计数入口。它在真正调用 atomic Adapter 前记账；初次、内部 gate retry、嵌套 helper retry 和页面请求消耗同一 execution 预算。外层 operation/subprocess 协调不计数。`SerialRequestGate.run_bounded` 在相同 request callback 上限制内部 retry；原 `run` 的接受行为继续保留。Module 不采信 Adapter 或调用者提供的 attempts 数字。

invoker 是 Module 签发的 opaque token，预算与执行状态不存放在调用者可修改的实例字段中。只有当前 execution 的 owning worker 可以使用 invoker；取消、执行结束或其他线程借用均在真实请求前被拒绝。后续 execution 不能重用旧 invoker。确定性测试 Adapter 通过同一 Interface 组合两个既有 gate，证明预算 1 时底层调用只有一次，而外层 helper 不被重复计数。

## Timeout 与恢复

worker 正常返回后才释放 group 和并发容量。timeout 设置 read-only cancellation signal，等待有限 grace；若 invocation 尚未返回，结果包含 `poisoned` quiescence，group 继续占用活动额度，同组后继立即失败关闭。Adapter 的取消声明本身不证明静止。

只有 worker 完成全部 owned invocation 后，Module 才解除 poison。`quiescence(group)` 可以观察这一恢复。历史返回证据保持原快照，不能因为后续恢复而改写。拒绝取消的永久调用将永久保留该 group 的活动额度；返回有界不等于调用已终止。

## 证据与诊断

attempt 与 quiescence 是 Module 签发的不可变对象，按真实调用顺序排列。`is_authoritative` 按本实例签发的对象身份验证；构造、复制、替换或其他实例的证据不能冒充权威快照。该保证不覆盖直接修改 Module 私有状态或解释器反射。

每个 attempt 只公开安全 group、序号和 `active`／`failed`／`succeeded` 状态。失败只有固定 code，不包含异常消息、请求参数、凭据、路径、headers 或供应商响应。gate diagnostics 继续使用既有固定 code 与有限延迟；非法非有限 timing/clock/jitter 失败关闭。Adapter 的领域返回值仍须由其 owner 验证为合格、脱敏的结果，lane 证据不证明来源可用或证据可采纳。


## 默认生产装配

默认 identity 的 `isolated_worker` 保持真实子进程隔离。固定 worker 内的 `UrlLibTransport` 把每个 HTTP leaf 交给私有控制桥，父端的 execution-owned invoker 在发送执行授权前消耗预算并签发记录；worker receipt 或 `outcome.diagnostics.attempts` 中的整数不成为计数权威。控制流只有 `begin`、`invoke`、`terminal`、`finish` 与既有规范化 outcome，不传 HTTP body、headers 或原始异常。

`get`、`get_with_timeout` 与 `post` 共用 `_open_once`。urllib 自动 redirect 被禁止，显式下一请求继续使用同一 invoker；URL 解析、允许的 scheme、POST 转 GET 及 redirect loop 上限沿用 Python 3.12 的 `HTTPRedirectHandler` 规则。现有 source helper 的 retry 和分页每次到达 leaf 都再次消耗同一预算，外层 operation／subprocess 协调不重复计数。

父端收到 cancellation 后请求 native worker termination，只有实际进程退出并完成协调后才证明 quiescence；kill 调用或消息中断本身不足以证明。拒绝结束的 worker 保持 poisoned group 和活动额度。默认 owned lane 在同一父运行时内跨 capability 调用共享，避免上一次调用返回后丢弃 poison 状态。

capability consumer 的接受预算仍由现有 `max_requests_per_plan × (1 + len(rate_limit_backoffs))` 派生，initial、followup、retry 和 redirect 共同使用。已开始但到期的 followup 仍贡献真实调用前缀；未启动队列保留失败范围，不能维持虚假的完成结果。领域 outcome 只通过 `to_dict()` 生成 JSON 原生投影，attempts 从 Module 证据覆盖调用者声明。原有四参数 `attempt_runner` 继续作为 atomic 确定性 Adapter Seam；真实 owned operation 使用 leaf-aware runner。

## 容量边界

| owning seam | 最大声明范围 | 必须对账 |
| --- | --- | --- |
| 现有 capability executor | 64 个 owners；每 owner 至多 4 个 logical requests、每请求至多 1 次 initial 和 3 次 retry；至多 3 个 independent groups | 最大合成样本 256 pages、1,024 actual invocations；reported count 等于 observable callbacks；初次与重试共预算 |
| 默认 SSE／SZSE identity owner | 各 3 个 logical requests × 2 attempts | 各至多 6 次真实 leaf 调用，包括 redirect；来源失败不消失 |
| 默认 CNINFO identity owner | 1 个 logical request × 3 attempts | 至多 3 次真实 leaf 调用 |

容量计时从第一个 observable provider invocation 开始，不计 imports、fixture 与 plan setup。合成测试只能证明 owning seam 的计数、活动 lane、内存和 outcome projection 边界，不能成为真实来源吞吐、统一研究结果或 checkpoint 容量证明；后两项由消费者独立对账，64 KiB checkpoint 门槛保持不变。timeout／取消、预算正好耗尽、poison recovery 与局部后续执行使用相同 Interface 验证，不据此制定资源 SLA。
