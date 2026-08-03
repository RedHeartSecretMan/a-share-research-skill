# Spec 0006：研究级完整交易日分时复盘

## Problem Statement

当前 Skill 可以查询最近完成交易日的日线走势，也可以在交易时段取得单点盘中行情快照，但不能回答“一只股票在某个已经结束的交易日内是怎样走出来的”。用户无法通过稳定 ResearchTask Interface 取得可复算的一分钟未复权实际成交序列、判断序列是否覆盖完整交易会话，或基于同一份证据生成盘后复盘摘要。

直接把行情供应商返回的“分钟 K 线”交给用户也不能解决该问题。来源可能使用不透明的时间标签、手与股等不同单位、倒序分页、集合竞价归桶、前值填充或缺失补零；单次成功响应不能证明证券身份、交易日、分钟含义和全日总量一致。若这些差异未被显式裁决，研究助手可能把来源缺口当成无成交，把集合竞价当成连续竞价，或在证据不完整时输出看似精确的走势结论。

用户还需要在明确请求时，对分时复盘和近期日线证据形成合理但受约束的未来情景判断。现有边界能区分事实、计算与推断，却没有规定这种预测所需的最低证据、固定期限、替代情景和失效条件，容易在“完全禁止预测”和“无依据给出确定性方向”之间摇摆。

## Solution

在现有公共 `run --request` 入口上增加独立任务类型 `intraday_replay`。一次请求只处理一只规范 SSE 或 SZSE A 股和一个明确的已完成交易日，返回按北京时间排序的一分钟未复权实际成交记录、交易阶段、覆盖裁决、来源证据、冲突与限制，以及能够从返回记录复算的分时复盘摘要。

完整分钟序列必须由一个版本化来源操作按同一口径形成，不能跨来源拼接。另一个独立来源操作在日级核验证券身份、交易日、开高低收、总成交量和成交额。系统明确区分完整、部分和不可判定覆盖，区分无成交、缺失和已确认停牌；绝不插值、前值填充或把缺失合成为零。实验来源即使相互一致，结果最高仍为 `limited`。

确定性的 `intraday_replay` 只返回证据和计算，不自动预测。用户明确要求预测时，研究助手可以在满足最低证据门槛后，基于该复盘结果和截至复盘日的最近 20 个完整日线交易日，分别对下一交易日和未来 5 个交易日形成一个主情景及上下行替代情景。每个情景必须披露依据、反向证据、假设、触发条件、失效条件和不确定性；不得编造精确概率，也不得转化为评级、目标价、买卖持有、仓位、止损或自动交易指令。

## User Stories

