# Intraday contracts

Keep a current-session snapshot, a completed-session minute series, a deterministic replay summary, Agent replay analysis, and future scenarios as five distinct concepts.

## `intraday_market_signal`

Use exactly one canonical SSE/SZSE A-share subject, the current China Standard Time trading date, `window: null`, empty `parameters`, and `source_policy.allow_experimental: true`. This is one research-grade snapshot, not a minute stream, continuous feed, order book, tick source, or trading signal.

The released operation uses default-installed `mootdx==0.11.7` and a Tencent cross-check. Preserve trading session, price type, each source observation time, latest/open/high/low, previous-close semantics, cumulative volume and amount, units, field lineage, conflicts, and limitations. A source or installation failure does not trigger a fallback. A valid `limited` or `blocked` result still exits zero.

For v0.4.0, `tencent_intraday_snapshot@1` and `tongdaxin_intraday_snapshot@1` retain their interfaces, installation/load checks, offline behavior, and failure boundaries. This delivery does not perform real-time intraday verification. Do not treat an installation, synthetic regression, or historical probe as current-candidate live success, and do not interpret missing real-time verification as withdrawn functionality.

Template: [`../assets/requests/intraday-market-signal.json`](../assets/requests/intraday-market-signal.json).

## `intraday_replay`

Use exactly one canonical SSE/SZSE A-share and one explicit completed trading date. Set `window.observed_from` and `window.observed_to` to that same date, and enable experimental sources. The default-installed `mootdx==0.11.7` operation returns normalized minute rows, exchange-session stages, coverage, a daily-boundary cross-check, and a recomputable summary.

One operation must form the complete minute series on one adjustment and timestamp basis; another operation checks security identity, trading date, daily OHLC, close, total volume, and amount. Never splice minute fields across sources. A daily-boundary conflict blocks the replay conclusion. Missing auction semantics, timestamps, calendar, price, volume, or amount remain explicit. The operation stays experimental and can never make the result `supported` by itself.

Template: [`../assets/requests/intraday-replay.json`](../assets/requests/intraday-replay.json).

## Agent analysis and scenarios

The deterministic replay JSON does not add prediction fields, direction claims, probabilities, or Agent views. A historical query or replay request is not a prediction request. Replay analysis must explain only the completed path.

Only when the user explicitly asks for a future judgment may the Agent consider an evidence-constrained scenario. Apply [analysis-boundary.md](analysis-boundary.md) and require canonical identity, a non-blocked replay with a usable close or closing stage, 20 complete daily trading sessions through the replay date, aligned security/date/unadjusted-close semantics, and no unresolved core source conflict. Refuse when coverage is indeterminate, the close is missing, the whole morning or afternoon is absent, daily evidence is incomplete, or the day is a confirmed suspension.

For an accepted request, separate the next trading day from the next 5 trading days. For each horizon choose a primary continuation, range, or reversal scenario, then state upside and downside alternatives, evidence, opposing evidence, assumptions, observable triggers, invalidation, and uncertainty. Missing optional news, announcement, flow, or event context limits the explanation to price behavior; it never licenses an invented cause. Do not provide exact probabilities without a real out-of-sample model, ratings, targets, position sizing, or automatic trading instructions.

## Maintainer live probes

Live probes live in the development repository, not the installed Skill, and are never an ordinary CI dependency:

```text
<python> tests/live_probe_intraday.py --confirm-live --as-of YYYY-MM-DD
<python> tests/live_probe_intraday_replay.py --confirm-live --as-of YYYY-MM-DD --replay-date YYYY-MM-DD
```

Each covers one SSE and one SZSE A-share, uses an ephemeral home, and emits only allow-listed diagnostics. Maintainers must never update fixtures, persist provider responses, credentials, server details, or raw payloads from a probe. One successful probe does not qualify an experimental operation for production.
