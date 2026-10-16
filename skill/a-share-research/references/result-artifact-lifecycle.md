# Result Artifact 内部生命周期

本文说明 v0.4.0 的内部持久化 Interface，保留模块级恢复与防篡改契约；Amendment C 已退出本版公共筛选、排名、候选深研及其结果包工作流，本文不提供可运行的公共筛选入口。唯一外部入口仍是 `entrypoint.py run --request`，`--output` 继续只保存与 stdout 相同的统一结果。实现见 [result_artifact.py](../scripts/a_share_research/result_artifact.py)。

## 调用与冻结研究上下文

`ResultArtifact.create(path, identity, initial_context=prepared)` 独占创建。`identity` 绑定请求语义、producer、plan、operation manifest 的精确字节。调用者须已从请求语义中移除整个 artifact 传输配置；绝对路径及 `create`/`resume` 不进入公共元数据。

冻结研究上下文 C 必须在 create 的初始提交点前持久化。#117 负责 C 的日期、时刻和日历证据语义，内部消费者把已验证的单条 context 记录交给 #115 的正式工厂；原 #69 公共消费者已退出本次版本。例如，使用实际首分区的合法坐标作 canonical 内容载体：

```python
prepared_context = prepare_completed_partition(
    kind="universe",
    exchange="SSE",
    shard_index=0,
    schema_version="screening-context@1.0",
    records=(validated_context_record,),
)
proof = ResultArtifact.checkpoint_capacity(
    identity,
    partition_count=planned_partition_count,
    candidate_count=planned_candidate_count,
    initial_context=prepared_context,
)
artifact = ResultArtifact.create(
    explicit_path, identity, initial_context=prepared_context
)
```

这里的坐标必须是真实合法的计划坐标。生命周期只消费载体的封存 canonical 内容和单记录声明；不把其 `relative_path` 登记为完成文件、不占用该 shard、不增加证券或来源 owner。之后该坐标的实际 Universe 分区仍可通过 `write_completed_partition()` 写入。生命周期不回读原 caller 对象，不重复敏感键、path、locator 或 transport 检查，不重新解释日历。

初始 checkpoint 包含完整 `frozen_context`；C 的 canonical SHA-256 进入 identity，并影响 `artifact_id`。后续 checkpoint 和 manifest 保持同一 C。恢复不能用新的研究时刻改写它。未提交的初始 preparation 不形成已经建立的研究结果包。

`ResultArtifact.resume(path, identity)` 在一个受保护事务内返回：

- 合法 partial 的 `ResultArtifact` handle；调用者读取 `checkpoint()` 的 detached 投影，交给 #117 复验 C 后才开始任何新来源调用。
- 已提交 manifest 的原 bounded receipt；调用者直接返回该 receipt，不能重跑研究或取得 writable handle。

`checkpoint()` 返回新的 plain JSON 副本，包括 C、完成文件声明及 identity。修改副本、普通 `==` 或 `|` 运算不会修改 artifact 或 handle 内状态。`read_completed_file(relative_path)` 在同一事务 Seam 内完整验证 inventory，只返回已完成文件的 canonical 字节；未完成文件、协议文件和路径逃逸被拒绝。恢复消费者由此重用冻结分区和排名，不直接操作文件系统。

`write_completed_partition(prepared)` 和 `write_candidate(prepared)` 只消费 #115 正式工厂的 sealed values。完成坐标不能覆盖、追加或重复登记。`finalize(coverage=...)` 最后发布 manifest 并返回 receipt。`recover_finalized_receipt()` 提供相同的终态恢复；`validate_receipt()` 只验证精确 canonical receipt，计数必须是整数；JSON boolean 或浮点数不能冒充整数计数。

## 事务状态及提交点

一个 artifact 的合作写入通过其目录 inode 的进程间锁串行化。create 在 staged root 公开目标前取得同一 inode 的锁，保持至创建的持久化和 ownership 验证结束；其间 resume、write 和 finalize 排队，不把合法合作进度误判为外部篡改。不同 artifact 不共享全局事务锁。只有首次创建共享受信 store 使用很短的 parent descriptor bootstrap 锁；后续 artifact 事务及记录更新不使用该锁。deterministic Adapter 同样在公开 root 前持有该 artifact 的锁；全局身份分配和独占名字登记只用短临界区，不包住 fault、持久化或整个创建事务。旧 handle 每次重新观察全部完成状态，并核对持久化 ownership、root 及 ancestor generation。

