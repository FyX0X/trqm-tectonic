from __future__ import annotations

import re
from typing import Any

from . import llm
from .store import Store

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "will",
    "have", "has", "not", "but", "you", "your", "our", "any", "what", "when",
    "where", "which", "about", "into", "than", "then", "them", "they", "their",
    "employee", "employees",  # keep domain terms actually — remove employee from stop
}

# Remove overly aggressive domain stops
STOPWORDS -= {"employee", "employees"}

COUNTRY_ALIASES = {
    "belgium": "BE",
    "belgian": "BE",
    "france": "FR",
    "french": "FR",
    "netherlands": "NL",
    "dutch": "NL",
    "germany": "DE",
    "german": "DE",
}


def tokenize(text: str) -> set[str]:
    tokens = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']+", text.lower()):
        if len(raw) < 2 or raw in STOPWORDS:
            continue
        tokens.add(raw)
        if raw in COUNTRY_ALIASES:
            tokens.add(COUNTRY_ALIASES[raw].lower())
    return tokens


def infer_filters_from_theme(theme: str) -> dict[str, list[str]]:
    tokens = tokenize(theme)
    countries = []
    for name, code in COUNTRY_ALIASES.items():
        if name in tokens or code.lower() in tokens:
            if code not in countries:
                countries.append(code)
    companies = []
    # Simple proper-noun-ish capture: sequences with NV/SA/BV/Ltd or known demo names
    for match in re.finditer(
        r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*(?:\s+(?:NV|SA|BV|Ltd|Inc))?)\b",
        theme,
    ):
        phrase = match.group(1)
        if phrase.lower() in COUNTRY_ALIASES:
            continue
        if len(phrase) >= 3:
            companies.append(phrase)
    # Demo client
    if "acme" in theme.lower():
        companies.append("Acme NV")
    return {"country": countries, "company": companies}


def meta_token_set(meta: dict[str, Any]) -> set[str]:
    parts: list[str] = []
    parts.extend(meta.get("keywords") or [])
    parts.extend(meta.get("topics") or [])
    parts.extend(meta.get("country") or [])
    parts.extend(meta.get("company") or [])
    if meta.get("title"):
        parts.append(meta["title"])
    if meta.get("summary"):
        parts.append(meta["summary"])
    return tokenize(" ".join(str(p) for p in parts))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _country_ok(meta: dict[str, Any], countries: list[str] | None) -> bool:
    if not countries:
        return True
    meta_c = {c.upper() for c in (meta.get("country") or [])}
    want = {c.upper() for c in countries}
    return bool(meta_c & want)


def _company_ok(meta: dict[str, Any], companies: list[str] | None) -> bool:
    if not companies:
        return True
    meta_co = [c.lower() for c in (meta.get("company") or [])]
    for want in companies:
        w = want.lower()
        if any(w in m or m in w for m in meta_co):
            return True
    return False


def keyword_prefilter(
    store: Store,
    theme: str,
    filters: dict[str, Any] | None = None,
    *,
    max_candidates: int = 25,
) -> list[dict[str, Any]]:
    filters = dict(filters or {})
    inferred = infer_filters_from_theme(theme)
    countries = filters.get("country") or inferred.get("country") or []
    companies = filters.get("company") or []
    # Prefer explicit company filter; else use inferred only as soft boost tokens
    hard_companies = filters.get("company") or []

    extra_kw = filters.get("keywords") or []
    query_tokens = tokenize(theme) | tokenize(" ".join(extra_kw))
    query_tokens |= {c.lower() for c in countries}
    query_tokens |= tokenize(" ".join(inferred.get("company") or []))

    results: list[dict[str, Any]] = []
    for record in store.list_docs():
        meta = record.meta
        if countries and not _country_ok(meta, countries):
            # Still allow through with score 0 only if no country on meta? Plan: hard filter when set
            continue
        if hard_companies and not _company_ok(meta, hard_companies):
            continue
        doc_tokens = meta_token_set(meta)
        overlap = len(query_tokens & doc_tokens)
        score = jaccard(query_tokens, doc_tokens)
        if overlap < 1 and score <= 0:
            continue
        results.append(
            {
                "doc_id": record.doc_id,
                "meta": meta,
                "body": record.body,
                "keyword_score": round(score, 4),
                "token_overlap": overlap,
            }
        )
    results.sort(key=lambda r: (r["keyword_score"], r["token_overlap"]), reverse=True)
    return results[:max_candidates]


SYSTEM_RELEVANCE = """You score how relevant a document is to a user theme/question
in an HR/payroll knowledge base. Return JSON:
{
  "relevance_score": number between 0 and 1,
  "relevance_reason": "one short sentence"
}
Score highly only if the document would help answer the theme for the right
country/company context. Wrong-country docs should score low."""


def score_relevance(theme: str, candidate: dict[str, Any]) -> dict[str, Any]:
    meta = candidate["meta"]
    excerpt = (candidate.get("body") or meta.get("summary") or "")[:4000]
    payload = llm.chat_json(
        SYSTEM_RELEVANCE,
        f"Theme: {theme}\n\nTitle: {meta.get('title')}\n"
        f"Country: {meta.get('country')}\nCompany: {meta.get('company')}\n"
        f"Keywords: {meta.get('keywords')}\nSummary: {meta.get('summary')}\n\n"
        f"Excerpt:\n{excerpt}",
    )
    score = float(payload.get("relevance_score", 0))
    score = max(0.0, min(1.0, score))
    return {
        **candidate,
        "relevance_score": score,
        "relevance_reason": payload.get("relevance_reason") or "",
    }


def heuristic_relevance(theme: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Offline relevance using keyword overlap + country/company boost."""
    meta = candidate["meta"]
    q = tokenize(theme)
    d = meta_token_set(meta)
    j = jaccard(q, d)
    kw = float(candidate.get("keyword_score") or 0)
    overlap = int(candidate.get("token_overlap") or 0)
    # Blend signals so prefilter survivors with modest Jaccard still clear 0.5
    score = max(j * 1.6, kw * 1.4) + 0.08 * min(overlap, 6)
    # Prefer owned/fresh-looking docs slightly in heuristic mode
    if meta.get("owner") or (meta.get("author") or {}).get("email"):
        score += 0.08
    if meta.get("updated_at", "").startswith("2026") or meta.get("updated_at", "").startswith("2025"):
        score += 0.05
    score = max(0.0, min(1.0, score))
    reason = (
        f"Heuristic relevance {score:.2f} "
        f"(jaccard={j:.2f}, keyword_score={kw:.2f}, overlap={overlap})."
    )
    return {**candidate, "relevance_score": round(score, 4), "relevance_reason": reason}


def ai_relevance_match(
    theme: str,
    candidates: list[dict[str, Any]],
    *,
    min_score: float = 0.5,
    top_n: int = 8,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for cand in candidates:
        if use_llm:
            try:
                scored.append(score_relevance(theme, cand))
            except Exception:
                scored.append(heuristic_relevance(theme, cand))
        else:
            scored.append(heuristic_relevance(theme, cand))
    scored = [s for s in scored if s["relevance_score"] >= min_score]
    scored.sort(key=lambda r: r["relevance_score"], reverse=True)
    return scored[:top_n]
