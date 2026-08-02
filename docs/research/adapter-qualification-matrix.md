# A 股研究数据 Adapter 准入矩阵

状态：**没有任何提供方已整体通过生产准入；本文只给逐操作候选级别和下一轮验证顺序**

研究快照：2026-08-01（Asia/Shanghai）

适用规格：[内核预览规格：当前估值证据简报](../specs/0001-current-valuation-evidence-brief.md)
前置调查：[内核预览免费数据源可行性](./free-source-viability.md)

## 结论先行

在“可信的 A 股研究数据助手”所需的证券身份、最近完成交易日未复权收盘价、研究时点有效总股本、PIT 财务报表和许可边界上，当前公开证据支持以下验证顺序：

1. **第一顺位：RQData HTTP API。** 它是本次公开资料中唯一同时明确给出日线不复权、逐日总股本、财报实际公开时间、财报版本标识、PIT 财务查询和所需归母字段的候选；HTTP 协议也可由 Python 3.12 标准库直接实现。主要缺口是 BSE 的正式代码/覆盖、HTTP 失败契约以及项目所需缓存、测试夹具、衍生输出和开源 Adapter 权利。
2. **第二顺位：同花顺 iFinD Quant API 的 HTTP 接口。** 官方示例已经给出按日期查询总股本、财报实际披露日和 PIT 归母净利润，并给出 TTM 组合方式；HTTP、配额和 QPS 也有较清楚的公开说明。主要缺口是 BSE 路由、权威停牌状态、PIT 归母净资产准确指标、版本/更正选择和合同权利。

Choice/EMQuant 与 Wind 的产品覆盖可能足够，但公开文档不足以逐字段证明本项目的时点语义；客户端接入还依赖专有 SDK，不能满足 Skill 的“Python 3.12 标准库、自包含、跨平台”运行基线。Tushare 的 HTTP 契约最透明，SSE/SZSE/BSE 身份以及多数所需字段也公开，但关键能力需要积分或单独开通，而且公开服务协议限定个人、非商业使用，因此适合作为**个人交叉核验选项**，不能据此批准通用生产 Adapter。

**重要边界：**“生产候选”只表示该单项已经有足够公开技术证据，值得进入带凭据的实测和合同审查；不表示提供方或 Adapter 已获生产批准。任何单项只有在许可、代表性样本、失败样本和 schema 漂移测试都通过后，才能从候选转为正式准入。

## 调查方法与事实边界

本文只使用五家提供方的官方产品页、官方接口文档、官方条款、官方权限/价格页和一手公开接口说明；没有使用二手文章、聚合比较或非官方 SDK。没有索取、读取或尝试猜测任何用户凭据，也没有在登录态发起请求。

标识含义：

- **[事实]**：由截至研究日期可公开访问的提供方一手文档或条款直接支持。
- **[推断]**：基于一手事实作出的工程准入判断，不冒充提供方承诺。
- **未验证**：公开资料不能确认；不能把“产品可能有”写成“接口已经支持”。

逐操作判定：

| 判定 | 含义 |
| --- | --- |
| **生产候选** | 公开技术契约基本覆盖该单项；仍需登录态样本、失败样本和适用合同确认 |
| **个人可选** | 技术上可用于个人研究或交叉核验，但权限、可靠性或公开许可不满足通用生产准入 |
| **当前 NO-GO** | 已有明确事实与当前项目约束冲突；改变运行方式或取得书面授权前不应实现该路径 |
| **未验证** | 公开一手资料不足，不能作肯定或否定判断 |

价格栏中的“未公开/未验证”只表示未在公开官方页面找到可核验价格，不表示免费。条款判断是工程准入判断，不是法律意见。

## 逐操作准入矩阵

### 1. 证券身份、市场路由与交易日历