| 状态 | 持久化证据与提交点 | 中断后公共动作 |
| --- | --- | --- |
| Preflight | 完整 inventory、所有文件及所有关联 stage 的身份和字节已观察；整个 immutable plan 在首个修改前重验 | 拒绝或首个 apply 前漂移保证工具造成的树、inode 和字节零变化。 |
| Initial preparation-abort | target 尚不存在；未公开的 sibling stage 可能处于目录建立、checkpoint 写入、owner 写入或全部持久化之后 | target 不被认领。新的 create 使用新的独占 staged root；不会收养或删除旧的外部 staging 对象，也不会收养后来出现的 caller target。 |
| Write preparation-abort | 已有 artifact 的原 checkpoint、C 和全部完成文件不变；sibling stage 可能处于空文件建立、data/checkpoint/journal 写入或持久化之后；尚未提交受信原件或把 journal 公开到 artifact | resume 验证并返回原 partial，保持外部对象原样。重新提交使用新的 journal，最多形成一条逻辑完成记录。此规则适用于已经存在的 artifact，不依赖“尚未发布 artifact”的状态。 |
| Root publication | 完整 staged root 已包含 durable C、checkpoint、精确 owner 和固定目录身份；原始 creation 承诺先在包外受信根持久化，再以 exclusive directory rename 公开 target | target 一旦公开即有可恢复 ownership。公开后的目录持久化失败仍返回失败；新进程 resume 重新证明边界。 |
| Publication intent | 原始 journal 先持久化于受信记录，再公开到 artifact 并持久化；记录 transaction token、精确 stage 名称及目录/文件 identities、预期 content metadata、旧/新 checkpoint identity 和 digest | 所有关联 stage 进入整库存和完整计划校验。resume 只完成明确拥有的此次发布；未知或被替换对象阻止尚未开始的 apply。 |
| Content published | 完成字节以 exclusive hard-link 公开；checkpoint 仍可为旧状态。journal 给出唯一恢复关系 | resume 完成 checkpoint publication，已有内容不覆盖、不追加。 |
| Checkpoint committed | 新 canonical checkpoint 原子替换并完成相关目录持久化；journal 保留原始文件 ownership | 完成文件继续写一次；cleanup 失败或再次中断通过同一 journal 恢复。 |
| Finalize intent | 全部内容、coverage、manifest 和 bounded receipt 已预检；manifest 的精确 bytes/identity 已进入 journal | resume 完成 manifest publication；不改写完成分区或候选。 |
| Manifest committed / sealed | manifest 最后 exclusive 发布，所有声明、内容及必要持久化边界闭合 | resume 只返回原 receipt。已完成内容、checkpoint 完成事实和 manifest 都保持 sealed。 |
| Owned stage cleanup | 先验证整个 sealed/partial 状态及全部待处理 stage，再重验并 apply | 先提交原始 stage 的 move intent，再独占移入包外受信 quarantine；同步两侧目录并 durable closure 后只清理 quarantine。closed 后外部原名再出现立即拒绝。一个无效的最后条目阻止首个 cleanup；apply 开始后中断不承诺多文件物理回滚。 |

创建在首个私有 stage mkdir 前同时重验 ancestor 身份和 target 不存在的前提。target 在此之前出现时，不建立任何 staging；只有合法 apply 已开始之后的冲突或中断才进入 initial preparation-abort。这个观察重验不承诺 namespace CAS，后续竞争仍由 exclusive publication 拒绝。

创建的预期库存从原始 canonical checkpoint、owner 字节和独占建立的描述符身份构造。目录、checkpoint、owner、持久化和公开边界均与该计划核对；scan 只提供观察，不能把写后出现的外部对象或改变过的元数据收养为预期状态。协议文件经已知目录描述符逐级打开，替换目录或 symlink 不能重定向后续写入。公开后只有仍能证明 target 名称、root 描述符和完整库存属于原计划，create 才返回 handle。拒绝不删除外部替换物。

preparation-abort 是明确的未提交状态，不是把自身窗口改称 unknown 来跳过验证。该阶段没有 publication intent，没有公开内容或新的完成声明；artifact 保持原已提交状态。没有已提交 journal 关联的外部对象不会被后续恢复扫描相似名称收养或删除。测试分别在每个实际 namespace/持久化边界由独立进程直接退出，再由新进程验证这些行为。

持久化协议中的 journal sequence、文件 byte/record count、coverage 及 receipt count 必须保留精确 JSON 整数类型；boolean 或浮点数不能依靠 Python 数值相等冒充整数。owner 和 journal 的 filesystem identity 必须是固定宽度的十六进制字符串；checkpoint 与 manifest 还须匹配重建的 canonical 字节。这些校验先于恢复发布和 cleanup，不改变由 #117 解释的冻结 C 内容语义。

完成 journal 留在 `.protocol/`，与包外受信原件核对，不能自行成为 ownership 授权；它不是需要自动删除的残留。journal 提交后的 staging aliases 则有完整 ownership 和合法 cleanup 规则。纯 filename、token、内容 digest 或共享 inode 单独都不构成清理授权。删除或替换 journal、同字节新 inode、伪造 hard-link、unknown inventory 和 ancestor/root/stage 替换均失败关闭。

