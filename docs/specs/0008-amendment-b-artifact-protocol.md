# Spec 0008 Amendment B：结果包恢复协议

本文是[主修订](0008-amendment-b-delivery-plan.md) B2 的配套规范；任务归属见[责任映射](0008-amendment-b-tickets.md)。维护者已采用 B1–B3 推荐方案，包括第 1 节五项 B2 决定及终态 resume 路由，并另行授权 Tracker 应用与定义快照更新。相应职责和 AC 已应用到 #108 等正式任务；这不构成 #108 验收、Definition Checkpoint 接受、DAG 启动或发布。基线依据为 `codex/v0.4.0-dag@b564f9e` 的 Spec 0008、当时 #108/#115 的完整定义及 #115 Maintainer Decision A；最新正式正文见 `.dag/definition-github-spec-0008.json`。未接受的 `96a24a9` 仅用于说明验证缺口，不作为已交付能力或已复现运行故障。

## 1. 修订目的与已批准边界

保留 Spec 0008 的显式结果目录、精确 resume、完整 shard 写一次、manifest-last、可验篡改、有界 receipt、唯一公共 CLI 和 unchanged `--output` 语义。修订只把 #108 的并发、持久化、所有权和恢复义务写成可实现、可审查的协议，避免把“验证失败零修改”与“多步 apply 中途失败仍没有任何物理修改”混为一谈。

以下五项决定均已随 B2 推荐方案获得维护者批准；落实时须绑定到 DAG Definition，不能由执行者自行改变：

1. **生产文件系统支持范围。** 采用第 2 节所述本地文件系统能力边界；生产支持不从 deterministic Adapter 能运行推导。若实施时发现现有发布承诺比此范围更广，须指出冲突并另行决定相应平台的实现或范围迁移，不静默改变既有承诺。
2. **AC2 的原子性解释。** apply 前拒绝保证工具零修改；apply 开始后的故障保证持久化 journal 支持安全恢复，不承诺任意多文件物理原子回滚。把 AC2 按此解释落文属于验收边界决定。
3. **故障范围。** 支持普通异常、进程突然终止和单次恢复再次中断；机器断电、内核崩溃或存储硬件违反已成功持久化承诺，不纳入本版本必备运行验收。该界定不允许忽略 `fsync` 失败或以读回成功代替持久化成功。
4. **非合作并发写入。** 对检测到的外部替换、篡改、重定向保持失败关闭；不承诺工具可以在任意第三方持续改写同一目录时提供整个目录的线性一致快照或恢复到其调用前状态。
5. **`resume` 的终态路由。** 同请求 `mode=resume` 在识别到已提交 manifest 时只返回恢复的 receipt。该路由已获批准，细化 #108 AC5，并修订 [Spec 0008](0008-v0.4.0-market-foundation-and-screening.md) 的 create/resume 条款“目标必须是未 finalize 的 partial”的限定；它是本次新增的集成义务，不表示旧版本已经实现。

## 2. 支持模型

### 2.1 生产文件系统

生产 Adapter 只在可以实际满足以下契约的文件系统上启用写入：

- 本地文件系统，同一事务需要的发布和替换发生在同一文件系统内。
- 支持独占创建、受锚定目录描述符约束的相对访问、拒绝 symlink/reparse 重定向、稳定的文件与目录身份检查，以及进程退出后自动释放的进程间合作锁。
- 支持本协议使用的单个 namespace 发布操作的原子性；不得把普通 `rename` 宣称为按期望 inode/字节执行的原子 compare-and-swap。
- 支持文件内容与有关目录项的必要持久化操作，并能报告其失败。

默认实施目标可以是具备上述能力的 macOS/Linux 本地 POSIX 文件系统。Windows、网络共享、对象存储、FUSE 或其他文件系统不因名称被推定安全或不安全；在没有同契约 Adapter 和运行证明前，生产写入必须在首次持久化修改前失败关闭。不得静默降级为路径字符串校验、进程内锁或忽略目录持久化。

deterministic Adapter 必须在 production anchored-FD 支持关闭时工作。它用于验证事务状态、计划和故障结果，不代替生产平台的真实路径、锁和持久化验证。

### 2.2 并发与外部修改

