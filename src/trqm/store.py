from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data" / "docs"
DEFAULT_DB_PATH = ROOT / "data" / "trqm.db"


@dataclass
class DocumentRecord:
    doc_id: str
    source_path: str
    title: str
    meta: dict[str, Any]
    body: str


class Store:
    def __init__(
        self,
        data_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    doc_id TEXT PRIMARY KEY,
                    source_path TEXT UNIQUE NOT NULL,
                    title TEXT,
                    country TEXT,
                    company TEXT,
                    keywords TEXT,
                    updated_at TEXT,
                    meta_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def meta_path_for(self, source_path: Path) -> Path:
        return source_path.with_suffix(".trqmmeta")

    def resolve_source(self, relative_or_absolute: str | Path) -> Path:
        path = Path(relative_or_absolute)
        if not path.is_absolute():
            candidate = self.data_dir / path
            if candidate.exists():
                return candidate
            repo_relative = ROOT / path
            if repo_relative.exists():
                return repo_relative
        return path

    def read_body(self, source_path: Path) -> str:
        return source_path.read_text(encoding="utf-8")

    def write_meta(self, source_path: Path, meta: dict[str, Any]) -> Path:
        meta_path = self.meta_path_for(source_path)
        base_real = os.path.realpath(self.data_dir)
        target_real = os.path.realpath(meta_path)
        if os.path.commonpath([base_real, target_real]) != base_real:
            raise Exception("Invalid file path")
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return meta_path

    def read_meta(self, source_path: Path) -> dict[str, Any] | None:
        meta_path = self.meta_path_for(source_path)
        if not meta_path.exists():
            return None
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def upsert(self, meta: dict[str, Any], source_path: Path) -> None:
        try:
            rel = str(source_path.relative_to(ROOT))
        except ValueError:
            rel = str(source_path)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO docs (doc_id, source_path, title, country, company, keywords, updated_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    source_path=excluded.source_path,
                    title=excluded.title,
                    country=excluded.country,
                    company=excluded.company,
                    keywords=excluded.keywords,
                    updated_at=excluded.updated_at,
                    meta_json=excluded.meta_json
                """,
                (
                    meta["doc_id"],
                    rel,
                    meta.get("title") or "",
                    json.dumps(meta.get("country") or []),
                    json.dumps(meta.get("company") or []),
                    json.dumps(meta.get("keywords") or []),
                    meta.get("updated_at") or "",
                    json.dumps(meta),
                ),
            )
            conn.commit()

    def get_by_id(self, doc_id: str) -> DocumentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_docs(self) -> list[DocumentRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM docs ORDER BY title").fetchall()
        return [self._row_to_record(r) for r in rows]

    def all_meta(self) -> list[dict[str, Any]]:
        return [d.meta for d in self.list_docs()]

    def _row_to_record(self, row: sqlite3.Row) -> DocumentRecord:
        meta = json.loads(row["meta_json"])
        source = self.resolve_source(row["source_path"])
        body = ""
        if source.exists() and source.suffix.lower() in {".md", ".txt"}:
            body = self.read_body(source)
        return DocumentRecord(
            doc_id=row["doc_id"],
            source_path=row["source_path"],
            title=row["title"] or meta.get("title") or "",
            meta=meta,
            body=body,
        )

    def find_doc_id_by_path(self, source_path: Path) -> str | None:
        try:
            rel = str(source_path.relative_to(ROOT))
        except ValueError:
            rel = str(source_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT doc_id FROM docs WHERE source_path = ?", (rel,)
            ).fetchone()
        return row["doc_id"] if row else None
