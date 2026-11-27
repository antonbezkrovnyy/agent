import json
from pathlib import Path
import re
import sqlite3
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import database as db
from .paths import FRONTEND_DIST, RESEARCH, ROOT, relative_path, resolve_workspace_path


app = FastAPI(title="Log Research Workbench", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, error: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(FileNotFoundError)
async def not_found_handler(_: Request, error: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_error_handler(_: Request, error: sqlite3.OperationalError):
    return JSONResponse(status_code=503, content={"detail": f"SQLite временно недоступна: {error}"})


@app.get("/api/health")
def health():
    return {"ok": True, "project": ROOT.name, "read_only": True}


@app.get("/api/databases")
def databases():
    return {"items": db.list_databases()}


@app.get("/api/database/overview")
def database_overview(path: str = Query(...)):
    return db.database_overview(path)


@app.get("/api/database/table")
def database_table(
    path: str = Query(...),
    table: str = Query(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=5, le=100),
    search: str = Query(default="", max_length=200),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
):
    return db.table_rows(path, table, page, page_size, search, sort, direction)


@app.get("/api/database/record")
def database_record(path: str = Query(...), table: str = Query(...), rowid: int = Query(...)):
    return db.table_record(path, table, rowid)


def bundle_path(value: str) -> Path:
    path = resolve_workspace_path(value)
    if not path.is_relative_to(RESEARCH) or not path.name.endswith(".bundle.json") or not path.is_file():
        raise ValueError("Bundle должен быть .bundle.json внутри research")
    return path


@app.get("/api/bundles")
def bundles():
    items = []
    for path in sorted(RESEARCH.glob("*.bundle.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            golden = Path(str(path).replace(".bundle.json", ".golden.json"))
            taxonomy = payload.get("taxonomy")
            items.append({
                "path": relative_path(path),
                "family_id": payload.get("family_id", path.stem),
                "parser_revision": payload.get("parser", {}).get("revision"),
                "normalization_revision": payload.get("normalization", {}).get("revision"),
                "mapping_revision": payload.get("mapping", {}).get("revision"),
                "taxonomy": taxonomy.get("name") if isinstance(taxonomy, dict) else taxonomy,
                "evidence_count": len(payload.get("mapping", {}).get("evidence", [])),
                "golden_path": relative_path(golden) if golden.exists() else None,
            })
        except (OSError, json.JSONDecodeError) as error:
            items.append({"path": relative_path(path), "family_id": path.stem, "error": str(error)})
    return {"items": items}


@app.get("/api/bundle")
def bundle(path: str = Query(...)):
    resolved = bundle_path(path)
    return {"path": relative_path(resolved), "content": json.loads(resolved.read_text(encoding="utf-8"))}


@app.get("/api/reports")
def reports():
    items = []
    for path in sorted(RESEARCH.glob("*.md"), reverse=True):
        content = path.read_text(encoding="utf-8")
        heading = next((line.removeprefix("# ") for line in content.splitlines() if line.startswith("# ")), path.stem)
        match = re.search(r"R?(\d{3})", path.stem)
        items.append({
            "path": relative_path(path),
            "name": path.stem,
            "title": heading,
            "research_id": f"R{match.group(1)}" if match else None,
            "size": path.stat().st_size,
        })
    return {"items": items}


@app.get("/api/report")
def report(path: str = Query(...)):
    resolved = resolve_workspace_path(path)
    if not resolved.is_relative_to(RESEARCH) or resolved.suffix != ".md" or not resolved.is_file():
        raise ValueError("Отчёт должен быть .md внутри research")
    return {"path": relative_path(resolved), "content": resolved.read_text(encoding="utf-8")}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint не найден")
        requested = (FRONTEND_DIST / full_path).resolve()
        if full_path and requested.is_relative_to(FRONTEND_DIST) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
