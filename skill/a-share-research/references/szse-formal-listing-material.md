# SZSE 正式上市公告书事实采集

`szse_listing_material.acquire_szse_listing_material` 读取 SZSE 官方 PDF 上市公告书，解析 #121 AJ 实际观察到的封面身份与正式批准条款。它是具名材料的 source-specific 获取路径，不是全所目录，不以一份发行人材料代替市场分母，也不把尚未证明的 native catalog 返回形状写入代码。

## 已证明的形状与语义

AJ 的实际材料为 SZSE `301087` 的[《首次公开发行股票并在创业板上市之上市公告书》](https://disc.static.szse.cn/disc/disk02/finalpage/2021-10-22/9e24a195-c002-499a-a368-968349991c2c.PDF)。封面同时列出股票简称和代码；正式条款引用深圳证券交易所创业板上市通知及 `深证上[...]...号` 文号，明确同意该具名证券的人民币普通股股票上市交易、证券简称、代码和正式批准的上市交易日。该语义强于发行人自行提出的“拟上市”日期，不要求观察真实成交，也不能外推后来每一天的名字或上市状态。

生产 parser 对该已观察体例解析正式批准条款，并与封面身份核对。人民币普通股类别只属于条款中的具名证券，不从证券代码前缀、其他交易所规则或“上市公司”字样推导。非普通股、缺正式批准、封面与条款身份冲突或日期无效不能形成正向事件；未知体例失败关闭。重复引用同一条款不会增加证券家数。

当前已实现的正式条款体例限该创业板批准文书；主板或其他文书体例没有因此获得支持。支持日期来自每次真实匹配文书的正式上市日，不是任意历史日期范围。

AJ 的有界正文提取未取得该事实版本的明确公开日期。单独调用材料采集仍保留 `publication_day=null`、`available_at=null`、`membership_proven=false` 和 `market_expected_count=null`。PDF 存储 URL 中的日期不是公开时间证据；后来取得的 `retrieved_at` 也不是首次可用时间。正式上市日、公开时间、请求 membership date 与实际 retrieval 分别表达。

AL 用同一 hash 的实际材料核验了固定 pypdf：全 PDF 为 51 页，在前 40 页取得 74,584 个 UTF-8 文本字节，五个事件字段与 AJ 一致。这只证明该固定 decoder 对这个实际输入的兼容性，不表示任意 PDF 都兼容。目录的单次请求返回公告数 775、50 行，且请求 page size 为 100；有效分页容量、目标材料公开时间和目录闭合仍未证明，不能据此声称没有该材料。

## 精确版本的公开日期与正向 partial

后续独立来源阶段以已发布的 SZSE `annList` 原生请求查询具名证券，实际取得第 1、16、15 页的 50、25、50 行，声明该证券和 channel 范围的公告数 775。在第 15 页找到同一 UUID/path 的“上市公告书（更新后）”版本，`annId` 为整数，`publishTime` 原值为 `2021-10-22 00:00:00`。这是该具名更新版本的公开元数据；没有查询更早版本或最早公开时间，125 行不是完整历史目录，更不是证券池数量。

`SzseListingEventAdapter` 从调用者给出的 source code 和文书 locator 进行泛化获取，不固化上述样本证券。它复用已发布原生 endpoint、headers/body 和现有 PDF parser，在同一个 `szse` lane 内先有限 POST，再取得匹配 PDF。原始 `publishTime` 没有 timezone token，午夜也没有独立的秒级公开证明。normalizer 只采用日期分量，以 **consumer conservative bound** `次日 Asia/Shanghai 00:00` 表达保守可用界，显式标记 `consumer_conservative_next_day_Asia_Shanghai`；该界仅绑定同一匹配文书版本，不能分配给其他名单行。

metadata 必须精确匹配相对路径、source code、文书身份与标题；PDF 的正式代码、名称、普通 A 股类别和批准上市日仍来自正文。routing 中的当前名称不回填历史名称。元数据和 PDF 实际 retrieval 的最晚时刻作为组合证据的 `retrieved_at`，不会倒填为公开时间。

仅当请求日期等于文书正式批准的上市日，且该版本保守可用界不晚于 cutoff 时，Adapter 才生成正向 `ExchangeListRecord`。前一日没有该事件成员记录；后一日也不从这份上市公告推断更名、终止或持续上市状态。source observations 仍保留具名事件及不适用原因。`membership_proven` 只表示该具名证券在这一事件日的证明；`market_expected_count`、`ExchangeListOutcome.expected_count/pages_expected` 始终为 `null`，source-result 始终为 `partial`，不会构造完整 SZSE 或全 A 股分母。

`universe_sources.acquire_default_universe` 可接收 `SzseListingEventTarget` 和该来源的注入 transport，实际装配该事件 owner 与另外两所既有 owners，再调用公共 #110。调用者不提供规范名单或证明后的 Universe。#110 保留此正向记录和未知总量；对不完整名单不启动 identity。没有事件 target 时仍使用当前名单 owners，它们尚缺的时点字段继续诚实保留。该有限具名模式不等于完整 #122 默认市场责任已满足。

## 传输、解码与资源界限

来源定位限定为实际官方 `disc.static.szse.cn` PDF 路径，以材料 UUID 形成 opaque provenance，公开结果不复制 URL、原始 PDF、财务值、绝对路径或解码异常。注入 transport 必须是同步单次 HTTP 请求；默认复用现有 `UrlLibTransport`，HTTP initial、retry 和 redirect 全部经 #119 同一 execution-owned invoker 计数。

该来源自身最多接受 4 MiB PDF，保留已有共享 HTTP 5 MiB 上限。固定 `pypdf==6.10.2` 复用 Python 3.12 既有运行环境，无强制新增传递依赖。解码在独立 worker 内进行，最多提取前 40 页和 262,144 个 UTF-8 文本字节；不执行 PDF 脚本，不打开嵌入链接，原始解码文本不离开 worker。timeout 或 pipe 失败后须观察 native worker 退出，同组 lane 才可释放；终止请求本身不证明 quiescence。

解码计算不计成另一条来源请求。安全事实通过有限私有控制结果返回；来源 source-result 与 #119 实际 attempted/attempt evidence 分开。公开时间缺失时，已观察的正式事件事实仍可作为 `partial` 保留，不能把未知市场数量填为零。

事件 owner 的 `max_metadata_pages` 限制顺序查询页数，每页请求 50 行；实际 HTTP initial、retry、后续页和 PDF GET 全部消耗同一个显式 `ProviderLanePolicy.actual_attempt_budget`。metadata 命中后立即停止分页；预算耗尽不隐式补抓 PDF，不以公告数量或页数推导证券数量。缺目标、错版本、重复目标、count 漂移和未知形状均关闭正向成员输出。没有证据时不返回 `observed_empty`。

## 离线验证与尚缺责任

测试使用完全合成的 11 页 PDF，包含已证明的封面和批准条款体例，不复制真实材料或其财务数据。copied-installed 测试复制实际锁定的 pypdf，HTTP 始终使用 fake，执行真实 decoder、parser 和 #119；覆盖正式批准、非普通股、仅申请、身份冲突、大小界限和 URL 日期不能补公开时间。

运行普通 PDF 测试时，固定 pypdf 必须存在于已安装 Skill 的 `.runtime` 或明确的 test-tool package path。测试只定位并复制这一真实库到独立 artifact，不导入其他 SDK、不联网安装、不跳过该 owner 的验证。全量测试仍须使用本次工程的 minimal environment 与内核 deny-network guard。

copied-installed 回归还覆盖 metadata→真实 PDF→公共 #110 的正向 partial，以及错版本/名称、公开时间缺失或晚于 cutoff、事件日前后、retry 计数、预算耗尽和不安全错误。全市场目录覆盖、其他交易所的时点成员字段和完整 #122 验收仍需各自来源证据与实现。离线通过不替代 #98 的最终同候选 live attestation。
