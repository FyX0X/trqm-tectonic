from __future__ import annotations

from typing import Any

from .convert import convert_foldl
from .filter import ai_relevance_match, infer_filters_from_theme, keyword_prefilter
from .store import Store
from .trust import map_and_filter_trust


def run_query(
    store: Store,
    theme: str,
    filters: dict[str, Any] | None = None,
    *,
    use_llm: bool = True,
    debug: bool = False,
) -> dict[str, Any]:
    filters = dict(filters or {})
    inferred = infer_filters_from_theme(theme)
    query_countries = filters.get("country") or inferred.get("country") or []
    # Explicit company filters are hard; inferred names are soft signals for trust scoring only
    query_companies = filters.get("company") or inferred.get("company") or []

    candidates = keyword_prefilter(store, theme, filters)
    relevant = ai_relevance_match(theme, candidates, use_llm=use_llm)
    trusted, discarded = map_and_filter_trust(
        theme,
        relevant,
        query_countries=query_countries or None,
        query_companies=query_companies or None,
        use_llm=use_llm,
    )

    summary = convert_foldl(theme, trusted, discarded, use_llm=use_llm)
    if not debug:
        return summary
    return {
        "summary": summary,
        "debug": {
            "filters_effective": {
                "country": query_countries,
                "company": query_companies,
            },
            "candidates": [
                {
                    "doc_id": c["doc_id"],
                    "keyword_score": c["keyword_score"],
                    "title": c["meta"].get("title"),
                }
                for c in candidates
            ],
            "relevant": [
                {
                    "doc_id": r["doc_id"],
                    "relevance_score": r["relevance_score"],
                    "relevance_reason": r["relevance_reason"],
                }
                for r in relevant
            ],
            "trusted": [
                {
                    "doc_id": t["doc_id"],
                    "trust_score": t["trust_score"],
                    "verdict": t["verdict"],
                }
                for t in trusted
            ],
            "discarded": [
                {"doc_id": d["doc_id"], "reason": d["reason"]} for d in discarded
            ],
        },
    }
