# Offline intraday replay case: SSE:600519

This is a fixture-only delivery case. It is not a provider response and contains no server address, credential, cache, or user configuration. The same public request seam is used for the installed Skill:

```json
{
  "schema_version": "1.0",
  "task_type": "intraday_replay",
  "subjects": [{"security": "SSE:600519"}],
  "as_of": "2026-08-04",
  "window": {"observed_from": "2026-08-03", "observed_to": "2026-08-03"},
  "parameters": {},
  "source_policy": {
    "allow_experimental": true,
    "allow_credentials": false,
    "allow_fallback": false
  }
}
```

The versioned request is [intraday-replay-sse.json](requests/intraday-replay-sse.json). For offline acceptance, the repository fixture CLI can be run with `A_SHARE_INTRADAY_REPLAY_SCENARIO=summary_metrics`; this produces deterministic partial coverage and an unavailable daily boundary. The `complete` fixture path separately proves the full expected session denominator and is used by the public replay tests.

If the installed environment does not contain `mootdx==0.11.7`, running the same request through `entrypoint.py run --request` returns a structured `blocked` result with `missing_optional_dependency`; it does not switch sources. A separate `security_identity` request remains available in that environment, so the missing capability dependency does not widen the block.

## Evidence-shaped output

The fixture's raw records include source timestamp, timestamp semantics, trading phase, trade state, OHLC, volume, amount, and an evidence locator. Representative raw rows are:

| raw source timestamp | phase/state | raw OHLC | raw volume/amount | locator |
| --- | --- | --- | --- | --- |
| `2026-08-03T09:30:00+08:00` | `continuous_morning` / `traded` | `10.00/10.20/9.90/10.10` | `100 shares` / `1000.00 CNY` | `fixture:summary:0930` |
| `2026-08-03T09:32:00+08:00` | `continuous_morning` / `no_trade` | unavailable | `0 shares` / `0.00 CNY` | `fixture:summary:no-trade-0932` |
| `2026-08-03T15:00:00+08:00` | `closing_auction` / `traded` | `10.00/10.00/10.00/10.00` | `50 shares` / `500.00 CNY` | `fixture:summary:closing` |

The normalized `records` retain explicit `+08:00` interval boundaries, `CNY/share`, shares, CNY, auction separation, and `evidence_ids`; the no-trade row retains unavailable OHLC rather than a synthetic flat price. The `coverage` result is `partial` for this bounded fixture path, reports the lunch break as excluded, and exposes missing intervals plus a reproducible ratio. The `complete` path reports `complete`, 237 expected continuous minutes, 237 covered minutes, and separate opening/closing auction results.

The recomputable `summary` exposes metrics such as VWAP, high/low ties, maximum drawdown, adjacent rise/fall, and morning/afternoon shares with formulas, operands, units, rounding, scope, and `evidence_ids`. This fixture path explicitly reports `unavailable_fields` / `unavailable_metrics` for opening gap, relative return, and relative range because no independent daily boundary operation is configured. It does not replace those fields with zero.

The evidence lineage identifies `fixture_intraday_replay@1`, the request date, source timestamps, source fields, and deterministic calculations. Limitations include fixture-only evidence, experimental-source qualification, bounded partial coverage, and missing independent daily cross-check. These limitations keep the result `limited` and prevent it from being cited as a current market fact.

## Prediction and refusal boundary

An ordinary query or replay remains evidence-only. Only an explicit future-judgment request may use the installed Agent guidance after canonical identity, a usable close, 20 complete daily sessions, aligned unadjusted semantics, and no core conflict are demonstrated. If coverage is `indeterminate`, the close or either whole session is missing, daily evidence is incomplete, or suspension is confirmed, return `blocked` and state the evidence needed. This fixture's partial path is not sufficient for a fully supported prediction.

When the evidence floor is met, the response has two independent horizons: `next trading day` and `next 5 trading days`. Each has one `primary` continuation/range/reversal scenario, an `upside` alternative, and a `downside` alternative, each with evidence basis, opposing evidence, assumptions, triggers, invalidation, and uncertainty. Facts, calculations, replay analysis, and prediction stay separate. No exact pseudo-probability, rating, target price, or investment action is produced.
