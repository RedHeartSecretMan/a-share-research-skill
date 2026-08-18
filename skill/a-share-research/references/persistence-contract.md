# Evidence persistence contract

The runtime is stateless by default. Without `source_policy.evidence_store`, it creates or accesses no evidence file, lock, cache, database, user configuration, or global provider configuration.

Use evidence persistence only when the user explicitly wants qualified observations reused across requests. Its exact policy shape is:

```json
{
  "mode": "read_write",
  "path": "/absolute/path/to/research-evidence.json"
}
```

Use `mode: "delete"` to remove only the current canonical operation records while preserving unrelated records. The path must be absolute; its parent must already be a real directory; the leaf must be absent or a regular file; and no ancestor, leaf, lock file, symlink, or reparse point may redirect access. The path is never copied into public output or diagnostics.

Persistence is available only on hosts with anchored directory-descriptor semantics that prevent parent-directory replacement races. Unsupported platforms fail closed before path, lock, read, write, or delete access. This does not affect stateless requests.

## Eligible tasks and evidence

Only `market_trend`, `research_content`, and `security_valuation` accept `evidence_store`.

Eligible outcomes are:

- completed unadjusted or forward-adjusted historical daily bars;
- announcements and research reports whose source operation establishes the license boundary;
- stable published financial statements.

Ineligible content remains freshly acquired or is rejected before filesystem access: news, market flashes, investor Q&A, F10, consensus material, current valuation snapshots, and intraday observations. Content with `license_boundary.status: not_established` is never persisted as reusable current evidence. A non-eligible operation reports `not_permitted / not_written` without touching the path.

## Stored shape and reuse

Each record contains only bounded, normalized, operation-owned evidence/outcome plus the audit facts needed to revalidate it. It excludes claims, calculations, views, briefs, credentials, request headers, private paths, and unlicensed complete provider payloads.

A hit must revalidate request scope, canonical subject, owner, source version, research boundary, freshness, fitness for use, and field dispositions, then rerun the current fold and serializer. Acquisition time is the operation completion boundary—the latest `retrieved_at` among its normalized observations—not publication, event, first-availability, or result-generation time.

`plan.evidence_storage` exposes only sanitized policy and scope. Each operation's coverage records lookup/write state, source version, original/result acquisition time, freshness, fitness, and a safe reason. Never describe a cache hit as fresh acquisition or let persisted evidence bypass current validation.
