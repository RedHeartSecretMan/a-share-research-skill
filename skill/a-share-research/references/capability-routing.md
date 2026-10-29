# Capability routing and public state

This document is the current v0.4.1 routing view. It classifies an entry by
`asset_class` × `research_scene`, then adds `scope`, `capability_kind`,
`implementation_status`, `public_state`, and `priority`. These fields describe
routing and audit boundaries. They do not change the public `run --request`
entry point, the `schema_version: "1.0"` request contract, or the result
envelope. The source of truth for the rows is the immutable runtime registry in
`scripts/a_share_research/capability_metadata.py`.

## How to select a route

1. Identify the asset being researched. An A-share security, an ETF, an ETF
   option, an index, and a market aggregate are separate asset classes.
2. Identify the research scene: identity and scope, market observation,
   valuation and fundamentals, materials and events, market signals, screening
   and ranking, workflow orchestration, or evidence and result handling.
3. Check `scope`. `subject_scoped` requires a canonical subject,
   `market_wide` has no security subject, and `cross_cutting` describes a task
   that can serve more than one scope without changing its public ID.
4. Check `public_state` before preparing a request. Only `public` rows with
   `supported` state are public routes. `blocked` and `deferred` rows are
   status disclosures, not permission to invent a request shape.

The route table below uses the exact metadata field names and stable registry
IDs. Public workflow labels append their registered request version where that
is part of the public route; this does not change the registry ID. `version` is
the classification-record version; it is separate from a workflow request
version. The request template and contract links are the existing public seam.

## Current P0 public routes

| Entry | Version | `asset_class` | `research_scene` | `scope` | `capability_kind` | `implementation_status` | `public_state` | Priority | Request / contract |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `security_identity` | 1.0 | `a_share_equity` | `identity_scope` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/security-identity.json) · [market contract](market-contracts.md) |
| `market_trend` | 1.0 | `a_share_equity` | `market_observation` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/market-trend.json) · [market contract](market-contracts.md) |
| `security_valuation` | 1.0 | `a_share_equity` | `valuation_fundamentals` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/security-valuation.json) · [research contract](research-contracts.md) |
| `valuation_compare` | 1.0 | `a_share_equity` | `valuation_fundamentals` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/valuation-compare.json) · [research contract](research-contracts.md) |
| `research_content` | 1.0 | `a_share_equity` | `materials_events` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/research-content.json) · [research contract](research-contracts.md) |
| `capital_events` | 1.0 | `a_share_equity` | `materials_events` | `cross_cutting` | public | retain | supported | P0 | [request](../assets/requests/capital-events.json) · [research contract](research-contracts.md) |
| `market_signals` | 1.0 | `a_share_equity` | `market_signals` | `cross_cutting` | public | retain | supported | P0 | [request](../assets/requests/market-signals.json) · [research contract](research-contracts.md) |
| `etf_market` | 1.0 | `etf` | `market_observation` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/etf-market.json) · [market contract](market-contracts.md) |
| `research_workflow` | 1.0 | `a_share_equity` | `workflow_orchestration` | `cross_cutting` | public | retain | supported | P0 | [workflow contract](workflow-contracts.md) |
| `single_security_research@1.0` | 1.0 | `a_share_equity` | `workflow_orchestration` | `subject_scoped` | public | retain | supported | P0 | [request](../assets/requests/workflow-single-security-research.json) · [workflow contract](workflow-contracts.md) |
| `single_security_valuation` | 1.0 | `a_share_equity` | `workflow_orchestration` | `subject_scoped` | public | retain | supported | P0 | [workflow contract](workflow-contracts.md) |
| `valuation_comparison` | 1.0 | `a_share_equity` | `workflow_orchestration` | `subject_scoped` | public | retain | supported | P0 | [workflow contract](workflow-contracts.md) |
| `theme_report_research` | 1.0 | `a_share_equity` | `workflow_orchestration` | `subject_scoped` | public | retain | supported | P0 | [workflow contract](workflow-contracts.md) |
| `new_security_research` | 1.0 | `a_share_equity` | `workflow_orchestration` | `subject_scoped` | public | retain | supported | P0 | [workflow contract](workflow-contracts.md) |
| `market_overview_research@2.0` | 2.0 | `market_aggregate` | `workflow_orchestration` | `market_wide` | public | retain | supported | P0 | [request](../assets/requests/workflow-market-overview.json) · [workflow contract](workflow-contracts.md) |

