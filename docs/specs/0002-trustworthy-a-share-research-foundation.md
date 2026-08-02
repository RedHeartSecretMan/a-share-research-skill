# 首个可构建规格：可信 A 股研究 Skill 基础能力

交付状态：规格已发布为 [GitHub Issue #1](https://github.com/RedHeartSecretMan/a-share-research-skill/issues/1)；实现子 Issue #2–#11 均已完成。

## Problem Statement

自主研究者希望让 AI Agent 获取 A 股数据并形成可复算、可追溯的研究结果。但免费接口的返回值不等于可信事实：HTTP 成功仍可能伴随错证券、僵尸报价、错误周期、错误列或空结果，数据获取、证券路由、供应商派生指标和自然语言结论也不应混为一层。

项目需要建立结构化 CLI、证据契约、失败关闭和清晰状态，使实验来源仍然有用但不能冒充正式证据。首个可构建范围必须小到能够通过端到端测试证明：Agent 可以解析沪深 A 股身份，取得并交叉核验最近完成交易日的未复权收盘价，也可以对调用者提供的完整证据包进行确定性验证和估值计算。北交所、自动股本和自动财务取数在资格条件满足前不能被偷偷推断或承诺。

## Solution

在独立仓库 `a-share-research-skill` 中交付一个平台中立、自包含的 `a-share-research` Skill。Skill 通过精简说明指导模型理解自然语言、解析日期、选择 CLI 子命令并解释版本化 JSON；内置 Python CLI 负责确定性的数据获取、证据校验、交叉核验和估值计算，不处理自然语言，也不调用模型。

首个范围同时提供两种路径：

- **提供证据模式**：调用者提供一个包含 manifest 和可选材料的研究证据包。CLI 校验身份、来源字段、时间边界、单位、口径、哈希和计算关系，并计算总市值、PE TTM 与 PB MRQ。没有独立来源核验时，整体简报状态最高为受限。
- **实验来源模式**：CLI 使用 SSE/SZSE 官方股票列表与巨潮证券字典交叉解析身份，使用交易所网页日线与腾讯行情交叉核验收盘价。每个操作明确标记为实验来源；冲突、空响应、陈旧数据、错证券或未知语义必须失败关闭，不能单独使事实主张达到已支撑。

首个联网 tracer 仅覆盖 SSE 和 SZSE。BSE 输入必须返回结构化的不支持结果，且绝不能自动路由到沪市或深市。`mootdx`、自动有效总股本、自动财务取数及付费来源 Adapter 延后到独立规格。

## User Stories

1. As an independent A-share researcher, I want to invoke one installable Skill, so that I do not need to reconstruct data-access code in every research session.
2. As an independent A-share researcher, I want every research request anchored to an explicit China Standard Time date, so that the result is reproducible and does not use future information.
3. As an independent A-share researcher, I want “current” resolved to an exact date before the CLI runs, so that relative time never remains implicit.
4. As an independent A-share researcher, I want a name or bare code treated as a security clue, so that the system does not silently assume an exchange.
5. As an independent A-share researcher, I want identity candidates returned with exchange, code, name, issuer and source evidence, so that I can resolve ambiguity explicitly.
6. As an independent A-share researcher, I want contradictory exchange hints rejected, so that a real response for the wrong security cannot be mistaken for success.
7. As an independent A-share researcher, I want SSE and SZSE identity observations independently compared with the CNINFO security dictionary, so that one opaque webpage response does not own identity alone.
8. As an independent A-share researcher, I want BSE securities to fail closed in the first tracer, so that old codes and venue fallbacks cannot produce zombie or wrong-market data.
9. As an independent A-share researcher, I want the latest completed trading session identified within the research boundary, so that an unfinished current-day session is never reported as a close.
10. As an independent A-share researcher, I want the exchange webpage daily close compared with Tencent’s observation, so that discrepancies become visible instead of being silently selected.
11. As an independent A-share researcher, I want every close observation to include security identity, trading date, retrieval time, currency, price type, adjustment basis and trading status, so that the number has interpretable provenance.
12. As an independent A-share researcher, I want stale, suspended, empty or structurally changed responses classified explicitly, so that “no data” is not confused with “nothing happened”.
13. As an independent A-share researcher, I want experimental data returned with an honest limitation, so that free access remains useful without being advertised as independently verified fact.
14. As an independent A-share researcher, I want to provide my own research evidence bundle, so that I can calculate valuation without granting the Skill a paid data account.
15. As an independent A-share researcher, I want each bundle to represent one security, one issuer, one research date and one research question, so that evidence from different subjects or dates cannot be mixed.
16. As an independent A-share researcher, I want every valuation operand linked to a complete evidence item, so that bare numbers cannot enter the calculation chain.
17. As an independent A-share researcher, I want evidence materials referenced by relative path and SHA-256 when included, so that the bundle remains portable and tampering can be detected.
18. As an independent A-share researcher, I want unavailable or non-redistributable materials represented by a source locator and exact observation reference, so that the system does not copy content without permission.
19. As an independent A-share researcher, I want a standalone bundle validation command, so that I can see all schema, time, unit, relationship and artifact errors before calculation.
20. As an independent A-share researcher, I want valuation to rerun the full bundle validation every time, so that an earlier validation cannot be used to bypass changed evidence.
21. As an independent A-share researcher, I want the CLI to calculate market capitalization from unadjusted close and effective total shares, so that a provider’s market-cap field is only a cross-check.
22. As an independent A-share researcher, I want the CLI to calculate TTM attributable profit from the required reported periods, so that a precomputed TTM value cannot bypass report lineage.
23. As an independent A-share researcher, I want the CLI to calculate PE TTM and PB MRQ from exact operands, so that formulas and units are reproducible.
24. As an independent A-share researcher, I want non-positive profit or equity reported as no valuation meaning, so that a valid economic state is not confused with missing data.
25. As an independent A-share researcher, I want missing or incompatible operands reported as not calculable, so that the system does not substitute forecasts, float shares or unrelated accounting values.
26. As an independent A-share researcher, I want report corrections and replacement relationships made explicit, so that the system does not choose a convenient version of a filing.
27. As an independent A-share researcher, I want precise decimal strings and explicit units in JSON, so that large share counts and financial values survive different Agent runtimes without floating-point loss.
28. As an independent A-share researcher, I want supported, limited and blocked research outcomes separated from process execution errors, so that a valid blocked result is still machine-readable.
29. As an independent A-share researcher, I want every valid JSON result to use a zero process exit code, so that Agent runners do not discard a limited or blocked research result as a crash.
30. As an independent A-share researcher, I want logs and diagnostics on standard error rather than standard output, so that JSON parsing remains deterministic.
31. As an independent A-share researcher, I want the CLI stateless by default, so that it does not leave hidden caches, databases or global configuration on my machine.
32. As an independent A-share researcher, I want saved results written only when I provide an explicit output destination, so that local artifacts remain under my control.
33. As an independent A-share researcher, I want optional credentials read only from documented environment variables, so that secrets do not enter command history, result JSON, logs or fixtures.
34. As an independent A-share researcher, I want provider-computed PE, PB and market capitalization preserved only as cross-check observations, so that the project’s result always retains its own calculation lineage.
35. As an independent A-share researcher, I want source conflicts preserved rather than averaged, so that disagreement remains visible for research judgment.
36. As an independent A-share researcher, I want the final Agent answer to distinguish source facts, model inference and unavailable evidence, so that I remain responsible for the investment decision.
37. As an independent A-share researcher, I want the Skill to avoid cheap/expensive labels and buy/sell recommendations, so that it remains a research evidence assistant rather than an adviser.
38. As a Skill maintainer, I want source-operation knowledge expressed as references, fixtures and regression cases rather than a second runtime, so that valuable behavior remains testable without preserving a monolith.
39. As a Skill maintainer, I want each provider operation qualified independently, so that one good endpoint does not approve an entire provider and one bad endpoint does not discard all useful knowledge.
40. As a Skill maintainer, I want deterministic offline tests and separate opt-in live probes, so that CI failures distinguish code regressions from external-source changes.
41. As a Skill maintainer, I want the runtime to use Python 3.12 standard-library capabilities only, so that the installed Skill runs across Windows, macOS and Linux without a package installation step.
42. As a Skill maintainer, I want development-only tools optional and versioned, so that stronger linting or test ergonomics do not become user runtime requirements.
43. As a Skill maintainer, I want the installed Skill validated with the standard Skill validator, so that metadata, naming and packaging remain portable across compatible Agents.

## Implementation Decisions

- Maintain the project as a standalone repository named `a-share-research-skill` with no compatibility requirement for other implementations.
- Keep the repository root for engineering documentation, tests, quality configuration and licensing; keep the sole installable artifact in a nested Skill folder with a concise Skill entry, UI metadata, bundled scripts and progressively disclosed references.
- Keep one evidence-contract runtime. Express endpoint mappings, routing knowledge, headers, units, failure cases and smoke-test observations as modular source operations, references and fixtures.
- Use one Python CLI entry point with fixed subcommands. The first spec owns identity resolution, close observation, evidence-bundle validation and evidence-bundle valuation; it does not accept natural language.
- Require explicit `as_of` dates on research operations. The Agent resolves relative dates in China Standard Time before invoking the CLI.
- Use exchange-qualified canonical security identifiers. Bare codes and names are clues for identity resolution, never sufficient research identifiers.
- Make SSE/SZSE official stock-list operations and the CNINFO security dictionary the first experimental identity sources. Require an exchange-specific official observation plus CNINFO agreement on code and name before returning a unique identity candidate. CNINFO may remain exchange-neutral, but an explicit exchange conflict must block; never infer the exchange from a code prefix or opaque `orgId`.
- Make SSE/SZSE webpage daily-line operations and Tencent quote observations the first experimental close sources. Compare security identity, trading date and close; do not compare only numeric proximity.
- Treat all first-source operations as experimental. They may supply observations and detect conflicts, but they cannot independently establish a supported factual claim until their operation-level contract, semantics, failure behavior and permission are qualified.
- Fail closed on BSE for the first tracer. Preserve BSE current/legacy-code cases as negative tests and never fall back to SSE or SZSE.
- Defer `mootdx` to an optional Adapter because it introduces a third-party runtime dependency and TCP/environment behavior. Do not make it part of the first standard-library tracer.
- Represent provider observations in one common evidence model with source role, source operation, canonical subject, observed value, unit, basis, evidence time, availability time, retrieval time, locator and limitations.
- Preserve applicable conflicts as separate evidence items. Do not average, overwrite or silently prefer one experimental source.
- Accept one portable research evidence bundle directory containing a manifest and optional artifacts. Referenced artifacts use bundle-relative paths and SHA-256; paths cannot escape the bundle.
- Require complete provenance fields for every calculation operand. Structurally incomplete items may be reported but are inadmissible for claims or calculations.
- Distinguish evidence admissibility from source verification. Schema correctness, hashes and caller assertions do not prove source authenticity.
- Cap provided-evidence results at limited unless every critical input has an independently qualified acquisition or verifiable official provenance chain.
- Do not parse arbitrary PDF, HTML, image or screenshot content in the first CLI milestone. The caller or Agent supplies structured observations with exact displayed text and location; the CLI validates normalization and relations without claiming it verified the document content.
- Require a trading-session evidence item and an unadjusted close observation for the same latest completed session. A price without session evidence cannot establish the common valuation price.
- Require an exact, effective-as-of total-share observation for bundle valuation. Do not rebuild the corporate-action ledger in this spec and do not substitute report-period shares, rounded webpage values or float shares.
- Require report identity, cumulative period, consolidation scope, attribution scope, unit, publication date and version relationship for financial evidence. Apply replacement/correction relationships conservatively; unresolved conflicts make the affected metric not calculable.
- Calculate TTM attributable profit from the previous full year plus the latest current-year cumulative period minus the matching prior-year cumulative period, except when an applicable full-year report directly supplies the TTM period.
- Calculate market capitalization, PE TTM and PB MRQ internally. Provider-derived values remain independent market-observation cross-checks.
- Use decimal arithmetic for all research calculations. Encode exact numeric values as decimal strings with explicit unit and scale; do not pass monetary, share-count, profit, equity or ratio evidence through binary floating point.
- Use a versioned JSON result as the only stable CLI output. Put diagnostics on standard error. Return zero whenever a valid contract result is produced, including supported, limited and blocked outcomes; reserve nonzero exits for invocation, protocol, I/O or internal failures that prevent a valid result.
- Keep runtime behavior stateless by default. Any saved result requires an explicit destination; do not create hidden caches, databases, credential stores or user configuration.
- Read optional provider credentials only from explicitly documented environment variables. Never accept secrets as CLI arguments or include them in logs, JSON, fixtures or repository files.
- Support Python 3.12 and later using only the standard library in the installable runtime. Allow optional, pinned development dependencies outside the installed Skill.
- Keep live source probes opt-in and diagnostic. They cannot change fixtures, lower evidence requirements or become ordinary CI dependencies.
- Maintain source-operation qualification separately from provider branding. Adding a production Adapter requires an operation-specific decision based on contract, semantics, failures and permission.

## Testing Decisions

- Test observable behavior rather than private helpers, internal call counts or implementation layout. Expected values must come from independent literals, worked accounting examples, official field definitions or captured failure cases, not from reimplementing the same algorithm inside the test.
- Use the CLI process as the primary test Seam. Invoke commands with arguments and evidence bundles, then assert only standard output JSON, standard error diagnostics, exit status and explicitly saved artifacts.
- Use the Adapter operation as the only necessary lower test Seam. Feed fixed, minimal responses from SSE, SZSE, CNINFO and Tencent into an operation and assert normalized observations or explicit failures.
- Do not establish a separate Research Module test Seam. Exercise orchestration and status aggregation through the CLI so internal modules remain refactorable.
- Keep default tests completely offline. Mock only the external network boundary; do not mock internal project modules.
- Maintain regression cases for wrong exchange prefixes, contradictory identifiers, BSE legacy and current codes, HTTP-success empty bodies, wrong-security payloads, stale quotes, missing required fields, changed array indexes, unexpected content types and schema drift.
- Test the latest-completed-session rule for historical dates, current dates before close, current dates after a formally completed daily observation, weekends, holidays and future dates.
- Test exact Decimal normalization and worked market-cap, TTM, PE and PB examples, including scale conversions and non-positive denominators.
- Test state aggregation independently from process status: all-valid provided evidence remains limited without source verification; missing common identity or price blocks; partial calculability limits; malformed invocation that prevents valid JSON uses nonzero exit.
- Run live source probes separately against a representative SSE main-board security, SSE STAR security, SZSE main-board security and SZSE ChiNext security, plus invalid, stale, suspended and corporate-action cases when available.
- Verify the installed artifact with the standard Skill validator and verify runtime compatibility under an actual Python 3.12 interpreter; a newer interpreter passing is not evidence of minimum-version compatibility.

## Out of Scope

- Claiming that an experimental free source is a formally supported production source.
- Automatically obtaining effective total shares, reported financial statements, TTM profit or MRQ equity from network sources.
- Automatically producing a fully supported current valuation brief from free network sources.
- BSE runtime support, BSE quote fallback or inference from legacy BSE codes.
- Intraday, real-time, minute, tick, order-book or trading functionality.
- Automatic parsing of PDF, HTML tables, images or screenshots.
- Corporate-action-ledger reconstruction and multi-class or multi-market issuer-wide valuation.
- Building a broad catalogue of news, sentiment, research reports, fund flows, limit-up pools, options, trading signals or unrelated data endpoints.
- Shipping `mootdx`, pandas, requests, stockstats, AKShare or another third-party runtime dependency in the first tracer.
- Shipping RQData, iFinD, Choice, Wind, Tushare or broker-terminal production Adapters.
- Persisting or redistributing provider raw responses without explicit permission.
- Natural-language processing or model calls inside the CLI.
- Investment ratings, cheap/expensive judgments, price targets, buy/sell recommendations, position sizing or automated trading.
- Detaching or renaming the GitHub repository, changing remotes, publishing releases or installing the Skill globally as part of this feature implementation.

## Further Notes

- The free-source viability report concluded that no current free combination satisfies the entire trusted valuation chain under the original constraints. This spec responds by retaining free source acquisition as explicitly experimental instead of either discarding it or overclaiming it.
- The Adapter qualification matrix ranks RQData HTTP and iFinD HTTP as future credentialed validation candidates, but their data-use permission remains unresolved and they are not dependencies of this spec.
- The first source combination was chosen deliberately: exchange and CNINFO observations for identity, exchange and Tencent observations for the daily close. Other source operations remain later optional Adapter candidates.
