"""Общие предметные контракты Evidence, findings, coverage и гипотез."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


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