同一 artifact 上的所有合作写入方必须经同一事务锁域串行化；create 对同一目标最多一个获胜者。已打开的旧 handle 必须重新校验 generation/身份或等价的完整预期状态，不能覆盖另一 handle 已完成的 shard。

外部非合作修改分两类处理：

- 在 observe、preflight 或 apply 前重验时发现：工具不得改变观察到的调用者或 artifact 字节、名字及身份。
- apply 已产生合法协议修改后才发现：立即停止会继续改变不确定对象的操作；保留已有可信 journal 和可证明拥有的状态，返回协议/I/O 失败。不得为“回滚到旧状态”而覆盖新出现的对象、收养未知 inode 或删除未知残留。

“零修改”只描述工具造成的变化。先 stat 后无条件替换不能被描述为不可竞争的 CAS；不引入分布式锁或跨主机事务。

### 2.3 中断与持久化

支持模型包含：普通返回的 I/O 错误或异常、`KeyboardInterrupt`、独立进程在持久化状态转移期间突然终止，以及恢复进程在后续转移期间再次终止。

实现必须明确各个状态的提交点和所需持久化顺序。对外宣称提交或可恢复的证据，必须已经达到所声明的 durability 边界。文件可读、digest 一致或 inode 相符都不能补足失败的 `fsync`。

`fsync` 失败时保留可信恢复证据并返回失败；后续显式恢复重验并重新完成必要持久化，不凭读回返回成功 receipt。此前已证明成功提交的 manifest 不被后续 cleanup 失败撤销，但 receipt recovery 仍须完成 sealed-state 校验。

## 3. 事务契约

生命周期仍使用一个小型内部事务 Adapter；以下是概念阶段，不要求增加公共方法或数据模型：

1. **Observe / preflight：** 在正确锁域内读取完整相关 inventory、checkpoint、pending/completed entries、journal、manifest 及身份；验证所有条目和容量，形成不可变计划。
2. **Revalidate / apply：** 在首个修改前重验完整观察；不一致则零修改拒绝。一致后按已批准状态机执行 journal 约束的转换。
3. **Recover / seal：** 根据持久化状态重新构建完整计划，完成或回滚可证明拥有的未完成工作；只在完成校验与必要持久化后产生 receipt。

“多项计划全有或全无”是首个修改前的全量有效性判定，不是进程死亡后的物理回滚。apply 后进度由 journal 记录，完成 shard/manifest 不因恢复回写。Adapter 替换、回滚或锁迁移的临时文件也必须受协议 inventory 约束；`finally` 不构成突然终止恢复证明。

## 4. 状态、提交点与合法恢复动作

下表为最低状态要求。实现可使用更少字段或合并等价状态，但不得存在没有归属与恢复规则的可持久化中间状态。表中动作是 Result Artifact 模块动作；外部仍使用现有 `run --request` 和 `create`/`resume` 配置，不增加第二 CLI。

