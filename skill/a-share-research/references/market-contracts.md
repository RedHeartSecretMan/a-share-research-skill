# Market contracts

Read this reference for `security_identity`, `market_trend`, `etf_market`, and `etf_options`. Every task uses one explicit China Standard Time research date and the common request envelope from [cli-contract.md](cli-contract.md). Subject-free market overview uses [workflow-contracts.md](workflow-contracts.md); its unsupported whole-market turnover and margin slots do not withdraw the security-scoped price/amount or margin-material contracts.

## `security_identity`

Use one subject clue such as `{"clue":"工业富联"}` or `{"clue":"601138"}`, `window: null`, and empty `parameters`. The clue is not identity. The runtime cross-checks applicable SSE/SZSE observations and returns a canonical subject such as `SSE:601138`; ambiguity, exchange conflict, unsupported security type, or BSE input fails closed. Dependent research may use only that canonical subject.

Template: [`../assets/requests/security-identity.json`](../assets/requests/security-identity.json).

## `market_trend`

Use one A-share clue, `window: {"trading_days": N}` with `N` from 2 through 250, and `parameters.adjustment` equal to `unadjusted` or `forward_adjusted`. The result preserves each retained trading date and applicable daily owner, then calculates cumulative return, maximum drawdown, annualized volatility, up/down sessions, and volume change from the adopted series.

Do not mix adjustment bases or treat a provider's returned row limit as the requested completed-session count. Missing or conflicting identity, dates, OHLCV, adjustment, or daily-boundary evidence remains visible and may block the trend.

Template: [`../assets/requests/market-trend.json`](../assets/requests/market-trend.json).

## `etf_market`

Use one six-digit SSE ETF clue, an explicit observation date, `window: null`, and empty `parameters`. The task resolves ETF identity, preserves SSE and cross-check observations, and reports price, change, volume, amount, observation time, agreement, and limitations. Keep source volume units and the lot-to-share conversion/rounding disposition explicit.

Template: [`../assets/requests/etf-market.json`](../assets/requests/etf-market.json).

## `etf_options`

Supported underlying clues are `510050`, `510300`, `510500`, and `588000`. Use one explicit observation date and `source_policy.allow_experimental: true`.

Parameters:

- `view`: `atm` or `chain`.
- `expiry`: `{"mode":"nearest_unexpired"}` or `{"mode":"exact","date":"YYYY-MM-DD"}`.
- `quote_mode`: `latest` or `latest_completed`.

`atm` returns every equally near standard `M` call-put pair in the source-observed set. `chain` returns the selected expiry's bounded standard `M` and adjusted `A` series without merging them. `latest_completed` requires a completed session; `latest` may use an intraday quote.

Provider-reported Delta, Gamma, Theta, Vega, and IV are source observations, not local BSM or exchange calculations. Current coverage is `partial`: there is no authoritative contract total, complete contract-unit definition, adjustment terms, or qualified independent fallback. Preserve quote state, bid/ask/last, volume, open interest, units, source time, `M`/`A` identity, and every limitation.

Template: [`../assets/requests/etf-options.json`](../assets/requests/etf-options.json).
