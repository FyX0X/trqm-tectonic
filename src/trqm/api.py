from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ingest import ingest_file
from .pipeline import run_query
from .seed import seed_sample_corpus
from .store import Store

STATIC_DIR = Path(__file__).resolve().parent / "static"
_ROOT = Path(__file__).resolve().parent.parent.parent
_API_KEY_PATH = _ROOT / "trqm_api_key.txt"

app = FastAPI(
    title="TRQM",
    description="Trust, Relevance, Quality, Metadata — organisational knowledge pipeline",
    version="0.1.0",
)
store = Store()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# API Key Authentication
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def _load_api_key() -> str:
    """Load API key from file or environment variable."""
    # Try environment variable first
    key = os.environ.get("TRQM_API_KEY")
    if key:
        return key.strip()
    
    # Try file
    if _API_KEY_PATH.exists():
        key = _API_KEY_PATH.read_text(encoding="utf-8").strip()
        if key:
            return key
    
    # Generate and save a new key if none exists
    key = secrets.token_urlsafe(32)
    _API_KEY_PATH.write_text(key, encoding="utf-8")
    print(f"Generated new API key and saved to {_API_KEY_PATH}")
    print(f"API Key: {key}")
    return key

_VALID_API_KEY = _load_api_key()

async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify the API key and return requester level."""
    if not secrets.compare_digest(api_key, _VALID_API_KEY):
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing API key",
        )
    # All authenticated requests get 'internal' level access
    # In a production system, this could map different keys to different levels
    return "internal"


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
    """Public UI endpoint - authentication enforced by API calls from the frontend."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    """Public health check endpoint for monitoring."""
    return {"status": "ok"}


@app.post("/seed")
def seed(
    reset_db: bool = False,
    requester_level: str = Depends(verify_api_key),
) -> list[dict[str, Any]]:
    """Seed sample corpus - requires authentication."""
    if reset_db and store.db_path.exists():
        store.db_path.unlink()
        store._init_db()
    docs = seed_sample_corpus(store)
    return [{"doc_id": d["doc_id"], "title": d.get("title")} for d in docs]


@app.get("/documents")
def list_documents(
    requester_level: str = Depends(verify_api_key),
) -> list[dict[str, Any]]:
    """List documents - requires authentication."""
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
def get_document(
    doc_id: str,
    requester_level: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Get document by ID - requires authentication."""
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
    requester_level: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Upload document - requires authentication."""
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
def query(
    req: QueryRequest,
    requester_level: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Query the corpus - requires authentication and passes requester level to trust pipeline."""
    filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
    return run_query(
        store,
        req.theme,
        filters,
        use_llm=req.use_llm,
        debug=req.debug,
        requester_level=requester_level,
    )