| 状态 | 已持久化的最低证据与提交点 | 允许动作及恢复结果 |
|---|---|---|
| 尚未发布 artifact | 目标不存在；任何建立目标前的 intent 都有明确归属和有效性规则 | `create` 独占建立新的事务。目标外未知残留不得删除，也不得仅按相似名称收养。 |
| 创建 intent / 初始化中 | 初始化 token、请求 identity、锚定父目录和发布所需证据；目标一旦公开存在，必须已有可恢复的 ownership 证明 | 目标不存在且未提交 target publication 时可安全重试 `create`；已发布可证明拥有的目标由 `resume` 完成初始化。不能把调用者后来创建的空目录认作本次创建成果。 |
| Checkpointed partial | 合法的初始或后续 checkpoint 已持久化，完整相关 inventory 与 identities 一致 | 写入新完成分区/候选、只读获取 checkpoint、显式 `resume`、满足条件时 finalize。无 pending 的精确 resume 不重写已有状态。 |
| Publication intent | 在发布工作字节前，持久化本次 token、目的坐标、预期 prepared bytes 摘要、允许的名字及 ownership 关联 | `resume` 依据完整 journal 完成或回滚本次未完成工作；不存在证明的对象保持原样并拒绝。intent 本身不等于“可以删除同名文件”。 |
| Owned working / prepared | 临时对象及有关目录的精确身份已与持久化 ownership 证据绑定；prepared 内容完成且已通过摘要与格式边界 | 可发布 final 名称或从中断继续。文件创建与 ownership 记录之间的终止窗口也必须被协议覆盖；不能仅因测试没有注入该窗口而称其不存在。 |
| Completed shard / candidate | 完成文件发布及 checkpoint 的完成声明达到规定提交点，二者有唯一可判定的恢复关系 | 对相同完成文件只验证，不覆盖、不追加、不重写。仍处于 pending 的同一发布操作可重放至完成；不会产生第二份逻辑记录。 |
| Finalize intent / prepared manifest | 全部内容文件、coverage 和 bounded receipt 所需事实已验证；journal 持久化 manifest 的精确字节摘要及发布 ownership | manifest 尚未提交时 `resume` 根据 journal 恢复到可 finalize 的 partial，或完成已允许的 finalize 转移；不得修改完成文件。 |
| Manifest committed | manifest 最后发布，内容及必要目录项持久化成功；所有文件摘要、计数及 identity 校验闭合 | 即使 receipt 尚未返回，也不再开放内容写入。显式恢复走 finalized-receipt recovery，返回同一确定性 receipt。普通写入、覆盖和删除仍被拒绝。 |
| Sealed / receipt recoverable | manifest 和全部声明文件有效；没有未知 inventory；协议允许的清理已完成或有精确 ownership journal | 重复 receipt recovery 产生相同 receipt。若仍有可证明拥有的协议残留，只能按下一节规则清理；未知或被替换残留只读拒绝。 |

`mode=resume` 先识别合法 partial 或已提交 manifest、receipt 丢失的状态；后者只走 receipt recovery，不重新执行研究、不改写完成文件、不新增 mode。该 CLI 路由已按第 1.5 项批准，须纳入 #69/#97 的集成验收；Result Artifact 模块已有的 receipt recovery 义务继续保留。

## 5. Ownership 与 finalized residue

删除、替换或收养对象至少需要：本次 artifact/transaction 的 durable ownership 关联、确切相对名称、当前与预期一致的文件/目录身份，以及该状态允许的动作。token、文件名、形状、digest 或共享 inode 单独都不构成删除授权。

Ownership 证据须先于其授权的公开状态转移持久化；内存记录、看起来正确的空目录或同字节对象不能被自动认领。初始化若需 staged publication，应在公开目标前建立归属；布局仍是 Adapter 内部实现。

Manifest 提交后，内容文件、checkpoint 中已封闭的完成事实和 manifest 不再被工具改写。#108 已允许的 finalized cleanup 仅针对 durable journal 明确拥有的协议残留，且必须先验证整个 sealed state 和全部待处理条目，再重验并 apply。一个未知、篡改或身份漂移条目会阻止所有尚未开始的 cleanup。

## 6. 64 KiB checkpoint 容量

Checkpoint 的 canonical UTF-8 编码大小上限继续精确为 **65,536 字节**，包括 JSON 标点、字段、token、坐标、摘要、identity、pending 与 cleanup 状态所需字节。恰好等于上限可接受，超过一字节必须在相关首次修改前拒绝。

仅验证当前 checkpoint 不足够。接纳新 shard/candidate 前，必须验证此次合法转换可能产生的所有 checkpoint 状态，包括 intent、claimed、prepared、cleanup 和 completed。不得用 `(0, 0)` 等较短占位 identity 证明实际最大状态可容纳。

为避免全市场任务运行到尾部才发现 checkpoint 无法提交，#68/#69 在开始 artifact 写入前必须用固定 shard 计划、三交易所分区和至多 20 个候选声明可容纳的 file-count envelope。Result Artifact 提供内部容量判定或等价预检；这一信息不成为第二个用户配置或新的公共结果模型。

容量证明记录固定开销、每条记录最大开销、pending/cleanup 预留、file-count envelope 和最坏总大小；identity/计数字段长度来自受支持 Adapter 契约。容量不足先提交最小反例并请求调整 shard/schema，不能截断证券、丢 unranked/谱系、取消候选、提高上限或静默缩小已批准规模。

## 7. 验证矩阵

验收从状态转移表派生，并通过既有 Result Artifact 公共模块 Seam 和同一个事务 Adapter 契约执行，不断言 helper 调用顺序。

