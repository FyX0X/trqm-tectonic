from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import llm


SYSTEM_FOLD = """You merge organisational knowledge into one trusted answer for a consultant.
You receive an accumulator (may be empty) and the next trusted document with its trust assessment.
Return JSON:
{
  "answer": "markdown answer so far, with citations like [doc_id]",
  "overall_confidence": number 0-1,
  "used_sources": [{"doc_id", "title", "trust_score", "why_trusted"}],
  "conflicts_detected": [{"claim", "sources", "resolution"}],
  "gaps": [string],
  "ask_expert": {"name", "reason"} or null
}
Prefer higher trust_score sources when claims conflict.
why_trusted must cite concrete signals (country, freshness, owner), not vague AI confidence.
Keep answer concise and actionable for SD Worx payroll/HR context."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _why_trusted(assessment: dict[str, Any]) -> str:
    parts = []
    for s in assessment.get("signals") or []:
        if s.get("passed"):
            parts.append(s["detail"])
    caveats = [s["detail"] for s in assessment.get("signals") or [] if not s.get("passed")]
    text = " ".join(parts[:3]) if parts else assessment.get("explanation") or "Passed trust filter."
    if caveats:
        text += " Caveats: " + "; ".join(caveats[:2])
    return text


def _empty_acc(theme: str) -> dict[str, Any]:
    return {
        "theme": theme,
        "answer": "",
        "overall_confidence": 0.0,
        "used_sources": [],
        "conflicts_detected": [],
        "gaps": [],
        "ask_expert": None,
    }


def fold_step_llm(theme: str, acc: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    meta = assessment.get("meta") or {}
    payload = llm.chat_json(
        SYSTEM_FOLD,
        f"Theme: {theme}\n\nAccumulator:\n{acc}\n\nNext document:\n"
        f"doc_id={assessment['doc_id']}\n"
        f"title={meta.get('title')}\n"
        f"trust_score={assessment['trust_score']}\n"
        f"verdict={assessment['verdict']}\n"
        f"explanation={assessment.get('explanation')}\n"
        f"signals={assessment.get('signals')}\n"
        f"uncertainties={assessment.get('uncertainties')}\n"
        f"expert_hint={assessment.get('expert_hint')}\n"
        f"summary={meta.get('summary')}\n"
        f"body:\n{(assessment.get('body') or '')[:3500]}",
    )
    # Ensure used_sources includes this doc if model omitted
    used = payload.get("used_sources") or acc.get("used_sources") or []
    ids = {u.get("doc_id") for u in used}
    if assessment["doc_id"] not in ids:
        used.append(
            {
                "doc_id": assessment["doc_id"],
                "title": meta.get("title") or "",
                "trust_score": assessment["trust_score"],
                "why_trusted": _why_trusted(assessment),
            }
        )
    return {
        "theme": theme,
        "answer": payload.get("answer") or acc.get("answer") or "",
        "overall_confidence": float(
            payload.get("overall_confidence")
            if payload.get("overall_confidence") is not None
            else max(acc.get("overall_confidence") or 0, assessment["trust_score"])
        ),
        "used_sources": used,
        "conflicts_detected": payload.get("conflicts_detected")
        or acc.get("conflicts_detected")
        or [],
        "gaps": payload.get("gaps") or acc.get("gaps") or [],
        "ask_expert": payload.get("ask_expert")
        if "ask_expert" in payload
        else acc.get("ask_expert"),
    }


def fold_step_heuristic(theme: str, acc: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    meta = assessment.get("meta") or {}
    title = meta.get("title") or assessment["doc_id"]
    snippet = (meta.get("summary") or (assessment.get("body") or "").strip().split("\n\n")[0])[
        :500
    ]
    citation = f"[{assessment['doc_id']}]"
    block = f"### {title} {citation}\n\n{snippet}\n\n*Trust: {assessment['trust_score']:.2f} — {_why_trusted(assessment)}*\n"
    answer = (acc.get("answer") or "").rstrip() + "\n\n" + block
    used = list(acc.get("used_sources") or [])
    used.append(
        {
            "doc_id": assessment["doc_id"],
            "title": title,
            "trust_score": assessment["trust_score"],
            "why_trusted": _why_trusted(assessment),
        }
    )
    gaps = list(acc.get("gaps") or [])
    for u in assessment.get("uncertainties") or []:
        if u not in gaps:
            gaps.append(u)
    conflicts = list(acc.get("conflicts_detected") or [])
    for c in assessment.get("conflicts") or []:
        conflicts.append(
            {
                "claim": c,
                "sources": [assessment["doc_id"]],
                "resolution": "Prefer higher trust_score sources; verify with owner.",
            }
        )
    ask = acc.get("ask_expert")
    if not ask and assessment.get("expert_hint"):
        ask = {
            "name": assessment["expert_hint"],
            "reason": "Document owner/author; confirm residual uncertainties.",
        }
    confidences = [u["trust_score"] for u in used]
    overall = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "theme": theme,
        "answer": answer.strip(),
        "overall_confidence": round(overall, 4),
        "used_sources": used,
        "conflicts_detected": conflicts,
        "gaps": gaps,
        "ask_expert": ask,
    }


def convert_foldl(
    theme: str,
    trusted: list[dict[str, Any]],
    discarded: list[dict[str, Any]] | None = None,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    acc = _empty_acc(theme)
    for assessment in trusted:
        if use_llm:
            try:
                acc = fold_step_llm(theme, acc, assessment)
            except Exception:
                acc = fold_step_heuristic(theme, acc, assessment)
        else:
            acc = fold_step_heuristic(theme, acc, assessment)

    discarded_out = []
    for d in discarded or []:
        discarded_out.append(
            {"doc_id": d["doc_id"], "reason": d.get("reason") or "Filtered by trust layer."}
        )

    if not trusted:
        acc["answer"] = (
            "No sufficiently trusted documents matched this theme. "
            "Try broadening filters or ask a domain expert."
        )
        acc["gaps"] = ["No trusted sources after filter → trust pipeline."]
        acc["overall_confidence"] = 0.0

    return {
        "theme": theme,
        "answer": acc.get("answer") or "",
        "overall_confidence": float(acc.get("overall_confidence") or 0),
        "used_sources": acc.get("used_sources") or [],
        "discarded_sources": discarded_out,
        "conflicts_detected": acc.get("conflicts_detected") or [],
        "gaps": acc.get("gaps") or [],
        "ask_expert": acc.get("ask_expert"),
        "generated_at": _now(),
    }
