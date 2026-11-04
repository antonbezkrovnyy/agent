"""Контракты независимой оценки завершённых runs."""

from typing import Literal

from pydantic import BaseModel, Field


class EvaluationIssue(BaseModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal[
        "unsupported_claim",
        "missing_data",
        "tool_error",
        "window",
        "memory",
        "limit",
        "report",
        "runtime",
        "scope_creep",
        "other",
    ]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    span_ids: list[str] = Field(default_factory=list)
    recommendation: str = ""


class EvaluationResult(BaseModel):
    verdict: Literal["pass", "warn", "fail"]
    score: float = Field(ge=0, le=1)
    summary: str
    issues: list[EvaluationIssue] = Field(default_factory=list, max_length=10)


class EvaluationRecord(EvaluationResult):
    run_id: str
    thread_id: str
    trace_id: str = ""
    model: str = ""
    metrics: dict = Field(default_factory=dict)
    deterministic_issues: list[EvaluationIssue] = Field(default_factory=list)
    publication_error: str = ""
