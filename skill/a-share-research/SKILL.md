---
name: a-share-research
description: Produce auditable A-share and SSE ETF research with a deterministic CLI for canonical identity, OHLCV trends, ETF and ETF-option quotes, valuation, reports, disclosures, capital and company events, market themes, board membership, industry rotation, limit pools, monitoring and abnormal-movement signals, market heat, and caller-provided evidence. Use when a user needs an explicit as-of date, provenance, calculation lineage, conflicts, coverage, and limitations. Do not use for recommendations, price targets, trading, or unsupported live-data claims.
---

# A-Share Research

Use the bundled deterministic CLI to gather and calculate research evidence. Interpret the structured result for the user; do not replace missing evidence with model knowledge or an unsupported estimate.

## Workflow

1. Identify the user's research question. Translate supported work into a versioned `ResearchTask`; state unsupported scope plainly.
2. Resolve relative dates such as “current”, “today”, or “yesterday” in China Standard Time. Pass only an explicit `YYYY-MM-DD` date to the CLI.
3. Treat a name, abbreviation, or bare code as a security clue. Use `security_identity` as a standalone preflight when the user is asking which security they mean. Subject-based research tasks, including `research_content`, `capital_events`, and subject-scoped `market_signals`, accept the clue and perform the same fail-closed resolution internally; use only the canonical subject returned in their result. Ask the user to choose when resolution requires clarification, and never guess the exchange.
4. Choose one research path:
   - For a recent A-share trend, run a `market_trend` task with one security clue, a trading-day window, and explicit `unadjusted` or `forward_adjusted` basis. If an unadjusted window contains a corporate action, stop on the blocked result; do not calculate around it yourself.
   - For an SSE ETF market quote, run an `etf_market` task with its six-digit ETF code clue. Do not route an ETF through A-share identity resolution.
   - For ETF options, run `etf_options` with exactly one supported underlying clue: 50ETF `510050`, 300ETF `510300`, 500ETF `510500`, or STAR 50ETF `588000`. Set `source_policy.allow_experimental` to `true`, select `parameters.view` as `atm` or `chain`, select `parameters.expiry.mode` as `nearest_unexpired` or `exact` with one `date`, and select `parameters.quote_mode` as `latest` or `latest_completed`. Use one explicit observation date. Keep standard `M` and adjusted `A` series separate; for ATM, preserve every equally near standard-series strike rather than choosing one. `chain` is the source-observed bounded set, not a proven authoritative full chain. Present quote availability, session state, bid/ask/last and sizes, volume, open interest, units, observation time, source, and coverage. Treat Delta, Gamma, Theta, Vega, implied volatility, and theoretical value as provider-reported observations, never as local BSM or exchange calculations. Disclose that Gamma/Theta/Vega use unverified provider-native units, IV is a decimal fraction, and authoritative contract totals, complete contract-unit definitions, adjustment terms, and a qualified independent fallback are unavailable.
   - For one current A-share valuation, run `security_valuation` with one clue, a positively established `issuer_security_class_count`, and a positive decimal-string `target_pe`. This task preserves complete numeric rows from the three statement series, produces quarterly snapshots, and acquires a current total-share snapshot and consensus EPS before calculating market capitalization, PE TTM, PB MRQ, forward PE, forecast EPS growth, PEG, and PE digestion years. It only accepts the current China Standard Time research date because its shares and consensus observations are current snapshots.
   - For a same-basis comparison, run `valuation_compare` with 2–10 unique A-share clues, an explicit class count for every subject, and the same `target_pe`. Preserve input order and show unavailable or meaningless metrics instead of dropping rows.
   - For time-bounded research materials, run `research_content`. Choose only the material types needed: `research_report`, `industry_report`, `consensus_material`, `issuer_profile`, `stock_news`, `announcement`, `market_flash`, or `investor_qa`. Use `parameters.limit` from 1 to 100 per material type. Query investor Q&A and F10 profiles separately: Q&A has historical publication times, while F10 is a current snapshot whose publication time is unknown. An F10 request window is still enforced and must include the current retrieval date; the runtime uses that retrieval time instead of inventing a publication time. For auditable Q&A theme counts, pass candidate labels in `parameters.theme_keywords`; if the user asks for open-ended discovery, first retrieve the materials, label proposed themes as model inference, then rerun with those candidate labels for literal counts. Preserve publication time, retrieval time, source role, document identity, and locator. Treat research opinions and investor replies as attributed statements, not established facts.
   - For funds, positions, trading events, and corporate actions, run `capital_events`. Choose `northbound_flow`, `stock_fund_flow`, `board_fund_flow`, `dragon_tiger`, `market_dragon_tiger`, `lockup`, `margin_trading`, `block_trade`, `shareholder_count`, or `dividend`. Stock and board flow use `parameters.period`; keep each returned period, metric unit, direction, and `dimensions.market_scope` visible. Request market-wide rankings without a subject; request stock-specific data with one clue, and run those scopes as separate tasks. Run a future lockup window separately and cap it at 90 days after `as_of`. A rolling board window may expose its trading-day count and end date while leaving `period.start` null; preserve that gap. Sources without an `available_at` are usable only when the research date is the current retrieval date, never for historical backtests. Never convert unavailable northbound disclosure into zero, infer an exchange for an all-market row, or present provider-derived fund-flow buckets as issuer facts.
   - For themes, market pools, monitoring, and attention signals, run `market_signals` with `source_policy.allow_experimental: true`; the current source operations otherwise return `source_policy_not_satisfied`. Choose `strong_stock_theme`, `security_board_membership`, `industry_rotation`, `limit_state`, `focus_monitoring`, `severe_abnormal_movement`, `monitoring_intersection`, or `market_heat`. `security_board_membership` requires one security clue; every other type is market-wide, takes no subject, and cannot be mixed with the subject-scoped type. Use a single explicit observation date for current pool and snapshot sources. For `limit_state`, optionally select unique `limit_states` from `limit_up`, `limit_break`, `limit_down`, and `previous_limit_up`; preserve pool state, rule, trading date, metrics, units, and coverage. For `market_heat`, `parameters.market_heat_period` is `hour` by default and also accepts `day`. Treat `focus_monitoring` as a provider watchlist unless the evidence itself is an applicable exchange disclosure. `monitoring_intersection` is a deterministic overlap of canonical-security focus-monitoring and severe-abnormal-movement evidence; never intersect bare provider codes. Present editorial annotations, source market signals, and model inference as different provenance classes. `observed_empty` proves only that the bounded source pool was completely collected and empty; `partial` and `indeterminate` never mean no signal existed.
   - For experimental-source identity or close research, run `resolve`, then run `close` only with the returned canonical SSE/SZSE identifier.
   - For caller-provided evidence, run `validate-bundle`, resolve every reported contract error, then run `valuation` with the same bundle and research date.