## 包外受信状态与维护

默认根为 `<skill-root>/.state/result-artifact-anchors/v1`。`ProductionFilesystemAdapter(anchor_root=...)` 仅供受信安装配置和测试注入；请求、环境变量、CLI 和 receipt 没有新增配置。构造不读写状态，observe/preflight 不创建或修复；只有合法 create 进入 apply 后才 bootstrap。未使用 Result Artifact 的请求保持无状态。

保护主体与安装代码相同。结果包编辑者必须没有修改程序、受信根及控制目录的权限；本版本不抵抗同一有效用户、root 或能改写这些受信区域的主体。每级目录以 no-follow descriptor 打开，核对 owner、类型及身份。受信目录由有效用户拥有且为 `0700`，记录为 `0600` 且单硬链。祖先可由 root 拥有；普通祖先不能 group/other 可写，root-owned sticky 临时祖先须同时通过 ACL 检查。macOS 拒绝忽略 ownership 的卷及无法确认权限能力的卷，接受无 ACL 或只含 DENY 的 ACL，拒绝任何 ALLOW/未知条目；Linux 接受无 ACL 或与 mode 相符的三条基础 access ACL，拒绝额外/default ACL。读取失败均拒绝，不删除 ACL 或修权限。创建前还核对有效用户的目录写入条件。

受信根、结果目标和其外部暂存域不得嵌套。v1 要求目标父目录和受信根同一 `st_dev` 且均具备原生产能力；不能跨文件系统或静默使用其他位置。只读安装须在首次修改前拒绝。真实路径和 mode 原文不入受信记录：索引使用实际 root 的固定宽度 dev/inode，原件只含相对名字、物理 identities、原始 owner/journal 字节和有限进度。私有位置、权限、随机物理身份与其摘要不进入公共 checkpoint/manifest/receipt。

| 受信状态 | 中断与拒绝规则 |
| --- | --- |
| Bootstrap preparation | 固定 `v1.preparing` 槽位处于受保护 parent；目录刚建立、marker 临时文件及 quarantine 初始化均是明确未发布准备态。合法新 create 可继续这一个有界槽位，完整 marker 与目录同步后才独占公开 `v1`。已提交 marker 损坏不重写。 |
| Published store | domain marker 绑定 store 与固定 quarantine descriptor identities；create 的原预检和首次 apply 前重验都验证完整域。已发布目录缺 marker、损坏、错域或保护条件不符均失败关闭；resume 永不 bootstrap，旧无 anchor artifact 不自动接受。 |
| Record preparation | 每 artifact 一个固定 `.pending` 槽位；它是未提交的私有准备字节，不能授权 artifact 动作。当前 canonical record 保持有效；原件完整写入并 fsync 后才原子发布。损坏或缺失的已发布 record 不从包重建。 |
| Original transaction committed | 允许恰好一个受信原件尚未公开的末尾 journal。恢复要求精确旧 checkpoint、完整原始 stage、没有对应内容发布；先补 journal durability，再推进内容。缺中间项、多个缺项或已 published 项丢失拒绝。 |
| Move intent / quarantine | 原始 stage 归属从受信原件取得；精确 inventory 校验后提交 move intent，以同 FS exclusive rename 移入受信 quarantine。原名与 quarantine 同时存在、未 closure 两者皆缺失或任何身份/字节变化均拒绝。 |
| Closure / GC | artifact 完成并同步、move 两侧同步后才关闭进度；随后清理受信 quarantine 的合法剩余子集。再次中断可继续 GC；即使最后 rmdir 已可读回且没有 stage，receipt 返回前也重新同步 quarantine 目录，不能跳过失败的最后 durability 边界。closed 后原名后建对象不重新收养或删除。sealed anchor 在成功返回 receipt 后保留，精确重复恢复不改写两域。 |

备份须在相关写入静止时配套保存 artifact 和受信状态；普通复制、旧状态恢复或位置/物理身份变化不构成有效重新绑定。v1 不提供自动迁移、无 anchor 升级、孤立原件清理或跨设备恢复。缺失/损坏时保留现场，可在新目标执行新的 create；不能从包导入可信原件。代码升级应保留 `.state`。分发和 clean-copy 排除 `.state`，不能携带其他运行的 authority；安装代码自包含并不表示 artifact 单独可移动恢复。

内部恢复消费者在开始新来源调用前使用同一受信根、验证 C 和归属；#117 继续解释 C 语义。#97 验证内部 Seam 的跨进程、丢 receipt、anchor 损坏/丢失及分发边界。原 #69 公共编排已退出，本模块的 copied-install 回归不构成公共筛选 CLI/E2E。

## 容量与持久化边界