| 提供方/操作 | SSE / SZSE | BSE | 判定与依据 |
| --- | --- | --- | --- |
| RQData 证券身份 | `all_instruments(type='CS', market='cn')`、`instruments` 可返回证券标识、名称、交易所、上市状态与日期 | 当前公开 Python 文档示例和代码约定只明确 `.XSHG`、`.XSHE`；更新日志曾出现北交所相关改动，但没有形成可调用契约 | SSE/SZSE **生产候选**；BSE **未验证**。见 [通用 API](https://rqopen.ricequant.com/doc/rqdata/python/generic-api) 与 [版本记录](https://rqopen.ricequant.com/doc/rqdata/python/changelogs.html) |
| RQData 交易日历 | `get_trading_dates`、`get_previous_trading_date` 支持 `market='cn'` | 文档没有证明 `cn` 对 BSE 的具体覆盖、例外日处理和代码路由 | SSE/SZSE **生产候选**；BSE **未验证**。见 [通用 API](https://rqopen.ricequant.com/doc/rqdata/python/generic-api) |
| iFinD 证券身份 | 官方命令与 HTTP 示例采用 `.SH`、`.SZ`，基础资料函数可返回证券资料 | 公开手册未找到 `.BJ` 或北交所当前代码规范 | SSE/SZSE **生产候选**；BSE **未验证**。见 [用户手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html) 与 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| iFinD 交易日历 | `THS_DateQuery` / `get_trade_dates` 有 SDK 与 HTTP 示例，可指定市场代码 | 公开示例只足以确认常见沪深市场；未找到 BSE `marketcode` | SSE/SZSE **生产候选**；BSE **未验证**。见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| Choice/EMQuant 证券身份 | `c.cec` 可校验证券，公开市场选项覆盖沪深等市场 | 官方更新日志称已适配北交所旧代码切换，但公开手册没有给出当前 BSE 后缀与代表性返回 | SSE/SZSE **未验证**；BSE **未验证**。公开页面不能证明精确字段和失败语义，见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) 与 [版本说明](https://quantapi.eastmoney.com/Download/GetDownloadDesc?from=web&language=Python&sys=Common) |
| Choice/EMQuant 交易日历 | `c.tradedates` 公开市场码包含 `CNSESH`、`CNSESZ` | 未找到 BSE 日历市场码 | SSE/SZSE **生产候选**；BSE **未验证**。见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) |
| Wind Client/Server API 证券身份 | 产品页声明覆盖股票与基础资料，但公开页没有代码规范、字段或代表性返回 | 同左 | SSE/SZSE/BSE 均 **未验证**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) 与 [Server API 产品页](https://www.wind.com.cn/portal/zh/WDS/sapi.html) |
| Wind 交易日历 | 公开产品页未给可引用的函数、参数、市场枚举和异常日契约 | 同左 | SSE/SZSE/BSE 均 **未验证**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) |
| Tushare 证券身份 | `stock_basic` 明确给出 `SSE`、`SZSE` 和 `.SH`、`.SZ` | 同一接口明确给出 `BSE`、北交所市场和 `.BJ` | SSE/SZSE/BSE **个人可选**；接口需 2000 积分，不能作为 free 基线。见 [`stock_basic`](https://tushare.pro/document/1?doc_id=25) 和 [积分规则](https://tushare.pro/document/1?doc_id=13) |
| Tushare 交易日历 | `trade_cal` 明确列出 SSE、SZSE | 当前文档的交易所枚举未列 BSE；不能仅因交易日通常一致就宣称覆盖 | SSE/SZSE **个人可选**；BSE **未验证**。见 [`trade_cal`](https://tushare.pro/document/2?doc_id=26) |

### 2. 最近完成交易日日线、未复权收盘价与停牌/交易状态

| 提供方/操作 | 公开契约 | 判定 |
| --- | --- | --- |
| RQData 未复权日线 | `get_price(..., frequency='1d', adjust_type='none')` 明确给出不复权选择和 `close`；可指定日期。`skip_suspended=False` 会使用停牌前值填充，生产实现不能把填充值误作当日成交 | SSE/SZSE **生产候选**；BSE **未验证**。见 [通用 API](https://rqopen.ricequant.com/doc/rqdata/python/generic-api) |
| RQData 停牌状态 | `is_suspended` 可返回指定日期是否全天停牌；深交所还存在 `trading_phase_code`，但其公开说明只覆盖 SZSE 且枚举有限 | SSE/SZSE **生产候选**，必须将价格与 `is_suspended` 联合取证；BSE **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) |
| iFinD 未复权日线 | `THS_HQ` 的历史行情默认 `CPS=1` 为不复权；可取收盘价。缺失填充默认 `Previous`，生产查询必须显式用 `Omit` 或 `Blank`，否则可能把前值当作当日值 | SSE/SZSE **生产候选**；BSE **未验证**。见 [用户手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html) |
| iFinD 停牌状态 | `Fill=Omit/Blank` 只能观察行情缺失，不能独立证明“全天停牌”而非无权限、无数据或接口失败；公开资料未找到本项目可直接使用的权威状态指标 | SSE/SZSE/BSE 均 **未验证**。需要在登录态命令生成器中确认状态指标和失败区分，见 [用户手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html) |
| Choice/EMQuant 未复权日线与状态 | `c.csd` 能取历史序列，但公开手册没有逐项证明本项目所需收盘指标、复权选项、停牌日返回和状态字段的完整组合；具体指标通常依赖登录态命令生成器 | SSE/SZSE/BSE 均 **未验证**。见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) |
| Wind 未复权日线与状态 | 产品页宣称覆盖历史行情，但公开页未给字段名、复权参数、停牌语义、完成日判定或返回样例 | SSE/SZSE/BSE 均 **未验证**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) |
| Tushare 未复权日线 | `daily` 明确为未复权行情，给出 `close`，通常在交易日 15:00–16:00 入库；单次最多 6000 行并有调用频率说明 | SSE/SZSE/BSE **个人可选**。见 [`daily`](https://tushare.pro/document/2?doc_id=27) |
| Tushare 停牌状态 | `daily` 明确停牌期间无数据；`suspend_d` 给出停复牌类型及盘中时间段，但更新频率写为“不定期” | **个人可选**，不能仅凭 `daily` 空行判停牌；生产及时性 **未验证**。见 [`suspend_d`](https://tushare.pro/document/2?doc_id=214) |

### 3. 研究时点有效总股本与公司行动

项目需要的是指定研究时点已经生效、包含限售股、精确到股的普通股总股本，而不是当前网页快照或四舍五入市值口径。

| 提供方/操作 | 公开契约 | 判定 |
| --- | --- | --- |
| RQData 有效总股本 | `get_shares` 返回按日期变化的 `total`、`total_a` 等股本序列；产品页同时声明覆盖拆股、分红、停复牌等事件 | SSE/SZSE **生产候选**。需实测单位、当日生效边界、同日多事件与 BSE；BSE **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) 和 [RQData 产品页](https://www.ricequant.com/welcome/rqdata) |
| RQData 公司行动 | `get_ex_factor` 提供公告日、除权日等信息；公开资料未证明所有影响总股本的发行、回购注销、转增、配售和登记修订都能形成可审计因果链 | 股本值 **生产候选**；完整公司行动血缘 **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) |
| iFinD 有效总股本 | 官方示例用 `ths_total_shares_stock` 按指定日期查询总股本 | SSE/SZSE **生产候选**；BSE、单位、事件生效日和修订 **未验证**。见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| iFinD 公司行动 | 产品覆盖广，但公开示例不足以证明总股本值与所有公司行动版本之间的可追溯关系 | SSE/SZSE/BSE 均 **未验证**，见 [产品与接口说明](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/) |
| Choice/EMQuant 有效总股本/行动 | `c.css` 可取截面指标且支持日期宏，但精确指标、单位、事件生效与修订需要登录态命令生成器确认 | SSE/SZSE/BSE 均 **未验证**。见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) |
| Wind 有效总股本/行动 | 产品页声明覆盖公司行动与基础资料，未公开本任务所需字段、单位、时点或版本语义 | SSE/SZSE/BSE 均 **未验证**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) |
| Tushare 有效总股本 | `stk_premarket` 给出交易日盘前 `total_share`，单位为万股且显示 4 位小数，样例含 `920002.BJ`；该接口与积分无关但需单独在线开通 | SSE/SZSE/BSE **个人可选**；是否可重复免费取得和正式价格 **未验证**。见 [`stk_premarket`](https://tushare.pro/document/2?doc_id=329) |
| Tushare 公司行动 | `stk_premarket` 文档没有把每日值与影响股本的生效事件、修订和来源版本连接成一条契约 | 完整公司行动血缘 **未验证**，见 [`stk_premarket`](https://tushare.pro/document/2?doc_id=329) |

### 4. 财报公开时间、版本/更正与 PIT

| 提供方/操作 | 公开契约 | 判定 |
| --- | --- | --- |
| RQData 公开时间和版本 | `get_pit_financials_ex` 返回 `quarter`、公告发布日 `info_date`、调整标识 `if_adjusted`，并可选 `statements='latest'` 或 `all` | SSE/SZSE **生产候选**；BSE **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) |
| RQData PIT / 无前视 | 官方明确说明原始与衍生财务数据按实际公开时间处理，以避免未来数据；这是五家公开资料中最强的直接 PIT 声明 | SSE/SZSE **生产候选**；仍需同日更正、撤回、多个版本和时间精度样本。见 [RQData 产品页](https://www.ricequant.com/welcome/rqdata) 和 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) |
| iFinD 公开时间和版本 | 官方示例提供 `ths_regular_report_actual_dd_stock`；FAQ 解释“合并”通常是首次披露口径，“合并调整”表示后续会计差错/口径调整 | SSE/SZSE **生产候选**，但版本键、同日顺序和 BSE **未验证**。见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) 与 [FAQ](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/faq.html) |
| iFinD PIT / 无前视 | 官方示例用 `ths_np_atoopc_pit_stock` 将研究日期与报告期同时传入，直接支持 PIT 归母净利润 | 该指标 **生产候选**；其他财务字段不能自动继承其 PIT 保证，归母权益 PIT **未验证**。见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| Choice/EMQuant 公开时间、版本和 PIT | 公开手册有日期宏与通用数据函数；没有找到 A 股财报实际公开时点、原始/更正版本和显式 PIT 的公开完整契约 | SSE/SZSE/BSE 均 **未验证**。见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) |
| Wind 公开时间、版本和 PIT | 产品页宣称覆盖财务与事件，没有公开逐字段接口和 PIT/无前视承诺 | SSE/SZSE/BSE 均 **未验证**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) 与 [Server API 产品页](https://www.wind.com.cn/portal/zh/WDS/sapi.html) |
| Tushare 公开时间和版本 | `income`、`balancesheet` 均含公告日 `ann_date`、实际公告日 `f_ann_date`、报表类型 `report_type` 和更新标识 `update_flag`；报表类型区分合并、调整后合并及调整前原始版本 | 字段获取 **个人可选**。见 [`income`](https://tushare.pro/document/2?doc_id=33) 与 [`balancesheet`](https://tushare.pro/document/2?doc_id=36) |
| Tushare PIT / 无前视 | 客户端可按 `f_ann_date` 和版本字段自行筛选，但官方文档没有作显式 PIT 保证，也未定义同日多版本顺序、历史回填或更正后旧版本可见性 | **未验证**，不能把“能筛公告日”升级为无前视保证；依据仍是 [`income`](https://tushare.pro/document/2?doc_id=33) 与 [`balancesheet`](https://tushare.pro/document/2?doc_id=36) 的公开字段契约 |

### 5. TTM 归母净利润与 MRQ 归母净资产

本项目定义：

- TTM 归母净利润 = 上一完整年度归母净利润 + 本年截至最近已公开报告期的累计归母净利润 − 上年同期累计归母净利润。
- MRQ 归母净资产 = 研究时点之前最近已公开合并定期报告中的归属于母公司股东权益。

| 提供方/操作 | 所需字段 | 判定 |
| --- | --- | --- |
| RQData TTM | `net_profit_parent_company` 可与 PIT 报告期和公开时间联合查询；RQData 还公开提供 MRQ/TTM/LYR 衍生口径 | 自行按三段公式形成 TTM：SSE/SZSE **生产候选**；直接衍生值只可作交叉核验。BSE **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) 与 [产品页](https://www.ricequant.com/welcome/rqdata) |
| RQData MRQ | `equity_parent_company` 对应归属于母公司股东权益，可按 PIT 的最近公开报告选择 | SSE/SZSE **生产候选**；BSE **未验证**。见 [股票专属 API](https://rqopen.ricequant.com/doc/rqdata/python/stock-mod) |
| iFinD TTM | 官方示例明确使用 `ths_np_atoopc_pit_stock`，并给出“上年年报 + 本年累计 − 上年同期”的 TTM 组合思路 | SSE/SZSE **生产候选**；BSE **未验证**。见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| iFinD MRQ | 公开文档未找到可直接确认“合并口径归属于母公司股东权益 + PIT 日期 + 版本”的精确指标 | SSE/SZSE/BSE 均 **未验证**，见 [示例中心](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/example.html) |
| Choice/EMQuant TTM / MRQ | 公开手册没有给出本任务所需两个精确指标、单位、合并口径、公开时间和版本选择的完整组合 | SSE/SZSE/BSE 均 **未验证**，见 [Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) |
| Wind TTM / MRQ | 公开产品页不能证明精确字段和时点选择 | SSE/SZSE/BSE 均 **未验证**，见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) |
| Tushare TTM | `income.n_income_attr_p` 是归属于母公司所有者净利润；报告类型、公告日和更新标识可支持三段公式 | SSE/SZSE/BSE **个人可选**；显式 PIT 保证仍 **未验证**。见 [`income`](https://tushare.pro/document/2?doc_id=33) |
| Tushare MRQ | `balancesheet.total_hldr_eqy_exc_min_int` 是归属于母公司股东权益 | SSE/SZSE/BSE **个人可选**；显式 PIT 保证仍 **未验证**。见 [`balancesheet`](https://tushare.pro/document/2?doc_id=36) |

## 传输、运行时、访问成本和失败契约

| 提供方/路径 | HTTP / SDK 与 Python 3.12 | 平台 | 凭据、试用、订阅/价格 | 错误、限流、schema/version | 判定 |
| --- | --- | --- | --- | --- | --- |
| RQData HTTP | `POST /auth` 换取 token，再向 `/api` 提交含 `method` 的 JSON，响应 CSV；可用 `urllib/json/csv`，无需第三方包 | HTTP 本身可跨 Windows/macOS/Linux | 30 天试用，需账号与手机验证；试用每日 1 GB。正式模块按年销售，但公开页未给可核验成交价格 | Python 侧有配额查询、版本日志和数据就绪检查；公开 HTTP 文档未给完整错误码、限流、部分响应及 schema 版本 | 传输 **生产候选**；HTTP 失败契约 **未验证**。见 [HTTP 数据处理](https://rqopen.ricequant.com/doc/rqdata/http/data-process)、[试用页](https://www.ricequant.com/welcome/trial/rqdata-cloud-vnpy)、[Python 手册](https://rqopen.ricequant.com/doc/rqdata/python/manual.html) 和 [版本记录](https://rqopen.ricequant.com/doc/rqdata/python/changelogs.html) |
| RQData SDK | 依赖 `rqdatac`/RQSDK 和 pandas 等，不符合标准库自包含要求；官方 RQSDK 矩阵明确支持 Python 3.12 | Windows/Linux/macOS Intel/Apple Silicon 均列出 | 商业授权；价格未公开 | SDK 有版本记录，但仍需供应链、ABI 和依赖管理 | 对当前 Skill **当前 NO-GO**。见 [RQSDK 支持矩阵](https://rqopen.ricequant.com/doc/rqsdk/manual-rqsdk) |
| iFinD HTTP | JSON POST，`access_token` 请求头；可由标准库直接调用。access token 官方说明有效 7 天，refresh token 与账号有效期绑定 | 官方明确 HTTP 可用于 macOS、国产系统等，协议本身平台无关 | 免费账户、试用和正式账户有不同权限；权限页列免费历史行情/基础资料额度和试用周额度，但 FAQ 对“免费账户”表述存在需购买终端的条件，实际资格必须登录确认；正式价格未公开 | 响应含 `errorcode/errmsg`；单函数常见 QPS 10、EDB 5、账号总 QPS 20，响应规模和绑定 IP 有说明；未找到稳定 schema 版本和完整错误目录 | 传输与限流 **生产候选**；账号资格、完整错误/版本契约 **未验证**。见 [部署授权](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/deploy.html)、[权限说明](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/permission.html)、[FAQ](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/faq.html) |
| iFinD SDK | Windows/Linux SDK；官方只笼统写 Python 3.5+，没有公开证明当前 Python 3.12 的逐平台测试；依赖专有 SDK | SDK 未列 macOS，macOS 路径由 HTTP 解决 | 同账号权限 | SDK/指标可能演进，FAQ 说明旧报表 ID 隐藏后仍可用 | 对标准库 Skill **当前 NO-GO**。见 [产品页](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/) 与 [FAQ](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/faq.html) |
| Choice/EMQuant SDK | 官方公开的是包含本地库的 Python SDK，没有公开可直接实现的 HTTP 协议；不满足标准库自包含 | 下载页列 Windows/Linux/macOS；需进一步验证 Python 3.12 ABI 和具体发行版 | 需账号登录、激活/权限；可申请试用，正式价格未公开 | `ErrorCode/ErrorMsg` 有较完整错误表，覆盖未登录、无权限、过期、无数据、超时、频率过高、非法代码/指标、日期范围等；可查额度，且有当前版本日志 | 失败契约 **生产候选**；当前运行方式 **当前 NO-GO**。见 [下载页](https://quantapi.eastmoney.com/Download?from=web)、[Python 手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html)、[版本说明](https://quantapi.eastmoney.com/Download/GetDownloadDesc?from=web&language=Python&sys=Common) |
| Wind Client API | 官方列 Python 等语言，但属于 Client SDK/终端环境，没有公开标准库 HTTP 协议 | 官方产品页声称 Windows/macOS/Linux/麒麟支持；具体 Python 3.12 兼容性未公开 | 需 Wind 账户/产品许可；试用与正式价格未公开 | 公开页未给错误码、限流、schema 或版本兼容契约 | 对当前 Skill **当前 NO-GO**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) 与 [下载中心](https://wind.com.cn/download.htm) |
| Wind Server API | 官方称企业级 API Gateway 和 SDK 集成，但公开页没有协议、endpoint、认证和响应 schema，不能假设为可自行实现 HTTP | 企业服务器部署，跨平台能力取决于合同和交付物 | 需企业洽购；试用、订阅与价格未公开 | 错误、限流、版本均未公开 | **未验证**。见 [Server API 产品页](https://www.wind.com.cn/portal/zh/WDS/sapi.html) |
| Tushare HTTP | 单一 JSON POST，body 含 `api_name/token/params/fields`，返回 `code/msg/data.fields/data.items`；标准库可直接调用 | HTTP 平台无关，适配 Windows/macOS/Linux | 注册给 100 积分；身份、日历、财务等常需 2000 积分，权限通常有期限；每日股本需另行开通。价格不是统一公开订阅表 | 明确 `code/msg`，文档示例给无权限码 `2002`；各接口列频率/行数，但没有全局 schema 版本或历史兼容承诺 | 传输/基本失败结构 **生产候选**；本项目数据访问 **个人可选**；free 基线 **当前 NO-GO**。见 [HTTP API](https://tushare.pro/document/1?doc_id=130) 与 [积分规则](https://tushare.pro/document/1?doc_id=13) |

## 使用权、缓存、夹具、开源 Adapter 与再分发

本项目要区分三类对象：

1. Adapter 源代码，不包含凭据和提供方原始数据；
2. 供测试使用的最小、脱敏、可能仍含真实数值的缓存/夹具；
3. 对用户输出的研究证据 JSON、估值比率、报告和来源定位。

允许开发官方 API 客户端并不自动授权保存第 2 类或公开第 3 类；“衍生值”也不自动脱离数据许可。

| 提供方 | 公开一手条款事实 | 工程准入判定 |
| --- | --- | --- |
| RQData | 公开用户协议对平台数据的修改、存储、传播、复制、分发及商业/公开使用设置权利限制；试用页还指向最终用户条款，正式产品合同文本和数据权利清单未公开 | 自动调用官方 HTTP 本身可进入验证；**个人/商业用途、缓存、真实夹具、公开衍生输出、开源 Adapter 边界和再分发均当前 NO-GO，直至取得适用于该产品的书面确认**。见 [RQData 用户协议 PDF](https://assets.ricequant.com/welcome/%E7%94%A8%E6%88%B7%E5%8D%8F%E8%AE%AE201907.7855c09d.pdf) 与 [试用页](https://www.ricequant.com/welcome/trial/rqdata-cloud-vnpy) |
| iFinD | 官方协议限制未经许可的复制、修改、链接、转载、发布、镜像、衍生作品以及第三方工具/系统接入；Quant API 是官方授权通道，但公开协议没有授予项目所需的缓存和输出权 | 官方 HTTP 技术验证可继续；**商业使用、缓存/夹具、公开衍生输出、开源 Adapter 和再分发当前 NO-GO，需 iFinD 合同书面许可**。见 [iFinD 服务协议](https://ft.10jqka.com.cn/thsft/iFindService/CellPhone/information/agreement?protocol=privacy&status=1) |
| Choice/EMQuant | Choice 官方最终用户许可将默认用途限定为个人、非商业、非营利，相关数据受保护；未经书面许可不得复制、修改、转载、发布或制作衍生内容 | 即使 SDK 技术验证通过，**通用生产、真实缓存/夹具、衍生公开输出与再分发仍当前 NO-GO**；机构合同是否另行授权 **未验证**。见 [Choice 最终用户许可协议](https://choice.eastmoney.com/Html/userprotocol/userprotocol12.html) |
| Wind | 可公开产品页没有足以覆盖本项目数据使用、自动化、缓存、测试、开源客户端、衍生输出和再分发的适用合同文本 | 所有许可操作 **未验证**；在合同确认前不得把数据落入仓库或对外输出，因而生产使用 **当前 NO-GO**。见 [Client API 产品页](https://www.wind.com.cn/portal/zh/ClientApi/index.html) 与 [Server API 产品页](https://www.wind.com.cn/portal/zh/WDS/sapi.html) |
| Tushare | 官方数据服务协议把许可限定为个人、不可转让、非商业、可撤销且有期限，并限制以营利/经营目的使用；用户协议还要求个人账号使用并限制非官方第三方访问 | 个人账户直接调用官方 HTTP 可作个人交叉核验；**商业/通用生产、共享凭据、真实缓存/夹具、公开数据或衍生结果再分发当前 NO-GO**。开源 Adapter 代码是否可在不附带数据的情况下发布仍应取得书面确认。见 [数据服务协议](https://tushare.pro/document/1?doc_id=405) 与 [用户协议](https://tushare.pro/document/1?doc_id=409) |

## 提供方逐项汇总

这张表不是品牌评分；一个提供方可以同时拥有“传输生产候选”和“许可 NO-GO”。

| 操作 | RQData HTTP | iFinD HTTP | Choice/EMQuant | Wind Client/Server | Tushare HTTP |
| --- | --- | --- | --- | --- | --- |
| SSE/SZSE 身份 | 生产候选 | 生产候选 | 未验证 | 未验证 | 个人可选 |
| BSE 身份与当前代码 | 未验证 | 未验证 | 未验证 | 未验证 | 个人可选 |
| SSE/SZSE 交易日历 | 生产候选 | 生产候选 | 生产候选 | 未验证 | 个人可选 |
| BSE 交易日历 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| 未复权日线收盘 | 生产候选 | 生产候选 | 未验证 | 未验证 | 个人可选 |
| 权威停牌/交易状态 | 生产候选 | 未验证 | 未验证 | 未验证 | 个人可选，及时性未验证 |
| 时点有效总股本 | 生产候选 | 生产候选 | 未验证 | 未验证 | 个人可选，需另开通 |
| 完整公司行动血缘 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| 财报公开时间/版本 | 生产候选 | 生产候选 | 未验证 | 未验证 | 个人可选 |
| 显式 PIT / 无前视 | 生产候选 | 净利润生产候选，权益未验证 | 未验证 | 未验证 | 未验证 |
| TTM 归母净利润字段链 | 生产候选 | 生产候选 | 未验证 | 未验证 | 个人可选 |
| MRQ 归母净资产字段链 | 生产候选 | 未验证 | 未验证 | 未验证 | 个人可选 |
| Python 3.12 标准库路径 | HTTP 生产候选；SDK NO-GO | HTTP 生产候选；SDK NO-GO | SDK 当前 NO-GO | Client NO-GO；Server 未验证 | HTTP 生产候选 |
| 正式错误/限流契约 | 未验证 | 限流候选；完整错误未验证 | 生产候选 | 未验证 | 基本结构候选；版本未验证 |
| free、无需积分/单独权限 | 当前 NO-GO | 未验证，公开说明存在资格条件 | 当前 NO-GO | 当前 NO-GO | 当前 NO-GO |
| 项目所需使用与输出权 | 当前 NO-GO，待书面授权 | 当前 NO-GO，待书面授权 | 当前 NO-GO，待机构合同 | 未验证，当前不得生产使用 | 当前 NO-GO；仅个人可选 |

## 下一轮正式验证顺序

### 第一顺位：RQData HTTP

原因：公开契约已覆盖最难的 PIT 财务边界、归母净利润、归母权益、日度有效股本和不复权价格；这能最快验证是否存在一条完整证据链，而不是只验证单个行情接口。

必须采用“试用凭据只在所有者本机配置、测试不记录 token/用户名/密码”的方式，依次验证：

1. 身份：SSE 主板、SSE 科创板、SZSE 主板、SZSE 创业板、BSE 各一只；另测非法代码、退市代码和歧义证券。
2. 价格/状态：正常交易日、全天停牌、除权日、最近完成交易日和未来日期；确认 `adjust_type='none'`、`skip_suspended` 与 HTTP 方法的等价参数。
3. 股本：回购注销、增发、送转、限售变化、同日多事件各一例；确认单位、精度和生效日。
4. 财报：首次披露、同日更正、跨日更正、调整前/后报表、缺失上年同期各一例；用本项目三段公式重建 TTM，并与供应商衍生值交叉核验。
5. 失败：无权限、超配额、过期 token、错误 method、错误字段、无数据、部分数据、服务端 4xx/5xx、CSV 列漂移和数据尚未就绪。
6. 合同：在任何真实缓存、夹具或对外输出进入仓库前取得书面数据权利确认。

### 第二顺位：iFinD HTTP

原因：官方示例对总股本、财报实际披露日、PIT 归母净利润和 TTM 公式已经相当具体，HTTP 又有配额/QPS 说明；它可作为独立供应商验证 RQData 的关键财务结论。

优先在登录态超级命令中确认三个阻断项：

1. BSE 当前证券后缀和交易日历 `marketcode`；
2. 可与行情缺失区分的全天停牌/交易状态指标；
3. 合并口径、PIT、带实际披露日和版本标识的“归属于母公司股东权益”指标。

若任一项只存在于桌面终端/SDK、不能通过正式 HTTP 调用，则该项不适合作为当前全平台 Skill 的生产 Adapter。

## 需要项目所有者在登录态或合同沟通中确认的问题

不要在 Issue、日志、测试夹具或对话中粘贴 token、密码、refresh token、机器码或完整授权响应。只记录脱敏后的权限名、产品模块、HTTP 状态、错误码、schema 摘要和合同结论。

### RQData

- 试用与正式 HTTP 权限是否包含 `all_instruments`、交易日历、`get_price` 不复权、`is_suspended`、`get_shares`、公司行动、`get_pit_financials_ex` 以及两个归母字段？SDK 方法到 HTTP `method` 的正式映射在哪里？
- BSE 当前代码后缀、交易日历、行情、停牌、股本和财务是否全部支持？请取得一组 `.BJ`/北交所正式样例，而不是依据更新日志推断。
- HTTP 对无权限、限流、日配额、错误字段、未知证券、无数据、部分响应、数据未就绪和服务端错误分别返回什么 HTTP 状态、body、错误码和重试提示？
- 正式订阅按哪些模块计价，年度价格、QPS、并发、历史范围、数据保留和 SLA 是什么？公开页面没有足够价格信息。
- 合同是否明确允许：自动访问；本地短期缓存；不含原始大表的最小测试夹具；输出价格/股本/财务证据及 PE/PB；发布不含数据与凭据的开源 Adapter；用户自行配置账号；不得再分发的精确边界？

### 同花顺 iFinD

- 当前账户究竟属于免费、试用还是正式权限？“免费接口额度”是否以已购买 iFinD 终端为前提？列出本项目每个指标的实际权限名、历史范围和月/周额度。
- 登录态超级命令给出的 BSE 当前代码、交易日历市场码、未复权收盘、全天停牌状态、总股本、实际披露日、PIT 归母净利润、PIT 归母权益、版本/更正指标分别是什么？
- `ths_np_atoopc_pit_stock` 对同日更正、调整前/后版本和历史回填如何排序？归母权益是否有同等级 PIT 指标？
- HTTP 是否有完整错误码表、响应 schema 版本、指标废弃/替换通知和机器可读的数据就绪信号？
- 合同是否允许项目所需自动化、缓存、最小夹具、衍生估值输出、开源 Adapter 及用户自行提供账号？

### 东方财富 Choice / EMQuant

- 登录态命令生成器中，未复权日线、全天停牌、精确总股本、股本生效事件、实际公告时间、报表版本、PIT 归母净利润和 PIT 归母权益的精确指标/参数/单位是什么？
- BSE 当前代码后缀、日历市场码和上述全部指标是否支持？“适配旧代码切换”不能替代逐操作样例。
- 官方是否提供无需专有 SDK 的正式 HTTP/Server API？若没有，Python 3.12 在 Windows、macOS Intel/Apple Silicon、Linux 各发行版的 ABI 支持和安装包校验是什么？
- 试用/正式权限、年度价格、额度、频率、历史范围、SLA 及机构合同的数据使用权是什么？

### Wind

- 请供应商提供当前 Client API 或 Server API 的正式字段字典和样例，逐项覆盖 SSE/SZSE/BSE 身份、交易日历、不复权日线、停牌状态、有效总股本、公司行动、公告时间、版本、PIT 和两个归母字段。
- Server API 究竟是公开 HTTP、私有 Gateway 还是必须使用 SDK？认证、错误码、限流、schema/version 和支持平台是什么？
- Python 3.12 的具体支持矩阵、试用/订阅价格、模块权限、SLA，以及自动化、缓存、夹具、开源 Adapter、衍生输出和再分发权是什么？

### Tushare

- 当前账户的 `stock_basic`、`trade_cal`、`daily`、`suspend_d`、`stk_premarket`、`income`、`balancesheet` 权限、积分、有效期、频率和历史范围分别是什么？每日股本单独开通是否付费？
- BSE 的 `trade_cal` 是否有正式支持；`suspend_d` 的“不定期更新”最迟延迟是多少；盘中停复牌如何归并成全天状态？
- 对历史研究日，`f_ann_date`、`report_type`、`update_flag` 如何保证不使用后来回填的版本？同日多版本和撤回如何排序？
- 是否能取得与公开个人非商业协议不同的书面授权，明确允许本项目的自动访问、缓存、最小夹具、开源 Adapter 和衍生研究输出？未取得前不得将其提升为生产来源。

## 当前准入决定

1. **可以进入最小登录态验证：** RQData HTTP 第一、iFinD HTTP 第二。只允许在本机秘密存储中配置凭据，输出脱敏的契约样本和失败摘要。
2. **可以保留为个人交叉核验：** Tushare HTTP。不得把个人账号、积分权限或非商业服务协议当作项目生产授权。
3. **暂不实现生产 Adapter：** Choice/EMQuant Client SDK、Wind Client API。它们与标准库自包含基线冲突，且关键时点字段公开证据不足。
4. **等待供应商资料：** Wind Server API、Choice 可能存在的服务端 API、所有 BSE 覆盖和所有未公开正式价格。
5. **任何提供方都不得在合同确认前提交真实数据缓存或夹具。** 可先用完全合成数据测试 Adapter 协议、错误归一化和 schema 漂移防护。

下一阶段的通过物不应是“接口能返回 200”，而应是每个操作的版本化契约样本：身份、市场、字段、单位、复权、交易状态、证据日期、公开日期/版本、权限、限流、失败码和适用许可全部可被机器验证，并且 SSE/SZSE/BSE 代表性正常与失败样本均能 fail closed。
