# Current valuation methodology

The current valuation evidence brief reports evidence and reproducible calculations. It does not determine whether a security is cheap or expensive.

## Common price

Use the unadjusted close from the latest completed trading session available within the research boundary. Record the trading date, currency, price type, and trading status. On the current date, use that day's close only after the session is complete and the source identifies it as a final daily close.

A suspended security's older close must retain its actual date and suspension limitation. Do not present it as a fresh current price.

## Market capitalization

```text
market_cap = unadjusted_close × effective_total_shares
```

Use ordinary shares effective at the research boundary, including tradable and restricted shares of the modeled class. Do not substitute float shares or an unverified period-end share count. Block issuer-wide valuation for multi-class or multi-market issuers until every relevant class, currency, share count, and price is modeled.

The automatic free-source task currently observes a current total-share snapshot but does not independently establish the underlying corporate-action effective start time. It keeps `effective_at` separate from `observed_at`, marks this limitation, and must not reuse that snapshot for a historical research date. It also requires the task to declare the issuer's security-class count; absence or a value above one blocks issuer-wide valuation.

## PE TTM

```text
ttm_attributable_profit =
    previous_full_year_attributable_profit
  + latest_current_year_cumulative_attributable_profit
  - matching_prior_year_cumulative_attributable_profit

pe_ttm = market_cap / ttm_attributable_profit
```

Use consolidated, reported profit attributable to owners of the parent from periodic reports that were publicly available within the research boundary. A full-year report already covers the TTM period directly. Do not substitute forecast profit, total net profit, or non-recurring-item-adjusted profit without a separately defined metric.

If TTM attributable profit is less than or equal to zero, return `no_valuation_meaning`. If a required period is absent or incompatible, return `not_calculable`.

## PB MRQ

```text
pb_mrq = market_cap / latest_attributable_equity
```

Use the latest publicly available consolidated equity attributable to owners of the parent. Do not use average equity, total equity including non-controlling interests, or parent-company-only equity.

If attributable equity is less than or equal to zero, return `no_valuation_meaning`. If the required value is absent or incompatible, return `not_calculable`.

## Cross-check values

A provider-supplied market cap, PE, or PB may be retained as separate market-observation evidence for comparison. It cannot replace the project's operands, formula, or calculated result. Surface discrepancies rather than silently forcing agreement.

## Consensus and forward metrics

Consensus EPS is a source-aggregated forecast opinion, not a reported company fact. Retain the forecast year, mean EPS, contributing institution count, retrieval time, and source limitation. Require at least two applicable forecast years and at least three institutions in each of the first two years.

```text
forward_pe = unadjusted_close / first_forecast_year_consensus_eps
forecast_eps_growth = next_forecast_year_eps / first_forecast_year_eps - 1
peg = forward_pe / forecast_eps_growth_percent
```

Nonpositive forecast EPS or growth has `no_valuation_meaning` where applicable; missing or inapplicable forecasts are `not_calculable`. Do not substitute reported profit for forecast EPS or silently switch forecast years.

## PE digestion scenario

```text
pe_digestion_years = ln(forward_pe / target_pe) / ln(1 + forecast_eps_growth)
```

Return zero when forward PE is already at or below the user-supplied target. `target_pe` is a scenario assumption, not a factual fair-value anchor, rating, target price, or recommendation. Always show it beside the result.

## Same-basis comparison

Compare two to ten securities using one research date, the latest completed unadjusted close for each security, one metric definition set, and one `target_pe`. Preserve every requested row. A missing forecast, nonpositive denominator, or blocked identity must remain explicit in that row instead of being filtered out or ranked as if comparable.
