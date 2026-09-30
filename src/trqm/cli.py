from __future__ import annotations

import argparse
import json
import sys

from .ingest import ingest_file
from .pipeline import run_query
from .seed import seed_sample_corpus
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trqm", description="TRQM knowledge trust pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a document and write .trqmmeta")
    p_ingest.add_argument("path", type=str)
    p_ingest.add_argument("--no-llm", action="store_true")
    p_ingest.add_argument("--overrides", type=str, default=None, help="JSON overrides")

    p_query = sub.add_parser("query", help="Run the trust pipeline")
    p_query.add_argument("theme", type=str)
    p_query.add_argument("--country", action="append", default=None)
    p_query.add_argument("--company", action="append", default=None)
    p_query.add_argument("--no-llm", action="store_true")
    p_query.add_argument("--debug", action="store_true")

    p_seed = sub.add_parser("seed", help="Load sample SD Worx demo documents")
    p_seed.add_argument("--reset-db", action="store_true")

    p_list = sub.add_parser("list", help="List indexed documents")

    p_serve = sub.add_parser("serve", help="Run FastAPI server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    store = Store()

    if args.cmd == "ingest":
        overrides = json.loads(args.overrides) if args.overrides else None
        meta = ingest_file(store, args.path, overrides=overrides, use_llm=not args.no_llm)
        print(json.dumps(meta, indent=2))
        return 0

    if args.cmd == "query":
        filters = {}
        if args.country:
            filters["country"] = args.country
        if args.company:
            filters["company"] = args.company
        result = run_query(
            store,
            args.theme,
            filters or None,
            use_llm=not args.no_llm,
            debug=args.debug,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "seed":
        if args.reset_db and store.db_path.exists():
            store.db_path.unlink()
            store._init_db()
        docs = seed_sample_corpus(store)
        print(json.dumps([{"doc_id": d["doc_id"], "title": d.get("title")} for d in docs], indent=2))
        return 0

    if args.cmd == "list":
        rows = [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "country": d.meta.get("country"),
                "path": d.source_path,
            }
            for d in store.list_docs()
        ]
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("trqm.api:app", host=args.host, port=args.port, reload=False)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
