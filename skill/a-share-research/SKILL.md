---
name: a-share-research
description: Research A shares and SSE ETFs through an evidence-first deterministic CLI. Use for canonical security identity, prices and trends, intraday market snapshots, ETF or ETF-option quotes, valuation and comparisons, research materials including F10, capital events, market signals, or preset research plans when explicit dates, provenance, calculation lineage, conflicts, coverage, limitations, and evidence-backed interpretation matter.
---

# A-Share Research

Use the bundled CLI to gather and calculate evidence, then answer only within the returned identity, time, provenance, coverage, and status boundaries.

## Research sequence

1. Define the exact question and resolve every relative date to an explicit China Standard Time `YYYY-MM-DD`. Completion: the question, subject scope, time boundary, and requested outputs are explicit.
2. Select the matching `ResearchTask` from [references/cli-contract.md](references/cli-contract.md). Use `security_identity` for identity-only questions; route prices and trends, current-date intraday snapshots, completed-day `intraday_replay`, ETF or ETF options, valuation, research materials, capital events, market signals, and preset plans only to their documented task types. Completion: `task_type`, subject scope, window, parameters, and source policy match the question exactly.
3. Treat a name, abbreviation, or bare code as a clue. Use only the canonical subject returned by identity resolution. If resolution needs a user choice, ask for it; if identity is blocked, stop dependent research. Completion: the subject is canonical or the unresolved identity is reported as the blocker.
4. Invoke the public entry point resolved relative to this file:

   ```text
   <python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>
   ```

   The CLI accepts structured JSON, not natural language. Follow [references/cli-contract.md](references/cli-contract.md) for the exact commands, platform-neutral Python selection, request shapes, task-specific branches, optional dependencies, and process semantics. Completion: one versioned JSON result is parsed from `stdout`, or a nonzero invocation/protocol failure is reported separately from research evidence.
5. Confirm the returned `task_type` and scope answer the user's question. If the result is narrower, present only what the CLI formed. Completion: no absent result field or wider capability has been inferred.
6. Apply [references/evidence-contract.md](references/evidence-contract.md) to facts, calculations, evidence locators, conflicts, source errors, coverage, and overall status. A zero-exit `limited` or `blocked` result is valid research output; a nonzero exit is not. Completion: every material claim is traceable and every limitation that could change the answer is visible.
7. For valuation, also read [references/valuation-methodology.md](references/valuation-methodology.md) before explaining a metric. Completion: every reported metric keeps its date, basis, formula or status, operands, units, and lineage.
8. Read [references/analysis-boundary.md](references/analysis-boundary.md) when the question calls for interpretation, could produce a research judgment or conditional trigger, or includes an external rating or target. For identity-only lookup and other direct evidence questions, answer the evidence without adding an unsolicited judgment. For an overall `blocked` result, report the blocking gap and the evidence needed to continue. Completion: interpretation is either appropriately omitted or satisfies every applicable boundary rule.
9. Lead with the direct answer and overall status. Name the canonical subject and date, present every requested field with its unit or explicit status, place evidence locators near supported claims, and separate source facts, project calculations, attributed opinions, market signals, and Agent inference. Completion: the response answers every supported part of the request without hiding conflicts, gaps, or blocked workflow steps.

## Core guardrails

- Preserve the CLI's canonical identity. Resolve ambiguity instead of inferring an exchange from code shape or a provider-local identifier.
- Preserve time boundaries. Use no evidence first published after the research boundary, and distinguish evidence time, publication time, and retrieval time.
- Preserve source roles. Cross-source agreement may expose consistency but does not by itself make an experimental observation source-verified.
- Preserve calculation lineage. Never replace total shares with float shares, reported profit with a forecast, one price-adjustment basis with another, or project calculations with provider-computed ratios.
- Preserve meaning and coverage. Keep unavailable, stale, rejected, conflicting, `no_valuation_meaning`, partial, indeterminate, and blocked states explicit; never turn source failure into zero or an empty market pool.
- Preserve workflow lineage. Show every step, status, result, or skip reason, and do not promote a leaf result or hide a blocked step behind an overall summary.
- Preserve credential boundaries. Pass permitted credentials only through documented environment variables; never place them in arguments, request JSON, output, diagnostics, or saved artifacts.
- Never invent a metric status or field that is absent from the CLI JSON.

## Intraday market-signal boundary

Use `intraday_market_signal` only for one canonical `SSE:<code>` or `SZSE:<code>` A-share on the current China Standard Time trading date. Set `window` to `null` and explicitly allow experimental sources. The capability is a single research-grade snapshot, not a minute series, trading feed, or action signal.

The result's `limited` or `blocked` status is valid JSON research output even when the process exits zero. Missing the capability-scoped `mootdx==0.11.7` dependency blocks only this task and any explicitly dependent task; the runtime never silently switches source or turns missing data into zero. Read the evidence, timing, units, conflicts, and limitations before answering.

Agent analysis remains outside the deterministic task: a `limited` result may support a clearly labelled **Agent inference** only within returned evidence and assumptions; a `blocked` result permits no research judgment. Do not infer a trend, cause, target, or buy/sell action from a snapshot.

## Intraday replay boundary

Use `intraday_replay` for one canonical SSE/SZSE A share and one explicit completed trading date. The optional `mootdx==0.11.7` candidate source is experimental forever in this capability: unknown timestamp, calendar, unit, price-basis, or auction semantics remain blocked, while independently usable evidence remains `limited`. The deterministic result does not forecast or recommend action; read `records`, `auction_results`, `coverage`, `daily_boundary`, `summary`, `source_errors`, and `unavailable_fields` before forming any separately labelled research judgment.

Terminology: A complete intraday trading-day series is the ordered unadjusted minute transaction record for one completed exchange session; an intraday replay summary is calculated from that record; intraday replay analysis is the Agent's explanation of the completed path; an evidence-constrained scenario prediction is formed only after an explicit future-judgment request passes the evidence floor. Keep these layers separate from an intraday market snapshot.

Use `--output <file>` only when the caller explicitly asks to persist the normalized ResearchResult. Without that option, `intraday_replay` creates no result file, cache, database, or global provider configuration.

## Intraday replay scenario prediction (Agent layer)

The deterministic `intraday_replay` result does not add prediction fields, direction claims, probabilities, or Agent views. A historical query or replay request is not a prediction request. Only when the user explicitly asks for a future judgment may the installed Skill form an evidence-constrained scenario prediction after presenting the replay evidence; never add that judgment to the CLI JSON.

Before forming a prediction, require canonical identity, a non-blocked replay with a usable close or closing stage, 20 complete daily trading sessions through the replay date, aligned security/date/unadjusted price semantics/close, and no unresolved core source conflict. Refuse with the missing evidence when coverage is indeterminate, the close is missing, the whole morning or whole afternoon is missing, daily evidence is incomplete, or the day is a confirmed suspension. A bounded partial result may be `limited` only when the close remains usable and every gap is listed.

For an accepted request, form separate horizons for the next trading day and the next 5 trading days. For each horizon choose one primary category from continuation, range, or reversal, then add an upside alternative and a downside alternative. Every scenario states its horizon, evidence basis, supporting and opposing evidence, assumptions, observable triggers, invalidation conditions, and uncertainty. Keep facts, calculations, replay analysis, and prediction visibly separate. Optional context may include announcements, news, fund flow, capital events, valuation, industry, or broader-market evidence; when absent, disclose the gap and limit the inference to price behavior without inventing causes or “main force” intent.