1. **验证拒绝。** 单项无效、多项中最后一项无效、完整 inventory 未声明文件、checkpoint/manifest 篡改、token/身份不符、apply 前漂移；证明工具造成的树、字节和 inode 零变化。
2. **合作并发。** 两进程 create 单获胜；两个 live handles 不丢已完成 shard；写入与 finalize 互斥；成功 finalize 后旧 handle 拒绝写入；不同 artifact 不共享无必要的全局锁。
3. **普通异常。** 在每个合法转移点注入 I/O 失败、`KeyboardInterrupt`，检查完整 journal、返回语义和恢复结果。
4. **真实进程终止。** 用独立生产进程在 namespace/持久化边界突然终止，恢复由全新进程和 Adapter 实例执行。使用不执行 Python `finally` 的退出方式；覆盖 create publication、checkpoint replacement、pending claim、file publication、manifest publication 和 cleanup。不得用继承内存中的 token/registry 代替磁盘证据。
5. **二次中断。** 至少覆盖 recovery apply 中再次终止，再由第三个新进程恢复；应产生同一完成字节和 receipt，或对被外部修改的状态完全拒绝继续变更。
6. **持久化失败。** 分开注入文件 `fsync`、目录 `fsync`、发布后持久化失败；即使随后可读回，也不得把未证明 durable 的状态当作成功提交。恢复后重新证明持久化边界。
7. **非合作变化。** 替换 ancestor/root/working directory、同名同字节新 inode、伪造 hard-link、删除或替换 journal，以及 apply 前后可检测漂移。验证不会覆盖外部新对象或清理无 ownership 的字节；不要求恢复第三方自己的变化。
8. **边界与对等。** Checkpoint 65,535/65,536/65,537 字节、未来状态预留、完整 file-count envelope、canonical JSON/JSONL、生产/确定性字节与 receipt 对等、production anchored-FD disabled 下 deterministic 仍可运行。
9. **集成回归。** #115 两个工厂权威只消费一次；唯一 CLI、installed-copy/distribution/docs、精确 create/resume、manifest 已有但 receipt 丢失的公共恢复路由、`--output` 语义和路径不泄漏保持通过。

## 8. #115 Decision A 保持不变

#108 消费 accepted #115 两个 exported factories 返回的 prepared values 作为权威；不得重新读取 caller 对象或重复 key/path/locator 验证。恶意输入对象、stateful getters、实例/字段修改、非法值、private-name 访问和 bounds 仍在 #115 的既定验收范围内。

任意同解释器 closure/default/cell/function/code/private-registry/`__getattribute__` 反射与修改继续排除。生命周期的新协议不撤销 Decision A，也不把文件系统竞争问题转化为重新实现 Python capability 安全边界。

## 9. 原 #108 AC 逐条映射

| 原 AC | 修订后承载与保留义务 | 变更性质 |
|---|---|---|
| AC1：owned state 加一个 unknown/tampered entry，拒绝且 pre-call tree/inode/bytes 不变 | 第 3 节全量 preflight；第 5 节 sealed-state 校验；第 7.1 项零修改证明 | 保留。工具自身在 preflight 期间不得先建临时文件再拒绝。 |
| AC2：完整不可变计划、plan/apply 漂移、无部分 mutation | 第 1.2、2.2、3 节区分 apply 前拒绝和 apply 后恢复；所有条目先一起验证 | **已批准的原子性解释。** 不再把该文字解读为任意多文件物理原子回滚；不放松 apply 前零修改。 |
| AC3：创建 marker 转移 atomic/recoverable，token/identity mismatch 不动 caller bytes | 第 4 节初始化状态和公开提交点；第 5 节 ownership；第 7.2/4/7 项 | 保留。原子性须绑定具体 namespace 提交点，marker 替换不能被无限扩展为跨文件原子事务。 |
| AC4：每个 publication interruption 可恢复，未知残留不动 | 第 2.3、4、5、7.3–5 节，包含真实进程终止和恢复再次中断 | 保留并明确证明方式；故障范围已按 B2 批准。实现不能把自身未建模窗口重新命名为 unknown 来免除恢复义务。 |
| AC5：durable manifest 丢 receipt 可公开恢复，重复恢复确定 | 第 4 节 manifest committed/sealed；第 7.9 项 | 模块义务保留；`mode=resume` 终态路由已按第 1.5 项批准。 |
| AC6：finalized residue 的确切 name/identity 必须有持久化 ownership | 第 5 节和第 7.7 项 | 保留。内容和 manifest 不改写，只清理经全量验证的协议残留。 |
| AC7：生产/确定性对等，全部接受的 failure phases，禁用 anchored-FD 不影响 deterministic | 第 2.1、7.3–8 节 | 保留；生产平台支持边界已按 B2 批准，不能从 deterministic 对等推导生产支持。 |
| AC8：检测 direct calls、aliases、global-object indirection，raw production persistence 不逃逸 | 第 3 节所有实际生产持久化与临时对象归属都经过 Adapter；继续原 architectural gate | 保留。概念状态表不授权 helper 绕过 Adapter。 |
| AC9：既有 #67/#106 bounds、identity/resume、路径/并发、write-once、tamper/inventory、installed-copy/docs、`--output` | 第 2、4、5、6、7 节及原回归集 | 保留；后续发现的平台或范围冲突须显式列出并另行决策。 |
| AC10：消费 #115 prepared-input，不重复生命周期验证 | 第 8 节、第 7.9 项 | 保留 Decision A，不扩大反射威胁模型。 |

