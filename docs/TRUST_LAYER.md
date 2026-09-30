# Trust layer

After keyword prefilter and AI relevance ranking, each surviving candidate is scored for **trustworthiness**. Trust is **rules + LLM**: deterministic signals first, then an LLM judge that explains and may adjust within a small clamp. Output is structured so the UI can show *why* something is (or is not) reliable.

Formal schemas:

- [`schemas/trust_assessment.schema.json`](../schemas/trust_assessment.schema.json)
- [`schemas/trusted_summary.schema.json`](../schemas/trusted_summary.schema.json)

## Goals (SD Worx moment of doubt)

Make visible:

- What is current?
- What applies to this country / company?
- Who owns it?
- Where do sources conflict?
- Who to ask when documents are not enough?

## Deterministic rules

Each rule emits a **signal**: `{ rule_id, passed, weight, detail, score_contribution }`.

| Rule ID | Weight (default) | Behaviour |
|---------|------------------|-----------|
| `R_FRESHNESS` | 0.20 | Score from `updated_at` age: ≤90d → 1.0, ≤365d → 0.7, ≤730d → 0.4, older/missing → 0.15 |
| `R_OWNER` | 0.15 | Pass if `owner` or `author.email` present; else fail (contribution 0) |
| `R_COUNTRY_FIT` | 0.25 | If query has country: pass if intersection with `meta.country`; empty meta country → 0.3; mismatch → 0 and may force `excluded` |
| `R_COMPANY_FIT` | 0.15 | If query has company: pass on overlap (case-insensitive); else N/A (neutral 1.0 contribution for this weight when filter absent) |
| `R_REVIEW_DUE` | 0.10 | Fail if `review_due_at` &lt; now; missing due date → 0.7 |
| `R_CONFIDENTIALITY` | 0.10 | Fail/block if doc level above requester context (PoC default requester = `internal`) |
| `R_CONFLICT` | 0.05 | Set after pairwise/LLM conflict pass; fail if this doc contradicts a higher-trust peer |

Weights should sum to **1.0**. Neutral/N/A rules still contribute their weight × 1.0 so absence of a filter does not punish the doc.

### Rules score formula

```
rules_score = Σ (weight_i × contribution_i)
contribution_i ∈ [0, 1]
```

Clamp result to `[0, 1]`.

### Hard exclude

A candidate is marked `verdict: excluded` (and dropped in filter mode) when:

- `R_COUNTRY_FIT` hard-mismatch (query country set and no intersection), or
- `R_CONFIDENTIALITY` blocks access, or
- `rules_score < 0.25` after signals (optional safety net)

## LLM trust judge

**Input:** theme, document excerpt (or summary + keywords), `.trqmmeta`, and the rule signal list.

**Role:** Explain trust in plain language; optionally adjust score by `llm_adjustment ∈ [-0.15, +0.15]`.

```
trust_score = clamp(rules_score + llm_adjustment, 0, 1)
```

The LLM must **not** invent new hard facts that contradict rule failures (e.g. cannot claim country fit if `R_COUNTRY_FIT` failed).

### Verdict mapping

| Verdict | Typical condition |
|---------|-------------------|
| `trusted` | `trust_score ≥ 0.75` and no hard caveats |
| `usable_with_caveats` | `0.55 ≤ trust_score < 0.75` or uncertainties non-empty |
| `low_trust` | `0.40 ≤ trust_score < 0.55` |
| `excluded` | hard exclude or `trust_score < 0.40` |

## Trust assessment output

One object per candidate document:

```json
{
  "doc_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "trust_score": 0.74,
  "rules_score": 0.71,
  "llm_adjustment": 0.03,
  "signals": [
    {
      "rule_id": "R_COUNTRY_FIT",
      "passed": true,
      "weight": 0.25,
      "detail": "Doc country BE matches query."
    },
    {
      "rule_id": "R_FRESHNESS",
      "passed": true,
      "weight": 0.2,
      "detail": "Updated 2026-01-15."
    },
    {
      "rule_id": "R_OWNER",
      "passed": false,
      "weight": 0.15,
      "detail": "No owner set."
    }
  ],
  "uncertainties": ["Owner missing; confirm with payroll BE lead."],
  "conflicts": [],
  "expert_hint": "Marie Dupont (Payroll Specialist BE)",
  "verdict": "usable_with_caveats",
  "explanation": "Relevant and country-aligned, but ownership gap reduces confidence."
}
```

### Field notes

| Field | Meaning |
|-------|---------|
| `signals` | Full rule trace for UI transparency |
| `uncertainties` | Residual doubt for the human |
| `conflicts` | Short strings or objects describing clashes with other candidates |
| `expert_hint` | Derived from `author` / `owner` when present |
| `explanation` | One or two sentences for the consultant |

## Map or filter

- **Map:** attach assessment to every relevance survivor.
- **Filter:** drop `excluded` and `trust_score < τ` (default `τ = 0.40`).

PoC default: **map then filter** before convert.

## Convert layer output (trusted summary)

Left-fold (FOLDL) over remaining docs ordered by `trust_score` descending. Final OUT object:

```json
{
  "theme": "Annual leave entitlement for Acme NV employee in Belgium",
  "answer": "Markdown answer with citations [doc_id]...",
  "overall_confidence": 0.78,
  "used_sources": [
    {
      "doc_id": "...",
      "title": "...",
      "trust_score": 0.74,
      "why_trusted": "Country match + recent update; caveats: no owner."
    }
  ],
  "discarded_sources": [
    {
      "doc_id": "...",
      "reason": "Country FR — not applicable."
    }
  ],
  "conflicts_detected": [
    {
      "claim": "Number of statutory leave days",
      "sources": ["doc-a", "doc-b"],
      "resolution": "Prefer newer BE policy doc."
    }
  ],
  "gaps": ["No client-specific CBA override found."],
  "ask_expert": {
    "name": "Marie Dupont",
    "reason": "Confirm edge case / missing owner."
  },
  "generated_at": "2026-09-30T18:05:00Z"
}
```

`why_trusted` must cite concrete signals (freshness, country, owner), not vague “AI confidence.”
