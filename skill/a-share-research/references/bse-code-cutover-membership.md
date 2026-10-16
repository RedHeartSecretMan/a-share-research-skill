# BSE 代码切换日的有限成员证据

`bse_membership_event` 是 v0.4.0 候选的内部 production Module，复用既有 dated BSE code mapping parser、锁定 pypdf、#119 leaf/lane 与 #110 规范结果。它没有公共 operation 或 CLI 注册，不改变旧单标的 BSE 支持范围。

## 资格与支持范围

| 获取角色 | 来源与必须事实 | 已证明边界 | 请求与资源 |
| --- | --- | --- | --- |
| dated code event | BSE `code_mapping/200025792.html` 的 248 行日期版本；目标旧新代码、当时简称、代码切换日及文章版本可用界 | 仅已核验的 2025-10-09 切换事件；248 不等于全 BSE，也不自动证明普通 A 类别 | 一页；初次、retry、redirect 共用实际 attempt budget |
| old-share material | 具名发行人的官方 CNINFO/BSE PDF；同一材料目标旧代码、人民币普通股与 BSE 上市语境 | 只补同股旧身份类别；拟发行或其他证券语境不能作为该事实 | 一份 PDF，不追链接 |
| new-share material | 同发行人的权益变动报告；同页目标股票代码与明确人民币普通股种类 | 只补同股新身份类别；不能证明获取日实时在市 | 一份 PDF，不追链接 |
| BSE admission material | 同发行人的报告；目标新代码、北交所与明确“上市时间/日期” | 真正 BSE 上市日必须不晚于目标切换日；精选层挂牌日继续单列 | 一份 PDF，不追链接 |
| version observation | 由本 Module 的真实 source observer 产生的 URL、body SHA 与实际 retrieval upper bound | 只证明这些相同字节在该上界前已经取得；不是原始公开时刻 | 每份一次 GET，显式有限 policy |

当前有用的 canonical 模式只支持上述代码切换有效日。其他历史日、相邻日和当前会话仍 unknown/unavailable，不把旧代码记录或代码切换后的区间扩成连续在市历史；终止上市、更名、类别变化日期没有证明时不补值。BSE 上市日证明与普通股链、同版切换事件共同支持具名证券在切换日的新代码身份，这是跨材料推断，不能推广到 cohort 其余成员或全市场。

实际核验的同证券链包括：旧码法律意见中人民币普通股/BSE 语境、同版权益变动报告中的新代码与股票种类、半年报明确 BSE 上市时间，以及正式旧新码切换事件。它们证明方法可行的单一正向实例。产品不保存样例证券、URL、SHA 或 retrieval 时钟，不硬编码成功对象；copied-installed 测试使用两家完全合成发行人与不同代码对，实际执行 PDF decoder 和全部 source parsers。

## 时间与版本

调用者先通过 `observe_bse_material` 准备三份文书的来源版本元数据，再为一个新的研究请求冻结 `research_now` 和 cutoff。observer 的 `BseMaterialObservation` 独立保留 attempted、source-result、实际 attempts 和 quiescence；只有成功取得的材料有 producer-issued `BseObservedMaterial`。`BseMembershipEventTarget` 接收新代码、具名发行人和三项 producer metadata；它不接收调用者声称的类别、状态、日期或 canonical 名单。

默认 factory 在任何 I/O 前核对 metadata 的 producer identity 与同一 `ProviderLanes` authority，分离其不可变版本值。正式获取再次读取相同 PDF，只有 body SHA 相同且当前 retrieval 不早于原观察才继承先前版本上界。复制、伪造、回填时间、换 body 或把另一个 lane 的 evidence 当作自己的观察均不成立。元数据只保留进程内来源观察，没有通用缓存、持久化票据或任意 metadata 导入入口。

`available_at` 取 dated code event 的 consumer conservative bound 与三份同版材料观察上界的最晚值。原始文书未证明的 publication、时区和秒级源时钟保持 unknown；PDF locator 日期、签署日期、展示日期与本次 retrieval 均不替代其语义。新请求的 cutoff 必须不早于该上界，之后 re-fetch 仍保留真实 `retrieved_at`。一个既有请求不能把 cutoff 后首次看到的版本补入自己，也不能通过恢复时重新计时取得资格。丢失 producer metadata 后，旧请求不凭哈希重建可用时间。

source acquisition 分别保留真实 BSE admission、mapping 中带限定的 displayed listing date、代码切换有效日和 material observation upper bounds。每项 prior observation 的实际 attempts 与当前执行的 stage/leaf sequence 分开列出，不把两个执行的 sequence 改造成一条虚构的全局计数。

## 默认装配与对账

`create_default_universe_adapters(..., bse_membership_event=target)` 返回 paired real BSE list/identity owners；`acquire_default_universe` 使用该 factory。source 代码/名称由 dated mapping 行确定，类别和 BSE admission 由实际 PDF parser 生成。同期 class/body/issuer/code 任何一环不合格时不产生 canonical 记录，不从代码前缀或字典标签补类别。

有效 list 为 `partial`，全 BSE `expected_count/pages_expected=None`；实际 mapping 页保留 `pages_received=1`，三份 PDF 属于事实材料而非市场名单页。#110 因市场总量未知保留已知成员、未知分母及 unranked 原因，按原契约跳过 identity，永不标 full。不能把一个已证明证券当作 expected=1 或完整页数=1。

显式 identity batch 以同一 paired owner 再获取同一来源，匹配已准备的 canonical code/name。expected 保持调用批次的已知整数，错名与缺失继续占分母；空批不制造来源请求。合格身份的 state=`listed`、effective_from 为代码切换日、effective_to 未知。`observed_at` 是该相同事实版本的先前观察上界，`retrieved_at` 是本次真实获取；该点位身份不支持邻日或实时在市推断。

## 预算、安全与未证明项

每次 list 或 identity execution 共用一个显式 actual-attempt 上限和整体 timeout：mapping 一次、三份材料各一次，成功最低四个 leaves。retry/redirect 消耗同一四者预算；不足时保留真实已发生 attempts、partial 与缺失原因，不缩分母。预备观察是另一次显式三 GET 工作，不能隐去：无重试时“观察 + list”为七次，“观察 + list + 显式 identity”为十一次；#110 对 partial list 不自动花费后四次。每组仍使用共享 #119 的 pacing、最多三 active groups、timeout/quiescence 和 poisoned lane 规则，BSE owner 不持有外层 BSE lane 再等待 CNINFO lane。

每份 PDF 最多 4 MiB；仅前 40 页、UTF-8 文本累计 262,144 字节；锁定 pypdf 在隔离子进程内解析，整体 timeout 内结束/回收，结构化输出最多 4 KiB。沿用 HTTP 5 MiB transport 上限，没有网络安装、账号、凭据、付费或 fallback。仅允许精确 HTTPS 官方 PDF locators，拒绝 query、fragment、凭据与其他 host；不执行 PDF 链接、JS、callback 或来源文本指令。公开输出仅有有限规范事实、安全 locator、hash/version refs 和计数，不含 raw payload、本地路径或原始异常。

普通测试全部替换 source-family transport 并使用 kernel deny-network。它们证明真实生产 parser/normalizer、身份和时点失败边界，不证明 live 成功。全 BSE/三所完整名单、连续历史范围、完整总量与市场 workload、正式发布可用性仍未证明；Amendment C 已退出本版公共全市场筛选、排名、候选深研及当前会话全市场盘中工作流。本有限事件模块仅保留内部 source/identity 契约，不证明这些未提供的公共能力，也不新增相应的发布 E2E 门禁。
