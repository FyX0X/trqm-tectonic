from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import llm


RULE_WEIGHTS = {
    "R_FRESHNESS": 0.20,
    "R_OWNER": 0.15,
    "R_COUNTRY_FIT": 0.25,
    "R_COMPANY_FIT": 0.15,
    "R_REVIEW_DUE": 0.10,
    "R_CONFIDENTIALITY": 0.10,
    "R_CONFLICT": 0.05,
}

CONF_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

LLM_ADJUST_CLAMP = 0.15
TRUST_MIN = 0.40


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signal(
    rule_id: str,
    passed: bool,
    contribution: float,
    detail: str,
) -> dict[str, Any]:
    weight = RULE_WEIGHTS[rule_id]
    return {
        "rule_id": rule_id,
        "passed": passed,
        "weight": weight,
        "detail": detail,
        "score_contribution": max(0.0, min(1.0, contribution)),
    }


def rule_freshness(meta: dict[str, Any]) -> dict[str, Any]:
    updated = _parse_dt(meta.get("updated_at"))
    if not updated:
        return _signal("R_FRESHNESS", False, 0.15, "Missing updated_at.")
    age_days = (_now() - updated).days
    if age_days <= 90:
        c, detail = 1.0, f"Updated {meta['updated_at']} (≤90d)."
    elif age_days <= 365:
        c, detail = 0.7, f"Updated {meta['updated_at']} (≤1y)."
    elif age_days <= 730:
        c, detail = 0.4, f"Updated {meta['updated_at']} (≤2y)."
    else:
        c, detail = 0.15, f"Stale: updated {meta['updated_at']}."
    return _signal("R_FRESHNESS", c >= 0.7, c, detail)


def rule_owner(meta: dict[str, Any]) -> dict[str, Any]:
    owner = meta.get("owner")
    email = (meta.get("author") or {}).get("email")
    if owner or email:
        who = owner or email
        return _signal("R_OWNER", True, 1.0, f"Owner/author email present: {who}.")
    return _signal("R_OWNER", False, 0.0, "No owner set.")


def rule_country_fit(
    meta: dict[str, Any], query_countries: list[str] | None
) -> tuple[dict[str, Any], bool]:
    """Returns (signal, hard_exclude)."""
    if not query_countries:
        return (
            _signal("R_COUNTRY_FIT", True, 1.0, "No country filter on query (neutral)."),
            False,
        )
    meta_c = {c.upper() for c in (meta.get("country") or [])}
    want = {c.upper() for c in query_countries}
    if not meta_c:
        return (
            _signal(
                "R_COUNTRY_FIT",
                False,
                0.3,
                "Document has no country metadata.",
            ),
            False,
        )
    if meta_c & want:
        return (
            _signal(
                "R_COUNTRY_FIT",
                True,
                1.0,
                f"Doc country {sorted(meta_c)} matches query {sorted(want)}.",
            ),
            False,
        )
    return (
        _signal(
            "R_COUNTRY_FIT",
            False,
            0.0,
            f"Country mismatch: doc {sorted(meta_c)} vs query {sorted(want)}.",
        ),
        True,
    )


def rule_company_fit(
    meta: dict[str, Any], query_companies: list[str] | None
) -> dict[str, Any]:
    if not query_companies:
        return _signal(
            "R_COMPANY_FIT", True, 1.0, "No company filter on query (neutral)."
        )
    meta_co = [c.lower() for c in (meta.get("company") or [])]
    for want in query_companies:
        w = want.lower()
        if any(w in m or m in w for m in meta_co):
            return _signal(
                "R_COMPANY_FIT", True, 1.0, f"Company overlap with '{want}'."
            )
    if not meta_co:
        return _signal(
            "R_COMPANY_FIT", False, 0.4, "No company on document; query specified one."
        )
    return _signal(
        "R_COMPANY_FIT",
        False,
        0.2,
        f"No company overlap; doc={meta.get('company')} query={query_companies}.",
    )


def rule_review_due(meta: dict[str, Any]) -> dict[str, Any]:
    due = _parse_dt(meta.get("review_due_at"))
    if not due:
        return _signal("R_REVIEW_DUE", True, 0.7, "No review_due_at (partial credit).")
    if due < _now():
        return _signal(
            "R_REVIEW_DUE", False, 0.2, f"Review overdue since {meta['review_due_at']}."
        )
    return _signal("R_REVIEW_DUE", True, 1.0, f"Review due {meta['review_due_at']}.")


def rule_confidentiality(
    meta: dict[str, Any], requester_level: str = "internal"
) -> tuple[dict[str, Any], bool]:
    level = meta.get("confidentiality") or "internal"
    doc_rank = CONF_RANK.get(level, 1)
    req_rank = CONF_RANK.get(requester_level, 1)
    if doc_rank > req_rank:
        return (
            _signal(
                "R_CONFIDENTIALITY",
                False,
                0.0,
                f"Doc {level} exceeds requester {requester_level}.",
            ),
            True,
        )
    return (
        _signal(
            "R_CONFIDENTIALITY",
            True,
            1.0,
            f"Confidentiality {level} OK for requester {requester_level}.",
        ),
        False,
    )


