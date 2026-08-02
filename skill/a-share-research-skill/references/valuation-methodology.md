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
