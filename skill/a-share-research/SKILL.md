---
name: a-share-research
description: Research A shares and SSE ETFs through an evidence-first deterministic CLI. Use for canonical security identity, prices and trends, ETF or ETF-option quotes, valuation and comparisons, research materials including F10, capital events, market signals, or preset research plans when explicit dates, provenance, calculation lineage, conflicts, coverage, limitations, and evidence-backed interpretation matter.
---

# A-Share Research

Use the bundled CLI to gather and calculate evidence, then answer only within the returned identity, time, provenance, coverage, and status boundaries.

## Research sequence

1. Define the exact question and resolve every relative date to an explicit China Standard Time `YYYY-MM-DD`. Completion: the question, subject scope, time boundary, and requested outputs are explicit.
2. Select the matching `ResearchTask` from [references/cli-contract.md](references/cli-contract.md). Use `security_identity` for identity-only questions; route prices and trends, ETF or ETF options, valuation, research materials, capital events, market signals, and preset plans only to their documented task types. Completion: `task_type`, subject scope, window, parameters, and source policy match the question exactly.
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
