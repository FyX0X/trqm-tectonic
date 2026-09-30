from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
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

# Security configuration
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
CONFIGURED_API_KEY = os.getenv("TRQM_API_KEY")

# Resource limits
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("TRQM_MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024))  # 10MB default
MAX_STORAGE_BYTES = int(os.getenv("TRQM_MAX_STORAGE_BYTES", 100 * 1024 * 1024))  # 100MB default
QUERY_TIMEOUT_SECONDS = int(os.getenv("TRQM_QUERY_TIMEOUT_SECONDS", 60))  # 60s default
MAX_CONCURRENT_QUERIES = int(os.getenv("TRQM_MAX_CONCURRENT_QUERIES", 3))  # 3 concurrent queries default

# Rate limiting state
_rate_limit_state: dict[str, list[float]] = defaultdict(list)
_rate_limit_window = 60.0  # 1 minute window
_upload_rate_limit = int(os.getenv("TRQM_UPLOAD_RATE_LIMIT", 10))  # 10 uploads per minute
_query_rate_limit = int(os.getenv("TRQM_QUERY_RATE_LIMIT", 20))  # 20 queries per minute
_concurrent_queries = 0
_query_semaphore_lock = asyncio.Lock()

app = FastAPI(
    title="TRQM",
    description="Trust, Relevance, Quality, Metadata — organisational knowledge pipeline",
    version="0.1.0",
)
store = Store()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    """Verify API key if authentication is configured."""
    if CONFIGURED_API_KEY is None:
        # No API key configured, allow unauthenticated access (backward compatibility)
        return None
    if api_key is None or api_key != CONFIGURED_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key


def check_rate_limit(endpoint: str, limit: int, identifier: str = "global") -> None:
    """Check if rate limit is exceeded for an endpoint."""
    now = time.time()
    key = f"{endpoint}:{identifier}"
    
    # Clean old entries outside the window
    _rate_limit_state[key] = [
        ts for ts in _rate_limit_state[key] if now - ts < _rate_limit_window
    ]
    
    # Check if limit exceeded
    if len(_rate_limit_state[key]) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} requests per {int(_rate_limit_window)}s",
        )
    
    # Record this request
    _rate_limit_state[key].append(now)


def get_storage_usage(data_dir: Path) -> int:
    """Calculate total storage usage in bytes."""
    total = 0
    try:
        for item in data_dir.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except Exception:
        pass
    return total


async def acquire_query_slot() -> None:
    """Acquire a slot for query execution with concurrency limit."""
    global _concurrent_queries
    async with _query_semaphore_lock:
        if _concurrent_queries >= MAX_CONCURRENT_QUERIES:
            raise HTTPException(
                status_code=503,
                detail=f"Maximum concurrent queries ({MAX_CONCURRENT_QUERIES}) reached. Please retry later.",
            )
        _concurrent_queries += 1


async def release_query_slot() -> None:
    """Release a query execution slot."""
    global _concurrent_queries
    async with _query_semaphore_lock:
        _concurrent_queries = max(0, _concurrent_queries - 1)


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
    api_key: str | None = Depends(verify_api_key),
) -> dict[str, Any]:
    # Rate limiting
    identifier = api_key or "anonymous"
    check_rate_limit("upload", _upload_rate_limit, identifier)
    
    if not file.filename:
        raise HTTPException(400, "filename required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise HTTPException(400, "Only .md and .txt supported in PoC")
    
    # Check storage quota before accepting upload
    current_usage = get_storage_usage(store.data_dir)
    if current_usage >= MAX_STORAGE_BYTES:
        raise HTTPException(
            507,
            f"Storage quota exceeded: {current_usage} bytes used of {MAX_STORAGE_BYTES} bytes limit",
        )
    
    dest = store.data_dir / Path(file.filename).name
    
    # Read file with size limit enforcement
    bytes_written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(8192):  # Read in 8KB chunks
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE_BYTES:
                    # Clean up partial file
                    out.close()
                    if dest.exists():
                        dest.unlink()
                    raise HTTPException(
                        413,
                        f"File size exceeds maximum allowed size of {MAX_UPLOAD_SIZE_BYTES} bytes",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        # Clean up on error
        if dest.exists():
            dest.unlink()
        raise HTTPException(500, f"Upload failed: {str(exc)}") from exc
    
    # Check storage quota after upload
    current_usage = get_storage_usage(store.data_dir)
    if current_usage > MAX_STORAGE_BYTES:
        # Rollback: remove the uploaded file
        if dest.exists():
            dest.unlink()
        raise HTTPException(
            507,
            f"Storage quota would be exceeded: {current_usage} bytes would exceed {MAX_STORAGE_BYTES} bytes limit",
        )
    
    overrides: dict[str, Any] | None = None
    if overrides_json:
        try:
            overrides = json.loads(overrides_json)
        except json.JSONDecodeError as exc:
            # Clean up uploaded file on invalid JSON
            if dest.exists():
                dest.unlink()
            raise HTTPException(400, f"Invalid JSON in overrides: {str(exc)}") from exc
    
    try:
        meta = ingest_file(store, dest, overrides=overrides, use_llm=use_llm)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"meta": meta, "meta_path": str(store.meta_path_for(dest))}


@app.post("/query")
async def query(
    req: QueryRequest,
    api_key: str | None = Depends(verify_api_key),
) -> dict[str, Any]:
    # Rate limiting
    identifier = api_key or "anonymous"
    check_rate_limit("query", _query_rate_limit, identifier)
    
    # Concurrency control
    await acquire_query_slot()
    
    try:
        # Execute query with timeout
        filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
        
        # Run query with timeout protection
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_query,
                    store,
                    req.theme,
                    filters,
                    use_llm=req.use_llm,
                    debug=req.debug,
                ),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
            return result
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                504,
                f"Query execution exceeded timeout of {QUERY_TIMEOUT_SECONDS} seconds",
            ) from exc
    finally:
        # Always release the query slot
        await release_query_slot()