## 10. 应精确更改 #108 的条目

1. **Objective：** 保留完整生命周期交付目标；将“一项操作先观察全部状态、返回计划、重验后 apply”表述为第 3 节事务契约，补充 apply 后通过 journal 恢复的承诺，删除任何可被理解为任意多文件物理原子的措辞。
2. **Scope and boundaries：** 新增第 2 节生产文件系统、合作并发、非合作修改与中断模型；保留 owned-only cleanup、manifest-last 和 #115 消费边界；明确 Adapter 的内部替换临时文件同样受 journal/inventory 规则约束。
3. **AC2：** 应用 Tracker 修订时，用第 9 节已批准的原子性解释替换现有泛化表述；完整计划在任何修改前全部通过，apply 后中断按已持久化 journal 恢复。
4. **AC3/AC4：** 引用状态转移表，要求列出所有实际可持久化窗口、各自 ownership 证据和合法 create/resume 动作；保留 marker/target mismatch 零修改与每个接受阶段可恢复。
5. **AC5/AC6：** 明确 manifest committed 后只允许 receipt recovery 及 journal-owned residue cleanup；增加公共 `mode=resume` 到 receipt recovery 的后续集成义务，不新增 mode，不重写任何完成内容。
6. **AC7：** 将异常注入、真实进程终止、恢复再次中断和文件/目录持久化失败分开验收；生产/确定性对等仍为必要条件，生产支持范围单独记录。
7. **AC9：** 明确 65,536 字节精确边界、当前及未来合法 checkpoint 状态的容量预检，并向 #68/#69 输出内部 file-count envelope 约束；不提高上限、不丢弃研究记录。
8. **Verification：** 加入第 7 节矩阵；保留全量 Python 3.12、Ruff、精确 strict mypy、Skill/distribution/installed-copy、scope/leak 和独立 Standards/Spec review 门禁。静态 readback 或异常测试不得标记为真实突然终止或 durability 运行证据。
9. **Dependencies / outputs：** 保持 #115 → #108 → #68 的现有边；本稿不自动新增 Ticket。若后续证明需要先独立交付事务 Adapter，该拆票应以已冻结的状态/ownership/故障契约作为输出，另行批准 tracker 重接，不能按 create/resume/finalize 或单个失败 helper 再切碎同一状态机。

## 11. 受信本地归属承诺（A）

维护者已选择推荐 A：由工具在结果包及其暂存域之外保留原始 ownership 承诺。此节补充 B2，并在受信状态、恢复信任来源及其维护责任上优先于此前文字；其余 AC、公共 Interface、图与质量门禁保持不变。两份可被同一外部主体修改的 artifact 内记录互相校验，只能证明当前一致，不能充当原始归属的独立依据。

### 11.1 保护主体、位置与支持条件

默认受信根为 `<skill-root>/.state/result-artifact-anchors/v1`，与可重建的 `.runtime` 依赖目录分开。它是工具管理的本机持久状态，信任级别与安装代码相同。部署方必须保证不受信的结果包编辑者不能改写代码、受信根或其控制目录；本版本不抵抗能够改写这些受信部分的主体，也不把目录名、随机 token 或摘要本身称为权限保护。