5. Parse the versioned JSON from `stdout`. Confirm `task_type` matches the user's requested capability. After `valuation`, confirm its `research.question` matches the user's requested capability. Do not expect `research.question` from `resolve`, `close`, or `validate-bundle`. If the result covers a narrower question, explain that mismatch and present only what the CLI actually formed.
6. Treat `stderr` and a nonzero exit as invocation, protocol, I/O, or internal failure—not as research evidence. Treat a zero-exit `limited` or `blocked` JSON result as a valid research result.
7. Always disclose when results use experimental source operations. Cross-source agreement can expose consistency or conflict but cannot alone establish a `supported` factual claim.
8. Follow [references/evidence-contract.md](references/evidence-contract.md) when presenting claims, evidence, conflicts, and limitations.
9. Follow [references/valuation-methodology.md](references/valuation-methodology.md) when explaining reported and forward valuation metrics.
10. Read [references/cli-contract.md](references/cli-contract.md) when choosing commands, interpreting protocol fields, or invoking the Skill on Windows, macOS, or Linux.

## CLI

Resolve `<python>` and the script path as described in [references/cli-contract.md](references/cli-contract.md). Use the stable research Interface for new workflows:

```text
<python> scripts/entrypoint.py run --request <research-task.json>
```

The request is structured JSON, not natural language. It includes `schema_version`, `task_type`, `subjects`, `as_of`, `window`, `parameters`, and `source_policy`. Existing commands remain compatibility entry points:

Registered research tasks currently include `security_identity`, `market_trend`, `etf_market`, `etf_options`, `security_valuation`, `valuation_compare`, `research_content`, `capital_events`, and `market_signals`. `market_trend` never silently mixes adjustment bases; ETF options never merge `M` / `A` series or hide tied ATM strikes; valuation comparisons never mix dates or metric definitions; research materials, capital events, and market signals never erase their source role, time boundary, unit, direction, provenance, or coverage.

