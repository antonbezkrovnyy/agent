import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from langchain_openai import OpenAIEmbeddings
from psycopg.types.json import Jsonb

from config import Settings
from memory_store import MemoryStore, safe_text


def _scalar(value: str):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(item) for item in value[1:-1].split(",") if item.strip()]
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        return value.strip('"\'')


def parse_markdown(path: Path) -> dict:
    text = safe_text(path.read_text(encoding="utf-8"))
    metadata = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            frontmatter = text[4:end]
            body = text[end + 5:]
            current_list = None
            for line in frontmatter.splitlines():
                if line.startswith("  - ") and current_list:
                    metadata.setdefault(current_list, []).append(_scalar(line[4:]))
                    continue
                if ":" not in line or line.startswith(" "):
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                if value.strip():
                    metadata[key] = _scalar(value)
                    current_list = None
                else:
                    metadata[key] = []
                    current_list = key
    return {"metadata": metadata, "body": body.strip(), "text": text}


def split_sections(body: str) -> list[dict]:
    sections = []
    title = "Введение"
    ordinal = 0
    lines = []
    for line in body.splitlines():
        match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if match:
            if lines:
                sections.append({"id": f"section-{ordinal}", "title": title, "content": "\n".join(lines).strip()})
            ordinal += 1
            title = match.group(2)
            lines = []
        else:
            lines.append(line)
    if lines or not sections:
        sections.append({"id": f"section-{ordinal}", "title": title, "content": "\n".join(lines).strip()})
    return [
        {**item, "id": f"{item['id']}-{index}", "ordinal": index}
        for index, item in enumerate(sections)
        if item["content"]
    ]


def page_summary(body: str) -> str:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", body) if item.strip() and not item.lstrip().startswith("#")]
    summary = paragraphs[0] if paragraphs else body
    return re.sub(r"\s+", " ", summary)[:500]


class KnowledgeStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database_url = settings.agent_memory_database_url
        self.dimensions = settings.agent_memory_embedding_dimensions
        self.memory_store = MemoryStore(self.database_url, self.dimensions)
        self._ready = False

    def _setup(self):
        if self._ready or not self.database_url:
            return
        self.memory_store._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS agent_memory.wiki_pages (
                        id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL DEFAULT 1,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'published',
                        entities JSONB NOT NULL DEFAULT '[]'::jsonb,
                        content_hash TEXT NOT NULL,
                        index_status TEXT NOT NULL DEFAULT 'ready',
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS agent_memory.wiki_sections (
                        id TEXT NOT NULL,
                        page_id TEXT NOT NULL REFERENCES agent_memory.wiki_pages(id) ON DELETE CASCADE,
                        ordinal INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        embedding vector({self.dimensions}),
                        PRIMARY KEY (id, page_id)
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS wiki_sections_embedding_idx
                    ON agent_memory.wiki_sections USING hnsw (embedding vector_cosine_ops)
                """)
        self._ready = True

    def upsert_page(self, page: dict, sections: list[dict]):
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agent_memory.wiki_pages (
                        id, revision, title, summary, content, status, entities,
                        content_hash, index_status, updated_at
                    ) VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        revision = CASE WHEN wiki_pages.content_hash = EXCLUDED.content_hash
                            THEN wiki_pages.revision ELSE wiki_pages.revision + 1 END,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        content = EXCLUDED.content,
                        status = EXCLUDED.status,
                        entities = EXCLUDED.entities,
                        content_hash = EXCLUDED.content_hash,
                        index_status = EXCLUDED.index_status,
                        updated_at = now()
                """, (
                    page["id"], page["title"], page["summary"], page["content"], page["status"],
                    Jsonb(page["entities"]), page["content_hash"], page["index_status"],
                ))
                cursor.execute("DELETE FROM agent_memory.wiki_sections WHERE page_id = %s", (page["id"],))
                for section in sections:
                    cursor.execute("""
                        INSERT INTO agent_memory.wiki_sections (id, page_id, ordinal, title, content, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s::vector)
                    """, (
                        section["id"], page["id"], section["ordinal"], section["title"],
                        section["content"], self._vector(section.get("embedding")),
                    ))

    def remove_missing(self, active_ids: set[str]):
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                if active_ids:
                    cursor.execute("DELETE FROM agent_memory.wiki_pages WHERE NOT (id = ANY(%s))", (list(active_ids),))
                else:
                    cursor.execute("DELETE FROM agent_memory.wiki_pages")

    def search(self, query: str, embedding: list[float] | None, limit: int) -> list[dict]:
        self._setup()
        results = []
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                if embedding is not None:
                    cursor.execute("""
                        SELECT p.id, p.revision, p.title, p.summary, s.title,
                               1 - (s.embedding <=> %s::vector) AS score
                        FROM agent_memory.wiki_sections s
                        JOIN agent_memory.wiki_pages p ON p.id = s.page_id
                        WHERE p.status = 'published' AND s.embedding IS NOT NULL
                        ORDER BY s.embedding <=> %s::vector
                        LIMIT %s
                    """, (self._vector(embedding), self._vector(embedding), limit))
                    results = self._cards(cursor.fetchall())
                if len(results) < limit:
                    pattern = f"%{query}%"
                    cursor.execute("""
                        SELECT p.id, p.revision, p.title, p.summary, s.title, 0.0 AS score
                        FROM agent_memory.wiki_sections s
                        JOIN agent_memory.wiki_pages p ON p.id = s.page_id
                        WHERE p.status = 'published'
                          AND (p.title ILIKE %s OR p.summary ILIKE %s OR s.content ILIKE %s)
                        ORDER BY p.updated_at DESC
                        LIMIT %s
                    """, (pattern, pattern, pattern, limit * 2))
                    known = {item["id"] for item in results}
                    results.extend(item for item in self._cards(cursor.fetchall()) if item["id"] not in known)
        return results[:limit]

    def read_page(self, page_id: str, section: str = "") -> dict | None:
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, revision, title, summary, content, status, entities, index_status, updated_at
                    FROM agent_memory.wiki_pages WHERE id = %s AND status = 'published'
                """, (page_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                content = row[4]
                if section:
                    cursor.execute("""
                        SELECT title, content FROM agent_memory.wiki_sections
                        WHERE page_id = %s AND (id = %s OR lower(title) = lower(%s))
                        ORDER BY ordinal LIMIT 1
                    """, (page_id, section, section))
                    selected = cursor.fetchone()
                    if not selected:
                        return None
                    content = selected[1]
        return {
            "id": row[0], "revision": row[1], "title": row[2], "summary": row[3],
            "content": content[:12000], "truncated": len(content) > 12000,
            "entities": row[6], "index_status": row[7],
            "updated_at": row[8].isoformat(),
        }

    @staticmethod
    def _cards(rows) -> list[dict]:
        return [{
            "id": row[0], "revision": row[1], "title": row[2],
            "summary": row[3], "section": row[4], "score": round(float(row[5]), 4),
        } for row in rows]

    @staticmethod
    def _vector(value: list[float] | None) -> str | None:
        return json.dumps(value, separators=(",", ":")) if value is not None else None


class KnowledgeIndex:
    def __init__(self, settings: Settings, space_dir: str):
        self.settings = settings
        self.space_dir = Path(space_dir)
        self.store = KnowledgeStore(settings)
        self._embeddings = None
        self._lock = threading.Lock()

    def _embed(self, text: str) -> list[float] | None:
        try:
            if self._embeddings is None:
                self._embeddings = OpenAIEmbeddings(
                    model=self.settings.agent_memory_embedding_model,
                    base_url=self.settings.agent_memory_embedding_base_url or self.settings.llm_base_url,
                    api_key=self.settings.agent_memory_embedding_token or self.settings.llm_token,
                    timeout=self.settings.knowledge_timeout_seconds,
                    max_retries=0,
                )
            return self._embeddings.embed_query(text[:30000])
        except Exception:
            return None

    def _index_all_unlocked(self) -> dict:
        active = set()
        indexed = 0
        pending = 0
        for path in sorted(self.space_dir.rglob("*.md")):
            if any(part.startswith(".") for part in path.parts) or path.name in ("CONFIG", "Memory.md"):
                continue
            relative = path.relative_to(self.space_dir).with_suffix("").as_posix()
            parsed = parse_markdown(path)
            metadata = parsed["metadata"]
            if metadata.get("status", "published") != "published":
                continue
            body = parsed["body"]
            raw_sections = split_sections(body)
            sections = []
            for section in raw_sections:
                sections.append({**section, "embedding": self._embed(f"{metadata.get('title', path.stem)}\n{section['content']}")})
            pending += int(any(item.get("embedding") is None for item in sections))
            page = {
                "id": relative,
                "title": str(metadata.get("title") or path.stem),
                "summary": page_summary(body),
                "content": body,
                "status": "published",
                "entities": metadata.get("entities") or [],
                "content_hash": hashlib.sha256(parsed["text"].encode("utf-8")).hexdigest(),
                "index_status": "pending" if any(item.get("embedding") is None for item in sections) else "ready",
            }
            self.store.upsert_page(page, sections)
            active.add(relative)
            indexed += 1
        self.store.remove_missing(active)
        return {"indexed": indexed, "pending_embeddings": pending, "indexed_at": datetime.now(UTC).isoformat()}

    def index_all(self) -> dict:
        with self._lock:
            return self._index_all_unlocked()
