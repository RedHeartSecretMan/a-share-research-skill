# Provider lane 内部 Interface

`ProviderLanes` 是内部来源执行 Module，不新增 CLI 或研究结果模型。一次获取流程中的 owners 必须共享同一 `ProviderLanes` 实例；最多三个 provider groups 同时占用，每个 group 共用一个 `SerialRequestGate`，并使用一致的 pacing/backoff 策略。

`run(provider_group, adapter, policy)` 接收有限的 `actual_attempt_budget`、执行 timeout 和有限 cancellation grace，返回 `ProviderLaneResult`。timeout 从进入 `run` 开始，包括等待同组及三组容量的时间；已启动的调用最多额外等待 cancellation grace。这里的 wall clock timeout 独立于可注入的 pacing clock，避免暂停的测试时钟或供应商拒绝取消使公开返回无界。

## 计数 Seam 与 Adapter

`ProductionInvocationAdapter` 接受一个同步、单次的真实供应商调用，例如 `transport.get`，其 `execute` 通过 execution-owned `ProviderInvocation.call` 调用一次 atomic `invoke`。`invoke` 必须拥有整个调用的终止，不得隐藏 retry 或启动未追踪的后台请求。已有 transport 自动 retry 必须禁用；分页、operation 协调或嵌套 retry helper 放在 `execute` 中，并把每次真实请求交给同一个 `invocation.call`。

`ProviderInvocation.call` 是每次 leaf invocation 唯一计数入口。它在真正调用 atomic Adapter 前记账；初次、内部 gate retry、嵌套 helper retry 和页面请求消耗同一 execution 预算。外层 operation/subprocess 协调不计数。`SerialRequestGate.run_bounded` 在相同 request callback 上限制内部 retry；原 `run` 的接受行为继续保留。Module 不采信 Adapter 或调用者提供的 attempts 数字。

只有当前 execution 的 owning worker 可以使用 invoker；取消、执行结束或其他线程借用均在真实请求前被拒绝。后续 execution 不能重用旧 invoker。确定性测试 Adapter 通过同一 Interface 组合两个既有 gate，证明预算 1 时底层调用只有一次，而外层 helper 不被重复计数。

## Timeout 与恢复

worker 正常返回后才释放 group 和并发容量。timeout 设置 read-only cancellation signal，等待有限 grace；若 invocation 尚未返回，结果包含 `poisoned` quiescence，group 继续占用活动额度，同组后继立即失败关闭。Adapter 的取消声明本身不证明静止。

只有 worker 完成全部 owned invocation 后，Module 才解除 poison。`quiescence(group)` 可以观察这一恢复。历史返回证据保持原快照，不能因为后续恢复而改写。拒绝取消的永久调用将永久保留该 group 的活动额度；返回有界不等于调用已终止。

## 证据与诊断

attempt 与 quiescence 是 Module 签发的不可变对象，按真实调用顺序排列。`is_authoritative` 按本实例签发的对象身份验证；构造、复制、替换或其他实例的证据不能冒充权威快照。该保证不覆盖直接修改 Module 私有状态或解释器反射。

每个 attempt 只公开安全 group、序号和 `active`／`failed`／`succeeded` 状态。失败只有固定 code，不包含异常消息、请求参数、凭据、路径、headers 或供应商响应。gate diagnostics 继续使用既有固定 code 与有限延迟；非法非有限 timing/clock/jitter 失败关闭。Adapter 的领域返回值仍须由其 owner 验证为合格、脱敏的结果，lane 证据不证明来源可用或证据可采纳。

本阶段的独立 Module 测试不证明既有 production worker/transport 已装配此入口。真实 owned caller 的接线及安装副本回归属于同一 #119 的后续整合阶段，须在 Coordinator 刷新 Ticket Base 后执行。
