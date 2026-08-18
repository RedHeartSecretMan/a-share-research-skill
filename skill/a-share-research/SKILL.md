---
name: a-share-research
description: Research A shares and SSE ETFs through an evidence-first deterministic CLI. Use to resolve A-share identity; inspect prices, trends, current-session snapshots, completed-session replay, ETFs, or ETF options; calculate or compare valuation; retrieve F10 and other research materials, capital events, or market signals; and run fixed single-security or market-overview plans. Do not use for open-ended screening, live trading feeds, automated trading, or personalized investment actions.
---

# A-Share Research

Route the user's question to the narrowest supported ResearchTask, run the bundled deterministic CLI, and answer only within the returned identity, time, evidence, coverage, and status boundaries.

## Execution sequence

1. Make the question explicit. Resolve relative dates to an exact China Standard Time `YYYY-MM-DD`; identify the requested subject scope, horizon, outputs, and whether interpretation is wanted.
2. Select one route from the table below. Read only its linked contract, plus [evidence-contract.md](references/evidence-contract.md). For valuation, also read [valuation-methodology.md](references/valuation-methodology.md). For interpretation, ratings, targets, or conditional triggers, also read [analysis-boundary.md](references/analysis-boundary.md).
3. Copy the linked request template to a writable location. Replace every `REPLACE_*` value, remove no required key, add no undocumented key, and never put credentials in JSON. A name, abbreviation, or bare code is only a clue; dependent work uses the canonical identity returned by the runtime.
4. If the installed runtime has not passed health checking, run `<python> <skill-root>/scripts/install.py` once and continue only after exit code 0 with `status: "complete"`. Then invoke the sole public entry point:

   ```text
   <python> <skill-root>/scripts/entrypoint.py run --request <research-task.json>
   ```

   Add `--output <file>` only when the caller explicitly requests persistence. Exact installation, invocation, process, credential, and JSON semantics are in [cli-contract.md](references/cli-contract.md).
5. Confirm the returned `task_type` and request scope answer the question. If the result is narrower, present only what it formed. No absent result field or wider capability may be inferred.
6. Lead with the direct answer and overall status. Name the canonical subject and research date; keep units and periods attached to values; place evidence locators near material claims; separate source facts, project calculations, attributed opinions, market signals, and Agent inference.

Completion means: no `REPLACE_*` value remains; one valid JSON result was parsed from `stdout` or a nonzero protocol failure was reported separately; every requested supported field was addressed; every conflict, source error, limitation, blocked step, and unsupported capability that could change the answer remains visible; and no undocumented fact, metric, status, source success, or investment action was invented.

## Task routing

| User intent | Route and template | Read next |
| --- | --- | --- |
| Identify one A share | [`security_identity`](assets/requests/security-identity.json) | [market-contracts.md](references/market-contracts.md) |
| Price trend | [`market_trend`](assets/requests/market-trend.json) | [market-contracts.md](references/market-contracts.md) |
| ETF quote | [`etf_market`](assets/requests/etf-market.json) | [market-contracts.md](references/market-contracts.md) |
| ETF options | [`etf_options`](assets/requests/etf-options.json) | [market-contracts.md](references/market-contracts.md) |
| Current-session snapshot | [`intraday_market_signal`](assets/requests/intraday-market-signal.json) | [intraday-contracts.md](references/intraday-contracts.md) |
| Completed-session minute replay | [`intraday_replay`](assets/requests/intraday-replay.json) | [intraday-contracts.md](references/intraday-contracts.md) |
| Valuation | [`security_valuation`](assets/requests/security-valuation.json) | [research-contracts.md](references/research-contracts.md) and [valuation-methodology.md](references/valuation-methodology.md) |
| Same-basis comparison | [`valuation_compare`](assets/requests/valuation-compare.json) | [research-contracts.md](references/research-contracts.md) and [valuation-methodology.md](references/valuation-methodology.md) |
| Materials or F10 | [`research_content`](assets/requests/research-content.json) | [research-contracts.md](references/research-contracts.md) |
| Capital or company events | [`capital_events`](assets/requests/capital-events.json) | [research-contracts.md](references/research-contracts.md) |
| Themes or market signals | [`market_signals`](assets/requests/market-signals.json) | [research-contracts.md](references/research-contracts.md) |
| General one-security review | [`single_security_research@1.0`](assets/requests/workflow-single-security-research.json) | [workflow-contracts.md](references/workflow-contracts.md) |
| Subject-free market overview | [`market_overview_research@1.0`](assets/requests/workflow-market-overview.json) | [workflow-contracts.md](references/workflow-contracts.md) |
| Fixed valuation, comparison, report, or new-security plan | `research_workflow` | [workflow-contracts.md](references/workflow-contracts.md) |
| Explicit cross-request evidence reuse | supported task plus `source_policy.evidence_store` | [persistence-contract.md](references/persistence-contract.md) |

Use a documented narrow task for an explicit valuation, announcement, material class, capital event, or signal. A workflow never accepts caller-defined steps, a DAG, or a nested workflow. `single_security_research` adds no five-level order-book or tick endpoint. For `market_overview_research`, completion additionally requires empty subjects, a valid `standard` or `fast` mode, and all five unsupported v0.3.0 capabilities preserved without proxying them.

## Guardrails

- Preserve canonical identity, research time, source role, adjustment basis, units, calculation operands, workflow lineage, and owner-local coverage.
- Treat zero-exit `limited` and `blocked` as valid research results. A nonzero exit is an invocation or protocol failure, not source evidence.
- Never turn failure, incomplete pagination, indeterminate coverage, or missing disclosure into zero or `observed_empty`.
- Never infer a trend, cause, target, rating, or buy/sell action from one intraday snapshot.
- `intraday_replay` contains deterministic historical facts and calculations. Only an explicit future-judgment request may proceed to evidence-constrained next trading day / next 5 trading days scenarios under [analysis-boundary.md](references/analysis-boundary.md), separating continuation, range, reversal, upside, downside, invalidation, and uncertainty.
- Never expose credentials, headers, provider raw payloads, private paths, or server details. Never invent a metric status or field absent from the CLI JSON.