生产 Adapter 在原有内部 Seam 中接收受信根配置以支持测试隔离和受信安装配置；不增加 ResearchTask、CLI、receipt 或调用者 commitment 参数，不从请求或环境变量接受受信根，不自动改用其他目录。默认位置必须属于私有、可写且受支持的安装区域；只读或不符合保护条件的安装不能静默降级。未使用 Result Artifact 的请求不初始化该状态。

- 受信目录由有效用户拥有，权限为 `0700`；记录为 `0600`。祖先由有效用户或受信系统账户拥有，不能允许不受信主体创建、替换或重定向受信路径。所有相关路径组件按锚定父目录描述符访问并拒绝 symlink，持续核对类型、owner 和身份。OS ACL 等额外授权不得使这些保护失效；不能仅凭 mode 位断言满足保护条件。能力或保护条件无法确认时，在首次修改前失败关闭。
- 结果包、公开目标及其 artifact 暂存域与受信根不得形成祖先/后代重叠；不能从结果包复制或恢复受信根。受信域自身的事务准备对象仍属于受信协议 inventory，不是外部 artifact 暂存域。
- v1 保留同文件系统范围：受信根和目标父目录必须具有一致的文件系统设备身份，且都满足第 2.1 节的本地能力要求；新建根在创建前从已验证父目录判定该条件。不同文件系统在首次修改前拒绝。每次 rename 只具有单个 namespace 操作的原子性，不宣称双目录或跨文件系统原子事务。

### 11.2 最小持久状态与授权方向

受信状态至少表示 store schema/domain、原始 creation ownership、每个事务不可被 artifact 当前声明覆盖的原始 journal 承诺与必要事务头，以及已持久化的提交/cleanup 进度。它绑定真实 artifact、工作对象、文件和目录身份、确切相对名称、请求/producer/plan/operation identity、原始字节摘要和允许动作。具体编码、索引及合并方式由 Adapter 实现，但不能只保存会随 artifact 一起重建的校验和。

授权方向固定为“已验证受信承诺 → 对照 artifact 完整状态 → 合法动作”。恢复不得以当前 artifact journal/binding 更新原始期望，也不得凭同名、同字节或新 inode 重新认领。closure 必须阻止已清理或已完成事务再次授权删除后来出现的对象。对应当存在却缺失、损坏、错域、身份不符或无法验证的受信状态，create/resume/receipt recovery 均保留现场并失败关闭；不得自动修复、重建或从结果包导入可信原件。

真实结果路径与 `mode` 继续只在内存使用；受信记录、公共输出、日志和其他 artifact 文件不得保存其原文。私有定位只能使用必要的不可逆 locator 或实际对象身份，不改变原请求语义摘要。受信根位置、权限、非确定性物理身份及其摘要不进入要求字节对等的公共 checkpoint、manifest 或 receipt。生产与 deterministic Adapter 的 canonical 公共字节对等要求不变。

### 11.3 创建、提交、恢复与零修改

1. 构造 Adapter、observe 和 preflight 保持只读。验证失败零修改同时覆盖结果包、调用者区域和受信存储；不能先创建 `.state`、marker、锁文件或修复权限再拒绝。只有已通过完整预检的合法 create 进入 apply 后才能 bootstrap 缺失 store。resume、receipt validation/recovery 永不 bootstrap。
2. 首次 bootstrap、创建准备对象、登记真实身份、发布 creation header、事务原件及进度替换的每个可持久化窗口都必须有确定的恢复规则。受信域内也不能以“尚未登记”忽略自身产生的状态；记录损坏与合法中断状态须可区分。创建后、ownership 登记前的突然终止仍属于 AC4。
3. creation 承诺必须在公开目标前达到所需 durability；每个事务的原始承诺必须先于对应 artifact journal 或对象发布持久化。checkpoint/manifest 成功完成文件及目录持久化之后，才能关闭对应受信提交状态。两域之间的中断通过明确状态推进恢复，不能从可读回推导 `fsync` 成功。
4. 新进程只从磁盘受信状态恢复，不借用旧进程内存。每次恢复先校验完整受信记录、artifact inventory 与所有计划条目，首个修改前重验；只操作确切拥有的对象。cleanup 及其 closure 的先后顺序必须支持再次中断，且不得重复授权后来创建的同名对象。
5. 已提交 manifest 的 receipt 丢失时，原受信状态仍须支持恢复相同 receipt；finalize 或成功返回 receipt 不自动删除该依据。没有待处理状态的精确 resume/重复 receipt recovery 不改写两域。

