"""Публичные контракты версионируемых investigation skills."""

from typing import Literal

from pydantic import BaseModel, Field


class Anchor(BaseModel):
    type: Literal["ip", "account", "host", "hash", "process", "event", "custom"]
    value: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class SkillDefinition(BaseModel):
    id: str
    version: int = Field(gt=0)
    triggers: list[str]
    allowed_tools: list[str]
    strategy: list[str]
    stop_rules: list[str]
    max_window_updates: int = Field(ge=0)
    max_depth: int = Field(default=0, ge=0)
    max_branches: int = Field(default=1, gt=0)


class SkillRunResult(BaseModel):
    skill_id: str
    version: int = Field(gt=0)
    status: Literal["done", "partial", "failed"]
    summary: str
    window_updates: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    visited_anchors: list[str] = Field(default_factory=list)
    depth: int = Field(default=0, ge=0)
    model: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    not_checked: list[str] = Field(default_factory=list)
