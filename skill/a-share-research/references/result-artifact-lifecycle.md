# Result Artifact 内部生命周期

本文说明 v0.4.0 的内部持久化 Interface，供排名和候选 workflow 消费；它不表示筛选 workflow 已发布。唯一外部入口仍是 `entrypoint.py run --request`，`--output` 继续只保存与 stdout 相同的统一结果。实现见 [result_artifact.py](../scripts/a_share_research/result_artifact.py)。

## 调用与冻结研究上下文

`ResultArtifact.create(path, identity, initial_context=prepared)` 独占创建。`identity` 绑定请求语义、producer、plan、operation manifest 的精确字节。调用者须已从请求语义中移除整个 artifact 传输配置；绝对路径及 `create`/`resume` 不进入公共元数据。

冻结研究上下文 C 必须在 create 的初始提交点前持久化。#117 负责 C 的日期、时刻和日历证据语义，#69 把已验证的单条 context 记录交给 #115 的正式工厂。例如，使用实际首分区的合法坐标作 canonical 内容载体：

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

`write_completed_partition(prepared)` 和 `write_candidate(prepared)` 只消费 #115 正式工厂的 sealed values。完成坐标不能覆盖、追加或重复登记。`finalize(coverage=...)` 最后发布 manifest 并返回 receipt。`recover_finalized_receipt()` 提供相同的终态恢复；`validate_receipt()` 只验证。

## 事务状态及提交点

一个 artifact 的合作写入通过其目录 inode 的进程间锁串行化；不同 artifact 不共享全局事务锁。旧 handle 每次重新观察全部完成状态，并核对持久化 ownership、root 及 ancestor generation。

| 状态 | 持久化证据与提交点 | 中断后公共动作 |
| --- | --- | --- |
| Preflight | 完整 inventory、所有文件及所有关联 stage 的身份和字节已观察；整个 immutable plan 在首个修改前重验 | 拒绝或首个 apply 前漂移保证工具造成的树、inode 和字节零变化。 |
| Initial preparation-abort | target 尚不存在；未公开的 sibling stage 可能处于目录建立、checkpoint 写入、owner 写入或全部持久化之后 | target 不被认领。新的 create 使用新的独占 staged root；不会收养或删除旧的外部 staging 对象，也不会收养后来出现的 caller target。 |
| Write preparation-abort | 已有 artifact 的原 checkpoint、C 和全部完成文件不变；sibling stage 可能处于空文件建立、data/checkpoint/journal 写入或持久化之后；尚未把 journal 公开到 artifact | resume 验证并返回原 partial，保持外部对象原样。重新提交使用新的 journal，最多形成一条逻辑完成记录。此规则适用于已经存在的 artifact，不依赖“尚未发布 artifact”的状态。 |
| Root publication | 完整 staged root 已包含 durable C、checkpoint、精确 owner 和固定目录身份；以 exclusive directory rename 公开 target | target 一旦公开即有可恢复 ownership。公开后的目录持久化失败仍返回失败；新进程 resume 重新证明边界。 |
| Publication intent | immutable journal 已公开到 artifact 并持久化；记录 transaction token、精确 stage 名称及目录/文件 identities、预期 content metadata、旧/新 checkpoint identity 和 digest | 所有关联 stage 进入整库存和完整计划校验。resume 只完成明确拥有的此次发布；未知或被替换对象阻止尚未开始的 apply。 |
| Content published | 完成字节以 exclusive hard-link 公开；checkpoint 仍可为旧状态。journal 给出唯一恢复关系 | resume 完成 checkpoint publication，已有内容不覆盖、不追加。 |
| Checkpoint committed | 新 canonical checkpoint 原子替换并完成相关目录持久化；journal 保留原始文件 ownership | 完成文件继续写一次；cleanup 失败或再次中断通过同一 journal 恢复。 |
| Finalize intent | 全部内容、coverage、manifest 和 bounded receipt 已预检；manifest 的精确 bytes/identity 已进入 journal | resume 完成 manifest publication；不改写完成分区或候选。 |
| Manifest committed / sealed | manifest 最后 exclusive 发布，所有声明、内容及必要持久化边界闭合 | resume 只返回原 receipt。已完成内容、checkpoint 完成事实和 manifest 都保持 sealed。 |
| Owned stage cleanup | 先验证整个 sealed/partial 状态及全部待处理 stage，再重验并 apply | 仅移除 journal 明确拥有的 exact names/identities。一个无效的最后条目阻止首个 cleanup；apply 开始后中断不承诺多文件物理回滚。 |

