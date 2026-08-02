---
name: a-share-research
description: Produce auditable A-share evidence with a deterministic CLI for canonical identity resolution, latest completed unadjusted-close cross-checks, evidence-bundle validation, and provided-evidence market-capitalization, PE TTM, and PB MRQ calculations. Use when a user needs evidence-backed research on an SSE or SZSE A-share with an explicit as-of date, provenance, calculation lineage, conflicts, and limitations. Do not use for recommendations, price targets, trading, broad company research, or unsupported live-data claims.
---

# A-Share Research

Use the bundled deterministic CLI to gather and calculate research evidence. Interpret the structured result for the user; do not replace missing evidence with model knowledge or an unsupported estimate.

## Workflow

1. Identify the user's research question. Support only identity resolution, completed-daily-close research, evidence-bundle validation, and provided-evidence valuation; state unsupported scope plainly.
2. Resolve relative dates such as “current”, “today”, or “yesterday” in China Standard Time. Pass only an explicit `YYYY-MM-DD` date to the CLI.
3. Treat a name, abbreviation, or bare code as a security clue. Run `resolve` and ask the user to choose when the result requires clarification. Never guess the exchange.
4. Choose one research path:
   - For experimental-source identity or close research, run `resolve`, then run `close` only with the returned canonical SSE/SZSE identifier.
   - For caller-provided evidence, run `validate-bundle`, resolve every reported contract error, then run `valuation` with the same bundle and research date.
5. Parse the versioned JSON from `stdout`. After `valuation`, confirm its `research.question` matches the user's requested capability. If the bundle asks a different question, explain that mismatch and present only the result the CLI actually formed. Do not expect `research.question` from `resolve`, `close`, or `validate-bundle`.
6. Treat `stderr` and a nonzero exit as invocation, protocol, I/O, or internal failure—not as research evidence. Treat a zero-exit `limited` or `blocked` JSON result as a valid research result.
7. Always disclose that `resolve` and `close` use experimental source operations. Their observations can expose agreement or conflict but cannot alone establish a `supported` factual claim.
8. Follow [references/evidence-contract.md](references/evidence-contract.md) when presenting claims, evidence, conflicts, and limitations.
9. Follow [references/valuation-methodology.md](references/valuation-methodology.md) when explaining market capitalization, PE TTM, and PB MRQ.
10. Read [references/cli-contract.md](references/cli-contract.md) when choosing commands, interpreting protocol fields, or invoking the Skill on Windows, macOS, or Linux.

## CLI

Resolve `<python>` and the script path as described in [references/cli-contract.md](references/cli-contract.md), then invoke exactly one fixed subcommand:

```text
<python> scripts/entrypoint.py resolve --query <security-clue> --as-of <YYYY-MM-DD>
<python> scripts/entrypoint.py close --security <SSE:code|SZSE:code> --as-of <YYYY-MM-DD>
<python> scripts/entrypoint.py validate-bundle --bundle <bundle-directory>
<python> scripts/entrypoint.py valuation --bundle <bundle-directory> --as-of <YYYY-MM-DD>
```

Treat `scripts/entrypoint.py` as the Skill's only public runtime entry point. Resolve its path relative to this `SKILL.md`; do not invoke implementation modules directly or assume a platform-specific home directory or shell.

The CLI does not interpret natural language or call a model. Do not pass a natural-language research request, credentials, or secrets as arguments. The first release has no credentialed Adapter and reads no credentials. If a qualified Adapter later documents an optional credential, provide it only through that Adapter's named environment variable—never a command argument—and never repeat its value in output or diagnostics.

## Present the result

- Lead with the direct answer and the overall result status.
- Name the security, exchange, issuer, requested date, actual valuation trading date, and retrieval time.
- Present every requested metric with its value and unit or its explicit status.
- Never invent a metric status or field that is absent from the CLI JSON.
- Label source facts, project calculations, and model inference separately.
- Cite the evidence locator supplied by the CLI near the claim it supports.
- List conflicts and unavailable, rejected, stale, or source-unverified evidence explicitly.
- Treat `no_valuation_meaning` as a valid evidence result, not as a missing value.
- When the result is blocked, report the blocking evidence gap and stop; do not improvise a number.
- Do not label a security cheap or expensive and do not give buy, sell, or position-sizing advice.

## Integrity rules

- Never infer an exchange from code prefixes alone after identity resolution fails.
- Never describe an issuer relationship as verified when the resolved candidate reports `issuer.security_relationship` as `unverified`.
- Never use information first published after the research boundary.
- Never treat a provider-computed PE or PB as the project's calculated metric.
- Never silently substitute float shares for total shares, forecast profit for reported profit, or adjusted price for an unadjusted close.
- Never hide disagreement between otherwise applicable sources.
- Never expose credentials in output, logs, saved results, or test artifacts.
- Leave the final investment judgment to the researcher.