1. As an A 股研究用户, I want to request one completed trading day for one canonical stock, so that I can replay its intraday path without ambiguity about the security or date.
2. As an A 股研究用户, I want to use the existing ResearchTask entrypoint, so that intraday replay composes with the rest of the Skill instead of requiring a separate command.
3. As an A 股研究用户, I want the replay date expressed through an explicit one-day observation window, so that a historical replay cannot be confused with a current snapshot.
4. As an A 股研究用户, I want the research `as_of` boundary kept separate from the replay date, so that I know which information was allowed when the result was formed.
5. As an A 股研究用户, I want any single day among the most recent 20 completed trading days to be supported, so that I can inspect recent sessions rather than only the latest close.
6. As an A 股研究用户, I want unfinished, future, non-trading, and indeterminate sessions rejected explicitly, so that partial live data is not presented as a completed replay.
7. As an A 股研究用户, I want every accepted minute record to carry an explicit Beijing-time interval start and end, so that the time represented by a source label is unambiguous.
8. As an A 股研究用户, I want the original source timestamp and its verified start-or-end semantics preserved, so that normalized intervals remain auditable.
9. As an A 股研究用户, I want minute records ordered chronologically and deduplicated deterministically, so that source pagination order cannot distort calculations.
10. As an A 股研究用户, I want unadjusted actual transaction OHLC prices, so that the intraday path is not silently mixed with adjusted daily history.
11. As an A 股研究用户, I want prices serialized as fixed-point decimal strings in CNY per share, so that binary floating-point noise does not create false conflicts.
12. As an A 股研究用户, I want volume normalized to shares and amount normalized to CNY, so that totals and VWAP can be compared and recomputed.
13. As an A 股研究用户, I want unit transformations to require a qualified source contract, so that a guessed lots-to-shares multiplier cannot become accepted evidence.
14. As an A 股研究用户, I want morning and afternoon continuous trading identified separately, so that session boundaries are visible in the replay.
15. As an A 股研究用户, I want the lunch break excluded from expected minute coverage, so that the normal market recess is not reported as a data gap.
16. As an A 股研究用户, I want a supported opening auction result represented separately, so that it is not fabricated as a continuous one-minute bar.
17. As an A 股研究用户, I want the closing auction represented according to the source's qualified semantics, so that a final match covering 14:57–15:00 is not expanded into invented minute bars.
18. As an A 股研究用户, I want unsupported auction semantics reported as unavailable or blocking, so that a provider convention is not guessed from its timestamps.
19. As an A 股研究用户, I want a proven no-trade minute represented with unavailable OHLC and zero volume and amount, so that absence of transactions is distinguishable from a flat fabricated bar.
20. As an A 股研究用户, I want missing records kept distinct from no-trade records, so that network or source gaps are not interpreted as market states.
21. As an A 股研究用户, I want no interpolation, forward filling, or zero synthesis, so that every returned price and amount remains attributable to source evidence.
22. As an A 股研究用户, I want sequence coverage classified as complete, partial, or indeterminate, so that I can tell how much of the trading day is safe to analyze.
23. As an A 股研究用户, I want partial coverage to list missing intervals and a reproducible coverage ratio, so that limitations are measurable rather than buried in prose.
24. As an A 股研究用户, I want indeterminate session coverage to block substantive replay conclusions, so that an unbounded gap is not treated as a usable partial series.
25. As an A 股研究用户, I want partial results to retain observed rows, so that trustworthy intervals remain usable even when the full day cannot be reconstructed.
26. As an A 股研究用户, I want calculations on partial data scoped to uninterrupted observed intervals, so that metrics do not jump across missing evidence.
27. As an A 股研究用户, I want a full-day suspension accepted only when independent sources agree, so that an empty provider response is not mistaken for a market suspension.
28. As an A 股研究用户, I want a confirmed suspended day returned as an explicit market-state result without a fabricated sequence, summary, or forecast, so that valid absence is represented honestly.
29. As an A 股研究用户, I want the whole minute sequence to come from one source operation, so that incompatible providers are not spliced minute by minute.
30. As an A 股研究用户, I want an independent daily-boundary cross-check, so that the sequence's identity, date, OHLC, close, volume, and amount can be challenged by separate evidence.
31. As an A 股研究用户, I want unavailable independent cross-check evidence to lower confidence to `limited`, so that usable experimental evidence is retained without being overstated.
32. As an A 股研究用户, I want unexplained core daily conflicts to block the full-day conclusion, so that internally neat minute data cannot override contradictory boundary evidence.
33. As an A 股研究用户, I want every source operation, evidence locator, acquisition time, calculation, conflict, error, and limitation retained, so that the result can be audited later.
34. As an A 股研究用户, I want the actual previous close distinguished from an ex-right reference price, so that opening-gap and relative-return calculations use comparable semantics.
35. As an A 股研究用户, I want unavailable previous-close semantics to suppress only dependent metrics, so that unrelated replay evidence is not discarded.
36. As an A 股研究用户, I want bar count, coverage, missing intervals, open-to-close change and return, high and low with all occurrence times, and intraday range, so that the day's basic path is summarized reproducibly.
37. As an A 股研究用户, I want VWAP with amount and volume operands, so that the reported average transaction price can be independently recomputed.
38. As an A 股研究用户, I want maximum drawdown over usable minute closes with peak and trough times, so that the largest observed decline is explicit rather than visually guessed.
39. As an A 股研究用户, I want morning and afternoon returns and volume shares, so that session-level differences are described with transparent scope.
40. As an A 股研究用户, I want maximum minute volume and amount with all tied intervals, so that repeated extrema are not silently reduced to one arbitrary timestamp.
41. As an A 股研究用户, I want maximum adjacent-minute rise and fall with operands and times, so that short-interval changes can be reproduced without relying on a chart.
42. As an A 股研究用户, I want every unavailable metric to carry a reason, so that `null` is never confused with zero or an implementation omission.
43. As an A 股研究用户, I want raw rows and deterministic summaries in the same versioned JSON result, so that I can audit calculations or build my own analysis.
44. As an A 股研究用户, I want chart-ready data without mandatory PNG or HTML generation, so that the same evidence can be visualized by whichever client surface I use.
45. As an A 股研究用户, I want normalized output saved only when I explicitly use the existing output option, so that replay does not create hidden caches or databases.
46. As an A 股研究用户 concerned with privacy, I want provider raw responses, server addresses, credentials, caches, and global configuration excluded from persisted results, so that research evidence does not leak operational details.
47. As an A 股研究用户, I want a missing optional market-data dependency to block only `intraday_replay`, so that unrelated research tasks remain available.
48. As an A 股研究用户, I want experimental source policy to be explicit, so that the system cannot silently treat an unqualified provider as production-grade.
49. As an A 股研究用户, I want deterministic replay separated from research-assistant interpretation, so that source facts and calculations cannot be confused with an Agent inference.
50. As an A 股研究用户, I want a forecast only when I explicitly request one, so that a historical replay does not unexpectedly contain speculative claims.
51. As an A 股研究用户, I want forecast evidence to include a usable replay and 20 complete daily sessions through the replay date, so that the scenario is anchored in both intraday and recent daily behavior.
52. As an A 股研究用户, I want replay identity, date, close, and adjustment semantics aligned with daily evidence before prediction, so that incompatible series are not combined.
53. As an A 股研究用户, I want fixed forecast horizons of the next trading day and next 5 trading days, so that the claim can be evaluated against an explicit time frame.
54. As an A 股研究用户, I want one primary continuation, range, or reversal scenario plus upside and downside alternatives, so that uncertainty is represented as branching possibilities rather than one deterministic call.
55. As an A 股研究用户, I want every scenario to state its basis, opposing evidence, assumptions, triggers, invalidation conditions, and uncertainty, so that I know what could change the judgment.
56. As an A 股研究用户, I want precise probabilities withheld unless they come from a reproducible out-of-sample validated model, so that confidence is not invented.
57. As an A 股研究用户, I want material partial-coverage gaps to prevent prediction, so that a missing close, whole morning, or whole afternoon cannot support a directional scenario.
58. As an A 股研究用户, I want non-material partial coverage disclosed and the prediction status limited, so that usable but incomplete evidence is not promoted to full support.
59. As an A 股研究用户, I want absent announcements, news, fund-flow, and other contextual evidence disclosed without automatically blocking a price-behavior scenario, so that the scope of the inference is honest.
60. As an A 股研究用户, I want ratings, target prices, buy/sell/hold calls, position sizing, stop-loss instructions, and automatic trading kept outside the result, so that research remains distinct from investment action.

