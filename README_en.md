<div align="center">

# A-Share Research Skill

**Every research number should carry an identity, time boundary, source, and calculation lineage.**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Public API: run --request](https://img.shields.io/badge/public%20API-run%20--request-0F766E)](skill/a-share-research/references/cli-contract.md)
[![Release: v0.3.0](https://img.shields.io/badge/release-v0.3.0-0F766E)](https://github.com/RedHeartSecretMan/a-share-research-skill/tree/v0.3.0)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-D22128)](LICENSE)

[中文](README.md) · [v0.3.0](#v030-release-status) · [Installation](#installation) · [Common uses](#common-uses) · [Case demos](#case-demos) · [Boundaries](#boundaries) · [Development](#development)

</div>

`a-share-research-skill` is the project repository name; the installable Skill is `$a-share-research`. It takes an evidence-first approach to mainland-China listed-security research: a deterministic Python CLI handles security identity, research time boundaries, evidence validation, and valuation calculations, then an Agent presents the versioned JSON as auditable research material.

The project does not treat “an endpoint returned data” as “the fact is trustworthy.” It provides auditable research analysis only after security identity, research time, provenance, basis, and assumptions are explicit.

## v0.3.0 release status

v0.3.0 closes the implementation and release evidence for the plan-driven research runtime. One public `run --request` entry point serves 12 versioned ResearchTask types, 6 fixed research plans, and 42 executable request examples. The comprehensive single-security and market-overview plans reuse the same narrow tasks; local failures, proven empty sets, conflicts, and unsupported capabilities remain visible in the unified result instead of disappearing behind an aggregate summary.

Final attestation for the frozen candidate completed at `2026-08-18T11:24:17+08:00`: 27/27 release request profiles formed valid public plans, and all 44/44 formal source operations were actually attempted. Formal-operation disposition, default-install loading, and active-request field destinations closed at 44/44, 44/44, and 740/740 respectively. “Attempted” does not mean “the source returned data successfully”: the final record still preserves `complete`, `observed_empty`, `partial`, `credential_blocked`, and `unavailable` source results.

- [Final sanitized live evidence](docs/research/core-v0.3.0-release-probe-evidence.json)
- [Reproducible operation release matrix](docs/research/core-v0.3.0-operation-release-matrix.json)
- [Matrix definitions and maintainer verification](docs/research/core-v0.3.0-operation-release-matrix.md)

## Why it exists

Most data tools optimize for how much they can retrieve. This project asks whether a number is safe to use in a research conclusion:

- **Explicit identity**: security and issuer are separate; names and bare codes are clues, never permission to guess an exchange.
- **Explicit time**: every request is anchored to a China Standard Time date and distinguishes evidence, publication, and retrieval times.
- **Explicit provenance**: factual claims link to evidence and source locators; contract completeness is not source verification.
- **Reproducible calculations**: market capitalization, PE TTM, and PB MRQ use Decimal arithmetic and preserve full calculation lineage.
- **Honest failure**: ambiguity, conflict, staleness, wrong-security payloads, or missing critical evidence return `limited` / `blocked` instead of invented values.

## Capabilities

An intraday market snapshot is one observation on the current China Standard Time trading date; a complete intraday trading-day series is the ordered unadjusted minute transaction record for a completed session; an intraday replay summary is calculated from that series; intraday replay analysis is the Agent's explanation of the completed path; and an evidence-constrained scenario prediction is formed only after an explicit future-judgment request passes its evidence floor. These are distinct capabilities and do not share unqualified fields.

| Capability | Input | Output and boundary |
| --- | --- | --- |
| Security identity resolution | Name, abbreviation, or code clue + explicit date | Cross-checks SSE/SZSE and CNINFO observations; ambiguity, conflicts, and BSE inputs fail closed |
| Latest completed close | Canonical `SSE:code` / `SZSE:code` + explicit date | Cross-checks exchange daily lines and Tencent observations while preserving trading date, basis, and conflicts |
| Recent N-session trend | A-share clue + 2–250 sessions + unadjusted/forward-adjusted basis | Cross-checked OHLCV, cumulative return, drawdown, volatility, up/down sessions, volume change, and corporate actions |
| Intraday market snapshot | Current China Standard Time trading date + one canonical `SSE:code` / `SZSE:code` A-share | One TongdaXin/Tencent experimental cross-check with session, price type, source times, units, conflicts, and explicit `limited` / `blocked` boundaries |
| Complete intraday replay | One canonical SSE/SZSE A-share + one explicit completed trading date | Returns minute records, trading phases, coverage, daily-boundary evidence, replay summary, evidence lineage, and unavailable-field reasons; experimental sources remain at most `limited` |
| ETF market | Six-digit SSE ETF code + explicit date | SSE ETF identity and snapshot, Tencent price cross-check, and explicit board-lot rounding differences |
| ETF options | 510050 / 510300 / 510500 / 588000 + one observation date + ATM/chain, expiry, and quote-time modes | Separate standard `M` and adjusted `A` series, call/put quotes, tied ATM strikes, provider-reported Greeks/IV, four-state coverage, source time, and limitations |
| Automatic security valuation | A-share clue + established security-class count + current China Standard Time date + scenario target PE | Preserves complete numeric rows from all three statements and quarterly series, then acquires current shares and consensus to calculate reported and forward metrics |
| Same-basis valuation comparison | 2–10 unique A-share clues + shared date/target PE | Preserves every requested row with one price and metric basis; unavailable, meaningless, and blocked metrics remain explicit |
| Research-content retrieval | Theme/industry or one A-share + publication window + material types | Stock/industry reports, consensus, F10 issuer-profile material, news, CNINFO/SSE/SZSE announcements, market flashes, and investor Q&A with role, time, document identity, and locators preserved |
| Capital, positioning, and company events | One A-share or a market/board scope + observation window + data types | Northbound disclosure gaps, stock/board fund flow, stock and market dragon-tiger records, 90-day lockups, margin data, block trades, shareholder counts, and distributions with period, unit, direction, and market scope preserved |
| Market themes and trading signals | One A-share clue or market-wide scope + explicit observation date + signal types | Strong-stock themes, security board membership, industry rotation, limit pools, focus monitoring, severe abnormal movements, canonical-identity intersections, and market heat with rules, attribution provenance, four-state coverage, conflicts, and limitations preserved |

Experimental operations can provide observations and expose conflicts, but they have not completed operation-level qualification and cannot establish a `supported` factual claim alone.

Here, **F10 material** means the issuer information conventionally opened through an “F10” entry in Chinese securities-market software. v0.3.0 can retrieve latest notices, company overview, financial analysis, shareholder research, capital structure, capital operations, industry commentary, industry analysis, and company events. These are provider-compiled text materials, not an exchange-standardized dataset, statutory company disclosure, or independently verified company fact.

## How it works

```mermaid
flowchart LR
    A["Natural-language research question"] --> B["SKILL.md<br/>Resolve intent and CST date"]
    B --> C["Deterministic Python CLI"]
    D["Experimental observations"] --> C
    C --> F["Versioned JSON<br/>evidence, calculations, conflicts, limits"]
    F --> G["Agent presents research material"]
```

Research results use three overall states:

- `supported`: every critical factual claim has applicable, source-verified evidence.
- `limited`: the core question remains answerable, but a non-critical gap, conflict, or source limitation must be disclosed.
- `blocked`: identity or critical evidence is insufficient, so substantive conclusions must stop.

## Analysis boundary

The CLI produces evidence, deterministic calculations, status, and limitations. When the result is not blocked and the question needs interpretation, the Agent may add a research judgment, risks, invalidation conditions, conditional trigger levels, and follow-up research suggestions. Direct-evidence questions such as identity checks receive no unrelated judgment; an overall `blocked` result states the gap and the evidence needed to continue.

This section is only a user-facing orientation. The normative rules for research judgments, conditional triggers, attributed external opinions, and investment-action advice are in the installed [`references/analysis-boundary.md`](skill/a-share-research/references/analysis-boundary.md). The researcher remains responsible for the final investment decision.

## Installation

The only installable artifact is [`skill/a-share-research`](skill/a-share-research/). Clone the repository, then copy the entire directory into a compatible Agent's Skill directory; do not copy `SKILL.md` alone.

```text
git clone --depth 1 --branch v0.3.0 --single-branch https://github.com/RedHeartSecretMan/a-share-research-skill.git

<skills-directory>/a-share-research/
├── SKILL.md
├── agents/openai.yaml
├── requirements.lock
├── references/
└── scripts/
```

The core runtime requires Python 3.12 or later. Below, `<python>` means an interpreter already confirmed to be version 3.12 or later: Windows normally uses `py -3.12`; macOS and Linux should prefer `python3.12`, and should use `python3` only after `python3 --version` confirms the requirement. After copying the complete directory, run the one standard installation step. It installs the locked core dependencies from the bundled manifest into that Skill copy's own `.runtime`, so no task-specific package installation is needed, and finishes by checking dependency and operation loads:

```text
<python> <skill-root>/scripts/install.py
```

A successful installation exits zero with JSON `status: "complete"`. Run `<python> <skill-root>/scripts/installation_health.py` at any time to recheck it; a missing, version-mismatched, or unloadable dependency exits nonzero with `status: "installation_incomplete"`. `mootdx==0.11.7` is currently the only direct extra dependency required by released core operations. `requirements.lock` exactly pins the minimum actual runtime closure, including platform-applicable entries, loaded by the released F10, intraday snapshot, and intraday replay operations; it is not the complete declared closure of unpublished or lazy `mootdx` utilities and CLI features. The installer consumes that complete minimum closure without resolving extra packages at install time. A generic upstream-package `pip check` is not a substitute for this Skill's formal-operation installation health. Installation health proves only that artifact-local software files, versions, modules, and operations load. It does not probe or promise source accessibility, credentials, paid permissions, or evidence-adoption qualification; request source policy and research results continue to express those facts.

`mootdx` enters the default install only for the released F10, `intraday_market_signal`, and `intraday_replay` operations. Its other candidate quote interfaces do not become default sources, dependency scope, or fallbacks merely because they ship in the same library. Each source operation must still separately qualify identity, timing, units, failure semantics, and licensing; an unavailable source never triggers a silent switch. See [`references/cli-contract.md`](skill/a-share-research/references/cli-contract.md) for the complete invocation contract.

After installation, validate the public entry point with a versioned example from the repository root:

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-industrial-fulian-single-security.json
```

## CLI

All capabilities use the stable research-task Interface:

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>
```

`research-task.json` is a structured task, not natural-language text. It carries the version, task type, subjects, research date, window, parameters, and source policy. Unknown tasks, policy-disallowed sources, and incomplete core Adapter installations return explicit `blocked` results.

`run --request` is the only supported public invocation. Callers do not need to know source endpoints, internal modules, or historical subcommands. `scripts/entrypoint.py` is the Skill's only public runtime entry point; every other Python module is an implementation detail. The CLI does not process natural language or call a model. `stdout` contains versioned JSON only and `stderr` contains diagnostics only. A valid `limited` or `blocked` result still exits with zero.

Only an explicit `--output <file>` option persists the normalized ResearchResult. Without the evidence-store policy below, the runtime remains stateless and creates no result file, implicit cache, database, lock file, user configuration, or global provider configuration.

To reuse qualified normalized observations across requests, add the exact `evidence_store` object to `source_policy`. `path` must be absolute with an existing real parent, and `mode` is either `read_write` or `delete`:

```json
{
  "allow_experimental": true,
  "allow_credentials": false,
  "allow_fallback": true,
  "evidence_store": {
    "mode": "read_write",
    "path": "/absolute/path/to/research-evidence.json"
  }
}
```

Only `market_trend`, `research_content`, and `security_valuation` accept this policy, and only for historical daily bars, announcements, reports, and published financial statements whose operation explicitly permits reuse. News, flashes, investor Q&A, F10, consensus material, current valuation snapshots, and intraday observations are never served from storage as current facts. A hit still revalidates request scope, security identity, owner, source version, research boundary, freshness, and field destinations; paths, credentials, and complete provider payloads never enter the public result. The exact license list, platform safety conditions, audit fields, and delete semantics are normative in [`references/cli-contract.md`](skill/a-share-research/references/cli-contract.md).

The intraday snapshot uses `task_type: "intraday_market_signal"`, exactly one canonical `SSE:<code>` or `SZSE:<code>` A-share, the current China Standard Time trading date, `window: null`, and `source_policy.allow_experimental: true`. `limited` means the snapshot remains answerable but experimental-source qualification or another disclosed limitation remains; `blocked` means identity, session, timing, or core-source evidence is insufficient. Agent analysis may interpret only a non-blocked result within the returned fields and limitations; it is not a trading feed, prediction, or action instruction.

## Intraday replay scenario prediction (Agent layer)

The deterministic `intraday_replay` JSON contains only source facts, coverage decisions, and calculations; it does not add prediction fields, direction claims, or Agent views. A historical query or replay request is not a prediction request. Only an explicit request for a future judgment lets the installed Skill form an evidence-constrained scenario prediction after presenting replay evidence. The evidence floor is canonical identity, a non-blocked replay with a usable close or closing stage, 20 complete daily trading sessions through the replay date, aligned security/date/unadjusted-close semantics, and no unresolved core conflict. Refuse when coverage is indeterminate, the close is missing, the whole morning or whole afternoon is missing, daily evidence is incomplete, or the day is a confirmed suspension; a bounded partial result is at most `limited` and must list every gap. Give separate next trading day and next 5 trading days horizons, each with one continuation, range, or reversal primary scenario plus upside and downside alternatives, evidence, opposing evidence, assumptions, triggers, invalidation, and uncertainty. If optional context is missing, disclose it and limit the inference to price behavior. Without genuine out-of-sample model validation, do not invent exact probabilities, ratings, targets, or investment actions.

### Preset research plans

`research_workflow` provides six fixed, versioned request plans. They are convenient orchestrations for common research questions, not separate data capabilities, and callers cannot supply custom steps or dependency graphs:

| Plan ID | Research question | Composition |
| --- | --- | --- |
| `single_security_research` | Research one security comprehensively | Identity → unadjusted trend → valuation → subject materials → capital and company events → future lockups → board membership |
| `market_overview_research` | Research a subject-free market overview | Industry-board breadth and limit pools → theme, capital, and flash enhancements |
| `single_security_valuation` | Value one security | Runs the existing `security_valuation` task |
| `valuation_comparison` | Compare several securities on one basis | Runs the existing `valuation_compare` task and preserves every row |
| `theme_report_research` | Find reports about a theme | Runs the existing `research_content` task |
| `new_security_research` | Perform a first systematic review of a security | Identity → institutional coverage → valuation → board membership → fund flow → dragon-tiger → lockup → margin trading |

Every plan inherits the request's research date and source policy and preserves each leaf task's status, evidence, conflicts, source errors, and limitations. `single_security_research` defaults to `completeness_mode: "standard"`, which attempts the core trend and all standard enhancements. Explicit `fast` guarantees only identity and the core trend and records valuation, materials, capital events, lockups, and board membership as `not_requested`. `deep_extensions` accepts only `long_history` and `large_materials`: the former deepens the same trend step to 250 trading sessions, while the latter expands the same materials step to 365 days and a limit of 100. `fast` may combine with `long_history` but rejects `large_materials`. Existing completed-day minute history remains an explicit `intraday_replay` request. This workflow adds no five-level order-book or tick endpoint; those capabilities remain deferred.

`market_overview_research@1.0` accepts no subjects. `standard` declares exactly 2 actual core capabilities, 8 standard enhancements, and 5 explicitly unsupported capabilities. The actual core is limited to per-industry-board change and up/down counts plus limit pools; the standard enhancements reuse the existing industry/theme rotation, heat, focus-monitoring, severe-abnormal-movement, board-flow, market-wide dragon-tiger, northbound-disclosure, and market-flash tasks. `fast` runs only the two actual core capabilities and fixes every standard enhancement at `not_requested`. Trading day/session, major broad indices, whole-market turnover, whole-market margin, and market-wide intraday remain `qualified_market_wide_owner_absent` / `v0.4.0_spec`. Explicit `market_wide_intraday` remains `unsupported` and compiles no child; unknown, duplicate, or legacy-shaped inputs fail closed. The plan never proxies a missing capability with one index or ranking and adds no owner, endpoint, proxy, or #60 five-level/tick path.

The comprehensive-single-security and new-security plans gate dependent work only on canonical identity. Other local source or step failures do not stop independent siblings, but the overall result is never described as complete or supported when required evidence is missing.

## Common uses

Invoke the Skill explicitly with `$a-share-research`. You may say “today” or “current”; the Agent resolves it to a concrete China Standard Time date first. The uses below match the task contracts in the v0.3.0 delivery baseline.

### Identity, market data, and valuation

**Identify the right security**

> Use `$a-share-research` to confirm the exchange and canonical security code for “贵州茅台 (600519),” and tell me the date through which the result is valid.

**Look up the latest close**

> Use `$a-share-research` to find the latest completed unadjusted close for `SSE:600519` as of today, and tell me the sources, whether they agree, and any limitations.

**Inspect an intraday market snapshot**

> Use `$a-share-research` to inspect the research-grade intraday snapshot for `SSE:600519` at the current point today. Preserve the trading session, price type, both source-observation times, latest price, open/high/low, previous-close semantics, cumulative volume and amount, field lineage, conflicts, and limitations. If the session is not applicable or either source lacks sufficient evidence, return `blocked` instead of substituting the latest close.

**Research a recent trend**

> Use `$a-share-research` to research BlueFocus over the latest 10 completed sessions as of today on an unadjusted basis. Include OHLCV, cumulative return, maximum drawdown, volatility, up/down sessions, and volume change, then explain the trend, material risks, and invalidation conditions from that evidence. Label the conclusion as Agent inference; if you state a conditional trigger level, explain its rule and research horizon rather than phrasing it as a trading instruction.

**Look up an ETF market quote**

> Use `$a-share-research` to find the current or latest-completed quote for SSE 50ETF (510050) as of today. Include price, change, volume, amount, observation time, source agreement, and limitations.

**Research ETF options**

> Use `$a-share-research` to show the nearest-unexpired ATM call and put for SSE 50ETF (510050) at the latest completed session. Preserve quote state, bid, ask, last, volume, open interest, provider-reported Greeks/IV, units, observation time, source, and limitations.

> Use `$a-share-research` to show the source-observed option chain for CSI 300ETF (510300) at an exact expiry using latest-completed quotes. Keep standard `M` and adjusted `A` series separate and preserve tied ATM strikes and the contract-total limitation.

> Use `$a-share-research` to show the nearest-unexpired source-observed option chain for CSI 500ETF (510500), allowing the latest intraday quote and stating whether the session and coverage are complete.

> Use `$a-share-research` to show ATM options for STAR 50ETF (588000) at an exact expiry, allowing the latest intraday quote. If the source identifies another ETF, omits contracts, or has no usable quote, block instead of falling back or estimating.

“Provider-reported” means Delta, Gamma, Theta, Vega, and implied volatility come directly from the source; they are neither local BSM calculations by this project nor exchange-calculated values. Provider-native units for Gamma, Theta, and Vega are not independently verified; IV is a decimal fraction. The current source exposes no authoritative contract total, complete contract-unit definition, or adjustment terms and has no qualified independent fallback, so coverage and limitations must remain visible.

**Use the single-security valuation preset**

> Use `$a-share-research` to research Industrial Fulian's valuation as of today. First establish whether the issuer has only one priced ordinary-share class. Use the latest completed unadjusted close and calculate market cap, PE TTM, PB MRQ, first-forecast-year PE, forecast EPS growth, PEG, and the theoretical time to reach 30x PE. Separate mirrored statement observations, consensus opinions, and scenario assumptions, then use an explicit benchmark to explain valuation pressure, key assumptions, and reassessment conditions.

**Use the same-basis valuation-comparison preset**

> Use `$a-share-research` to compare Industrial Fulian, Kweichow Moutai, CATL, Midea Group, and Wuliangye using the same date, unadjusted-close basis, and 30x target PE. Establish each issuer's class scope first and preserve every missing item and limitation instead of dropping a security.

### Research plans and materials

**Use the theme-report preset**

> Use `$a-share-research` to find reports published in the last 90 days about humanoid robots, lead screws, and reducers. List publication time, title, author, source, and PDF locator; merge duplicate documents and keep institutional opinions separate from disclosed facts.

**Use the new-security research preset**

> Use `$a-share-research` to run a complete new-security review for Industrial Fulian: establish identity, then review institutional reports and consensus, current valuation, provider board membership, latest five-session fund flow, dragon-tiger records, scheduled lockups over the next 90 days, and margin trading. Preserve evidence, source time, status, and limitations for every step. If one step is unavailable, continue steps that do not depend on it without describing the workflow as complete or supported.

**Use the market-overview research preset**

> Use `$a-share-research` with `market_overview_research@1.0` in `standard` mode for today's market. Preserve per-industry-board changes and up/down counts, limit pools, themes and market signals, board flow, market-wide dragon-tiger, northbound disclosure, and flashes with each step's local status. Keep session, broad-index, whole-market turnover, whole-market margin, and market-wide intraday gaps explicit instead of proxying them with one index, ranking, or a security-level five-level/tick feed.

**Research a security's announcements and news**

> Use `$a-share-research` to find BlueFocus announcements and stock news from the last 30 days. Prefer CNINFO or exchange original-document locators, explain the timeline, distinguish disclosure from media reporting, and state every remaining evidence gap.

**Review market flashes**

> Use `$a-share-research` to summarize today's market flashes through the current retrieval time, preserving each source and original publication time. Do not interpret source failure as “there was no news.”

**Review investor Q&A**

> Use `$a-share-research` to group the most common themes in BlueFocus investor Q&A over the last 90 days. First propose candidate themes from the raw material, then rerun auditable literal-frequency counts for those labels. Keep question time, company reply time, and original locator separate; present company replies as attributed statements rather than automatically verified facts.

### Capital, events, and market signals

**Review stock and board fund flow**

> Use `$a-share-research` in two separate tasks: review Industrial Fulian's main and order-size fund flows over the latest five completed sessions; then list the ten highest industry-board main inflows for the trading date attached to the provider's current ranking. State the period, amount unit, sign direction, market scope, retrieval time, and whether session completeness is established; do not present provider-defined flow buckets as company fundamentals.

**Check dragon-tiger records and lockups**

> Use `$a-share-research` to check whether BlueFocus appeared on the dragon-tiger list in the last 30 days, including the latest buy/sell top five and institution net amount. In a separate task, check scheduled lockups over the next 90 days. Preserve trigger reason, seat amount unit, released shares, and ratio basis; do not translate a failed or empty source into “none.”

**Review leverage, positioning, and distributions**

> Use `$a-share-research` to review Industrial Fulian's recent margin balances, block trades, shareholder-count changes, and distribution history. Distinguish trading dates, reporting periods, and implementation dates; keep every unit and direction explicit, and do not infer trading intent from shareholder-count changes.

**Check the northbound disclosure boundary**

> Use `$a-share-research` to explain which northbound metrics remain verifiable under the current disclosure regime and which net-flow fields are unavailable. Missing values must remain disclosure gaps and must never be converted to zero.

**Research themes, board membership, and industry rotation**

> Use `$a-share-research` in separate tasks to review strong securities and source-attributed theme reasons for the latest completed session, the provider boards currently associated with BlueFocus, and the day's industry performance ranking. Keep editorial reasons, board membership, and market snapshots distinct; do not present a theme tag as company fundamentals.

**Review limit pools, monitoring, anomalies, and heat**

> Use `$a-share-research` in separate tasks to review the latest completed session's limit-up, break, limit-down, and consecutive-limit ecology, the current provider focus-monitoring pool, severe abnormal movements with rule codes, and current market heat. Form a monitoring intersection only from matching canonical security identity and an overlapping monitoring window; never translate source failure or incomplete coverage into “none.”

These presets add no data shortcut: each runs a versioned plan of existing ResearchTasks, and the overall `limited` / `blocked` state must be presented together with step-level gaps. The future lockup step in the new-security plan uses its own explicit window capped at 90 days.

## Case demos

The cases start from real user research questions. BlueFocus cross-explains a 10-session trend with dragon-tiger, lockup, board, announcement, and news evidence; Industrial Fulian uses the new-security preset across identity, institutional material, valuation, boards, fund flow, dragon-tiger, lockups, and margin trading:

- [BlueFocus (SZSE:300058)](examples/bluefocus.md)
- [Industrial Fulian (SSE:601138)](examples/industrial-fulian.md)
- [Research-grade intraday snapshot (SSE:600519)](examples/intraday-snapshot.md)
- [Complete SSE-session intraday replay (SSE:600519)](examples/intraday-replay-sse.md)
- [Complete SZSE-session intraday replay (SZSE:000001)](examples/intraday-replay-szse.md)
- [Evidence-constrained intraday replay scenarios](examples/intraday-replay-prediction.md)

The BlueFocus and Industrial Fulian cases keep fixed records anchored to `2026-08-02` and separately label the status and gaps of `2026-08-03` real-source smoke runs; the intraday case records only the request contract and stores no provider response. A rerun must use a new explicit research date and the evidence returned by the CLI.

## Boundaries

The current version is deliberately conservative:

- Network identity and close tracers cover SSE and SZSE only; BSE never falls back to another market.
- Free network operations are experimental sources, not production-qualified Adapters.
- Automatic valuation currently accepts only the current research date; shares, statements, and consensus operations remain experimental, so results are at most `limited`.
- The share count is a current observation, not an independently verified effective event; statements are provider-mirror observations whose correction/replacement semantics remain unqualified.
- Security-class count is never silently assumed. Unknown scope or A/H, A/B, or other multi-class issuers block issuer-wide valuation.
- Consensus is aggregated opinion, not a reported company fact; target PE is a user scenario input, not a fair-value conclusion.
- Reports, news, announcements, flashes, investor Q&A, and F10 material currently use experimental sources, so results are at most `limited`. F10 is a provider-compiled current snapshot whose publication time, document identity, and version-replacement semantics remain unqualified. A PDF locator does not prove download or parsing; only an explicit document-verification run may claim retrieval was checked.
- Fund flow, dragon-tiger records, lockups, margin data, block trades, shareholder counts, and distributions also use experimental sources. Provider-derived fund direction is a market signal, not authoritative disclosure. A rolling board metric keeps `period.start: null` when the first session is not exposed; a source with unknown first-availability time is usable only for research on its current retrieval date, never as a historical backtest input. When the post-19-August-2024 regime does not expose the old daily northbound net-buy metric, the task blocks explicitly instead of inserting zero.
- Themes, board membership, industry rotation, limit pools, monitoring, abnormal movement, and heat also use experimental sources. A provider watchlist is not an official exchange list, and editorial reasons or popularity labels do not prove causality or fundamentals. Only a completely collected zero pool is `observed_empty`, and provider-local codes cannot establish a monitoring intersection.
- ETF options currently cover experimental snapshots for 50ETF, 300ETF, 500ETF, and STAR 50ETF only. Provider-reported Greeks/IV are neither local-model nor exchange calculations; authoritative contract totals, contract units, adjustment terms, and an independent fallback remain unavailable. Preserve `M` / `A` series, quote state, units, timing, source, and coverage.
- Theme-report research defaults to exact title-keyword filtering over the Eastmoney market-wide report feed; this is not semantic search and does not prove a complete theme universe. Semantic iWencai search is an optional enhancement when source policy permits it and reads credentials only from `IWENCAI_API_KEY`; values never enter request JSON or output.
- Research-grade snapshots for one SSE/SZSE A-share, completed-session minute-level `intraday_replay`, and exchange ETF snapshots are supported. Current-session minute streams, five-level order books, ticks, continuous feeds, automated trading, news-sentiment scoring, full-company profiles, and open-ended batch screening remain unsupported.
- Research analysis and advice follow the installed [`analysis-boundary.md`](skill/a-share-research/references/analysis-boundary.md); this README does not define a second operational policy.

### Versions and release evidence

- [`CONTEXT.md`](CONTEXT.md) defines the current product domain and terminology. [`Spec 0002`](docs/specs/0002-trustworthy-a-share-research-foundation.md) through [`Spec 0005`](docs/specs/0005-research-grade-intraday-snapshot.md) preserve the v0.0.1–v0.2.0 evolution contracts.
- [`Spec 0006`](docs/specs/0006-research-grade-intraday-replay.md) defines completed-session intraday replay. [`Spec 0007 Amendment A`](docs/specs/0007-implementation-amendment-a.md) defines the core-v0.3.0 unified runtime, plans, and release gates.
- The [`v0.2.0 release audit`](docs/research/v0.2.0-release-audit-2026-08-03.md) remains a historical baseline. v0.3.0 final sanitized evidence and its derived matrix close 27/27 request profiles, 44/44 formal operations, and 740/740 active-request field destinations for the frozen candidate. One successful source response still does not qualify an operation for production.

## Repository layout

```text
skill/a-share-research/        sole installable artifact
tests/                         offline contract, regression, and distribution tests
examples/                      versioned requests and real-security research cases
docs/adr/                      architecture decisions
docs/research/                 time-anchored source feasibility research
docs/specs/                    product and implementation specifications
CONTEXT.md                     domain language and boundaries
```

## Development

Default tests are fully offline. Live-source probes are opt-in diagnostics and are not part of the ordinary CI gate.

```text
<python> -m unittest discover -s tests -p "test_*.py"
ruff check .
ruff format --check .
mypy skill/a-share-research/scripts
<python> /path/to/skill-creator/scripts/quick_validate.py skill/a-share-research
```

The live-source diagnostic entry point is `tests/live_probe_close.py`. The maintainer-only intraday dual-exchange probe is `tests/live_probe_intraday.py` and must be explicitly confirmed with a date:

```text
<python> tests/live_probe_intraday.py --confirm-live --as-of YYYY-MM-DD
```

It covers one SSE and one SZSE A-share and emits only a dated observation report with source identity, timing, session, price agreement, units, status, and sanitized failures. It is excluded from ordinary CI, writes no fixtures, persists no provider response, accepts no credentials, and creates no project-owned global configuration.

The complete intraday replay probe is `tests/live_probe_intraday_replay.py`; it additionally requires `--replay-date YYYY-MM-DD`. It covers one SSE and one SZSE A-share, uses an ephemeral home, emits only allow-listed sanitized statuses and failure codes; operators must never update fixtures, persist provider responses, or promote one successful observation to production qualification.

All versioned request examples are indexed in [`examples/README.md`](examples/README.md). These six entries cover the most common narrow tasks and comprehensive plans; a live run still preserves that run's source states and limitations:

```text
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-10-day-trend.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/intraday-market-snapshot.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/intraday-replay-sse.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-industrial-fulian-single-security.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/workflow-market-overview-research.json
<python> skill/a-share-research/scripts/entrypoint.py run --request examples/requests/bluefocus-f10.json
```

`intraday-market-snapshot.json` records the v0.2.0 release-date request shape. Before a later run, copy it and replace `as_of` with that run's current China Standard Time date; historical dates, non-trading dates, and non-applicable sessions intentionally return `blocked`. Without credentials, `theme-report-search.json` uses the limited Eastmoney title-keyword baseline. When source policy permits a local `IWENCAI_API_KEY`, iWencai is only an optional enhancement; no request may reuse or expose that value. `bluefocus-f10.json` exercises the default-installed `mootdx==0.11.7` F10 operation; missing or mismatched software is `installation_incomplete` health, while source-access failure remains a research-result fact.

The dated results and environment limitations for all eight market-signal scenarios are recorded in the [2026-08-02 live smoke record](docs/research/market-signals-smoke-2026-08-02.md).

## License and provenance

The project is licensed under [Apache-2.0](https://github.com/RedHeartSecretMan/a-share-research-skill/blob/main/LICENSE). It originated from [`simonlin1212/a-stock-data`](https://github.com/simonlin1212/a-stock-data) and retains its copyright and attribution; the current implementation is rebuilt around an evidence-first contract and maintained as an independent project.
