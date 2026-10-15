# BSE 日期版本代码切换事实

`bse_code_mapping_source.acquire_bse_code_mapping` 取得固定官方日期文档中的代码切换 cohort，通过实际 HTML table parser 解析旧代码、新代码、名称和显示上市日期。该来源复用 #110 的请求准备与 #119 的真实 provider lane；它不构造另一个 Universe，也不声称 cohort 就是历史全 BSE 分母。

## 已证明的来源与日期

#121 的 T 核验确认 [2025-09-12 新旧代码对照表](https://www.bse.cn/code_mapping/200025792.html) 在实际 HTML 中内嵌唯一表格，表头为“序号、证券简称、上市日期、旧代码、新代码”，表头后 249 行，其中 248 个完整且唯一的代码对，另有一行非映射说明。来源检查只保留必要身份样本与总量，普通测试使用合成的 248 个代码对，不复制真实来源样本。

同一固定日期文档及其已证明的事实版本可使用 `2025-09-13T00:00:00+08:00` 作为保守可用时间上界；它不是虚构的精确首发时刻。既有 N 的正式切换通知证明切换于 `2025-10-09` 生效。此日期只属于被通知的代码变更，不是公司首次上市日。

[服务页](https://www.bse.cn/service/code_mapping.html) 的关键脚注明确：原精选层平移进入北交所的公司，表内“上市日期”是原精选层挂牌日期。因此 parser 保留 `displayed_listing_date` 与该限定，`bse_listing_effective_date` 为 `null`。未有实际 BSE admission 或适用历史基线依据时，不能统一把该列变成 BSE 上市生效日。

## 生产解析与安全边界

来源 Interface 固定读取上面的日期文章，而非无日期的动态服务表；外部 transport 必须是同步、单次、无内部 retry/redirect 的注入 seam。默认使用现有 urllib，实际 HTTP、redirect 和有界 rate-limit retry 均经同一个 BSE lane。调用者提供有限 `ProviderLanePolicy`；本操作只有一个固定文档请求，不另起不受计数的请求。

Parser 从实际 HTML 表头定位列，校验唯一映射表、代码、名称、日期与唯一性。完整 248-code cohort、固定日期/标题和记录日期边界共同支持这个已证明的日期版本；不要求无关页面布局或完整 raw body 的 archived hash 永远相同。缺失、重复、坏记录和日期版本漂移阻断相应 closure，并保留其他安全记录。出现后来增加的行或不同日期时，不把最初日期分配给新事实；当前 parser 对未闭合的 cohort 保持 `available_at` 与代码变更日期未知。

输出中的 `cohort_complete` 只表示固定切换 cohort 的解析闭合。`membership_proven=false`，`historical_expected_universe_count=null`。合法的晚获取保留实际 `retrieved_at`，请求的 cutoff 与冻结 research clock 不随获取改变。完整 cohort 的 acquisition 可以是 `complete`，但它仍不是覆盖任意历史日期的证券池，也不独自使下游可采纳。source-result 与真实 attempted/attempts/quiescence 分别保存。

公共投影只含规范代码、名称、显示日期、日期限定及 opaque 来源引用。它不输出 HTML、headers、凭据、原异常文本、绝对路径、网站页脚或其他 payload。timeout 后 BSE lane 仍占用至 quiescence；同组当前名单 POST 不会重叠启动。

## 尚需补齐的整体证券池证据

该表是 248-company 切换 cohort。cohort 之外的公司、后续 IPO/退市、更名、代码和类别变化、每个请求日期的完整范围与所用事实版本仍须由适用正式材料证明。日期文档不能为后来动态行倒填原始公开时间，普通 parser/installed-copy 通过也不能代替最终 #98 live attestation。
