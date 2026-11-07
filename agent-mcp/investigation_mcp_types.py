"""TypedDict output schemas для штатного MCP structuredContent."""

from typing import Any, NotRequired, TypedDict


class ErrorFields(TypedDict, total=False):
    error: str
    error_code: str
    details: str
    retryable: bool


class StartResult(ErrorFields):
    ok: bool
    investigation_id: NotRequired[str]
    run_id: NotRequired[str | None]
    status: NotRequired[str]


class StatusResult(ErrorFields):
    ok: bool
    investigation_id: NotRequired[str]
    run_id: NotRequired[str | None]
    stage: NotRequired[str]
    status: NotRequired[str]
    message: NotRequired[str]
    tool_calls: NotRequired[int]
    tool_limit: NotRequired[int]
    work_mode: NotRequired[str]
    scope: NotRequired[dict[str, Any]]
    objective: NotRequired[str]
    completed_items: NotRequired[int]
    total_items: NotRequired[int]
    plan_version: NotRequired[int]
    memory_error: NotRequired[str]
    complete: NotRequired[bool]
    progress: NotRequired[dict[str, Any]]


class ResultResponse(ErrorFields):
    ok: bool
    investigation_id: NotRequired[str]
    status: NotRequired[str]
    title: NotRequired[str]
    report: NotRequired[str]
    claims: NotRequired[list[dict[str, Any]]]
    limitations: NotRequired[list[str]]
    not_checked: NotRequired[list[str]]
    scope: NotRequired[dict[str, Any]]
    next_directions: NotRequired[list[dict[str, Any]]]
    memory_operations: NotRequired[list[dict[str, Any]]]
    run_metrics: NotRequired[list[dict[str, Any]]]
    artifacts: NotRequired[list[dict[str, Any]]]
    verification: NotRequired[dict[str, Any]]
    tool_calls: NotRequired[list[dict[str, Any]]]
    analysis_datasets: NotRequired[list[dict[str, Any]]]
    analysis_results: NotRequired[list[dict[str, Any]]]
    package_sha256: NotRequired[str]
    manifest_sha256: NotRequired[str]
    progress: NotRequired[dict[str, Any]]


class EvidenceResult(ErrorFields):
    ok: bool
    investigation_id: NotRequired[str]
    evidence: NotRequired[list[dict[str, Any]]]


class CancelResult(ErrorFields):
    ok: bool
    investigation_id: NotRequired[str]
    run_id: NotRequired[str | None]
    status: NotRequired[str]