The three directly discoverable product paths are therefore
`single_security_research@1.0`, `market_overview_research@2.0`, and
`etf_market`. A workflow still uses the existing request shape and does not
accept caller-defined steps or a nested workflow.

## P2 extensions kept outside P0

| Entry | `asset_class` | `research_scene` | `scope` | `capability_kind` | `implementation_status` | `public_state` | Priority | Boundary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `etf_options` | `etf_option` | `market_observation` | `subject_scoped` | public | retain | supported | P2 | Contract identity, quote session, units, empty proof and partial or unknown coverage remain explicit. It does not become ETF base data. |
| `intraday_market_signal` | `a_share_equity` | `market_signals` | `subject_scoped` | public | retain | supported | P2 | Current-session snapshot only; it is not a continuous feed or completed-session replay. |
| `intraday_replay` | `a_share_equity` | `market_observation` | `subject_scoped` | public | retain | supported | P2 | Requires an explicit completed trading date and preserves minute coverage and session stages. |

P2 installation and offline checks can establish implementation behavior and
failure boundaries. They do not turn an omitted live verification into live
source proof or block the P0 routes.

## P1 screening remains blocked

| Entry | Version | `asset_class` | `research_scene` | `scope` | `capability_kind` | `implementation_status` | `public_state` | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `market_screen_research@1.0` | 1.0 | `a_share_equity` | `screening_ranking` | `market_wide` | planned | refactor | **blocked** | P1 |
| `fundamental_research_priority@1.0` | 1.0 | `a_share_equity` | `screening_ranking` | `market_wide` | planned | refactor | **blocked** | P1 |

These rows preserve the P1 product intent: a three-exchange ordinary A-share
denominator, fixed factors, deterministic ranking, bounded candidate research,
and caller-owned result artifacts. The evidence gate is not complete, so the
runtime continues to reject the public screening workflow before source or
artifact I/O. Internal Universe, provider-lane, candidate, and artifact
modules do not make either row a supported public route. Do not use an ETF,
index, board, or one security as a screening denominator or whole-market proxy.

## Policy and internal rows

The metadata registry also records non-route rows so that an implementation
cannot be mistaken for public support:

| Row family | `capability_kind` | `public_state` | Meaning |
| --- | --- | --- | --- |
| `capital_events_subject_scoped`, `capital_events_market_wide`, `market_signals_subject_scoped`, `market_signals_market_wide` | policy | blocked | Scope facets for existing task IDs; they do not rename or widen the public request contract. |
| `whole_market_turnover`, `whole_market_margin`, `market_wide_intraday` | policy | blocked | Explicit unsupported market-overview slots; no source operation, proxy observation, claim, calculation, or coverage is created. |
| `market_foundation`, `result_artifact` | internal | blocked | Internal implementation seams; module presence is not public capability. |

## P3 deferred asset classes

`bond_research`, `fund_research`, `stock_option_research`,
`futures_research`, `warrant_research`, and `open_ended_screening` are
`capability_kind=deferred`, `implementation_status=defer`,
`public_state=deferred`, and priority P3. They have no public source
execution, coverage, observation, claim, or calculation. Shared fields in a
stock or ETF result cannot be used to infer support for these asset classes.

## Historical evidence boundary

This routing view describes the current v0.4.1 classification. It does not
rewrite v0.3.0 or v0.4.0 specifications, ADRs, release audits, operation
qualification, release matrices, probe evidence, or dated examples. Those
artifacts remain immutable historical evidence and retain their original
candidate, date, scope, and conclusion. A current route must not be used to
retroactively relabel a historical result or to claim live proof for a new
candidate.
