# CLI contract

Read this reference when selecting a command, invoking the bundled runtime, or interpreting its process and JSON contracts.

## Portable invocation

The runtime requires Python 3.12 or later and only the Python standard library. Select an interpreter available on the host:

- Windows: `py -3.12`
- macOS: `python3`
- Linux: `python3`

Resolve `<skill-root>` from the loaded `SKILL.md` location. The Skill's only public runtime entry point is `<skill-root>/scripts/entrypoint.py`; modules under `<skill-root>/scripts/a_share_research/` are implementation details and must not be invoked directly. Do not assume a particular Agent, home directory, installation directory, working directory, or shell. Quote the resolved path when it contains spaces.

Conceptually invoke:

```text
<python> <skill-root>/scripts/entrypoint.py <subcommand> <arguments>
```

Use `pathlib.Path` or the host platform's normal path API when an Agent must construct the path programmatically. Do not manually replace `/` with `\` in user-supplied paths.

## Research Interface

- `run --request <research-task.json> [--output <file>]`: execute the stable `research(request) -> research result` Interface. The request object contains `schema_version`, `task_type`, `subjects`, explicit `as_of`, `window`, `parameters`, and `source_policy`. Unknown tasks and unavailable optional Adapter dependencies return explicit `blocked` JSON results.

Current task types:

- `security_identity`: one A-share name/code clue;
- `market_trend`: one A-share clue, `window.trading_days` from 2 to 250, and `parameters.adjustment` of `unadjusted` or `forward_adjusted`;
- `etf_market`: one six-digit SSE ETF code clue and a current/latest-completed market snapshot.
- `security_valuation`: one A-share clue whose subject includes a positively established integer `issuer_security_class_count`, the current China Standard Time date, and a positive decimal-string `parameters.target_pe`; an absent class scope blocks instead of defaulting to one;
- `valuation_compare`: two to ten unique A-share clues with an explicit class count per subject, on the same current date and `target_pe`; returns one ordered, same-basis comparison table without dropping limited or blocked rows.

Never pass natural-language text as the request document. The Agent translates the user's question into a versioned research task and never places credentials in that document.

## Compatibility subcommands

- `resolve --query <security-clue> --as-of <YYYY-MM-DD> [--output <file>]`: cross-check an identity clue with experimental SSE/SZSE and CNINFO operations. The exchange-specific official observation establishes the exchange; an exchange-neutral CNINFO record may corroborate code and name but never supplies a guessed venue. A name or bare code remains a clue until the JSON identifies one canonical security.
- `close --security <SSE:code|SZSE:code> --as-of <YYYY-MM-DD> [--output <file>]`: cross-check the latest completed daily unadjusted close from experimental exchange and Tencent operations.
- `validate-bundle --bundle <bundle-directory>`: validate `manifest.json`, referenced material hashes, evidence applicability, and relationships without calculating a valuation.
- `valuation --bundle <bundle-directory> --as-of <YYYY-MM-DD> [--output <file>]`: rerun full bundle validation and calculate total market capitalization, PE TTM, and PB MRQ from admissible provided evidence.

These four commands remain compatibility entry points while capabilities migrate behind `run`. Never add an undocumented option or infer an exchange for `close`.

## Process and JSON semantics

`stdout` contains exactly one compact JSON document with `schema_version`. Parse fields according to that version; do not infer a schema from prose examples.

A zero exit code means the CLI formed a valid contract result. Inspect its domain `status` separately:

- `supported`: every key claim meets the evidence contract;
- `limited`: the core question remains answerable with disclosed limitations;
- `blocked`: the result is valid, but identity or core evidence prevents a substantive answer.

A nonzero exit means invocation, protocol, I/O, or internal processing prevented a valid research result. Read the sanitized diagnostic from `stderr`; do not turn it into a source fact. Diagnostics never replace JSON evidence.

`--output` is opt-in persistence. The same JSON remains on `stdout`; without `--output`, the runtime does not create a result file, cache, database, or global configuration.

## Credentials and network behavior

The current preview has no credentialed Adapter and reads no credential environment variable. `resolve`, `close`, and currently registered network research tasks use capability-scoped source operations; missing optional dependencies fail closed and name the unavailable capability. `validate-bundle` and `valuation` operate on local provided evidence. Never place a key, token, password, or secret in a command argument, request document, log, JSON result, example, or fixture.

Default tests are offline and replace only the external network boundary with fixed responses. `tests/live_probe_close.py` belongs to the development repository, not the installed Skill; a maintainer must invoke it explicitly for source diagnostics. A live probe must never update fixtures or become an ordinary CI dependency.
