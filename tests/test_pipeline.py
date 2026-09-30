from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_trqmmeta_schema_accepts_example():
    schema = load_schema("trqmmeta.schema.json")
    example = {
        "schema_version": "1.0",
        "doc_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "source_path": "data/docs/be-payroll-leave.md",
        "title": "Belgium annual leave policy",
        "author": {
            "name": "Marie Dupont",
            "email": "marie.dupont@example.com",
            "role": "Payroll Specialist BE",
        },
        "country": ["BE"],
        "company": ["Acme NV", "SD Worx Internal"],
        "keywords": ["annual leave", "Belgium", "payroll"],
        "topics": ["leave", "payroll"],
        "language": "en",
        "created_at": "2024-03-01T00:00:00Z",
        "updated_at": "2026-01-15T00:00:00Z",
        "owner": "marie.dupont@example.com",
        "review_due_at": "2026-07-15T00:00:00Z",
        "confidentiality": "internal",
        "summary": "Defines statutory annual leave entitlement for Belgium.",
        "generated_at": "2026-09-30T18:00:00Z",
        "generator": {"model": "gpt-4o-mini", "prompt_version": "trqmmeta-v1"},
    }
    Draft202012Validator(schema).validate(example)


def test_trust_assessment_schema():
    schema = load_schema("trust_assessment.schema.json")
    example = {
        "doc_id": "doc-be-leave-001",
        "trust_score": 0.74,
        "rules_score": 0.71,
        "llm_adjustment": 0.03,
        "signals": [
            {
                "rule_id": "R_COUNTRY_FIT",
                "passed": True,
                "weight": 0.25,
                "detail": "Doc country BE matches query.",
            }
        ],
        "uncertainties": [],
        "conflicts": [],
        "expert_hint": "Marie Dupont",
        "verdict": "usable_with_caveats",
        "explanation": "Country-aligned with minor caveats.",
    }
    Draft202012Validator(schema).validate(example)


def test_trusted_summary_schema():
    schema = load_schema("trusted_summary.schema.json")
    example = {
        "theme": "leave in Belgium",
        "answer": "20 days [doc-be-leave-001]",
        "overall_confidence": 0.78,
        "used_sources": [
            {
                "doc_id": "doc-be-leave-001",
                "title": "Belgium annual leave policy",
                "trust_score": 0.74,
                "why_trusted": "Country match + recent update.",
            }
        ],
        "discarded_sources": [
            {"doc_id": "doc-fr-leave-001", "reason": "Country FR — not applicable."}
        ],
        "conflicts_detected": [],
        "gaps": [],
        "ask_expert": {"name": "Marie Dupont", "reason": "Confirm CBA."},
        "generated_at": "2026-09-30T18:05:00Z",
    }
    Draft202012Validator(schema).validate(example)


@pytest.fixture()
def seeded_store(tmp_path, monkeypatch):
    from trqm.seed import seed_sample_corpus
    from trqm.store import Store

    data_dir = tmp_path / "docs"
    db_path = tmp_path / "trqm.db"
    store = Store(data_dir=data_dir, db_path=db_path)
    seed_sample_corpus(store)
    return store


def test_seed_writes_trqmmeta(seeded_store):
    metas = list(seeded_store.data_dir.glob("*.trqmmeta"))
    assert len(metas) == 4
    docs = seeded_store.list_docs()
    assert len(docs) == 4


def test_pipeline_demo_no_llm(seeded_store):
    from trqm.pipeline import run_query

    result = run_query(
        seeded_store,
        "leave days for Acme employee in Belgium",
        filters={"country": ["BE"]},
        use_llm=False,
        debug=True,
        requester_level="internal",
    )
    summary = result["summary"]
    used_ids = {u["doc_id"] for u in summary["used_sources"]}
    discarded_ids = {d["doc_id"] for d in summary["discarded_sources"]}

    assert "doc-be-leave-001" in used_ids or any(
        "be" in (u.get("title") or "").lower() for u in summary["used_sources"]
    )
    # FR doc should not be trusted for BE query
    assert "doc-fr-leave-001" in discarded_ids or "doc-fr-leave-001" not in used_ids
    assert summary["overall_confidence"] > 0
    assert summary["answer"]


def test_country_mismatch_excludes_fr(seeded_store):
    from trqm.filter import keyword_prefilter
    from trqm.trust import assess_trust

    cands = keyword_prefilter(
        seeded_store, "annual leave Belgium", {"country": ["BE"]}
    )
    ids = {c["doc_id"] for c in cands}
    assert "doc-fr-leave-001" not in ids

    fr = seeded_store.get_by_id("doc-fr-leave-001")
    assert fr is not None
    assessment = assess_trust(
        "leave in Belgium",
        {"doc_id": fr.doc_id, "meta": fr.meta, "body": fr.body},
        query_countries=["BE"],
        use_llm=False,
        requester_level="internal",
    )
    assert assessment["verdict"] == "excluded"


def test_public_requester_cannot_access_internal_docs(seeded_store):
    """Verify that unauthenticated (public) requesters cannot access internal documents."""
    from trqm.pipeline import run_query

    # Query without specifying requester_level (defaults to "public")
    result = run_query(
        seeded_store,
        "leave days for Acme employee in Belgium",
        filters={"country": ["BE"]},
        use_llm=False,
        debug=True,
    )
    summary = result["summary"]
    used_ids = {u["doc_id"] for u in summary["used_sources"]}
    
    # All seeded documents are "internal", so public requester should get no results
    assert len(used_ids) == 0
    assert summary["overall_confidence"] == 0.0
    assert "No sufficiently trusted documents" in summary["answer"]


def test_internal_requester_can_access_internal_docs(seeded_store):
    """Verify that internal requesters can access internal documents."""
    from trqm.pipeline import run_query

    # Query with requester_level="internal"
    result = run_query(
        seeded_store,
        "leave days for Acme employee in Belgium",
        filters={"country": ["BE"]},
        use_llm=False,
        debug=True,
        requester_level="internal",
    )
    summary = result["summary"]
    used_ids = {u["doc_id"] for u in summary["used_sources"]}
    
    # Internal requester should be able to access internal documents
    assert len(used_ids) > 0
    assert summary["overall_confidence"] > 0
    assert summary["answer"]
