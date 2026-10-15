# Baostock 日期证券名册采集

`baostock_universe_source.acquire_baostock_dated_roster` 通过锁定的 [Baostock 0.9.4](https://pypi.org/project/baostock/0.9.4/) 查询显式 membership effective date。SDK 的 `query_all_stock(day=...)` 声明查询给定日期的证券信息。#121 AG 实际观察到 `2026-10-14` 请求返回 4 页，分别为 2,000、2,000、2,000、1,430 行，合计 7,430 个唯一 provider code。这是该次 provider 名册闭合的证据，包含指数，不是普通 A 股分母，也没有证明 BSE 的适用范围。

## 真实传输与有限调用

标准 Skill 依赖精确锁定 `baostock==0.9.4`；复用已锁定的 `pandas==3.0.5`。SDK 使用独立进程中的匿名默认 session，不能继承父解释器中的账户或 SDK globals。官方 SDK 负责登录与请求消息构造，owner 在单次 `send_msg` seam 实现同步、无内部重试、有限 timeout、有限 frame 大小和 EOF 失败关闭。固定连接仅使用已验证的公开 endpoint，不读个人凭据，也不改用 VIP endpoint。

登录、初次名单请求、每个后续页和登出分别消耗同一次 #119 lane execution 的 actual attempt budget；每个 worker 命令只能包含一个 application send。页预算与 actual attempt budget 分别有限，满 2,000 行需要成功取得下一页，短页或成功空末页才能证明该次枚举结束。错误响应、缺页、达到页上限或重复身份不能当作成功结束。provider count 只有枚举闭合且无坏行/重复时才给出；否则为 `null`。

默认 worker 在导入并核验 artifact-local SDK 后才建立 session。导入失败不制造 source attempt；调用后无论成功、解析失败或预算耗尽，owner 都关闭 worker，只有观察到进程退出才释放 lane。外层 timeout 仍执行 #119 的 poison/quiescence 契约，同组执行不会绕过占用。

## 字段与资格边界

生产 parser 使用已观察的 `code`、`tradeStatus`、`code_name` 字段顺序，核对实际 response day、页号、page size 和 operation。`tradeStatus` 仅保留来源的 `0`/`1` token，不把它解释为上市或终止上市状态。`sh.`/`sz.` 保留为 provider 标识符，不据此猜测 canonical exchange 或普通 A 股类别。未知形状、日期不符、超长 frame 和不安全身份文本不能进入公开观察；其他安全记录继续保留。

`query_stock_basic` 没有日期参数；AG 对普通 A 股与指数的两个 `type` 取值观察，不能证明每条历史记录的类别。当前 basic 名称不回填历史 `code_name`，当前 `status` 也不回填历史上市状态。历史名称版本、普通 A 股分类、BSE 范围、首次可用时间与更广的历史日期范围继续为 `unproved`。请求 date 与研究 cutoff、冻结 research clock、真实 `retrieved_at` 分别保存。

`source_result=partial` 表示这些规范资格尚未闭合，不表示 provider 请求没有执行。`closed_provider_count` 与 `expected_a_share_count` 分开，后者为未知时保留 `null`。此采集值不能直接作为已证明的 #110 expected universe。copied-installed、真实 SDK 加显式 fake socket、parser 和 #119 测试只证明本地实现路径，最终真实三交易所发布证明仍由 #98 负责。
