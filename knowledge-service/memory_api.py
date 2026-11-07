from datetime import UTC, datetime
from uuid import uuid4

from langchain_openai import OpenAIEmbeddings

from config import Settings
from memory_models import ContextFact
from memory_store import MemoryStore


class MemoryEditor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = MemoryStore(
            settings.agent_memory_database_url,
            settings.agent_memory_embedding_dimensions,
        )
        self.scope = settings.agent_memory_scope
        self._embeddings = None

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
            return self._embeddings.embed_query(text)
        except Exception:
            return None

    def list_facts(self, query: str = "", limit: int = 20) -> list[dict]:
        if query.strip():
            embedding = self._embed(query)
            if embedding is not None:
                return self.store.search_facts(
                    self.scope,
                    embedding,
                    limit,
                    self.settings.agent_memory_similarity_threshold,
                )
            facts = self.store.recent_facts(self.scope, max(limit, 50))
            lowered = query.lower()
            return [item for item in facts if lowered in item["statement"].lower()][:limit]
        return self.store.recent_facts(self.scope, limit)

    def get_fact(self, fact_id: str) -> dict | None:
        fact = self.store.get_fact(self.scope, fact_id)
        return fact.model_dump(mode="json") if fact else None

    def create_fact(self, statement: str, valid_to=None) -> dict:
        fact = ContextFact(
            statement=statement.strip(),
            status="confirmed",
            source_kind="user",
            scope=self.scope,
            valid_to=valid_to,
        )
        return self._save(fact)

    def update_fact(self, fact_id: str, statement: str, valid_to, base_revision: int) -> dict:
        current = self.store.get_fact(self.scope, fact_id)
        if current is None:
            raise LookupError("Факт не найден")
        if current.revision != base_revision:
            raise ValueError("Факт изменился после загрузки; обновите страницу")
        now = datetime.now(UTC)
        fact = ContextFact(
            **{
                **current.model_dump(),
                "id": uuid4().hex,
                "statement": statement.strip(),
                "status": "confirmed",
                "source_kind": "user",
                "evidence_ids": [],
                "revision": current.revision + 1,
                "valid_to": valid_to,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self._save(fact, current)

    def set_controls(
        self,
        fact_id: str,
        base_revision: int,
        *,
        pinned: bool | None = None,
        excluded: bool | None = None,
    ) -> dict:
        fact = self.store.update_fact_controls(
            self.scope,
            fact_id,
            base_revision,
            pinned=pinned,
            excluded=excluded,
            expire=excluded is True,
        )
        if fact is None:
            raise ValueError("Факт изменился после загрузки; обновите страницу")
        if excluded is False:
            current = self.store.get_fact(self.scope, fact_id)
            if current and current.valid_to and current.valid_to <= datetime.now(UTC):
                return self.update_fact(fact_id, current.statement, None, fact.revision)
        return fact.model_dump(mode="json")

    def _save(self, fact: ContextFact, target: ContextFact | None = None) -> dict:
        embedding = self._embed(fact.statement)
        fact.index_status = "ready" if embedding is not None else "pending"
        if target is None:
            self.store.put_fact(fact, embedding)
        else:
            self.store.supersede_fact(target, fact, embedding)
        return fact.model_dump(mode="json")
