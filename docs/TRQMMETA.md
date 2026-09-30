# `.trqmmeta` files

Sidecar JSON metadata for every document in the TRQM knowledge store. Generated (or refreshed) by AI at ingest so queries can prefilter by keywords, country, and company before deeper LLM steps.

## Naming convention

| Document | Sidecar |
|----------|---------|
| `data/docs/be-payroll-leave.md` | `data/docs/be-payroll-leave.trqmmeta` |
| `path/to/foo.pdf` | `path/to/foo.trqmmeta` |

Rules:

- Same directory and basename as the source document.
- Extension is always `.trqmmeta` (replaces the source extension).
- UTF-8 JSON, 2-space indent preferred for human readability.
- Machine writers may emit compact JSON; parsers must accept either.

## Schema (v1)

Formal schema: [`schemas/trqmmeta.schema.json`](../schemas/trqmmeta.schema.json).

### Required fields (PoC)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Currently `"1.0"` |
| `doc_id` | string | Stable UUID (or hash of path); unique in the store |
| `author` | object | `{ name, email?, role? }` — at least `name` |
| `country` | string[] | ISO 3166-1 alpha-2 codes, e.g. `["BE"]` |
| `company` | string[] | Client or internal org names this doc applies to |
| `keywords` | string[] | Searchable terms extracted from the body |
| `updated_at` | string | ISO-8601 datetime of last known content update |
| `summary` | string | One-paragraph abstract |

### Recommended fields

| Field | Type | Description |
|-------|------|-------------|
| `source_path` | string | Relative path from repo/data root |
| `title` | string | Human title |
| `topics` | string[] | Coarser topics than keywords (`leave`, `payroll`) |
| `language` | string | BCP 47 / ISO 639-1, default `en` |
| `created_at` | string | ISO-8601 |
| `owner` | string | Email of accountable owner (trust signal) |
| `review_due_at` | string | ISO-8601; past due lowers trust |
| `confidentiality` | string | `public` \| `internal` \| `confidential` \| `restricted` |
| `generated_at` | string | When this meta file was (re)generated |
| `generator` | object | `{ model, prompt_version }` |

### Example

```json
{
  "schema_version": "1.0",
  "doc_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "source_path": "data/docs/be-payroll-leave.md",
  "title": "Belgium annual leave policy",
  "author": {
    "name": "Marie Dupont",
    "email": "marie.dupont@example.com",
    "role": "Payroll Specialist BE"
  },
  "country": ["BE"],
  "company": ["Acme NV", "SD Worx Internal"],
  "keywords": ["annual leave", "Belgium", "payroll", "entitlement"],
  "topics": ["leave", "payroll"],
  "language": "en",
  "created_at": "2024-03-01T00:00:00Z",
  "updated_at": "2026-01-15T00:00:00Z",
  "owner": "marie.dupont@example.com",
  "review_due_at": "2026-07-15T00:00:00Z",
  "confidentiality": "internal",
  "summary": "Defines statutory annual leave entitlement for white-collar employees in Belgium.",
  "generated_at": "2026-09-30T18:00:00Z",
  "generator": {
    "model": "gpt-4o-mini",
    "prompt_version": "trqmmeta-v1"
  }
}
```

## Ingest flow

1. Accept upload or path under `data/docs/` (`POST /docs` or `trqm ingest`).
2. Extract text (`.md` / `.txt` in PoC).
3. Call LLM with prompt version `trqmmeta-v1` to extract author, country, company, keywords, topics, title, summary, and date hints.
4. Merge user overrides (explicit country/company/owner win over AI).
5. Assign `doc_id` if new; reuse existing `doc_id` on re-ingest of the same path.
6. Write `{basename}.trqmmeta` next to the document.
7. Upsert SQLite index row (keywords, country, company, updated_at, path).

Re-ingest is idempotent: refreshes fields and bumps `generated_at`.

## Generation prompt contract (`trqmmeta-v1`)

The model must return **only** JSON matching the required + recommended fields above (minus `doc_id`, `source_path`, `generated_at`, `generator` — filled by the pipeline).

Guidance given to the model:

- Prefer ISO country codes.
- Keywords: 5–15 concrete terms from the document.
- If author/owner unknown, set `author.name` to `"unknown"` and omit `owner`.
- If dates unknown, omit `created_at` / `updated_at` so trust rules can flag missing freshness.

## Keyword prefilter usage

Query tokenizes `theme` plus optional filters (`country`, `company`, `keywords`). A document matches when:

- Explicit filter country (if set) intersects `meta.country`, **and**
- Explicit filter company (if set) intersects `meta.company` (case-insensitive substring OK), **and**
- Jaccard / overlap score over `keywords ∪ topics ∪ country ∪ company` vs query tokens is **≥ 1 shared token** (or score > 0).

See [QUERY_PIPELINE.md](QUERY_PIPELINE.md).