## Implementation Decisions

- The public interface remains the existing ResearchTask command. `intraday_replay` is a new task type, not a new top-level command and not an extension of `intraday_market_signal`.
- A request contains exactly one resolvable canonical SSE or SZSE A-share subject. ETF, option, index, multiple-security, and batch requests are rejected for this capability.
- `as_of` remains the research boundary. `window.observed_from` and `window.observed_to` identify the replay date and must be the same explicit calendar date. The replay date must not be later than `as_of` and must be a completed exchange trading day.
- The minimum availability promise is any one trading day among the 20 most recently completed trading days as determined at the request's research boundary. One call never returns more than one day's minute sequence.
- If the replay date is the current Beijing date, the task is accepted only after the applicable session has conclusively ended. An unfinished, future, non-trading, or unprovable session returns a structured `blocked` result.
- The capability uses a deep internal replay module behind the existing runtime. The module owns request validation, source-operation orchestration, normalization, session coverage, daily-boundary adjudication, deterministic summary calculations, and ResearchResult projection.
- The minute-sequence source operation returns one security and one date under a versioned contract. It must establish canonical identity, timestamp semantics, price adjustment, price precision, volume and amount units, auction treatment, pagination behavior, ordering, duplicate handling, and sanitized failure modes before its rows are admissible.
- The entire admitted minute sequence comes from one source operation and one coherent contract. Fields or gaps are never filled from a second minute provider. A second independently qualified minute sequence may be retained as optional evidence but is not required in this specification.
- A separate independent daily operation cross-checks canonical identity, date, unadjusted daily open/high/low/close, actual close, total volume, and total amount. Shared upstream data does not qualify as independent merely because it is exposed by another client library.
- Missing or unavailable independent daily evidence permits a usable sequence only as `limited`. An unexplained conflict in identity, date, daily OHLC, close, total volume, or total amount blocks the full-day conclusion. Differences caused by qualified auction bucketing or unit semantics must be explained in calculation lineage rather than hidden with numeric tolerances.
- Experimental source operations require `source_policy.allow_experimental=true`. A result derived from experimental operations is at most `limited`, even when sequence and daily boundary agree. A successful live probe never changes this qualification.
- `mootdx==0.11.7` remains a capability-scoped optional dependency for the candidate minute operation. If it is absent or the exact supported version is unavailable, only `intraday_replay` returns `missing_optional_dependency`; the core CLI and other tasks continue to work. The runtime does not silently switch to another provider.
- The normalized result uses fixed-point decimal strings. Prices are CNY per share, volume is shares, amount is CNY, and timestamps include an explicit `+08:00` offset. No pandas, NumPy, or binary floating-point representation crosses the JSON boundary.
- Each continuous record contains interval start, interval end, original source timestamp, verified timestamp semantics, trading phase, trade state, OHLC availability, OHLC values when available, volume, amount, and row-level evidence lineage.
- Expected trading-path coverage excludes the lunch recess. The regular path spans the morning and afternoon market sessions, while continuous-auction and closing-auction records remain semantically distinct. Coverage is calculated against the intervals that the qualified exchange/session contract requires, not against provider row count alone.
- An opening auction result at 09:25 is optional evidence and never manufactured. If admitted, it is a separate auction result and is not part of the continuous-minute coverage denominator.
- Closing-auction evidence must preserve the qualified source meaning. When the source exposes only a final auction match, the result is one separate 14:57–15:00 closing-auction result; the runtime must not synthesize three one-minute bars. A source that exposes actual auction subinterval transactions may retain them only after those semantics are qualified.
- A no-trade interval is admitted only when the source contract explicitly proves that state. It has unavailable OHLC and zero volume and amount. A missing row, empty response, repeated prior price, or zero volume alone does not prove no trade.
- Coverage status is `complete`, `partial`, or `indeterminate`. `complete` means every required interval and the applicable closing stage are resolved as traded or proven no-trade. `partial` means bounded gaps are known. `indeterminate` means the expected session path or gaps cannot be bounded and therefore blocks substantive replay.
- Coverage output includes the expected scope, observed traded intervals, proven no-trade intervals, missing intervals, and a reproducible ratio. Proven no-trade intervals count as covered; the lunch recess and optional opening auction do not enter the denominator.
- Partial coverage retains every admissible record but never interpolates, forward fills, backfills, or synthesizes zeros. Each summary metric is independently marked calculable or unavailable. On partial data, calculations cannot cross a missing interval; their output states the exact continuous interval or intervals used.
- Full-day suspension requires independent-source agreement. It produces an explicit confirmed-suspension market-state result with no minute sequence, replay summary, or scenario prediction. A single-source status, empty response, source error, flat price, or zero volume cannot independently establish suspension.
- The result distinguishes the previous trading day's actual unadjusted close from an ex-right or ex-dividend reference price. Opening-gap and relative-price calculations are emitted only when the selected baseline semantics are explicit and comparable to unadjusted actual transaction prices. A missing baseline suppresses only dependent metrics.
- The deterministic replay summary includes: row and coverage counts; missing intervals; open-to-close absolute change and return; full-day high and low with every tied occurrence time; absolute intraday range and range divided by the compatible baseline when available; VWAP as total amount divided by total shares; maximum drawdown over chronological usable closes with peak and trough times; morning and afternoon returns and volume shares; maximum minute volume and amount with all ties; and maximum adjacent-minute rise and fall with both operands and intervals.
- The day's open and close are actual admitted first and final transaction prices, including separately represented auction results when their semantics are qualified. No-trade and unavailable records do not supply prices. When these endpoints cannot be established, dependent metrics are unavailable.
- Maximum drawdown is the largest decline from a prior running peak close to a later close. Adjacent-minute changes use consecutive comparable continuous records within an uninterrupted observed interval and never jump over a missing interval or an auction/session boundary. Every metric records its formula, operands, units, rounding rule, and observation scope.
- RSI, MACD, KDJ, Bollinger bands, proprietary technical scores, pattern labels, causal fund-flow narratives, and other indicator suites are not produced in the first implementation.
- ResearchResult retains the canonical subject, request boundary, replay date, trading status, coverage decision, normalized records, auction results, baselines, summary, versioned source operations, evidence locators, acquisition times, calculation lineage, conflicts, source errors, limitations, and field-level unavailability reasons.
- Valid `limited` and `blocked` domain results retain the existing exit-zero JSON behavior. Invocation, I/O, malformed protocol, and internal failures retain nonzero exit status and sanitized stderr.
- The existing explicit output option may persist only the normalized ResearchResult. No provider raw response, endpoint or server address, implicit cache, credential, user-global provider configuration, or automatic database is created or committed.
- The JSON sequence is chart-ready. The CLI does not generate PNG, HTML, an interactive TUI, or another visualization artifact. A supporting Agent surface may visualize the returned data without changing evidence status.
- Deterministic `intraday_replay` never predicts. Agent-facing research guidance is extended so that scenario prediction occurs only after an explicit user request and remains visibly separated from source facts, calculations, and replay analysis.
- The minimum prediction evidence is: canonical identity; a non-blocked replay with a usable close or closing stage; 20 complete daily trading sessions ending on the replay date; aligned security, date, unadjusted price semantics, and close; and no unresolved core source conflict.
- A partial replay can support only a `limited` scenario prediction when every gap is disclosed and the close remains usable. No prediction is formed when the close is missing, the whole morning is missing, the whole afternoon is missing, coverage is indeterminate, the day is confirmed suspended, or daily evidence is incomplete.
- The two fixed forecast horizons are the next trading day and the next 5 trading days. For each horizon, the Agent chooses one primary scenario category from continuation, range, or reversal and adds both upside and downside alternatives.
- Every scenario states the horizon, evidence basis, opposing evidence, assumptions, observable triggers, invalidation conditions, and uncertainty. Announcements, news, fund flow, capital events, valuation, and broader market evidence are optional enhancements; if absent, the Agent narrows and discloses the claim to price behavior.
- Exact probabilities are allowed only when produced by a named, reproducible model with documented inputs and genuine out-of-sample validation. This specification introduces no such model, so the default Agent output uses qualitative uncertainty and never invents percentages.
- Scenario prediction is research inference, not an investment action. The Skill does not generate its own rating, target price, buy/sell/hold call, position size, stop-loss instruction, execution timing, or automatic trading command.

