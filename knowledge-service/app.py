import asyncio
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import Settings
from indexer import KnowledgeIndex, page_summary
from memory_api import MemoryEditor

settings = Settings()
space_dir = Path(os.environ.get("KNOWLEDGE_SPACE_DIR", "/space"))
seed_dir = Path(os.environ.get("KNOWLEDGE_SEED_DIR", "/seed"))
public_agui_url = os.environ.get("KNOWLEDGE_AGUI_PUBLIC_URL", "http://localhost:2027").rstrip("/")
index = KnowledgeIndex(settings, str(space_dir))
memory = MemoryEditor(settings)
app = FastAPI(title="LogiCore Knowledge — SilverBullet / Memory API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
state = {"last_index": {}, "last_error": ""}


class FactCreate(BaseModel):
    statement: str = Field(min_length=3, max_length=5000)
    valid_to: datetime | None = None


class FactUpdate(FactCreate):
    base_revision: int = Field(ge=1)


class FactControl(BaseModel):
    base_revision: int = Field(ge=1)
    pinned: bool | None = None
    excluded: bool | None = None


class ReportCard(BaseModel):
    case_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(default="", max_length=128)
    title: str = Field(min_length=1, max_length=300)
    summary: str = ""
    verification_status: str = "not_reviewed"
    saved_at: str = ""
    artifacts: list[dict] = Field(default_factory=list)


def ensure_seed():
    if not seed_dir.exists():
        return
    space_dir.mkdir(parents=True, exist_ok=True)
    for source in seed_dir.rglob("*"):
        relative = source.relative_to(seed_dir)
        target = space_dir / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def run_index():
    try:
        state["last_index"] = index.index_all()
        state["last_error"] = ""
    except Exception as exc:
        state["last_error"] = f"{type(exc).__name__}: {exc}"


@app.on_event("startup")
async def startup():
    ensure_seed()
    await asyncio.to_thread(run_index)

    async def worker():
        while True:
            await asyncio.sleep(max(2, int(os.environ.get("KNOWLEDGE_REINDEX_SECONDS", "5"))))
            await asyncio.to_thread(run_index)

    app.state.index_worker = asyncio.create_task(worker())


@app.on_event("shutdown")
async def shutdown():
    worker = getattr(app.state, "index_worker", None)
    if worker:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


@app.get("/health")
def health():
    return {"ok": not state["last_error"], **state}


@app.post("/reindex")
async def reindex():
    await asyncio.to_thread(run_index)
    if state["last_error"]:
        raise HTTPException(status_code=503, detail=state["last_error"])
    return state["last_index"]


@app.get("/wiki/search")
def wiki_search(query: str = "", limit: int = 3):
    limit = max(1, min(limit, 10))
    embedding = index._embed(query) if query.strip() else None
    return {"ok": True, "items": index.store.search(query, embedding, limit)}


@app.get("/wiki/read")
def wiki_read(page_id: str, section: str = ""):
    page = index.store.read_page(page_id, section)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return {"ok": True, "page": page}


@app.post("/reports/publish")
async def publish_report(card: ReportCard):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", card.case_id):
        raise HTTPException(status_code=422, detail="Invalid case_id")
    directory = space_dir / "Reports"
    directory.mkdir(parents=True, exist_ok=True)
    links = []
    for artifact in card.artifacts:
        key = str(artifact.get("key") or "")
        if key.startswith("investigations/") and ".." not in key.split("/"):
            filename = artifact.get("filename") or Path(key).name
            links.append(f"- [{filename}]({public_agui_url}/artifacts/{key})")
    metadata = {
        "title": card.title,
        "type": "investigation_report",
        "status": "published",
        "case_id": card.case_id,
        "run_id": card.run_id,
        "verification": card.verification_status,
        "saved_at": card.saved_at,
    }
    frontmatter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    summary = card.summary.strip()[:4000] or "Вывод не указан."
    body = (
        f"---\n{frontmatter}\n---\n\n# {card.title}\n\n"
        f"- **Дело:** `{card.case_id}`\n"
        f"- **Run:** `{card.run_id or '—'}`\n"
        f"- **Проверка:** `{card.verification_status}`\n"
        f"- **Сохранено:** {card.saved_at or '—'}\n\n"
        f"## Краткий вывод\n\n{summary}\n\n"
        f"## Артефакты\n\n" + ("\n".join(links) if links else "Файлы не созданы.") + "\n"
    )
    target = directory / f"{card.case_id}.md"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
    await asyncio.to_thread(run_index)
    return {"ok": True, "page_id": f"Reports/{card.case_id}", "summary": page_summary(summary)}


@app.get("/memory")
def memory_page():
    return FileResponse(Path(__file__).with_name("static") / "memory.html")


@app.get("/memory/facts")
def memory_facts(query: str = "", limit: int = 20):
    return {"ok": True, "items": memory.list_facts(query, max(1, min(limit, 50)))}


@app.get("/memory/facts/{fact_id}")
def memory_fact(fact_id: str):
    fact = memory.get_fact(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return {"ok": True, "fact": fact}


@app.post("/memory/facts")
def memory_create(fact: FactCreate):
    return {"ok": True, "fact": memory.create_fact(fact.statement, fact.valid_to)}


@app.put("/memory/facts/{fact_id}")
def memory_update(fact_id: str, fact: FactUpdate):
    try:
        return {"ok": True, "fact": memory.update_fact(fact_id, fact.statement, fact.valid_to, fact.base_revision)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/memory/facts/{fact_id}/controls")
def memory_controls(fact_id: str, control: FactControl):
    try:
        return {"ok": True, "fact": memory.set_controls(
            fact_id,
            control.base_revision,
            pinned=control.pinned,
            excluded=control.excluded,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
