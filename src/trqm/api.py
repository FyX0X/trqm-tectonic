from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ingest import ingest_file
from .pipeline import run_query
from .seed import seed_sample_corpus
from .store import Store

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="TRQM",
    description="Trust, Relevance, Quality, Metadata — organisational knowledge pipeline",
    version="0.1.0",
)
store = Store()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class QueryFilters(BaseModel):
    country: list[str] | None = None
    company: list[str] | None = None
    keywords: list[str] | None = None


class QueryRequest(BaseModel):
    theme: str
    filters: QueryFilters | None = None
    use_llm: bool = True
    debug: bool = False
    requester_level: str = "public"


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/seed")
def seed(reset_db: bool = False) -> list[dict[str, Any]]:
    if reset_db and store.db_path.exists():
        store.db_path.unlink()
        store._init_db()
    docs = seed_sample_corpus(store)
    return [{"doc_id": d["doc_id"], "title": d.get("title")} for d in docs]


@app.get("/documents")
def list_documents() -> list[dict[str, Any]]:
    return [
        {
            "doc_id": d.doc_id,
            "title": d.title,
            "source_path": d.source_path,
            "country": d.meta.get("country"),
            "company": d.meta.get("company"),
            "keywords": d.meta.get("keywords"),
            "updated_at": d.meta.get("updated_at"),
        }
        for d in store.list_docs()
    ]


@app.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    rec = store.get_by_id(doc_id)
    if not rec:
        raise HTTPException(404, "Document not found")
    return {
        "doc_id": rec.doc_id,
        "source_path": rec.source_path,
        "title": rec.title,
        "meta": rec.meta,
        "body": rec.body,
    }


@app.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    use_llm: bool = Form(True),
    overrides_json: str | None = Form(None),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "filename required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise HTTPException(400, "Only .md and .txt supported in PoC")
    dest = store.data_dir / Path(file.filename).name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    overrides: dict[str, Any] | None = None
    if overrides_json:
        overrides = json.loads(overrides_json)
    try:
        meta = ingest_file(store, dest, overrides=overrides, use_llm=use_llm)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"meta": meta, "meta_path": str(store.meta_path_for(dest))}


@app.post("/query")
def query(req: QueryRequest) -> dict[str, Any]:
    filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
    return run_query(
        store, req.theme, filters, use_llm=req.use_llm, debug=req.debug, requester_level=req.requester_level
    )
