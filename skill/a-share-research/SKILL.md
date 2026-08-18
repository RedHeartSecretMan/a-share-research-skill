---
name: a-share-research
description: Research A shares and SSE ETFs through an evidence-first deterministic CLI. Use for canonical identity, prices and trends, current intraday snapshots, completed-session intraday replay, ETF or ETF-option quotes, valuation and comparisons, research materials including F10, capital events, market signals, single-security or market-overview plans, and evidence-backed interpretation with explicit dates, provenance, calculation lineage, conflicts, coverage, and limitations.
---

# A-Share Research

Use the bundled CLI to gather and calculate evidence, then answer only within the returned identity, time, provenance, coverage, and status boundaries.

## Research sequence

1. Define the exact question and resolve every relative date to an explicit China Standard Time `YYYY-MM-DD`. Completion: the question, subject scope, time boundary, and requested outputs are explicit.
2. Select the narrowest matching route below, then read [references/cli-contract.md](references/cli-contract.md) for its exact request shape. Completion: `task_type`, subject scope, window, parameters, and source policy match the question exactly.
3. Treat a name, abbreviation, or bare code as a clue. Use only the canonical subject returned by identity resolution. If resolution needs a user choice, ask for it; if identity is blocked, stop dependent research. Completion: the subject is canonical or the unresolved identity is reported as the blocker.
4. Invoke the public entry point resolved relative to this file:

   ```text
   <python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>
   ```

   The CLI accepts structured JSON, not natural language. The standard Skill install runs `scripts/install.py` once and must finish with installation-health `status: "complete"`; `installation_incomplete` means the locked core dependency installation must be repaired before research. Add `--output <file>` only when the caller explicitly requests persistence. Follow [references/cli-contract.md](references/cli-contract.md) for the exact commands, platform-neutral Python selection, request shapes, installation boundary, task-specific branches, and process semantics. Completion: one versioned JSON result is parsed from `stdout`, or a nonzero invocation/protocol failure is reported separately from research evidence.
5. Confirm the returned `task_type` and scope answer the user's question. If the result is narrower, present only what the CLI formed. Completion: no absent result field or wider capability has been inferred.
6. Apply [references/evidence-contract.md](references/evidence-contract.md) to facts, calculations, evidence locators, conflicts, source errors, coverage, and overall status. A zero-exit `limited` or `blocked` result is valid research output; a nonzero exit is not. Completion: every material claim is traceable and every limitation that could change the answer is visible.
7. For valuation, also read [references/valuation-methodology.md](references/valuation-methodology.md) before explaining a metric. Completion: every reported metric keeps its date, basis, formula or status, operands, units, and lineage.
8. Read [references/analysis-boundary.md](references/analysis-boundary.md) when the question calls for interpretation, could produce a research judgment or conditional trigger, or includes an external rating or target. For identity-only lookup and other direct evidence questions, answer the evidence without adding an unsolicited judgment. For an overall `blocked` result, report the blocking gap and the evidence needed to continue. Completion: interpretation is either appropriately omitted or satisfies every applicable boundary rule.
9. Lead with the direct answer and overall status. Name the canonical subject and date, present every requested field with its unit or explicit status, place evidence locators near supported claims, and separate source facts, project calculations, attributed opinions, market signals, and Agent inference. Completion: the response answers every supported part of the request without hiding conflicts, gaps, or blocked workflow steps.

## Task routing

| User intent | Route | Routing boundary |
| --- | --- | --- |
| Identify one A share | `security_identity` | Resolve identity before dependent research; never infer an exchange from code shape. |
| Close, trend, ETF, or ETF options | `market_trend`, `etf_market`, or `etf_options` | Preserve each task's security type, date, adjustment, quote, and coverage contract. |
| Current-session snapshot | `intraday_market_signal` | Exactly one canonical SSE/SZSE A share on the current China Standard Time trading date; this is one research snapshot, not a minute stream or action signal. |
| Completed-session minute replay | `intraday_replay` | Exactly one canonical SSE/SZSE A share and one explicit completed trading date. |
| Valuation or same-basis comparison | `security_valuation` or `valuation_compare` | Establish issuer security-class scope and preserve unavailable, meaningless, limited, and blocked metrics. |
| Materials, capital events, or signals | `research_content`, `capital_events`, or `market_signals` | Keep an explicit valuation, announcement or other material class, capital event, or signal on its documented narrow task. |
| General one-security review | `single_security_research@1.0` | Use for core trend plus multiple subject enrichments. Its only deep extensions are `long_history` and `large_materials`; it adds no five-level order-book or tick endpoint. |
| Subject-free market overview | `market_overview_research@1.0` | Preserve the fixed 15-entry inventory, including all five unsupported gaps. Completion for this branch: subjects are empty, `standard` or `fast` is valid, and every local step gap remains visible without widening a capability. |
| Fixed valuation, comparison, report, or new-security plan | The corresponding workflow in the CLI contract | Callers never supply steps, a dependency graph, or a nested workflow. |

## Core guardrails

- Preserve the CLI's canonical identity. Resolve ambiguity instead of inferring an exchange from code shape or a provider-local identifier.
- Preserve time boundaries. Use no evidence first published after the research boundary, and distinguish evidence time, publication time, and retrieval time.
- Preserve source roles. Cross-source agreement may expose consistency but does not by itself make an experimental observation source-verified.
- Preserve calculation lineage. Never replace total shares with float shares, reported profit with a forecast, one price-adjustment basis with another, or project calculations with provider-computed ratios.
- Preserve meaning and coverage. Keep unavailable, stale, rejected, conflicting, `no_valuation_meaning`, partial, indeterminate, and blocked states explicit; never turn source failure into zero or an empty market pool.
- Preserve workflow lineage. Show every step, status, result, or skip reason, and do not promote a leaf result or hide a blocked step behind an overall summary.
- Preserve credential boundaries. Pass permitted credentials only through documented environment variables; never place them in arguments, request JSON, output, diagnostics, or saved artifacts.
- Never invent a metric status or field that is absent from the CLI JSON.

## Intraday interpretation boundary

The default installation includes locked `mootdx==0.11.7` for the released F10, `intraday_market_signal`, and `intraday_replay` operations. Source inaccessibility remains evidence state and never triggers a silent source switch. A `limited` snapshot may support a clearly labelled **Agent inference** within returned evidence; a `blocked` snapshot permits no research judgment. Never infer a trend, cause, target, or buy/sell action from one snapshot.

A complete intraday trading-day series is the ordered unadjusted minute transaction record for one completed exchange session. An intraday replay summary is calculated from that record. An intraday replay analysis is the Agent's explanation of the completed path. An evidence-constrained scenario prediction is formed only after an explicit future-judgment request passes the evidence floor. Keep all four distinct from a current intraday market snapshot.

The deterministic `intraday_replay` result does not add prediction fields, direction claims, probabilities, or Agent views. A historical query or replay request is not a prediction request. Before any prediction, require canonical identity, a non-blocked replay with a usable close or closing stage, 20 complete daily trading sessions through the replay date, aligned security/date/unadjusted-close semantics, and no unresolved core source conflict. Refuse when coverage is indeterminate, the close is absent, the whole morning or whole afternoon is absent, daily evidence is incomplete, or the day is a confirmed suspension.

For an accepted explicit request, separate the next trading day and next 5 trading days. For each horizon choose one primary category from continuation, range, or reversal, then give an upside and downside alternative. State evidence, opposing evidence, assumptions, observable triggers, invalidation, and uncertainty. Keep facts, calculations, replay analysis, and prediction visibly separate; absent optional context limits the inference to price behavior and never licenses an invented cause.