checkpoint 的精确 65,536 字节边界、所有未来状态及 file-count envelope 继续有效。外置承诺不允许把 checkpoint 所需的逻辑事实外移来规避上限；另须声明受信记录的有限大小、每 artifact 文件数、活动事务/closure 保留规则与最大批准工作量下的磁盘占用，禁止无界事务历史。受信状态的所有原语、权限检查、故障注入和持久化仍封装在同一个 ProductionFilesystemAdapter 边界，继续接受原 architectural gate；deterministic Adapter 在独立内存域模拟该契约。

### 11.4 维护、分发与消费责任

受信状态与 artifact 是配套恢复材料。备份必须在没有相关写入的受信维护边界上取得一致状态；普通 artifact 复制、安装复制或恢复旧版 anchor 不能证明原始物理身份。v1 不提供自动重新绑定、跨设备迁移、孤立记录清理或无 anchor 旧包升级。位置或对象身份变化、配套记录缺失、落后的进度不满足恢复条件时明确拒绝，保留现有包；可在新的目标执行新的 create。不得把这些限制隐去，也不得以恢复旧备份跳过身份、generation 或完整状态校验。

| Owner | 新增交付义务 |
| --- | --- |
| #108 | 交付本节受信承诺、完整中断恢复、同文件系统/权限/错域检查、容量与 failure-closed 行为；通过公开模块 Seam 覆盖 artifact ownership 声明变化而原受信记录不变、记录缺失/损坏、同名后建对象、create bootstrap 中断、receipt 丢失及 recovery 再次中断。原全量门禁与完整双审保留。 |
| #69 | 在原 create/resume 公共编排内使用工具配置的同一受信域；恢复先验归属与 C，再开始新的来源调用。受信状态无效时不重新取数、不另建 anchor、不改排名或已有包；不新增用户字段或 mode。 |
| #97 | copied-installed E2E 验证独立进程使用同一受信根、丢 receipt 恢复、anchor 缺失/损坏拒绝及路径不泄漏；分发与 clean-copy 排除 `.state`，安装不携带其他运行的受信记录。说明首次合法 create 初始化、保护条件、同文件系统限制、备份/迁移边界和丢失后处理。自包含安装代码不等于仅凭 artifact 就能跨安装自动恢复。 |

#68 继续消费原 Result Artifact Interface 和容量判定，不新增受信存储管理责任。#115 工厂、C 的 #117 语义责任、#116/#119 既有输出不变；不增加硬依赖，不修改 #121 的具体来源审批或 #98 的最终发布门禁。

## 证据定位

- Spec 0008：190–220 行（路径、create/resume、摘要、manifest、write-once、恢复）；246–254 行（Result Artifact 与文件系统 Seam）；256–265 行（失败与无泄漏）；347–351 行（V4-08 验收）；359–375 行（候选与公共 E2E）。
- #108 完整定义：<https://github.com/RedHeartSecretMan/a-share-research-skill/issues/108>；权威本地快照为 `b564f9e:.dag/definition-github-spec-0008.json`。
- #115 Decision A：<https://github.com/RedHeartSecretMan/a-share-research-skill/issues/115#issuecomment-5477817464>；同一 `.dag` 快照已绑定该澄清。
- 未接受候选 `96a24a9:skill/a-share-research/scripts/a_share_research/result_artifact.py`：261 行说明合作锁；656–784 行展示替换临时对象及 `finally` 清理；755–768、1440–1459 行展示持久化失败与读回处理的静态审查点。不能据此宣称当前稳定版存在已复现故障。
- 未接受候选 `96a24a9:tests/test_result_artifact.py`：2311、2346、3269 行附近的所谓 crash 使用异常注入；3302–3315 行区分 create/resume 恢复分支。它们说明应独立提供真实突然终止证据，不能代替实际运行证明。
