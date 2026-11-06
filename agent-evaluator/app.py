"""Фоновый контур оценки завершённых runs по Aegra state и Phoenix traces."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from evaluator import deterministic_checks, evaluate
from models import EvaluationIssue, EvaluationRecord
from phoenix_client import PhoenixClient
from store import EvaluationStore


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    llm_model: str
    llm_model_evaluator: str = ""
    llm_base_url: str
    llm_token: str
    agent_memory_database_url: str
    aegra_api_url: str = "http://aegra:2026"
    phoenix_url: str = "http://phoenix:6006"
    phoenix_public_url: str = "http://localhost:6006"
    phoenix_project: str = "default"
    evaluator_enabled: bool = True
    evaluator_poll_seconds: int = 20
    evaluator_thread_limit: int = 25
    evaluator_timeout_seconds: int = 120
    evaluator_trace_attempts: int = 15
    evaluator_trace_attribute_limit: int = 2500
    evaluator_phoenix_annotations_enabled: bool = True


settings = Settings()
store = EvaluationStore(settings.agent_memory_database_url)
phoenix = PhoenixClient(settings.phoenix_url, settings.phoenix_project)
_trace_attempts: dict[str, int] = {}


def evaluation_markdown(record: EvaluationRecord) -> str:
    verdict = {"pass": "пройдена", "warn": "есть замечания", "fail": "обнаружены проблемы"}[record.verdict]
    lines = [
        "## Автодиагностика",
        f"**Результат:** {verdict}; оценка **{record.score:.0%}**.",
        record.summary,
    ]
    if record.issues:
        lines.append("\nОсновные замечания:")
        for issue in record.issues[:3]:
            lines.append(f"- **{issue.severity} / {issue.category}:** {issue.summary}")
    if record.trace_id:
        project_id = record.metrics.get("phoenix_project_id") or "UHJvamVjdDox"
        lines.append(
            f"\n[Открыть trace в Phoenix]({settings.phoenix_public_url}/projects/{project_id}/traces/{record.trace_id})"
        )
    lines.append("\nОценка вынесена в отдельный контур и не изменяет исходный отчёт.")
    return "\n".join(lines)


async def _state(client: httpx.AsyncClient, thread_id: str) -> dict:
    response = await client.get(f"{settings.aegra_api_url}/threads/{thread_id}/state")
    response.raise_for_status()
    payload = response.json()
    return payload.get("values") or payload


async def _publish(client: httpx.AsyncClient, record: EvaluationRecord) -> None:
    runs = await client.get(f"{settings.aegra_api_url}/threads/{record.thread_id}/runs", params={"limit": 1})
    runs.raise_for_status()
    latest = (runs.json() or [{}])[0]
    latest_id = latest.get("run_id") or latest.get("runId")
    if latest_id != record.run_id or latest.get("status") != "success":
        record.publication_error = "В треде начался другой run; оценка сохранена без сообщения."
        store.save(record)
        return
    response = await client.post(
        f"{settings.aegra_api_url}/threads/{record.thread_id}/state",
        json={
            "values": {
                "messages": [{
                    "type": "ai",
                    "id": f"evaluation-{record.run_id}",
                    "name": "evaluator",
                    "content": evaluation_markdown(record),
                }],
                "evaluation": record.model_dump(mode="json"),
            },
            "as_node": "memory_save",
        },
    )
    response.raise_for_status()
    store.mark_published(record.run_id)


async def evaluate_run(thread_id: str, run_id: str) -> EvaluationRecord | None:
    if store.has(run_id):
        return None
    async with httpx.AsyncClient(timeout=60) as client:
        state = await _state(client, thread_id)
    run_index = await asyncio.to_thread(phoenix.recent_runs)
    run_trace = run_index.get(run_id)
    if run_trace is None:
        _trace_attempts[run_id] = _trace_attempts.get(run_id, 0) + 1
        if _trace_attempts[run_id] < settings.evaluator_trace_attempts:
            return None
        record = EvaluationRecord(
            run_id=run_id,
            thread_id=thread_id,
            verdict="warn",
            score=0.5,
            summary="Не удалось найти trace run в Phoenix; независимая оценка не выполнена.",
            issues=[EvaluationIssue(
                severity="warning",
                category="runtime",
                summary="Trace не появился в Phoenix после завершения run.",
                recommendation="Проверить доставку OpenTelemetry и retention проекта.",
            )],
        )
    else:
        trace = await asyncio.to_thread(phoenix.trace, run_trace["trace_id"])
        try:
            result = await asyncio.to_thread(evaluate, state, trace, settings)
            issues = result.issues
            verdict, score, summary = result.verdict, result.score, result.summary
            model = settings.llm_model_evaluator or settings.llm_model
        except Exception as exc:
            deterministic, _ = deterministic_checks(state, trace)
            issues = [*deterministic, EvaluationIssue(
                severity="warning",
                category="runtime",
                summary=f"LLM-evaluator недоступен: {type(exc).__name__}.",
                recommendation="Повторить оценку после восстановления модели.",
            )][:10]
            failed = any(item.severity == "critical" for item in deterministic)
            verdict = "fail" if failed else "warn"
            score = 0.4 if failed else 0.5
            summary = "Независимая LLM-оценка не завершена; сохранены детерминированные проверки."
            model = ""
        _, metrics = deterministic_checks(state, trace)
        metrics["phoenix_project_id"] = run_trace.get("project_id")
        record = EvaluationRecord(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=run_trace["trace_id"],
            verdict=verdict,
            score=score,
            summary=summary,
            issues=issues,
            model=model,
            metrics=metrics,
        )
    if settings.evaluator_phoenix_annotations_enabled and record.trace_id:
        try:
            await asyncio.to_thread(phoenix.annotate, record, state)
            record.metrics["phoenix_annotation"] = "saved"
        except Exception as exc:
            record.metrics["phoenix_annotation"] = "error"
            record.metrics["phoenix_annotation_error"] = type(exc).__name__
    store.save(record)
    async with httpx.AsyncClient(timeout=60) as client:
        await _publish(client, record)
    _trace_attempts.pop(run_id, None)
    return record


async def scan() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.aegra_api_url}/threads/search",
            json={"limit": settings.evaluator_thread_limit},
        )
        response.raise_for_status()
        threads = response.json()
        candidates = []
        for thread in threads:
            thread_id = thread.get("thread_id") or thread.get("threadId")
            runs = await client.get(f"{settings.aegra_api_url}/threads/{thread_id}/runs", params={"limit": 5})
            if runs.status_code != 200:
                continue
            for run in runs.json():
                run_id = run.get("run_id") or run.get("runId")
                if run.get("status") == "success" and run_id and not store.has(run_id):
                    candidates.append((thread_id, run_id))
    for thread_id, run_id in candidates:
        try:
            await evaluate_run(thread_id, run_id)
        except Exception:
            logging.exception("Evaluation failed: thread=%s run=%s", thread_id, run_id)


async def worker() -> None:
    while True:
        if settings.evaluator_enabled:
            try:
                await scan()
            except Exception:
                logging.exception("Evaluation scan failed")
        await asyncio.sleep(settings.evaluator_poll_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(worker())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="agent-evaluator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    metrics = {}
    if store.enabled:
        try:
            metrics = store.summary()
        except Exception as exc:
            metrics = {"storage_error": type(exc).__name__}
    return {
        "ok": not metrics.get("storage_error"),
        "enabled": settings.evaluator_enabled,
        "storage": store.enabled,
        "evaluations": metrics,
    }


@app.post("/evaluate/{thread_id}/{run_id}")
async def evaluate_now(thread_id: str, run_id: str) -> dict:
    record = await evaluate_run(thread_id, run_id)
    if record is None:
        return {"ok": True, "status": "already_evaluated_or_waiting_for_trace"}
    return {"ok": True, "evaluation": record.model_dump(mode="json")}
