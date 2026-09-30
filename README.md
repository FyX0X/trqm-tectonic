# TRQM (Tectonic × SD Worx)

Proof of concept that helps employees move from **“I found something”** to **“I understand why I can rely on it.”**

Focused moment of doubt: a payroll/HR consultant gets multiple answers for a customer question and cannot tell which applies to *this country / client*, or why it deserves trust.

![TRQM Pipeline](images/visual-representation.jpg)

## Pipeline

1. **Ingest** — add a document; AI writes a `.trqmmeta` sidecar (author, country, company, keywords, …).
2. **Filter** — keyword/metadata prefilter, then AI relevance ranking.
3. **Trust** — deterministic rules + LLM judge → explainable trust score.
4. **Convert (FOLDL)** — merge trusted sources into one summary with *why trusted*, conflicts, gaps, and who to ask.

Docs:

- [docs/PLAN.md](docs/PLAN.md)
- [docs/TRQMMETA.md](docs/TRQMMETA.md)
- [docs/TRUST_LAYER.md](docs/TRUST_LAYER.md)
- [docs/QUERY_PIPELINE.md](docs/QUERY_PIPELINE.md)

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Load sample SD Worx-flavoured corpus (writes .md + .trqmmeta, indexes SQLite)
trqm seed

# Query without calling Gemini (heuristic relevance/trust/fold)
trqm query "leave days for Acme employee in Belgium" --no-llm --debug

# Use Gemini via ai_integration/ (reads api_key.txt in project root)
# echo 'YOUR_GOOGLE_AI_KEY' > api_key.txt
trqm query "leave days for Acme employee in Belgium" --country BE --company "Acme NV"

# API
trqm serve
# POST http://127.0.0.1:8000/query
# GET  http://127.0.0.1:8000/documents
```

## LLM setup

1. Put your Google AI API key in `api_key.txt` at the project root (gitignored).
2. Calls go through [`ai_integration/ai.py`](ai_integration/ai.py) (`call_ai`), model `gemini-3.1-flash-lite`.
3. Pass `--no-llm` to use heuristics instead (no key required).

## CLI

| Command | Purpose |
|---------|---------|
| `trqm seed` | Write sample docs + `.trqmmeta` + DB index |
| `trqm ingest PATH` | Ingest one `.md`/`.txt` (AI meta unless `--no-llm`) |
| `trqm ingest-missing` | Ingest every `.md`/`.txt` in `data/docs/` that has no `.trqmmeta` |
| `trqm query "…"` | Run full pipeline |
| `trqm list` | List indexed documents |
| `trqm serve` | FastAPI on `:8000` |

## Demo expectation

Theme: *leave days for Acme employee in Belgium*

- FR policy should fail country fit / be discarded.
- Undated Teams note should score low trust and surface as conflicting (26 vs 20 days).
- BE policy (+ checklist) should dominate the trusted summary with visible reasons (country, freshness, owner).
