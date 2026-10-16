# Research-domain contracts

Read this reference for valuation, materials, capital events, and market signals. Keep each explicit question on its narrow task; do not route a single announcement, metric, event, or signal through a comprehensive workflow.

## `security_valuation` and `valuation_compare`

`security_valuation` accepts one A-share clue; `valuation_compare` accepts 2–10 unique clues and preserves input order. Every subject must include a positively established integer `issuer_security_class_count`. Unknown scope or A/H, A/B, or another multi-class issuer blocks issuer-wide valuation instead of defaulting the count to one.

Use the current China Standard Time date, `window: null`, and a positive decimal-string `parameters.target_pe`. The runtime preserves statement rows, share-count scope, price basis, consensus role, unavailable or meaningless metrics, and calculation lineage. Read [valuation-methodology.md](valuation-methodology.md) before explaining market cap, PE TTM, PB MRQ, forward PE, growth, PEG, or target-PE time.

Templates: [`security-valuation.json`](../assets/requests/security-valuation.json) and [`valuation-compare.json`](../assets/requests/valuation-compare.json).

## `research_content`

Use `window.published_from` / `published_to`, `parameters.material_types`, `parameters.query`, and a per-type `limit` from 1 through 100.

- `research_report`: one security clue or a subject-free theme query.
- `industry_report` and `market_flash`: no subject; industry reports also require `industry_code`.
- `consensus_material`, `issuer_profile`, `stock_news`, `announcement`, and `investor_qa`: exactly one security clue.

The no-credential theme-report baseline performs exact title-keyword filtering over a bounded provider feed; it is not semantic search or a complete theme universe. Credentialed iWencai is only an optional enhancement. F10 `issuer_profile` is a current provider snapshot with unknown publication time and must use a window containing the retrieval date; it is not statutory disclosure. Run it separately from historical investor Q&A. `verify_documents: true` actively verifies eligible document locators; ordinary discovery does not claim every PDF was downloaded.

Every requested material type needs coverage. Preserve publication time, retrieval time, document identity, locator, author/source role, duplicate grouping, source errors, and limitations. One material-source failure does not erase usable sibling material, but missing all usable material for a required type can block the task.

Template: [`research-content.json`](../assets/requests/research-content.json).

## `capital_events`

Use an `observed_from` / `observed_to` window, `parameters.data_types`, and per-type `limit` from 1 through 100.

Subject-required types: `stock_fund_flow`, `dragon_tiger`, `lockup`, `margin_trading`, `block_trade`, `shareholder_count`, and `dividend`.

Subject-free types: `northbound_flow`, `board_fund_flow`, and `market_dragon_tiger`. Do not mix subject-required and subject-free types. `stock_fund_flow` uses period `5d` or `10d`. `board_fund_flow` also requires `board_type` (`industry`, `concept`, or `region`) and period `today`, `5d`, or `10d`. Only a standalone `lockup` task may end after `as_of`, capped at 90 days.

Preserve each observation's period, unit, direction, market scope, availability boundary, pagination, and truncation. A complete error-free zero batch may be `observed_empty`; failure, incomplete pagination, unknown coverage, or absent qualified source remains `indeterminate`. A usable type may be `partial` without erasing siblings. Missing post-2024 northbound disclosure fields remain unavailable and never become zero.

Template: [`capital-events.json`](../assets/requests/capital-events.json).

## `market_signals`

Use an `observed_from` / `observed_to` window and one or more signal types: `strong_stock_theme`, `security_board_membership`, `industry_rotation`, `limit_state`, `focus_monitoring`, `severe_abnormal_movement`, `monitoring_intersection`, or `market_heat`. Enable experimental sources.

`security_board_membership` requires exactly one A-share clue. The other types are market-wide, use no subject, and cannot be mixed with the subject-scoped type. `limit` is 1–100 per signal type. `limit_state` may include a unique `limit_states` list from `limit_up`, `limit_break`, `limit_down`, and `previous_limit_up`. `market_heat_period` is `hour` or `day` and defaults to `hour`.

Preserve provider trading date, market scope, signal rule, editorial attribution, time/period semantics, total/page/retained/truncated facts, pool partitions, source errors, and limitations. Pool `truncated` requires a filled request limit and a larger source or collected count. A count gap below that limit remains source partiality or schema loss with its original total, retained count, and errors; it cannot become a complete empty pool. Complete, error-free coverage with an unexplained count gap fails closed. A provider-local code does not establish canonical security identity or a monitoring intersection. A watchlist, popularity label, or theme reason is a market signal, not an issuer fundamental or proven cause.

Template: [`market-signals.json`](../assets/requests/market-signals.json).