preparation-abort 是明确的未提交状态，不是把自身窗口改称 unknown 来跳过验证。该阶段没有 publication intent，没有公开内容或新的完成声明；artifact 保持原已提交状态。没有已提交 journal 关联的外部对象不会被后续恢复扫描相似名称收养或删除。测试分别在每个实际 namespace/持久化边界由独立进程直接退出，再由新进程验证这些行为。

完成 journal 留在 `.protocol/`，作为持久化 ownership 记录；它不是需要自动删除的残留。journal 提交后的 staging aliases 则有完整 ownership 和合法 cleanup 规则。纯 filename、token、内容 digest 或共享 inode 单独都不构成清理授权。删除或替换 journal、同字节新 inode、伪造 hard-link、unknown inventory 和 ancestor/root/stage 替换均失败关闭。

## 容量与持久化边界

canonical UTF-8 checkpoint 的上限精确为 65,536 字节，包含完整 C。65,535 和 65,536 字节可接受；65,537 字节必须在有关修改前拒绝。每次写入预检未来 checkpoint。pending、prepared 和 cleanup 的物理身份记录位于有界 immutable journal，不会产生另一个更大的隐藏 checkpoint。

#68/#69 必须在 artifact 开始写入前用固定计划声明完整 file-count envelope，包括三交易所分区及至多 20 个候选。`checkpoint_capacity()` 使用最长合法坐标、128 字节 schema token、真实最大 byte/record count、完整 C 及 JSON 标点计算最坏大小；identity 契约是每分量 64 位、固定 16 个十六进制字符。它返回固定开销、每条最大开销、pending/cleanup 布局及最坏总量。容量不足先调整合规 shard 方案或报告约束；不能提高上限、截断证券、丢弃 unranked 或取消已批准候选。

每个文件及有关目录均需要 `fsync` 成功。失败时不凭读回、digest 或 inode 相符返回成功；显式恢复重新验证并同步必要边界。普通异常、`KeyboardInterrupt`、不执行 Python `finally` 的独立进程突然退出，以及恢复再次中断分别有验证。支持的是进程中断模型，不承诺机器断电、内核崩溃或硬件违反已成功持久化承诺时的恢复。

## 生产平台与验证范围

生产 Adapter 在首次修改前检查 anchored FD、exclusive publication、进程间锁、local filesystem 和目录同步能力。macOS 使用 `MNT_LOCAL`、非只读挂载及 volume `RENAME_EXCL` capability；目录公开使用 `renameatx_np(..., RENAME_EXCL)`。Linux 要求已知 local filesystem、内核至少 4.9 和 `renameat2(..., RENAME_NOREPLACE)`。未知或不具备契约的环境失败关闭；不回退到普通 rename、字符串路径检查、进程内锁或忽略 `fsync`。

checkpoint replacement 由合作锁及重验保护。普通 rename 只对一个 namespace 操作原子，不能被当作期望 inode/字节的 CAS。检测到 apply 后的非合作漂移时立即停止不确定操作并保留可信 journal；不会为物理回滚覆盖外部新对象。

本机生产进程证据来自 macOS 本地 APFS。Linux 契约依据官方定义，尚无本机 Linux 运行证明；deterministic Adapter 的通过不能代替平台证明。deterministic Adapter 不使用 production anchored FD，关闭该能力后仍可执行相同事务状态与故障规则，并产生相同公共 checkpoint、manifest 和 receipt 字节。

官方原语依据：[Apple rename](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/man/man2/rename.2)、[Apple mount definitions](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/mount.h)、[Apple volume capabilities](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/attr.h)、[Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html)、[Python ctypes](https://docs.python.org/3.12/library/ctypes.html)。
