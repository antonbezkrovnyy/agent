"""Предметные схемы долговременной памяти расследований и окружения."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from config import Settings

settings = Settings()


class CaseRecord(BaseModel):
    """Последний полный снимок дела, независимый от checkpoint треда."""

    investigation_id: str
    user_id: str
    thread_id: str = ""
    task: str = ""
    summary: str = ""
    signature: str = ""
    entities: list[dict] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    context: dict = Field(default_factory=dict)
    work_mode: str = "quick"
    depth_history: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    hypotheses: list[dict] = Field(default_factory=list)
    skill_runs: list[dict] = Field(default_factory=list)
    anchors: list[dict] = Field(default_factory=list)
    visited_anchors: list[str] = Field(default_factory=list)
    inv_summary: str = ""
    inv_result: dict = Field(default_factory=dict)
    last_report: dict = Field(default_factory=dict)
    memory_operations: list["MemoryOperationRecord"] = Field(default_factory=list)
    investigation_scope: dict = Field(default_factory=dict)
    investigation_plan: dict = Field(default_factory=dict)
    work_items: list[dict] = Field(default_factory=list)
    run_metrics: list[dict] = Field(default_factory=list)
    chain: dict = Field(default_factory=dict)


class MemoryEntity(BaseModel):
    type: str = "custom"
    value: str = Field(min_length=1)


class ContextFactDraft(BaseModel):
    """Атомарный факт, предложенный LLM после анализа завершённого дела."""

    statement: str = Field(min_length=3, description="Один проверяемый факт без гипотез и рекомендаций")
    entities: list[MemoryEntity] = Field(default_factory=list)
    category: Literal[
        "network",
        "infrastructure",
        "identity",
        "application",
        "security",
        "organization",
        "other",
    ] = "other"
    status: Literal["confirmed", "observed"] = Field(
        description="confirmed — явный ввод пользователя или доказанный вывод; observed — разовое наблюдение"
    )
    source_kind: Literal["user", "investigation"]
    source_case_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class ContextFact(ContextFactDraft):
    id: str = Field(default_factory=lambda: uuid4().hex)
    scope: str = "default"
    source_case_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContextMemoryOperation(BaseModel):
    action: Literal["insert", "update", "supersede", "ignore"]
    target_id: str | None = Field(
        default=None,
        description="Существующий факт для update/supersede/ignore; только из existing_facts",
    )
    fact: ContextFactDraft | None = Field(
        default=None,
        description="Обязателен для insert/update/supersede",
    )
    reason: str = ""


class ContextMemoryConsolidation(BaseModel):
    operations: list[ContextMemoryOperation] = Field(
        default_factory=list,
        max_length=settings.agent_memory_max_operations,
    )


class MemoryOperationRecord(BaseModel):
    """Фактический результат применения одного предложения консолидации."""

    action: Literal["insert", "update", "supersede", "ignore"]
    requested_action: Literal["insert", "update", "supersede", "ignore"]
    statement: str = ""
    target_id: str | None = None
    fact_id: str | None = None
    status: Literal["saved", "rejected", "ignored"]
    reason: str = ""
    error: str = ""
