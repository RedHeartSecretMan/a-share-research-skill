# SZSE 正式上市公告书事实采集

`szse_listing_material.acquire_szse_listing_material` 读取 SZSE 官方 PDF 上市公告书，解析 #121 AJ 实际观察到的封面身份与正式批准条款。它是具名材料的 source-specific 获取路径，不是全所目录，不以一份发行人材料代替市场分母，也不把尚未证明的 native catalog 返回形状写入代码。

## 已证明的形状与语义

AJ 的实际材料为 SZSE `301087` 的[《首次公开发行股票并在创业板上市之上市公告书》](https://disc.static.szse.cn/disc/disk02/finalpage/2021-10-22/9e24a195-c002-499a-a368-968349991c2c.PDF)。封面同时列出股票简称和代码；正式条款引用深圳证券交易所创业板上市通知及 `深证上[...]...号` 文号，明确同意该具名证券的人民币普通股股票上市交易、证券简称、代码和正式批准的上市交易日。该语义强于发行人自行提出的“拟上市”日期，不要求观察真实成交，也不能外推后来每一天的名字或上市状态。

生产 parser 对该已观察体例解析正式批准条款，并与封面身份核对。人民币普通股类别只属于条款中的具名证券，不从证券代码前缀、其他交易所规则或“上市公司”字样推导。非普通股、缺正式批准、封面与条款身份冲突或日期无效不能形成正向事件；未知体例失败关闭。重复引用同一条款不会增加证券家数。

AJ 的有界正文提取未取得该事实版本的明确公开日期。PDF 存储 URL 中的日期不是公开时间证据；后来取得的 `retrieved_at` 也不是首次可用时间。当前输出保留 `publication_day=null`、`available_at=null`、`membership_proven=false` 和 `market_expected_count=null`，不生成可被 #110 采纳的成员记录。原文正式批准日、公开时间、请求 membership date 与实际 retrieval 分别表达。

AL 用同一 hash 的实际材料核验了固定 pypdf：全 PDF 为 51 页，在前 40 页取得 74,584 个 UTF-8 文本字节，五个事件字段与 AJ 一致。这只证明该固定 decoder 对这个实际输入的兼容性，不表示任意 PDF 都兼容。目录的单次请求返回公告数 775、50 行，且请求 page size 为 100；有效分页容量、目标材料公开时间和目录闭合仍未证明，不能据此声称没有该材料。

## 传输、解码与资源界限

来源定位限定为实际官方 `disc.static.szse.cn` PDF 路径，以材料 UUID 形成 opaque provenance，公开结果不复制 URL、原始 PDF、财务值、绝对路径或解码异常。注入 transport 必须是同步单次 HTTP 请求；默认复用现有 `UrlLibTransport`，HTTP initial、retry 和 redirect 全部经 #119 同一 execution-owned invoker 计数。

该来源自身最多接受 4 MiB PDF，保留已有共享 HTTP 5 MiB 上限。固定 `pypdf==6.10.2` 复用 Python 3.12 既有运行环境，无强制新增传递依赖。解码在独立 worker 内进行，最多提取前 40 页和 262,144 个 UTF-8 文本字节；不执行 PDF 脚本，不打开嵌入链接，原始解码文本不离开 worker。timeout 或 pipe 失败后须观察 native worker 退出，同组 lane 才可释放；终止请求本身不证明 quiescence。

解码计算不计成另一条来源请求。安全事实通过有限私有控制结果返回；来源 source-result 与 #119 实际 attempted/attempt evidence 分开。公开时间缺失时，已观察的正式事件事实仍可作为 `partial` 保留，不能把未知市场数量填为零。

## 离线验证与尚缺责任

测试使用完全合成的 11 页 PDF，包含已证明的封面和批准条款体例，不复制真实材料或其财务数据。copied-installed 测试复制实际锁定的 pypdf，HTTP 始终使用 fake，执行真实 decoder、parser 和 #119；覆盖正式批准、非普通股、仅申请、身份冲突、大小界限和 URL 日期不能补公开时间。

运行普通 PDF 测试时，固定 pypdf 必须存在于已安装 Skill 的 `.runtime` 或明确的 test-tool package path。测试只定位并复制这一真实库到独立 artifact，不导入其他 SDK、不联网安装、不跳过该 owner 的验证。全量测试仍须使用本次工程的 minimal environment 与内核 deny-network guard。

可重复的全市场目录获取、匹配材料的公开元数据、三交易所时点成员投影和完整 #122 验收仍需各自来源证据与实现。该材料 parser 的离线通过不表示默认市场源已经可用，也不替代 #98 的最终同候选 live attestation。