canonical UTF-8 checkpoint 的上限精确为 65,536 字节，包含完整 C。65,535 和 65,536 字节可接受；65,537 字节必须在有关修改前拒绝。每次写入预检未来 checkpoint。pending、prepared 和 cleanup 的物理身份记录位于有界 journal 及其受信原件，不外移 C 或完成逻辑事实，不会产生另一个更大的隐藏 checkpoint。

内部调用者必须在 artifact 开始写入前用固定计划声明完整 file-count envelope；模块保留三交易所分区及至多 20 个候选的容量边界，供模块回归使用，不对外提供筛选工作流。`checkpoint_capacity()` 使用最长合法坐标、128 字节 schema token、真实最大 byte/record count、完整 C 及 JSON 标点计算最坏大小；identity 契约是每分量 64 位、固定 16 个十六进制字符。它返回固定开销、每条最大开销、pending/cleanup 布局及最坏总量。容量不足先调整合规 shard 方案或报告约束；不能提高上限、截断证券、丢弃 unranked 或取消已批准候选。

受信记录上限为 4 MiB，每 artifact 保留一个 current record、最多一个同上限的未提交 pending record 和最多一个活动 quarantine（目录加至多三个文件）。原始事务及 published/claimed/closed 集合有 512 项硬上限；每个完成声明至少 175 字节，因此 64 KiB checkpoint 不可能容纳 375 个完成文件，实际合法历史加一次 finalize 小于 512。每个文件事务的固定字段、最长 schema 和 identities 的受信编码低于 8 KiB，creation 与唯一 finalize 原件分别受 64 KiB 编码约束；在最大合法工作量下原件和 closure 合计低于 4 MiB。每次 apply 前用完整旧历史、当前真实 identities 与未来 64 位最大 identities 预检含全部 closure 的实际 envelope，不能等 stage 写入后才发现不足。

受信磁盘额外上限为两份 4 MiB record、一个最多 64 MiB 数据别名及两个最多 64 KiB 的 stage 元数据；数据 hard-link 不重复占用完成文件块。每 store 另有一个有限 domain marker 和一个 bootstrap 槽位，不按每次重试累积 bootstrap。已合法 apply 但尚未提交的外部准备对象保持原样，不能借自动清理回收未知 caller 区域；这一原 B2 abort 规则不属于受信事务历史增长。

每个文件及有关目录均需要 `fsync` 成功。失败时不凭读回、digest 或 inode 相符返回成功；显式恢复重新验证并同步必要边界，receipt 返回前再次检查已观察状态；这不扩展为任意非合作写入下的 CAS 或目录线性快照承诺。普通异常、`KeyboardInterrupt`、不执行 Python `finally` 的独立进程突然退出，以及恢复再次中断分别有验证。支持的是进程中断模型，不承诺机器断电、内核崩溃或硬件违反已成功持久化承诺时的恢复。

## 生产平台与验证范围

生产 Adapter 在首次修改前检查 anchored FD、exclusive publication、进程间锁、local filesystem 和目录同步能力。macOS 使用 `MNT_LOCAL`、非只读挂载及 volume `RENAME_EXCL` capability；目录公开使用 `renameatx_np(..., RENAME_EXCL)`。Linux 要求已知 local filesystem、内核至少 4.9 和 `renameat2(..., RENAME_NOREPLACE)`。未知或不具备契约的环境失败关闭；不回退到普通 rename、字符串路径检查、进程内锁或忽略 `fsync`。

checkpoint replacement 由合作锁及重验保护。普通 rename 只对一个 namespace 操作原子，不能被当作期望 inode/字节的 CAS。检测到 apply 后的非合作漂移时立即停止不确定操作并保留可信 journal；不会为物理回滚覆盖外部新对象。

本机生产进程证据来自 macOS 本地 APFS。Linux 契约依据官方定义，尚无本机 Linux 运行证明；deterministic Adapter 的通过不能代替平台证明。deterministic Adapter 不使用 production anchored FD，关闭该能力后仍可执行相同事务状态与故障规则，并产生相同公共 checkpoint、manifest 和 receipt 字节。

权限依据：[Apple ACL 实现](https://github.com/apple-oss-distributions/Libc/tree/main/posix1e)、[Apple 内核 ACL 授权](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_authorization.c)、[Linux ACL](https://man7.org/linux/man-pages/man5/acl.5.html)、[Linux fgetxattr](https://man7.org/linux/man-pages/man2/fgetxattr.2.html)。原生权限运行证据目前仅来自本机 macOS；Linux 支持条件尚无运行证明。

官方原语依据：[Apple rename](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/man/man2/rename.2)、[Apple mount definitions](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/mount.h)、[Apple volume capabilities](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/attr.h)、[Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html)、[Python ctypes](https://docs.python.org/3.12/library/ctypes.html)。