## Testing Decisions

- The primary acceptance seam is the existing public `entrypoint.py run --request` behavior with `task_type: intraday_replay`. Tests invoke the same command and parse the same stdout JSON that users receive; no second public test-only command is introduced.
- Default tests are fully offline. They inject deterministic synthetic source operations behind the runtime seam and never contact a provider, depend on a local provider cache, read user-global configuration, or record real responses.
- Public-seam tests cover canonical SSE and SZSE subjects, the one-day window contract, recent-20-day eligibility, completed-session gating, research-boundary separation, experiment-policy refusal, and capability-scoped optional dependency failure.
- Public-seam tests cover chronological sorting, duplicate handling, explicit `+08:00` intervals, source timestamp preservation, start/end label semantics, decimal serialization, price precision, lots-to-shares normalization, amount units, and rejection of unknown units or timestamp meanings.
- Public-seam tests cover morning and afternoon continuous sessions, lunch exclusion, optional opening-auction evidence, separately represented closing-auction evidence, unsupported auction semantics, and prevention of invented auction minute bars.
- Public-seam tests cover traded intervals, proven no-trade intervals, missing intervals, complete/partial/indeterminate coverage, bounded gap reporting, coverage-ratio lineage, no interpolation, and per-metric availability on partial data.
- Public-seam tests cover full-day suspension confirmed by independent sources, disagreement about suspension, empty responses, zero-volume ambiguity, wrong security, wrong date, schema drift, source failure, and sanitized diagnostics.
- Public-seam tests cover independent daily-boundary agreement, unavailable cross-check evidence, daily OHLC/close/volume/amount conflicts, price-minimum-tick normalization, explainable auction-bucketing differences, and prohibition of cross-source minute splicing.
- Public-seam tests recompute every summary metric from emitted rows and operands. They include ties, no-trade intervals, lunch boundaries, missing gaps, unusable endpoints, previous-close and ex-right-reference semantics, and formulas that must not cross gaps or incompatible stages.
- Public-seam tests assert complete evidence lineage, operation versions, acquisition times, conflicts, source errors, limitations, unavailable-field reasons, domain-result exit semantics, and absence of raw provider or credential material.
- Fine-grained unit tests may exercise pure normalization and calculation modules directly for arithmetic edge cases, but they do not replace public-seam acceptance and must not assert private call order or incidental object structure.
- Existing intraday snapshot CLI end-to-end tests are the primary prior art for fixture-driven subprocess behavior, structured `limited`/`blocked` results, evidence, lineage, conflicts, errors, and limitations. Existing daily market-series tests are prior art for adjustment semantics, corporate actions, missing sessions, conflicts, suspension, wrong identity, and empty responses.
- Agent-facing distribution tests verify that the installed Skill routes an explicit prediction request through replay and daily evidence, preserves the two fixed horizons and three-scenario shape, refuses prediction below the evidence floor, labels partial-evidence predictions `limited`, and excludes ratings and investment actions. These tests validate the distributed guidance and examples rather than adding a prediction command to the deterministic runtime.
- A separate explicit live probe exercises at least one SSE and one SZSE A-share with an ephemeral provider home and sanitized output. It is opt-in, excluded from ordinary CI, does not update fixtures, and reports source-contract observations without changing production qualification.
- Live qualification must specifically examine timestamp interval meaning, price scaling, volume and amount units, auction behavior, pagination order, duplicates, recent-20-day coverage, schema drift, timeout/empty/error behavior, licensing constraints, and agreement with an independent daily operation.
- A one-time successful live probe is evidence of transport viability only. Production admission requires a reviewed, versioned operation contract and repeatable qualification evidence.
- Completion requires Python 3.12 full unittest, Ruff, strict mypy, Skill validation, `git diff --check`, and separate Standards and Spec reviews.

