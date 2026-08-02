# Market signals live smoke — 2026-08-02

This record captures the live-source acceptance run for GitHub Issue #18. It is a dated observation, not a guarantee that an external endpoint will remain available or return the same pool.

## Environment

- Run date and research date: 2026-08-02, China Standard Time
- Host: Darwin 25.5.0 arm64
- Runtime: Python 3.14.6
- Code state: commit `493381c` plus the uncommitted Issue #18 delivery diff
- Invocation: `python3 skill/a-share-research/scripts/entrypoint.py run --request <request> --output <temporary-json>`
- Network: direct host access to credential-free public endpoints; no proxy credential, API key, or optional data subscription was used
- Raw outputs were kept only as temporary local JSON because they are volatile provider snapshots. The SHA-256 values below identify the exact outputs inspected during acceptance.

## Results

| Scenario | Request | Public result | Coverage | Observations | Acceptance observation | Output SHA-256 |
| --- | --- | --- | --- | ---: | --- | --- |
| Strong-stock themes | `examples/requests/market-strong-stock-themes.json` | `limited` | `observed_nonempty` | 20 | Editorial theme reasons retained their provenance; no source errors or conflicts. | `38be89a6051cc46f2172768cdb190a50e3ced61e1b95a6a5cecb914efb2d68eb` |
| BlueFocus board membership | `examples/requests/bluefocus-board-membership.json` | `limited` | `observed_nonempty` | 20 | The clue resolved to one canonical subject; provider board class remained explicitly unexposed. | `3751fb7bfd6fdb362cf24f9c15d4552673ce0489fbe2153717071c5a630221ad` |
| Industry rotation | `examples/requests/market-industry-rotation.json` | `blocked` | `indeterminate` | 0 | The upstream closed the connection without a response. The runtime retained `upstream_unavailable`; it did not report an empty ranking. | `b78e3667b233d1252a6b05ee07375807e9245c041155141e71dc7a1f9752b2e8` |
| Limit ecology | `examples/requests/market-limit-ecology.json` | `limited` | `partial` | 100 selected from 356 collected | The complete primary pools contained 99 limit-up, 107 limit-break, and 0 limit-down securities; break rate was 51.94174757281553398058252427%, maximum consecutive height was 9. The supplementary editorial-reason source could not prove its provider total, so the combined coverage correctly remained partial. | `0170a99c5e86157368e62d6e5a598024fe95f9a8120bb453df56600d08f4a376` |
| Focus monitoring | `examples/requests/market-focus-monitoring.json` | `limited` | `observed_nonempty` | 17 | Returned records were labelled as a provider watchlist, not an official exchange monitoring list. | `b376b6efcc191f050703ee9932b8910fc7d84046168e8048210d5efe0defcbad` |
| Severe abnormal movements | `examples/requests/market-severe-abnormal-movements.json` | `limited` | `observed_nonempty` | 6 | The provider trading date was 2026-07-31; raw rule and state codes were retained with unverified-semantics limitations. | `4891fead811e9bf2b6837fd62d9716adc18cb3161ef8b5c17abc9020f1de630a` |
| Monitoring × anomaly intersection | `examples/requests/market-monitoring-intersection.json` | `limited` | `observed_empty` | 0 | Both basis pools were completely collected and no canonical security had an overlapping monitoring window and anomaly date. This is a proven bounded empty intersection, not a source failure. | `2c62a043ac0386828062d93b59043acdb6de938441ee9e3e3363ee1c4c461a2b` |
| Market heat | `examples/requests/market-heat.json` | `limited` | `observed_nonempty` | 20 | Popularity labels and rankings remained market signals; provider total and ranking observation time were not exposed. | `8631a74085c97f77a51771ea2d2ce2923a893c7b1e16d9959fc16e6e3f96eb0c` |

All successful source-backed results disclosed `experimental_market_signal_sources` and `no_qualified_independent_fallback`. No live result contained a cross-source conflict. The industry-rotation failure demonstrates the required distinction between a proven empty pool and an unavailable source.