```text
<python> scripts/entrypoint.py resolve --query <security-clue> --as-of <YYYY-MM-DD>
<python> scripts/entrypoint.py close --security <SSE:code|SZSE:code> --as-of <YYYY-MM-DD>
<python> scripts/entrypoint.py validate-bundle --bundle <bundle-directory>
<python> scripts/entrypoint.py valuation --bundle <bundle-directory> --as-of <YYYY-MM-DD>
```

Treat `scripts/entrypoint.py` as the Skill's only public runtime entry point. Resolve its path relative to this `SKILL.md`; do not invoke implementation modules directly or assume a platform-specific home directory or shell.

The CLI does not interpret natural language or call a model. Do not pass a natural-language research request, credentials, or secrets as arguments or JSON fields. A missing optional Adapter dependency produces an explicit source failure or `blocked` result. Semantic iWencai content search is opt-in: set `source_policy.allow_credentials` and provide its value only through `IWENCAI_API_KEY`; an alternate endpoint may be selected through `IWENCAI_BASE_URL`. A subject-free thematic report search is unavailable without that credential, while stock-specific and provider-industry report discovery remain available through the credential-free source operation. Never place either value in arguments, request JSON, saved output, or diagnostics. F10 content uses the optional `mootdx` dependency and fails explicitly when it is absent.

## Present the result

- Lead with the direct answer and the overall result status.
- Name the security, exchange, issuer, requested date, actual valuation trading date, and retrieval time.
- Present every requested metric with its value and unit or its explicit status.
- Never invent a metric status or field that is absent from the CLI JSON.
- Label source facts, project calculations, and model inference separately.
- Cite the evidence locator supplied by the CLI near the claim it supports.
- For research materials, distinguish authoritative disclosures, market observations, attributed opinions, and market signals. A PDF locator is not proof that its document was downloaded or parsed; disclose document-verification failures.
- For capital events, preserve `period`, `units`, `directions`, market scope, and every disclosure or pagination limitation. A positive fund-flow metric means the named provider bucket's net inflow; it is not a verified motive or recommendation.
- For market signals, preserve `observed_on`, `observed_at`, `available_at`, `period`, `rule`, attribution provenance, market scope, and `brief.coverage`. A source reason or popularity tag is evidence that the source expressed the label, not proof of causality or fundamentals.
- For ETF options, preserve contract month, expiry, `M` / `A` series, call/put type, strike, quote state, every quoted unit and time, provider-reported analytic origin, source, tied ATM candidates, and coverage. Do not present missing or indeterminate contracts as an empty chain.
- List conflicts and unavailable, rejected, stale, or source-unverified evidence explicitly.
- Treat `no_valuation_meaning` as a valid evidence result, not as a missing value.
- When the result is blocked, report the blocking evidence gap and stop; do not improvise a number.
- Do not label a security cheap or expensive and do not give buy, sell, or position-sizing advice.

## Integrity rules

- Never infer an exchange from code prefixes alone after identity resolution fails.
- Never describe an issuer relationship as verified when the resolved candidate reports `issuer.security_relationship` as `unverified`.
- Never use information first published after the research boundary.
- Never treat a provider-computed PE or PB as the project's calculated metric.
- Never describe consensus EPS as a reported fact. Keep its forecast year, aggregation method, institution count, and source limitation visible.
- Never describe `target_pe` or PE digestion years as an objective fair-value conclusion; the target is a user-supplied scenario parameter.
- Never silently substitute float shares for total shares, forecast profit for reported profit, or adjusted price for an unadjusted close.
- Never invent `issuer_security_class_count`. If the issuer's ordinary-share classes or listing venues have not been established, accept the blocked result and request evidence instead of assuming one.
- Never compare or combine adjusted and unadjusted bars. Preserve corporate-action annotations and the exact adjustment basis.
- Never hide disagreement between otherwise applicable sources.
- Never describe a provider watchlist as an official exchange monitoring list without applicable exchange evidence.
- Never derive `monitoring_intersection` from a bare code or provider-local identifier; require matching canonical security identity and cite both basis evidence IDs.
- Never describe provider-reported ETF-option Greeks, IV, or theoretical value as calculations performed by this project or an exchange. Never convert provider-native Gamma, Theta, or Vega into an assumed unit.
- Never expose credentials in output, logs, saved results, or test artifacts.
- Leave the final investment judgment to the researcher.
