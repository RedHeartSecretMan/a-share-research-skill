# Evidence contract

Use this contract when turning CLI JSON into a research answer.

## Research boundary

Every request has one explicit research date in China Standard Time. For a historical date, do not use information first available after that date. For the current date, do not cross the actual retrieval time or use an unfinished trading session as a completed close.

Keep these times distinct:

- `as_of`: the request's information boundary.
- `evidence_time`: the time or period described by the evidence.
- `available_at`: when the evidence first became publicly available.
- `retrieved_at`: when the CLI obtained it.

## Claims and evidence

A factual claim must point to one or more evidence items. Each evidence item must identify:

- the security or issuer to which it applies;
- its source role and source locator;
- the normalized observed value and unit;
- its statistical or accounting basis;
- its evidence, availability, and retrieval times;
- any transformation from the observed value to the claim input.

Source roles describe use, not a global provider ranking:

- `authoritative_disclosure`: a regulator, exchange, or issuer's legally relevant disclosure;
- `market_observation`: a provider's stated observation of price, trading, or market state;
- `attributed_opinion`: a statement attributable to a named person or institution;
- `market_signal`: a derived description of attention, behavior, or sentiment.

Preserve applicable conflicting values as separate evidence. Explain the conflict and downgrade the affected result; do not average or silently choose a convenient value.

## Status

Overall research status:

- `supported`: all key claims have applicable, non-stale, source-verified evidence;
- `limited`: the core question can be answered, but a material non-blocking gap, conflict, stale item, access restriction, or source-verification limitation remains;
- `blocked`: identity or core evidence is insufficient for a substantive answer.

Metric status:

- `supported`: the metric is calculable with complete compatible evidence; this metric status does not by itself establish source verification or an overall `supported` brief;
- `no_valuation_meaning`: inputs are complete, but the applicable denominator is less than or equal to zero;
- `not_calculable`: a required input is missing, rejected, or incompatible.

Metric status alone does not determine the brief status. A provided-evidence brief remains `limited` without independent source verification even when all three metrics are calculable. When source verification is complete, all three valid metric results, including `no_valuation_meaning`, can support an overall `supported` brief. If at least one metric is valid and another is `not_calculable`, the brief is `limited`. If identity or the common valuation price is unresolved, or all metrics are `not_calculable`, the brief is `blocked`.

## Numeric integrity

Treat exact values as decimal strings with explicit units. Do not convert monetary amounts, share counts, profits, equity, or ratios through binary floating point. Preserve the calculation operands and formula so the user can reproduce each result.
