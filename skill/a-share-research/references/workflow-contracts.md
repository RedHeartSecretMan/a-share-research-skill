# Workflow contracts

`research_workflow` is fixed, versioned orchestration of existing ResearchTasks. Top-level `window` is always `null`. `parameters` contains exactly `workflow` and `inputs`; `workflow` contains `id` plus its exact registered version (`2.0` for market overview, `1.0` for the other workflows). Callers never supply steps, a dependency graph, a caller plan, or a nested workflow.

Every leaf inherits the top-level `as_of` and exact `source_policy`. A workflow returns the same 14-key result as a direct task and stores only stable references to common top-level records. Identity is the sole gate for comprehensive security plans. A blocked or limited non-identity leaf does not stop independent siblings; all local records, errors, conflicts, limitations, and skip reasons remain visible.

## `single_security_research@1.0`

Use exactly one A-share clue. Inputs require decimal-string `target_pe`; `completeness_mode` defaults to `standard` and may be `fast`; optional `deep_extensions` is a unique list containing only `long_history` and/or `large_materials`.

- `standard`: identity, one 20-session unadjusted trend, valuation, subject materials, past subject capital/company events, a separate future lockup window, and subject board membership.
- `fast`: identity and the same trend only; every enhancement remains in the fixed plan as `not_requested` with no fabricated leaf result.
- `long_history`: deepens the same trend acquisition to 250 sessions; it does not add another trend step.
- `large_materials`: in standard mode, deepens the same materials step to 365 days and limit 100. It is invalid with `fast`.

The plan adds no current-session minute stream, five-level order book, tick endpoint, or hidden screening step.

Template: [`../assets/requests/workflow-single-security-research.json`](../assets/requests/workflow-single-security-research.json).

## `market_overview_research@2.0`

Use `subjects: []`. Inputs default `completeness_mode` to `standard` and may select `fast`. The only allowed `deep_extensions` value is `market_wide_intraday`; it is retained as an explicit request but remains unsupported and compiles no child. Unknown keys, duplicate values, legacy-shaped inputs, and other extensions fail closed.

The capability inventory is fixed:

| Capability ID | Tier | `standard` / `fast` | Leaf |
| --- | --- | --- | --- |
| `industry_board_breadth` | core | `supported` / `supported` | `market_core_signals` |
| `limit_pools` | core | `supported` / `supported` | `market_core_signals` |
| `industry_theme_rotation` | standard | `supported` / `not_requested` | `market_signal_enhancements` |
| `market_heat` | standard | `supported` / `not_requested` | `market_signal_enhancements` |
| `focus_monitoring` | standard | `supported` / `not_requested` | `market_signal_enhancements` |
| `severe_abnormal_movement` | standard | `supported` / `not_requested` | `market_signal_enhancements` |
| `board_fund_flow` | standard | `supported` / `not_requested` | `market_capital_enhancements` |
| `market_dragon_tiger` | standard | `supported` / `not_requested` | `market_capital_enhancements` |
| `northbound_disclosure` | standard | `supported` / `not_requested` | `market_capital_enhancements` |
| `market_flash` | standard | `supported` / `not_requested` | `market_flash` |
| `market_trading_session` | core | `supported` / `supported` | `market_trading_session` |
| `major_broad_indices` | core | `supported` / `supported` | `major_broad_indices` |
| `whole_market_turnover` | core | `supported` / `supported` | `whole_market_turnover` |
| `whole_market_margin` | standard | `supported` / `not_requested` | `whole_market_margin` |
| `market_wide_intraday` | deep | `unsupported` / `unsupported` | none |

The two core capabilities preserve per-industry-board change and up/down counts plus owner-local limit-pool totals, pages, empty proof, and partial proof. They do not establish deduplicated whole-A-share breadth. The eight standard enhancements reuse existing market-wide signal, capital-event, and flash operations.

The session owner freezes three-exchange calendar proof once. Turnover and margin use its common completed trading date and information cutoff; indices use the fixed four identities and the latest completed regular-auction session (historical requests keep the frozen completed basis). Exact totals require every applicable exchange and compatible units; a missing or ineligible partition retains its siblings and cannot form a complete total. A blocked session skips its dependent steps.

`market_wide_intraday` has `reason_code: "free_anonymous_intraday_unproved"`, `gap_route: "future_scope"`, and no step, operation obligation, observation, claim, calculation, or coverage, including when explicitly requested. Never proxy it with one index, ranking, ETF, security-scoped owner, or five-level/tick path. Completion for this branch means empty subjects, a valid mode, exact 15-entry inventory, and every leaf-local failure or empty/partial state still visible.

Current offline owner qualification is recorded in repository `docs/research/v0.4.0-overview-v2-operation-qualification.json` (52 producers). It activates the six unchanged accepted report contracts plus session and fixed indices; it does not establish live usability. The earlier pending qualification and v0.3.0 release artifacts remain historical evidence.

Runtime rejects `market_overview_research@1.0` and `market_screen_research` before any source call or artifact write. Accepted internal screening and artifact modules do not expose a public screening workflow.

Template: [`../assets/requests/workflow-market-overview.json`](../assets/requests/workflow-market-overview.json).

## Other fixed plans

- `single_security_valuation`: one subject; inputs contain `target_pe`; wraps `security_valuation`.
- `valuation_comparison`: 2–10 subjects; inputs contain `target_pe`; wraps `valuation_compare` and retains every row.
- `theme_report_research`: no subject; inputs contain `query`, `published_from`, `published_to`, `limit`, and `verify_documents`.
- `new_security_research`: one subject; inputs contain `target_pe`, explicit `report_window`, `market_window`, `lockup_window`, `fund_flow_period`, `limit`, and `verify_documents`. Its fixed path covers identity, reports/consensus, valuation, provider board membership, stock flow, dragon-tiger, future lockup, and margin trading.

Use repository examples for these four specialized plan shapes; do not synthesize optional keys from memory.
