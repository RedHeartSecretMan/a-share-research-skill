# CLI contract

Read this reference for installation, public invocation, process semantics, credentials, and the map to task-specific contracts. This is the current v0.3.0 contract.

## Installation

The core runtime requires Python 3.12 or later. Select and verify an interpreter before installing:

- Windows: `py -3.12`
- macOS: prefer `python3.12`; use `python3` only when it reports Python 3.12 or later
- Linux: prefer `python3.12`; use `python3` only when it reports Python 3.12 or later

In this documentation, `<python>` means the selected command, including both tokens in `py -3.12`. Resolve `<skill-root>` from the loaded `SKILL.md` location. After copying the complete Skill, run:

```text
<python> <skill-root>/scripts/install.py
```

The installer creates a real, artifact-local `.runtime`, installs the platform-applicable minimum runtime closure from `requirements.lock`, and runs `<python> <skill-root>/scripts/installation_health.py`. Continue only after exit code 0 with `status: "complete"`. `installation_incomplete` means required software is missing, mismatched, non-local, incomplete, or unloadable. It says nothing about provider accessibility, credentials, paid permissions, or evidence fitness.

The default lock includes `mootdx==0.11.7` for released F10, `intraday_market_signal`, and `intraday_replay` operations. Generic upstream `pip check` does not replace formal operation health. Other interfaces exposed by that package are not implicit sources or fallbacks.

## Public invocation

The only public runtime entry point is `<skill-root>/scripts/entrypoint.py`; modules under `scripts/a_share_research/` are implementation details. Invoke:

```text
<python> <skill-root>/scripts/entrypoint.py run --request <research-task.json> [--output <file>]
```

The request has exactly these top-level concepts: `schema_version`, `task_type`, `subjects`, explicit `as_of`, `window`, `parameters`, and `source_policy`. Unknown fields, old shapes under the same version, malformed values, unsupported tasks, and unsafe JSON fail closed. Never pass natural-language text as the request document.

Common source policy:

```json
{
  "allow_experimental": true,
  "allow_credentials": false,
  "allow_fallback": true
}
```

Enable only what the question and user permit. A permitted fallback must be declared by the task's operation authority; `allow_fallback` never licenses an arbitrary source switch. For opt-in evidence reuse, read [persistence-contract.md](persistence-contract.md).

## Process and JSON semantics

`stdout` contains exactly one compact JSON document with `schema_version`. A zero exit code means a valid contract result was formed; inspect its domain `status` separately:

- `supported`: material claims have evidence adopted for their use.
- `limited`: the core question is answerable with disclosed gaps or limitations.
- `blocked`: identity or critical evidence is insufficient for a substantive conclusion.

A nonzero exit means invocation, protocol, I/O, or internal processing prevented a valid result. Read the sanitized diagnostic from `stderr`; never convert it into a source fact. `--output` is opt-in result persistence: the same JSON remains on `stdout`, and no result file is created without it.

Every valid result has the same 14 top-level keys: `schema_version`, `status`, `research_anchor`, `plan`, `capability_registry`, `coverage`, `observations`, `claims`, `calculations`, `conflicts`, `limitations`, `source_errors`, `task_views`, and `briefs`. Interpret them with [evidence-contract.md](evidence-contract.md). A workflow keeps each leaf and skipped step visible; it never turns an unavailable source into “none.”

## Task-specific contracts

- Identity, trend, ETF, and ETF options: [market-contracts.md](market-contracts.md)
- Current-session snapshot and completed-session replay: [intraday-contracts.md](intraday-contracts.md)
- Valuation, materials, capital events, and market signals: [research-contracts.md](research-contracts.md)
- Six fixed research plans: [workflow-contracts.md](workflow-contracts.md)
- Explicit evidence reuse: [persistence-contract.md](persistence-contract.md)
- Facts, claims, calculations, and coverage: [evidence-contract.md](evidence-contract.md)
- Interpretation and action boundary: [analysis-boundary.md](analysis-boundary.md)

## Credentials and network behavior

Most registered operations are credential-free. Semantic iWencai search is enabled only when `source_policy.allow_credentials` is true and reads `IWENCAI_API_KEY`; `IWENCAI_BASE_URL` may select an explicitly configured compatible endpoint. Values never belong in request JSON, arguments, logs, results, examples, or fixtures.

Network research uses capability-scoped operations behind the ResearchTask Interface. Source failure remains a source-result fact and never triggers a silent source switch. Default tests replace only the external boundary with fixed responses. Development-repository live probes are explicit maintainer diagnostics, never an ordinary CI dependency, and must never update fixtures, persist provider responses, or promote one successful response to production qualification.
