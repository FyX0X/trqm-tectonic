# Query pipeline

End-to-end contract for `POST /query` and `trqm query`.

## Input

```json
{
  "theme": "Annual leave entitlement for Acme NV employee in Belgium",
  "filters": {
    "country": ["BE"],
    "company": ["Acme NV"],
    "keywords": ["leave"]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `theme` | yes | Natural-language question / theme |
| `filters.country` | no | ISO country codes to require |
| `filters.company` | no | Company names to prefer / require overlap |
| `filters.keywords` | no | Extra keywords forced into the prefilter |

If filters are omitted, the pipeline may still **infer** country/company tokens from `theme` (lightweight heuristic + optional LLM parse).

## Stages

### 1. Metadata keyword prefilter

- Load all indexed `.trqmmeta` records.
- Apply hard filter on `country` / `company` when provided.
- Score remaining docs by token overlap (Jaccard) between query tokens and `keywords ∪ topics ∪ country ∪ company ∪ title tokens`.
- Keep docs with `keyword_score > 0` (at least one shared token), or top-K by score if too many (default K = 25).

**Output item:**

```json
{
  "doc_id": "...",
  "meta": { "...": "trqmmeta object" },
  "keyword_score": 0.42
}
```

### 2. AI relevance match

- For each candidate, LLM returns `relevance_score` ∈ [0, 1] and `relevance_reason`.
- Drop if `relevance_score < 0.5`.
- Keep top `N = 8` by relevance.

```json
{
  "doc_id": "...",
  "relevance_score": 0.82,
  "relevance_reason": "Directly addresses BE leave entitlement for white-collar staff.",
  "meta": {},
  "keyword_score": 0.42
}
```

### 3. Trust layer

See [TRUST_LAYER.md](TRUST_LAYER.md).

- Run deterministic rules with query context (country/company/requester).
- Optional conflict pass across the set.
- LLM judge → `trust_assessment`.
- Filter: drop `excluded` / `trust_score < 0.40`.

### 4. Convert (FOLDL)

- Sort by `trust_score` desc.
- Fold with LLM: accumulator starts empty; each doc merges claims, citations, conflicts, gaps.
- Emit `trusted_summary` ([TRUST_LAYER.md](TRUST_LAYER.md#convert-layer-output-trusted-summary)).

## Thresholds (PoC defaults)

| Name | Value |
|------|-------|
| Keyword keep | score > 0, max 25 |
| Relevance min | 0.50 |
| Relevance top-N | 8 |
| Trust min τ | 0.40 |
| LLM trust adjustment clamp | ±0.15 |

## Output

Full pipeline returns the **trusted summary** JSON. Optional debug mode may also return intermediate arrays (`candidates`, `relevant`, `assessments`).

## Demo narrative

Query: *“leave days for Acme employee in Belgium”*

Expected behaviour with sample corpus:

1. FR policy fails country fit or low keyword/country match.
2. Undated Teams note survives relevance but low trust / conflict flag.
3. BE policy wins; summary cites it and explains trust signals; may suggest Marie Dupont as expert.