def rule_conflict(conflicts_for_doc: list[str], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if conflicts_for_doc:
        # Ownerless / stale informal notes get a harder conflict penalty
        meta = meta or {}
        has_owner = bool(meta.get("owner") or (meta.get("author") or {}).get("email"))
        contribution = 0.35 if has_owner else 0.0
        return _signal(
            "R_CONFLICT",
            False,
            contribution,
            "; ".join(conflicts_for_doc),
        )
    return _signal("R_CONFLICT", True, 1.0, "No conflicts flagged.")


def run_rules(
    meta: dict[str, Any],
    *,
    query_countries: list[str] | None = None,
    query_companies: list[str] | None = None,
    requester_level: str = "internal",
    conflicts_for_doc: list[str] | None = None,
) -> tuple[list[dict[str, Any]], float, bool]:
    signals: list[dict[str, Any]] = []
    hard_exclude = False

    signals.append(rule_freshness(meta))
    signals.append(rule_owner(meta))
    s, excl = rule_country_fit(meta, query_countries)
    signals.append(s)
    hard_exclude = hard_exclude or excl
    signals.append(rule_company_fit(meta, query_companies))
    signals.append(rule_review_due(meta))
    s, excl = rule_confidentiality(meta, requester_level)
    signals.append(s)
    hard_exclude = hard_exclude or excl
    signals.append(rule_conflict(conflicts_for_doc or [], meta))

    rules_score = sum(
        s["weight"] * s["score_contribution"] for s in signals
    )
    rules_score = max(0.0, min(1.0, rules_score))
    if rules_score < 0.25:
        hard_exclude = True
    return signals, rules_score, hard_exclude


def verdict_for(trust_score: float, hard_exclude: bool, uncertainties: list[str]) -> str:
    if hard_exclude or trust_score < TRUST_MIN:
        return "excluded"
    if trust_score >= 0.75 and not uncertainties:
        return "trusted"
    if trust_score >= 0.55:
        return "usable_with_caveats"
    if trust_score >= TRUST_MIN:
        return "low_trust"
    return "excluded"


def expert_hint_from_meta(meta: dict[str, Any]) -> str | None:
    author = meta.get("author") or {}
    name = author.get("name")
    if not name or name.lower() == "unknown":
        if meta.get("owner"):
            return meta["owner"]
        return None
    role = author.get("role")
    if role:
        return f"{name} ({role})"
    return name


SYSTEM_TRUST = """You are a trust judge for organisational HR/payroll knowledge.
Given a theme, document metadata, excerpt, and deterministic rule signals,
return JSON:
{
  "llm_adjustment": number between -0.15 and 0.15,
  "uncertainties": [string],
  "conflicts": [string],
  "explanation": "one or two sentences"
}
Do NOT contradict hard rule failures (e.g. country mismatch). Prefer small adjustments.
Highlight residual doubt a consultant should know."""


def llm_trust_judge(
    theme: str,
    meta: dict[str, Any],
    body: str,
    signals: list[dict[str, Any]],
    rules_score: float,
) -> dict[str, Any]:
    payload = llm.chat_json(
        SYSTEM_TRUST,
        f"Theme: {theme}\nRules score: {rules_score}\n"
        f"Meta: title={meta.get('title')} country={meta.get('country')} "
        f"company={meta.get('company')} owner={meta.get('owner')} "
        f"updated_at={meta.get('updated_at')}\n"
        f"Signals: {signals}\n\nExcerpt:\n{body[:3000]}",
    )
    adj = float(payload.get("llm_adjustment") or 0)
    adj = max(-LLM_ADJUST_CLAMP, min(LLM_ADJUST_CLAMP, adj))
    return {
        "llm_adjustment": adj,
        "uncertainties": payload.get("uncertainties") or [],
        "conflicts": payload.get("conflicts") or [],
        "explanation": payload.get("explanation") or "",
    }


def heuristic_trust_judge(
    meta: dict[str, Any],
    signals: list[dict[str, Any]],
    rules_score: float,
) -> dict[str, Any]:
    uncertainties = []
    for s in signals:
        if not s["passed"] and s["rule_id"] != "R_CONFLICT":
            uncertainties.append(s["detail"])
    failed = [s["rule_id"] for s in signals if not s["passed"]]
    explanation = (
        f"Rules score {rules_score:.2f}. "
        + ("Issues: " + ", ".join(failed) if failed else "No major rule failures.")
    )
    return {
        "llm_adjustment": 0.0,
        "uncertainties": uncertainties[:5],
        "conflicts": [],
        "explanation": explanation,
    }


def assess_trust(
    theme: str,
    candidate: dict[str, Any],
    *,
    query_countries: list[str] | None = None,
    query_companies: list[str] | None = None,
    requester_level: str = "internal",
    conflicts_for_doc: list[str] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    meta = candidate["meta"]
    body = candidate.get("body") or meta.get("summary") or ""
    signals, rules_score, hard_exclude = run_rules(
        meta,
        query_countries=query_countries,
        query_companies=query_companies,
        requester_level=requester_level,
        conflicts_for_doc=conflicts_for_doc,
    )
    if use_llm:
        try:
            judged = llm_trust_judge(theme, meta, body, signals, rules_score)
        except Exception:
            judged = heuristic_trust_judge(meta, signals, rules_score)
    else:
        judged = heuristic_trust_judge(meta, signals, rules_score)

    trust_score = max(
        0.0, min(1.0, rules_score + judged["llm_adjustment"])
    )
    if hard_exclude:
        trust_score = min(trust_score, TRUST_MIN - 0.01)

    uncertainties = list(judged.get("uncertainties") or [])
    conflicts = list(judged.get("conflicts") or []) + list(conflicts_for_doc or [])
    verdict = verdict_for(trust_score, hard_exclude, uncertainties)

    return {
        "doc_id": candidate["doc_id"],
        "trust_score": round(trust_score, 4),
        "rules_score": round(rules_score, 4),
        "llm_adjustment": round(judged["llm_adjustment"], 4),
        "signals": signals,
        "uncertainties": uncertainties,
        "conflicts": conflicts,
        "expert_hint": expert_hint_from_meta(meta),
        "verdict": verdict,
        "explanation": judged.get("explanation") or "",
        # carry through for convert
        "meta": meta,
        "body": body,
        "relevance_score": candidate.get("relevance_score"),
        "relevance_reason": candidate.get("relevance_reason"),
        "keyword_score": candidate.get("keyword_score"),
    }


def detect_conflicts(assessed: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Lightweight conflict heuristic: same topic keywords, contradictory leave-day numbers."""
    import re

    day_mentions: list[tuple[str, str]] = []
    for a in assessed:
        text = (a.get("body") or "") + " " + (a.get("meta") or {}).get("summary", "")
        for m in re.finditer(r"(\d+)\s*(?:days?|journ[ée]es?)", text.lower()):
            day_mentions.append((a["doc_id"], m.group(0)))
    conflicts: dict[str, list[str]] = {a["doc_id"]: [] for a in assessed}
    if len({d for _, d in day_mentions}) > 1 and len({i for i, _ in day_mentions}) > 1:
        ids = sorted({i for i, _ in day_mentions})
        note = f"Conflicting leave-day figures across {ids}: " + "; ".join(
            f"{i}→{d}" for i, d in day_mentions[:6]
        )
        for i in ids:
            conflicts[i].append(note)
    return conflicts


def map_and_filter_trust(
    theme: str,
    candidates: list[dict[str, Any]],
    *,
    query_countries: list[str] | None = None,
    query_companies: list[str] | None = None,
    use_llm: bool = True,
    trust_min: float = TRUST_MIN,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (kept assessments, discarded with reason stub)."""
    # First pass without conflicts
    draft = [
        assess_trust(
            theme,
            c,
            query_countries=query_countries,
            query_companies=query_companies,
            use_llm=use_llm,
        )
        for c in candidates
    ]
    conflict_map = detect_conflicts(draft)
    # Re-assess if conflicts found
    if any(conflict_map.values()):
        draft = [
            assess_trust(
                theme,
                {
                    "doc_id": a["doc_id"],
                    "meta": a["meta"],
                    "body": a.get("body") or "",
                    "relevance_score": a.get("relevance_score"),
                    "relevance_reason": a.get("relevance_reason"),
                    "keyword_score": a.get("keyword_score"),
                },
                query_countries=query_countries,
                query_companies=query_companies,
                conflicts_for_doc=conflict_map.get(a["doc_id"]),
                use_llm=use_llm,
            )
            for a in draft
        ]

    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for a in draft:
        if a["verdict"] == "excluded" or a["trust_score"] < trust_min:
            discarded.append(
                {
                    "doc_id": a["doc_id"],
                    "reason": a["explanation"]
                    or f"verdict={a['verdict']} score={a['trust_score']}",
                    "assessment": a,
                }
            )
        else:
            kept.append(a)

    # Prefer owned high-trust sources over ownerless conflicting notes
    has_strong = any(
        (x.get("meta") or {}).get("owner") and x["trust_score"] >= 0.7 for x in kept
    )
    if has_strong:
        still_kept: list[dict[str, Any]] = []
        for a in kept:
            meta = a.get("meta") or {}
            ownerless = not (
                meta.get("owner") or (meta.get("author") or {}).get("email")
            )
            if ownerless and a.get("conflicts"):
                discarded.append(
                    {
                        "doc_id": a["doc_id"],
                        "reason": (
                            "Ownerless source conflicts with higher-trust owned documents."
                        ),
                        "assessment": a,
                    }
                )
            else:
                still_kept.append(a)
        kept = still_kept

    kept.sort(key=lambda x: x["trust_score"], reverse=True)
    return kept, discarded
