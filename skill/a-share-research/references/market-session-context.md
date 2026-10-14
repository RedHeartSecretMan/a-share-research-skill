# Market Session Context Module

This is the internal #117 implementation checkpoint for the approved v0.4.0 work. It is not a released workflow, default production calendar owner, or live qualification. The existing public `run --request` Interface and v0.3.0 workflows retain their published contracts.

## Interface and authority

`DeterministicMarketSessionAdapter.execute(MarketSessionOperationRequest, timeout_seconds)` returns the existing immutable `OperationOutcome`. Its clock is injected and read once per execution. Fixtures explicitly mark `synthetic: true`; they demonstrate deterministic behavior, not exchange-source authenticity.

A complete `CalendarEvidence` names its exchange, calendar/rule/completion evidence IDs, version, source locators, effective and covered ranges, first availability, actual retrieval time, explicit daily states and regular-auction schedule. Every date in a proved range must be present. Unknown/stale authority, missing exchanges or dates, conflicting states/schedules and impossible source times fail closed. No weekday subtraction or generic age limit supplies missing proof.

The small calendar Interface expects already established source authority. A caller setting `authority: proved` does not authenticate a source. A future production owner must establish acquisition, applicable source/version/exception authority and full-day completion before supplying these values.

## Regular auction and completed trading day

The phase labels are `pre_open`, `opening_auction`, `opening_intermission`, `continuous_auction`, `midday_break`, `closing_auction`, `post_close`, `weekend` and `holiday`. The model uses half-open intervals: the interval beginning at a boundary owns that instant. `opening_intermission` labels the gap between the declared opening-auction end and continuous-auction start; that name is a model convention, not a quoted exchange phase.

A regular-auction end is distinct from the completion of all applicable A-share trading-day sessions. `trading_day_completion_time` and `completion_evidence_id` require independent authority. The synthetic tests use a 15:30 full-day completion with a 15:00 regular-auction end so that a post-close phase cannot silently advance `screen_date`.

Current-session applicability and `freshness_basis` distinguish active auctions, the midday break and completed-session use. They impose no observation-age threshold. On resume, the original phase descriptor is marked `is_current_phase: false`; it cannot authorize a new current-session observation.

## Frozen C and restore

`ContextBindings` are internal request, producer, plan, operation-manifest and artifact identities. They are not additional ResearchTask inputs. Create freezes:

- `requested_as_of` and the original aware Asia/Shanghai `research_now`;
- current-date `information_cutoff` equal to that original clock, or historical-date 23:59:59;
- the greatest proved three-exchange common completed `screen_date`;
- full calendar evidence, its IDs/ranges/versions and the normalized context digest;
- the supplied internal identity bindings.

Future dates fail closed. Historical screening `screen_date` is independent of the current descriptor's `latest_completed_session`.

Resume receives the saved plain-JSON context and `expected_context_digest` from #108's already validated original commitment. #108 owns byte, request, plan, manifest, partition and ownership validation before this call; #117 adds calendar/time semantics. This Module creates no trust store and performs no source acquisition. It verifies the independently supplied digest, exact bindings, original clock/cutoff derivation, calendar authority and a full semantic reconstruction against the original calendar evidence. Updating a local file digest cannot authorize a changed cutoff, screen date or calendar reference.

New evidence retains its actual retrieval time. The restored C remains unchanged and provides the original cutoff to downstream evidence-adoption owners. #120/#69 remain responsible for checking financial/material availability and version lineage against that cutoff; completed partitions/ranking are controlled by #108/#69.

## Declared-document parser and unresolved production work

`ExchangeCalendarDocumentAdapter` parses bounded extracted SSE/SZSE yearly holiday declarations and the exact numbered regular-auction/effect clauses: SSE 2.4.2/11.9 and SZSE 2.3.2/10.9. Its inputs are extracted text, not an invented JSON provider response. It retains declared closure ranges, reopening dates, auction times, effect date and safe locators; it excludes raw document text from outcomes.

The adapter always reports these currently insufficient document facts as partial, retaining explicit proof gaps. It does not infer unlisted daily states, first availability, temporary exceptions, deferral applicability, full historical coverage or full-day completion. BSE is not substituted with SSE/SZSE rules. It is intentionally unregistered until those production obligations are proved.

Frozen #121 A/B/C/D evidence permits this local work but leaves BSE authority, SSE's two incomplete deferral rows, SZSE's specific deferral context, exception coverage, source transport/extraction/permission contracts and whole-day completion unresolved. Closing them requires the owning source/authority decision and an approved concrete source-access budget. The complete original #117 three-exchange, public-proof, installed-copy, quality and independent-review obligations remain in force; this checkpoint is not Ready for Acceptance.
