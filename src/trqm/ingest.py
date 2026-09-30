from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm
from .store import ROOT, Store


SYSTEM_META = """You extract structured metadata from organisational knowledge documents
(HR, payroll, policy). Return a single JSON object with these fields:
- title (string)
- author: { name, email (optional), role (optional) } — use name "unknown" if unclear
- country: array of ISO 3166-1 alpha-2 codes found or clearly implied
- company: array of company / client / organisation names the doc applies to
- keywords: 5-15 concrete search terms from the document
- topics: 1-5 coarse topics (e.g. leave, payroll)
- language: ISO 639-1 code
- created_at / updated_at: ISO-8601 datetimes if stated or strongly implied; omit if unknown
- owner: email if an accountable owner is stated; omit if unknown
- review_due_at: ISO-8601 if a review date appears; omit if unknown
- confidentiality: one of public, internal, confidential, restricted (default internal)
- summary: one paragraph abstract
Do not invent precise legal claims. Prefer empty arrays over guesses for country/company."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _rel_path(source: Path) -> str:
    try:
        return str(source.relative_to(ROOT))
    except ValueError:
        return str(source)


def generate_meta(
    body: str,
    *,
    source_path: str,
    overrides: dict[str, Any] | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    extracted = llm.chat_json(
        SYSTEM_META,
        f"Document path: {source_path}\n\n---\n{body[:12000]}\n---",
    )
    overrides = overrides or {}
    meta: dict[str, Any] = {
        "schema_version": "1.0",
        "doc_id": doc_id or str(uuid.uuid4()),
        "source_path": source_path,
        "title": overrides.get("title")
        or extracted.get("title")
        or Path(source_path).stem,
        "author": overrides.get("author")
        or extracted.get("author")
        or {"name": "unknown"},
        "country": overrides.get("country")
        if "country" in overrides
        else (extracted.get("country") or []),
        "company": overrides.get("company")
        if "company" in overrides
        else (extracted.get("company") or []),
        "keywords": overrides.get("keywords")
        if "keywords" in overrides
        else (extracted.get("keywords") or []),
        "topics": overrides.get("topics")
        if "topics" in overrides
        else (extracted.get("topics") or []),
        "language": overrides.get("language") or extracted.get("language") or "en",
        "summary": overrides.get("summary") or extracted.get("summary") or "",
        "confidentiality": overrides.get("confidentiality")
        or extracted.get("confidentiality")
        or "internal",
        "generated_at": _now(),
        "generator": {
            "model": llm.get_model(),
            "prompt_version": llm.PROMPT_TRQMMETA,
        },
    }
    for key in ("created_at", "updated_at", "owner", "review_due_at"):
        if key in overrides and overrides[key] is not None:
            meta[key] = overrides[key]
        elif extracted.get(key):
            meta[key] = extracted[key]
    if "updated_at" not in meta:
        meta["updated_at"] = _now()
    if not isinstance(meta["author"], dict) or "name" not in meta["author"]:
        meta["author"] = {"name": "unknown"}
    return meta


def _heuristic_meta(
    body: str,
    source_path: str,
    overrides: dict[str, Any] | None,
    doc_id: str | None,
) -> dict[str, Any]:
    overrides = overrides or {}
    words = [w.strip(".,;:()[]\"'").lower() for w in body.split()]
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "are", "was",
        "will", "have", "has", "not", "but", "you", "your", "our", "any",
    }
    keywords: list[str] = []
    seen: set[str] = set()
    for w in words:
        if len(w) < 4 or w in stop or w in seen:
            continue
        seen.add(w)
        keywords.append(w)
        if len(keywords) >= 12:
            break
    meta: dict[str, Any] = {
        "schema_version": "1.0",
        "doc_id": doc_id or str(uuid.uuid4()),
        "source_path": source_path,
        "title": overrides.get("title")
        or Path(source_path).stem.replace("-", " ").title(),
        "author": overrides.get("author") or {"name": "unknown"},
        "country": overrides.get("country") or [],
        "company": overrides.get("company") or [],
        "keywords": overrides.get("keywords") or keywords,
        "topics": overrides.get("topics") or [],
        "language": "en",
        "updated_at": overrides.get("updated_at") or _now(),
        "summary": overrides.get("summary")
        or (body.strip().split("\n\n")[0][:400]),
        "confidentiality": overrides.get("confidentiality") or "internal",
        "generated_at": _now(),
        "generator": {"model": "heuristic", "prompt_version": "heuristic-v1"},
    }
    for key in ("created_at", "owner", "review_due_at"):
        if overrides.get(key):
            meta[key] = overrides[key]
    return meta


def ingest_file(
    store: Store,
    path: str | Path,
    *,
    overrides: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    source = store.resolve_source(path)
    if not source.exists():
        raise FileNotFoundError(f"Document not found: {source}")
    if source.suffix.lower() not in {".md", ".txt"}:
        raise ValueError(f"Unsupported file type: {source.suffix}")

    body = store.read_body(source)
    rel = _rel_path(source)
    existing_id = store.find_doc_id_by_path(source)
    # Also try lookup by relative path string used in DB
    if existing_id is None:
        existing_id = store.find_doc_id_by_path(Path(rel)) if Path(rel) != source else None

    if use_llm:
        try:
            meta = generate_meta(
                body, source_path=rel, overrides=overrides, doc_id=existing_id
            )
        except Exception:
            meta = _heuristic_meta(body, rel, overrides, existing_id)
    else:
        meta = _heuristic_meta(body, rel, overrides, existing_id)

    store.write_meta(source, meta)
    store.upsert(meta, source)
    return meta


def ingest_path_with_seed_meta(
    store: Store,
    path: str | Path,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Write a fully specified meta (sample corpus) without calling the LLM."""
    source = store.resolve_source(path)
    if not source.exists():
        raise FileNotFoundError(source)
    rel = _rel_path(source)
    meta = {**meta, "source_path": rel, "schema_version": "1.0"}
    if "doc_id" not in meta:
        meta["doc_id"] = str(uuid.uuid4())
    if "generated_at" not in meta:
        meta["generated_at"] = _now()
    if "updated_at" not in meta:
        meta["updated_at"] = _now()
    store.write_meta(source, meta)
    store.upsert(meta, source)
    return meta
