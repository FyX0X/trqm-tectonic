from __future__ import annotations

from pathlib import Path
from typing import Any

from .ingest import ingest_path_with_seed_meta
from .store import Store

SAMPLES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "be-payroll-leave.md",
        """# Belgium annual leave policy (white-collar)

**Owner:** Marie Dupont (marie.dupont@example.com), Payroll Specialist BE  
**Applies to:** Acme NV and SD Worx Internal guidance for Belgium  
**Last updated:** 2026-01-15  
**Review due:** 2026-07-15

## Entitlement

Full-time white-collar employees in Belgium are entitled to **20 days** of statutory annual leave per calendar year, based on performance in the previous year, unless a more favourable company CBA applies.

Part-time employees receive a pro-rata entitlement.

## Booking

Leave requests must be approved by the line manager and recorded in the payroll calendar at least two weeks in advance where possible.

## Notes for consultants

- Confirm whether a client-specific CBA grants additional days.
- Public holidays are separate from the 20 statutory days.
""",
        {
            "doc_id": "doc-be-leave-001",
            "title": "Belgium annual leave policy",
            "author": {
                "name": "Marie Dupont",
                "email": "marie.dupont@example.com",
                "role": "Payroll Specialist BE",
            },
            "country": ["BE"],
            "company": ["Acme NV", "SD Worx Internal"],
            "keywords": [
                "annual leave",
                "Belgium",
                "payroll",
                "entitlement",
                "white-collar",
                "20 days",
                "CBA",
            ],
            "topics": ["leave", "payroll"],
            "language": "en",
            "created_at": "2024-03-01T00:00:00Z",
            "updated_at": "2026-01-15T00:00:00Z",
            "owner": "marie.dupont@example.com",
            "review_due_at": "2027-07-15T00:00:00Z",
            "confidentiality": "internal",
            "summary": "Defines statutory 20-day annual leave entitlement for white-collar employees in Belgium, with CBA caveats.",
            "generator": {"model": "seed", "prompt_version": "sample-v1"},
        },
    ),
    (
        "fr-payroll-leave.md",
        """# France Congés payés — reference note

**Applies to:** France payroll operations  
**Last updated:** 2025-11-01

## Entitlement

In France, full-time employees typically accrue **25 days** (or 30 working days depending on counting method) of paid annual leave per year under the Labour Code baseline, subject to sector agreements.

This note is **not** valid for Belgian clients.

## Reminder

Always check the applicable convention collective before advising a customer.
""",
        {
            "doc_id": "doc-fr-leave-001",
            "title": "France paid leave reference",
            "author": {
                "name": "Jean Martin",
                "email": "jean.martin@example.com",
                "role": "Payroll Specialist FR",
            },
            "country": ["FR"],
            "company": ["SD Worx Internal"],
            "keywords": [
                "congés payés",
                "France",
                "annual leave",
                "25 days",
                "payroll",
            ],
            "topics": ["leave", "payroll"],
            "language": "en",
            "created_at": "2023-06-01T00:00:00Z",
            "updated_at": "2025-11-01T00:00:00Z",
            "owner": "jean.martin@example.com",
            "review_due_at": "2026-11-01T00:00:00Z",
            "confidentiality": "internal",
            "summary": "France baseline paid leave reference (approx. 25 days); not applicable to Belgium.",
            "generator": {"model": "seed", "prompt_version": "sample-v1"},
        },
    ),
    (
        "teams-leave-hearsay.md",
        """# Teams export — leave days chat (undated)

Copied from a Teams channel. No owner tagged.

> Quick question — for Acme I always tell them they get **26 days** in Belgium, right?
>
> Yeah I think so, someone said that last year.

No link to policy. No country confirmation beyond the chat message. Do not treat as authoritative.
""",
        {
            "doc_id": "doc-teams-hearsay-001",
            "title": "Teams leave hearsay (undated)",
            "author": {"name": "unknown"},
            "country": ["BE"],
            "company": ["Acme NV"],
            "keywords": [
                "leave",
                "Belgium",
                "Acme",
                "26 days",
                "Teams",
                "hearsay",
            ],
            "topics": ["leave"],
            "language": "en",
            "updated_at": "2023-01-01T00:00:00Z",
            "confidentiality": "internal",
            "summary": "Undated Teams chat claiming 26 leave days for Acme in Belgium; no owner; contradicts formal policy.",
            "generator": {"model": "seed", "prompt_version": "sample-v1"},
        },
    ),
    (
        "acme-be-checklist.md",
        """# Acme NV — Belgium payroll consultant checklist

**Owner:** Marie Dupont  
**Updated:** 2026-02-01

## Onboarding a question about leave

1. Open the Belgium annual leave policy (statutory **20 days** baseline).
2. Check Acme NV CBA folder for extras (none recorded as of 2026-02-01).
3. If chat/Teams claims differ, prefer the owned policy document and escalate to Marie.

## Contacts

- Payroll Specialist BE: marie.dupont@example.com
""",
        {
            "doc_id": "doc-acme-checklist-001",
            "title": "Acme NV Belgium payroll checklist",
            "author": {
                "name": "Marie Dupont",
                "email": "marie.dupont@example.com",
                "role": "Payroll Specialist BE",
            },
            "country": ["BE"],
            "company": ["Acme NV"],
            "keywords": [
                "Acme",
                "checklist",
                "Belgium",
                "leave",
                "payroll",
                "CBA",
                "20 days",
            ],
            "topics": ["leave", "payroll", "handover"],
            "language": "en",
            "created_at": "2025-09-01T00:00:00Z",
            "updated_at": "2026-02-01T00:00:00Z",
            "owner": "marie.dupont@example.com",
            "review_due_at": "2027-08-01T00:00:00Z",
            "confidentiality": "internal",
            "summary": "Checklist for Acme NV BE leave questions: prefer owned 20-day policy over informal chat.",
            "generator": {"model": "seed", "prompt_version": "sample-v1"},
        },
    ),
]


def seed_sample_corpus(store: Store | None = None) -> list[dict[str, Any]]:
    store = store or Store()
    store.data_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for filename, body, meta in SAMPLES:
        path = store.data_dir / filename
        path.write_text(body.strip() + "\n", encoding="utf-8")
        results.append(ingest_path_with_seed_meta(store, path, meta))
    return results
