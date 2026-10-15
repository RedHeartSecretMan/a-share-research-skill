# SSE 具名科创板上市交易公告

`SseListingNoticeAdapter` 从调用者给出的 SSE 官方上市交易公告 locator 取得静态 HTML，以同一文书的人民币普通股题名、A 股正文、简称、代码及正式上市交易日生成有限事件事实。它不固化发行人、代码或日期，也不将当前名单回填为历史名单。

第一方依据为[华兴源创上市交易公告](https://www.sse.com.cn/disclosure/announcement/listing/c/c_20190715_4863535.shtml)：题名明确人民币普通股和科创板，正文将 `688001`、华兴源创和 `2019-07-22` 起上市交易绑定。同一题名下方的 article header 原文为 `2019-07-16`，没有 literal“发布日期”标签，也没有时区或秒级证明。URL 中的 `20190715` 不参与日期归一化。原始 HTML 由来源 reader 观察，origin HTTP status、原始字节 hash 和内部重定向不独立可见，不能声称这个证据已经验证生产 urllib HTTP 成功。

parser 使用标准库 HTML decoder 读取 UTF-8 静态文本，不依赖未见的站点 CSS selector，不执行脚本；head、script、style、noscript 内容不参与文本匹配。它要求同一公告的题名与相邻头日、相同发行主体的正式上市正文、明确的 A 股身份、简称和代码一致。正文中的无关财务片段只在内存中越过，不保留。未知编码、缺头日、非普通股、仅申请上市、重复或不一致条款均失败关闭；没有把其他公告体例自动列为支持。

同页头日只作为此具名 HTML 版本的日粒度依据。normalizer 明示采用 **consumer conservative bound**：次日 Asia/Shanghai 00:00；它不是源字段报告的公开时钟，不是该发行人最早公开时刻，也不赋给其他清单行。代码/名称/类别的范围限同一文书版本，没有证明后来的更名、退市或更正历史。

只有请求日期等于正文的正式上市日且保守可用界不晚于 cutoff，才输出一条可被 #110 保留的 `ExchangeListRecord`。请求前一日、后一日或截止时间不满足时，保留 source fact 与具体原因，不补造持续上市区间。source-result 为 `partial`，市场 expected count 与 expected pages 为 `null`；一份公告不证明全 SSE 或全 A 股。

`universe_sources.acquire_default_universe` 的 `sse_listing_event=SseListingNoticeTarget(document_url)` 可选择该真实 owner，`sse_listing_transport` 是单次同步外部传输 seam。另两所仍使用各自真实 owner，不借 SSE 代理 BSE。GET、retry 和 redirect 复用同一 `sse` provider lane 的真实 invocation 计数与有限 policy，外部错误代码和未脱敏异常不进入公开输出；来源自身最多接受 1 MiB HTML，不放宽共享 HTTP 上限。

copied-installed 测试使用完全合成的证券和 HTML 文本，经真实 parser、默认装配、#110 及 #119 验证正向 partial、URL 日期不被采用、缺日期/错误类别/仅申请、cutoff 和事件日前后。测试只证明本地可执行路径，不成为 live attestation。完整 SSE 名单版本、其他事件体例和三所完整市场能力仍须各自证明。