## Out of Scope

- Current unfinished-session minute data, real-time streaming, polling, subscriptions, WebSocket feeds, tick data, order books, queue depth, and execution-grade latency or availability.
- Multiple securities per request, multi-day minute batches, cross-security scans, portfolio replay, and automatic scheduled collection.
- ETF, index, option, futures, non-SSE/SZSE securities, and markets outside mainland A-share regular sessions.
- Adjusted intraday prices, inferred corporate-action adjustments, hidden provider-derived returns, and mixing adjusted daily evidence with unadjusted minute transactions.
- Cross-provider minute-field splicing, automatic provider fallback, unqualified unit guessing, interpolation, forward filling, synthetic flat bars, and treating missing rows as zero.
- Persisting provider raw responses, implicit caches, databases, server discovery details, credentials, or user-global provider configuration.
- Merging the prototype TUI, making the prototype a supported interface, or treating prototype observations as a production source contract.
- Mandatory chart rendering, PNG/HTML generation, a dedicated replay UI, or storing visualization artifacts in the core result.
- RSI, MACD, KDJ, Bollinger bands, proprietary technical scores, automatic chart-pattern detection, and claims about “main force” intent or causal market behavior.
- Exact forecast probabilities without a separately specified and validated model, price targets, ratings, buy/sell/hold recommendations, position sizing, stop-loss instructions, automated alerts, orders, and trading execution.
- Automatically incorporating announcements, news, social sentiment, fund flows, valuation, sector context, or macro evidence; these remain optional evidence enhancements under existing research capabilities.
- Promoting an experimental operation to production qualification or promising that every external provider will remain available for the recent-20-day window.

