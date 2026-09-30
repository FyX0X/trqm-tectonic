from __future__ import annotations

import json
import os
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Header, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ingest import ingest_file
from .pipeline import run_query
from .seed import seed_sample_corpus
from .store import Store

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Authentication configuration
# In production, set TRQM_API_KEY environment variable
# For development/testing, a default key is used (should be changed in production)
API_KEY = os.environ.get("TRQM_API_KEY", "dev-key-change-in-production")

# Metadata fields that can be overridden by client uploads
# Restricting to prevent poisoning of trust-critical fields
ALLOWED_OVERRIDE_FIELDS = {
    "title",
    "keywords",
    "topics",
    "language",
    "summary",
}


def verify_api_key(x_api_key: str = Header(None)) -> None:
    """Verify API key for write operations."""
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


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


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/seed", dependencies=[Depends(verify_api_key)])
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


@app.post("/documents", dependencies=[Depends(verify_api_key)])
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
    
    # Generate a unique filename to prevent overwrites
    # Use UUID to ensure uniqueness and prevent client-controlled filenames
    original_stem = Path(file.filename).stem
    # Sanitize the original stem to use as a prefix (optional, for human readability)
    safe_stem = "".join(c for c in original_stem if c.isalnum() or c in "-_")[:50]
    unique_name = f"{safe_stem}_{uuid.uuid4().hex[:12]}{suffix}"
    dest = store.data_dir / unique_name
    
    # Ensure destination is within data_dir (defense in depth)
    try:
        dest.resolve().relative_to(store.data_dir.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid file path")
    
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    
    # Parse and validate metadata overrides
    overrides: dict[str, Any] | None = None
    if overrides_json:
        try:
            raw_overrides = json.loads(overrides_json)
            # Filter to only allowed fields to prevent metadata poisoning
            overrides = {
                k: v for k, v in raw_overrides.items() 
                if k in ALLOWED_OVERRIDE_FIELDS
            }
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON in overrides_json")
    
    try:
        meta = ingest_file(store, dest, overrides=overrides, use_llm=use_llm)
    except Exception as exc:
        # Clean up the uploaded file on ingestion failure
        if dest.exists():
            dest.unlink()
        raise HTTPException(500, str(exc)) from exc
    return {"meta": meta, "meta_path": str(store.meta_path_for(dest))}


@app.post("/query")
def query(req: QueryRequest) -> dict[str, Any]:
    filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
    return run_query(
        store, req.theme, filters, use_llm=req.use_llm, debug=req.debug
    )
