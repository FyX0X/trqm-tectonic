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

# Security limits for document uploads
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_TOTAL_STORAGE_BYTES = 100 * 1024 * 1024  # 100 MB aggregate storage
COPY_BUFFER_SIZE = 64 * 1024  # 64 KB buffer for bounded copying

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


def _calculate_storage_usage(data_dir: Path) -> int:
    """Calculate total bytes used by documents and metadata in data directory."""
    total = 0
    if not data_dir.exists():
        return 0
    for item in data_dir.iterdir():
        if item.is_file():
            total += item.stat().st_size
    return total


def _bounded_copy(source, dest_path: Path, max_bytes: int) -> int:
    """Copy from source stream to dest_path with size limit enforcement.
    
    Returns the number of bytes written.
    Raises HTTPException if max_bytes is exceeded.
    """
    bytes_written = 0
    with dest_path.open("wb") as out:
        while True:
            chunk = source.read(COPY_BUFFER_SIZE)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > max_bytes:
                # Clean up partial file
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    f"Upload exceeds maximum size of {max_bytes} bytes"
                )
            out.write(chunk)
    return bytes_written


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
    
    # Check aggregate storage quota before accepting upload
    current_usage = _calculate_storage_usage(store.data_dir)
    if current_usage >= MAX_TOTAL_STORAGE_BYTES:
        raise HTTPException(
            507,
            f"Storage quota exceeded. Current usage: {current_usage} bytes, "
            f"limit: {MAX_TOTAL_STORAGE_BYTES} bytes"
        )
    
    dest = store.data_dir / Path(file.filename).name
    
    # Perform bounded copy with per-file size limit
    try:
        bytes_written = _bounded_copy(file.file, dest, MAX_UPLOAD_SIZE_BYTES)
    except HTTPException:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Upload failed: {str(exc)}") from exc
    
    # Verify aggregate quota after write (defense in depth)
    new_usage = _calculate_storage_usage(store.data_dir)
    if new_usage > MAX_TOTAL_STORAGE_BYTES:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            507,
            f"Upload would exceed storage quota. Limit: {MAX_TOTAL_STORAGE_BYTES} bytes"
        )
    
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
        store, req.theme, filters, use_llm=req.use_llm, debug=req.debug
    )