## Further Notes

- This specification uses the domain language in `CONTEXT.md`, especially complete intraday trading-day series, intraday replay summary, intraday series coverage status, intraday time interval, no-trade interval, confirmed suspended trading day, and evidence-constrained scenario prediction.
- ADR-0017 is controlling: one source operation forms the minute sequence and an independent operation cross-checks at the daily boundary. This specification also preserves the existing decisions separating facts from inference, separating research/evidence/acquisition times, qualifying operations rather than provider brands, limiting experimental sources, and scoping optional dependencies by capability.
- Prototype branch `codex/prototype-intraday-replay-sources` at commit `fa73bbd` is evidence only. In an isolated `mootdx==0.11.7` environment on 2026-08-04, candidate `bars(frequency=8)` calls returned recent one-minute-shaped rows for both `SSE:600519` and `SZSE:000001`, including 20 recent dates with 240 timestamps per date after pagination.
- The same prototype also found unresolved production blockers: pages arrive newest-first and require deterministic sorting/deduplication; price scaling must be normalized; volume appeared to require a candidate lots-to-shares conversion; open/close and auction bucketing did not always align with daily aggregates; timestamp interval meaning and units were not proven by the library contract; and no independent daily source was qualified. These observations justify a candidate transport, not source admission.
- The prototype's terminal interface is deliberately disposable and must never be merged into the product. No raw provider response, server address, credential, cache, or user provider configuration was retained.
