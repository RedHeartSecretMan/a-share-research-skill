# CLI contract

Read this reference when selecting a command, invoking the bundled runtime, or interpreting its process and JSON contracts.

## Portable invocation

The runtime requires Python 3.12 or later and only the Python standard library. Select an interpreter available on the host:

- Windows: `py -3.12`
- macOS: `python3`
- Linux: `python3`

Resolve `<skill-root>` from the loaded `SKILL.md` location. The entry point is `<skill-root>/scripts/a_share_research.py`. Do not assume a particular Agent, home directory, installation directory, working directory, or shell. Quote the resolved path when it contains spaces.

Conceptually invoke:

```text
<python> <skill-root>/scripts/a_share_research.py <subcommand> <arguments>
```

Use `pathlib.Path` or the host platform's normal path API when an Agent must construct the path programmatically. Do not manually replace `/` with `\` in user-supplied paths.

## Fixed subcommands

- `resolve --query <security-clue> --as-of <YYYY-MM-DD> [--output <file>]`: cross-check an identity clue with experimental SSE/SZSE and CNINFO operations. A name or bare code remains a clue until the JSON identifies one canonical security.
- `close --security <SSE:code|SZSE:code> --as-of <YYYY-MM-DD> [--output <file>]`: cross-check the latest completed daily unadjusted close from experimental exchange and Tencent operations.
- `validate-bundle --bundle <bundle-directory>`: validate `manifest.json`, referenced material hashes, evidence applicability, and relationships without calculating a valuation.
- `valuation --bundle <bundle-directory> --as-of <YYYY-MM-DD> [--output <file>]`: rerun full bundle validation and calculate total market capitalization, PE TTM, and PB MRQ from admissible provided evidence.

Never pass a natural-language research request to the CLI. Translate it into one of these commands before invocation. Never add an undocumented option or infer an exchange for `close`.

## Process and JSON semantics

`stdout` contains exactly one compact JSON document with `schema_version`. Parse fields according to that version; do not infer a schema from prose examples.

A zero exit code means the CLI formed a valid contract result. Inspect its domain `status` separately:

- `supported`: every key claim meets the evidence contract;
- `limited`: the core question remains answerable with disclosed limitations;
- `blocked`: the result is valid, but identity or core evidence prevents a substantive answer.

A nonzero exit means invocation, protocol, I/O, or internal processing prevented a valid research result. Read the sanitized diagnostic from `stderr`; do not turn it into a source fact. Diagnostics never replace JSON evidence.

`--output` is opt-in persistence. The same JSON remains on `stdout`; without `--output`, the runtime does not create a result file, cache, database, or global configuration.

## Credentials and network behavior

The first release has no credentialed Adapter and reads no credential environment variable. `resolve` and `close` access experimental public operations; `validate-bundle` and `valuation` operate on local provided evidence. Never place a key, token, password, or secret in a command argument, log, JSON result, example, or fixture.

Default tests are offline and replace only the external network boundary with fixed responses. `tests/live_probe_close.py` belongs to the development repository, not the installed Skill; a maintainer must invoke it explicitly for source diagnostics. A live probe must never update fixtures or become an ordinary CI dependency.
