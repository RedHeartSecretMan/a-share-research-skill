<div align="center">

# A-Share Research Skill

**Give every research number an identity, time boundary, source, and calculation lineage.**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Public API: run --request](https://img.shields.io/badge/public%20API-run%20--request-0F766E)](skill/a-share-research/references/cli-contract.md)
[![Release: v0.3.0](https://img.shields.io/badge/release-v0.3.0-0F766E)](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.3.0)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-D22128)](LICENSE)

[中文](README.md) · [Quick start](#quick-start) · [Capabilities](#what-it-does) · [Chinese examples](examples/README.md) · [Docs](docs/README.md) · [v0.3.0 release audit](docs/research/v0.3.0-release-audit-2026-08-18.md)

</div>

`a-share-research-skill` is the repository name; the installed Skill is `$a-share-research`. It routes natural-language questions to a deterministic Python CLI and returns versioned JSON containing security identity, research time, source observations, calculation lineage, conflicts, coverage, and limitations. The Agent then presents that evidence as auditable research.

This branch delivers the **v0.4.0 candidate**, with 12 narrow ResearchTask types, six fixed research plans, and one public `run --request` entry point. The copied artifact identifies its version in [`VERSION`](skill/a-share-research/VERSION). Market overview now uses `market_overview_research@2.0`. Applicable anonymous-source proof for this candidate is recorded in the [#98 candidate audit](docs/research/v0.4.0-release-audit-2026-10-17.md); source failures and intraday applications without real-time verification remain explicit. The current stable release, GitHub Release, and tag remain v0.3.0.

## Quick start

### 1. Get and install

The command below installs released v0.3.0. To verify this branch candidate, replace clone’s `--branch v0.3.0` with `--branch codex/v0.4.0-dag` and record the actual candidate commit; a branch name is not release evidence. After cloning the repository, copy the complete `skill/a-share-research/` directory into a Skills location supported by your Agent; that copied directory is `<skill-root>`. Discovery and activation paths differ by Agent, so follow the relevant platform documentation. If the target already exists, inspect it and perform an intentional upgrade; do not overwrite unknown files.

```text
git clone --depth 1 --branch v0.3.0 --single-branch https://github.com/RedHeartSecretMan/a-share-research-skill.git
<python> <skill-root>/scripts/install.py
```

`<python>` must resolve to Python 3.12 or later: usually `python3.12` on macOS/Linux and `py -3.12` on Windows. The installer installs runtime dependencies from [`requirements.lock`](skill/a-share-research/requirements.lock) and then runs `installation_health.py`. Success means exit code 0 with `status: "complete"`; `installation_incomplete` is a software-installation state, not a claim that a provider is available that day.

You may instead copy the complete [`skill/a-share-research`](skill/a-share-research/) folder to another compatible Skills directory. Do not copy only `SKILL.md`: `agents/`, `assets/`, `references/`, `scripts/`, and the dependency lock are all part of the artifact. Source inaccessibility fails closed in the research result and never triggers a silent switch.

### 2. Run a request

Templates on this branch use the candidate contract; existing narrow tasks retain their dated examples, while market overview uses `@2.0`. Copy a template first. For “current,” “today,” or intraday tasks, replace `as_of` with the actual China Standard Time date.

```text
<python> skill/a-share-research/scripts/entrypoint.py run \
  --request examples/requests/security-identity.json
```

After installation, the only supported public invocation is:

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json> [--output <file>]
```

The CLI accepts structured JSON, not natural language, and does not invoke a model. `stdout` contains exactly one versioned JSON result; `stderr` contains diagnostics only. Valid `supported`, `limited`, and `blocked` research results may all exit zero. Protocol, I/O, and internal failures use a nonzero exit code.

## What it does

| Research question | Direct task | v0.4.0 candidate boundary |
| --- | --- | --- |
| Security identity | `security_identity` | Names, abbreviations, and bare codes are clues; ambiguity or exchange conflict fails closed |
| Price trend | `market_trend` | 2–250 completed sessions, unadjusted or forward-adjusted, preserving dual-source OHLCV and conflicts |
| ETF and ETF options | `etf_market`, `etf_options` | SSE ETFs; options cover 510050, 510300, 510500, and 588000 |
| Current intraday snapshot | `intraday_market_signal` | One canonical SSE/SZSE A share on the current China Standard Time date; not a live stream |
| Completed-session replay | `intraday_replay` | One canonical A share and one explicit completed session, with minute coverage and a daily boundary |
| Valuation and comparison | `security_valuation`, `valuation_compare` | Prove issuer security-class scope before calculating; otherwise preserve non-calculable status |
| Research materials | `research_content` | Reports, consensus material, F10, news, announcements, flashes, and investor Q&A |
| Capital and events | `capital_events` | Fund flow, dragon-tiger, lockups, margin, block trades, shareholder count, and distributions |
| Market signals | `market_signals` | Themes, board membership, rotation, limit pools, monitoring, anomalies, and heat |
| Fixed research plans | `research_workflow` | Compose existing narrow tasks; callers cannot define steps or a dependency graph |

Experimental sources may contribute observations and expose conflicts, but cannot independently promote a conclusion to `supported`. F10 is a provider-compiled company-material interface, not exchange statutory disclosure. Provider-reported option Greeks/IV are not calculations made locally by this project or by an exchange.

### Preset research plans

| Plan ID | Use | Key boundary |
| --- | --- | --- |
| `single_security_research` | Comprehensive single-security research | `standard` runs core and enrichments; `fast` guarantees identity and trend only; deepening is limited to `long_history` and `large_materials` |
| `market_overview_research` | Subject-free market overview | 15 fixed capabilities: 4 core and 8 standard executable by applicability; whole-market turnover, margin, and intraday are unsupported; preserves three-exchange partitions and four fixed indices |
| `single_security_valuation` | One-security valuation | Reuses `security_valuation` |
| `valuation_comparison` | Same-basis multi-security comparison | Reuses `valuation_compare` and retains blocked members |
| `theme_report_research` | Theme-report retrieval | Reuses `research_content` |
| `new_security_research` | Systematic new-security review | Identity is the sole gate; other local failures do not stop independent siblings |

`single_security_research` defaults `completeness_mode` to `standard`; `deep_extensions` accepts only `long_history` and `large_materials`. `market_overview_research@2.0` runs core only in `fast` and core plus standard in `standard`. It adds three-exchange trading sessions and four fixed indices; missing partitions retain local evidence. The `whole_market_turnover`, `whole_market_margin`, and `market_wide_intraday` slots are unsupported and create no source obligation, observation, claim, calculation, or coverage; deep requests remain explicit. Single-security turnover, margin materials, intraday snapshots, and completed-session replay retain their existing boundaries.

## Reading a result

The overall status has three values:

- `supported`: material claims have applicable evidence adopted for their use.
- `limited`: the core question remains answerable, but non-critical gaps, conflicts, or source limits must be disclosed.
- `blocked`: identity or critical evidence is insufficient, so no substantive conclusion is safe.

Read `plan`, `observations`, `coverage`, `source_errors`, `conflicts`, `limitations`, `claims`, `calculations`, and `briefs` together rather than relying on the top-level status alone. Source-result states—`complete`, `observed_empty`, `partial`, `credential_blocked`, and `unavailable`—have different meanings. A source failure is never rewritten as zero or “no records.”

The CLI owns evidence, deterministic calculation, and status. When a result is not globally blocked and the question genuinely calls for interpretation, the Agent may add a clearly labelled research judgment, risks, invalidation conditions, and conditional triggers. Direct evidence questions such as identity lookup should not receive unsolicited opinions. The installed [`analysis-boundary.md`](skill/a-share-research/references/analysis-boundary.md) is authoritative.

## Example questions

> Use `$a-share-research` to confirm the exchange and canonical security identity for Industrial Fulian (601138), and state the date through which the answer is valid.

> Use `$a-share-research` to research BlueFocus over the latest ten completed sessions on an unadjusted basis, preserving evidence and conflicts and explaining risks and invalidation conditions.

> Use `$a-share-research` to compare Industrial Fulian, Kweichow Moutai, CATL, Midea Group, and Wuliangye using one date and basis, retaining every missing item and limitation.

> Use `$a-share-research` to run `single_security_research@1.0` in `standard` mode for Industrial Fulian. If one material source fails, continue every step that does not depend on it.

> Use `$a-share-research` to run `market_overview_research@2.0`, preserving local states for three-exchange sessions, four indices, and existing signals while disclosing unsupported whole-market turnover, margin, and intraday.

See [`examples/README.md`](examples/README.md) for a dated real run, Chinese request templates, and boundary guides. `real-runs/` preserves actual public CLI output; `requests/` contains editable inputs rather than live quotes or current conclusions.

## Boundaries

- Single-security network identity and price tasks still cover SSE/SZSE only. Market foundations use separate SSE/SZSE/BSE partitions; missing BSE never falls back to another market.
- Valuation must prove security-class scope. Unknown A/H, A/B, or other multi-class scope blocks issuer-wide valuation.
- A current intraday snapshot is not a trading-grade feed, minute stream, five-level book, or tick source. `intraday_replay` covers completed sessions only. This v0.4.0 delivery retains single-security intraday functionality, installation checks, and offline regression, without real-time intraday verification; installation or offline success does not establish live source usability.
- ETF options lack an authoritative complete contract total, full contract-unit definition, adjustment terms, and a qualified independent fallback; coverage and limitations remain visible.
- External attribution in reports, news, F10, flows, themes, and heat retains its attributed-opinion or market-signal role and does not automatically become issuer fact.
- Open-ended screening, continuous feeds, automated trading, position/leverage advice, project ratings, and price targets are unsupported.
- Whole-market `whole_market_turnover`, `whole_market_margin`, and `market_wide_intraday` remain unsupported. Existing single-security `intraday_market_signal` and `intraday_replay` keep their versions and boundaries.
- Whole-market screening, ranking, candidate research, and their public result-artifact workflow are withdrawn from this version. `market_screen_research` is rejected before any source call or artifact write. Internal Universe, provider-lane, and Result Artifact modules do not constitute a public screening product.

## Docs and examples

- [`docs/README.md`](docs/README.md): documentation entry point, version status, and reading order.
- [`examples/README.md`](examples/README.md): a real run, Chinese request templates, and boundary guides.
- [`skill/a-share-research/SKILL.md`](skill/a-share-research/SKILL.md): concise routing used by the installed Agent.
- [`skill/a-share-research/references/`](skill/a-share-research/references/): exact contracts split by task domain.
- [`CONTEXT.md`](CONTEXT.md): domain language and product boundaries.
- [`Spec 0008 Amendment D`](docs/specs/0008-amendment-d-qualified-public-scope.md): v0.4.0 candidate anonymous scope.
- [`Spec 0008 Amendment E`](docs/specs/0008-amendment-e-no-intraday-live-gate.md): real-time verification applicability for this delivery.
- [`Spec 0007 Amendment A`](docs/specs/0007-implementation-amendment-a.md): historical v0.3.0 implementation and acceptance scope.

## Version and release evidence

v0.3.0 was released on 2026-08-18. The release audit relates the code commit, annotated tag, GitHub Release, and frozen-runtime attestation. Large JSON evidence stays under the research documentation directory instead of occupying the README's main path.

- [GitHub Release v0.3.0](https://github.com/RedHeartSecretMan/a-share-research-skill/releases/tag/v0.3.0)
- [v0.3.0 release audit](docs/research/v0.3.0-release-audit-2026-08-18.md)
- [v0.3.0 matrix methodology and verification](docs/research/v0.3.0-operation-release-matrix.md)
- [Research and release-document index](docs/research/README.md)
- [Historical v0.2.0 release audit](docs/research/history/v0.2.0-release-audit-2026-08-03.md)

## Development verification

Default tests are fully offline. Live probes are explicit maintainer diagnostics, not ordinary CI, and one successful call never replaces operation qualification.

```text
<python> -m unittest discover -s tests -p "test_*.py"
ruff check --isolated --no-cache --select E4,E7,E9,F .
ruff format --check .
mypy --strict skill/a-share-research/scripts
<python> /path/to/skill-creator/scripts/quick_validate.py skill/a-share-research
```

Maintainer commands, live-probe rules, and release-evidence generation are documented in [`docs/README.md`](docs/README.md) and [`docs/research/README.md`](docs/research/README.md).

## License and provenance

The project is licensed under [Apache-2.0](LICENSE). It originated from [`simonlin1212/a-stock-data`](https://github.com/simonlin1212/a-stock-data) and retains that project's copyright and attribution. The current implementation is independently rebuilt and maintained around an evidence-first contract.
