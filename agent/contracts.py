"""Общие предметные контракты Evidence, findings, coverage и гипотез."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EvidenceRecord(BaseModel):
    id: str
    investigation_id: str | None = None
    step_id: str
    tool: str
    arguments: dict = Field(default_factory=dict)
    iql: str | None = None
    window: dict = Field(default_factory=dict)
    status: Literal["ok", "partial", "error"]
    complete: bool
    result_ref: str
    created_at: str
    result: Any = Field(default_factory=dict)
    error: Any = None


class FindingRecord(BaseModel):
    timestamp: str
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    next_action: str = ""


class Coverage(BaseModel):
    status: Literal["complete", "partial", "empty", "error"] = "partial"
    details: str = ""


class RecommendedExportQuery(BaseModel):
    filter: Any = None
    columns: list[str] | None = None
    order: Literal["asc", "desc"] = "asc"


class CriticIssue(BaseModel):
    claim: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    suggested_action: Literal["rework_report", "rework_data"]


class ScopeDirection(BaseModel):
    objective: str = Field(min_length=3)
    rationale: str = ""


def _scope_objective(value):
    if isinstance(value, str):
        return value
    objective = getattr(value, "objective", None)
    if isinstance(objective, str):
        return objective
    if isinstance(value, dict):
        for key in ("objective", "goal", "text", "description"):
            if isinstance(value.get(key), str):
                return value[key]
    return value


class InvestigationScope(BaseModel):
    mode: Literal["discovery", "targeted", "alert"] = "targeted"
    objective: str = Field(min_length=3)
    included: list[str] = Field(default_factory=list, max_length=5)
    optional: list[ScopeDirection] = Field(default_factory=list, max_length=3)
    max_items: int = Field(default=1, ge=1, le=5)
    sampling_policy: str = "Агрегаты, топы, timeline и небольшие выборки; без полного перебора событий."
    alert: dict = Field(default_factory=dict)

    @field_validator("included", mode="before")
    @classmethod
    def normalize_included(cls, value):
        if not isinstance(value, list):
            return value
        return [_scope_objective(item) for item in value]

    @field_validator("optional", mode="before")
    @classmethod
    def normalize_optional(cls, value):
        if not isinstance(value, list):
            return value
        directions = []
        for item in value:
            if isinstance(item, ScopeDirection):
                directions.append(item)
            elif isinstance(item, str):
                directions.append({"objective": item})
            elif isinstance(item, dict):
                directions.append({**item, "objective": _scope_objective(item)})
            else:
                directions.append(item)
        return directions


class WorkItem(BaseModel):
    id: str
    goal: str = Field(min_length=3)
    acceptance: str = ""
    priority: Literal["high", "medium", "low"] = "medium"
    estimated_cost: Literal["low", "medium", "high"] = "medium"
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True
    requested_window: dict = Field(default_factory=dict)
    status: Literal["pending", "running", "critic_review", "done", "failed", "skipped"] = "pending"
    attempt: int = 0
    started_at: str = ""
    finished_at: str = ""
    result: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    critic: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)


class InvestigationPlan(BaseModel):
    goal: str = ""
    reason: str = ""
    version: int = 1
    items: list[WorkItem] = Field(default_factory=list, min_length=1, max_length=5)


class HypothesisEvent(BaseModel):
    status: Literal["open", "supported", "rejected"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: f"hyp-{uuid4().hex}")
    text: str = Field(min_length=3)
    status: Literal["open", "supported", "rejected"] = "open"
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    history: list[HypothesisEvent] = Field(default_factory=list)
