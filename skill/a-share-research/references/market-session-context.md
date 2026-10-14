# Market Session Context Module

This internal v0.4.0 Module implements `market_trading_session` through the existing `execute(OperationRequest, timeout_seconds) -> OperationOutcome` Adapter Interface. It does not add a public command or activate a workflow. Source operations for quotes, daily totals and financial facts remain responsible for their own applicability and completeness.

## Default source path

`build_default_market_session_adapter` loads the three fixed first-party document contracts in [market-session-sources.json](market-session-sources.json). It performs no acquisition during construction. Execute makes at most six sequential document requests: one annual closure notice and one current rule document for each of SSE, SZSE and BSE. It has no retry, redirect, account API, browser-cookie import or fallback route. An unrecognized annual notice stops that exchange before its rule request; successful other exchange proofs remain visible.

All six annual/rule responses are bound to independently qualified exact body digests. Any changed bytes, including added or negated declarations, fail closed and require reviewed source-version qualification; a successful keyword match cannot authorize an unknown revision. HTML and DOCX declarations are also parsed from bounded response bodies. For the named SZSE 2026 PDF, the exact reviewed body digest binds the separately qualified rule semantics. The document catalogue is a version-specific qualification boundary, not a claim of general parsing or automatic updates; the PDF path has no general text parser. HTML/DOCX critical version, scope, weekday, auction and completion clauses must match the registered declaration. Every dated annual closure/opening is checked against the qualified schedule; extra or unparsed closure statements fail closed. The BSE notice must preserve its exact deferred-clause inventory and paragraph limits; acquired contradictions cannot silently make the completion sections operative. DOCX accepts bounded stored/DEFLATE entries, and malformed packages produce a sanitized partial outcome while retaining unaffected exchange proofs. Provider bodies, headers and exception text are excluded from outcomes.

The current production contract declares the normal version period **2026-07-06 through 2026-12-31**. Requests outside proved coverage fail closed. A context also needs a proved preceding completed date; the first covered morning cannot invent a previous session. This period records the source version actually qualified, rather than claiming arbitrary historical coverage. A new annual/rule version requires updating and reviewing the source contract and rerunning affected checks. Source-version/producer drift also prevents reuse of an old artifact under changed producer identity.

## Calendar authority and time

A complete calendar proof names its exchange, calendar/rule/completion evidence IDs, document versions, locators, response digests, effective and covered ranges, actual retrieval time and registered publication evidence. Every date in the range is present. Monday–Friday classification comes from the proved rule; named annual closures and known rule exceptions are applied. No weekday subtraction supplies a missing previous date.

Publication dates have **day precision**. The labeled conservative availability bound is the following Asia/Shanghai midnight; it is never called an exact first-publication timestamp. Signature, URL date, HTTP Date and retrieval time cannot replace publication evidence. A response retrieved before the declared publication day starts is impossible and rejected; retrieval within that day is allowed, without treating the conservative following-midnight bound as an exact publication time. The deterministic and restore paths enforce the same distinction. Retrieval can occur after the frozen research cutoff when the applicable document semantics were already published and version-bound before it.

Unknown/stale authority, missing exchanges or dates, conflicting states/schedules, impossible timestamps and unknown document revisions do not produce a proved current context. Coverage and known exceptions are explicit; the Module does not claim absence of every possible later emergency announcement or require a new real-time exchange-status API.

## Auction and scheduled completion

The phase labels are `pre_open`, `opening_auction`, `opening_intermission`, `continuous_auction`, `midday_break`, `closing_auction`, `post_close`, `weekend` and `holiday`. Intervals are half open: the interval beginning at a boundary owns that instant. `opening_intermission` names the gap between the declared opening-auction end and continuous-auction start; it is a model label, not a quoted official phase.

Regular auction completion and scheduled full-day completion are separate. The registered normal full-day reference is 15:30. SSE/SZSE applicable after-hours fixed-price rules support it. For BSE, operative block-trade confirmation and daily-volume rules support it; the whole 3.7 after-hours chapter remains deferred. An overall effective date does not enable deferred clauses.

The result exposes `latest_regular_auction_completed_session` separately from `latest_completed_session`, whose basis is `calendar_scheduled_completion`. `actual_trade_or_quote_completeness_proved` is false. The result never asserts that a quotation, daily total or report has actually become complete or public at 15:30.

Active auctions use `freshness_basis: active_session`; the midday break uses `current_session_hold`. Other applicable completed-session uses remain distinguishable. No universal observation-age threshold is introduced.

## Frozen C and restore

`ContextBindings` are internal request, producer, plan, operation-manifest and artifact identities, not additional ResearchTask inputs. The original aware research clock is captured once for C. A separate actual evidence-check clock records acquisition/verification completion without moving the information boundary.

Create freezes `requested_as_of`, original `research_now`, `evidence_checked_at`, `information_cutoff`, common `screen_date`, its scheduled-completion basis, full source-bound calendar proofs, internal bindings and normalized context digest. Today's cutoff is the original research clock; a historical cutoff is that day's Asia/Shanghai 23:59:59. Future requests fail closed.

Historical C is independent of today's calendar coverage. Proved historical C may survive as an explicit partial outcome when the current descriptor cannot be established. It never guesses a current latest session from an old covered range.

Resume receives saved plain JSON and the expected original context digest from #108's validated commitment. #108 owns request/producer/plan/manifest/partition/ownership validation; #117 owns calendar and time semantics. Restoration validates bindings, the original clock/cutoff, registered references, calendar dates/rules and full context reconstruction before any new-source path. It performs no document acquisition. Updating a local digest cannot authorize a changed cutoff, screen date, calendar reference or calendar day.

The restored C and original document retrieval times remain fixed; the operation verification time is actual. The original phase descriptor is explicitly not a current phase on later restoration. #120/#69 consume the original cutoff and remain responsible for financial/material availability and version lineage; #108/#69 control write-once partitions and rankings.

## Validation and source evidence

The deterministic Adapter uses injected aware clocks and explicit calendar fixtures. Production-path offline tests use synthetic document layouts and explicitly supplied transport; all synthetic document bytes have explicit test-only digest bindings and never stand in for the real PDF. Outcomes reuse #116 recursive sealing and detached plain JSON. Clean copied-install loading and existing unified-result evidence-reference projection are verified through the same Interface.

Source contracts consume the independently verified #121 A–F facts and first-party publication metadata. Their normal schedule semantics are distinct from final live qualification. Live provider success, final operation closure and current-market completeness remain the final frozen-candidate #98 obligations. All ordinary tests use a process-level network denial inherited by child processes, a minimal environment and explicit transport fixtures.
