"""Детерминированная выгрузка событий в таблицу и Markdown-файл."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

import llm as llm_mod
from agents import _agent_final
from config import Settings
from contracts import EvidenceRecord, RecommendedExportQuery
from progress import done_progress, report_suggestions
from reporting import build_report
from state import State
from tools import _result_status, extract_data_tool

settings = Settings()


def _export_source(state: State) -> dict:
    recommended = (
        (state.get("report_result") or {}).get("recommended_export_query")
        or (state.get("inv_result") or {}).get("recommended_export_query")
    )
    if recommended:
        return RecommendedExportQuery.model_validate(recommended).model_dump()
    records = state.get("evidence") or []
    source = next((item for item in reversed(records) if item.get("tool") == "extract_data"), None)
    if source is None:
        source = next(
            (item for item in reversed(records) if (item.get("arguments") or {}).get("filter") is not None),
            None,
        )
    return dict((source or {}).get("arguments") or {})


def _export_text(result: Any) -> str:
    text = getattr(result, "text", None)
    if text:
        return text
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return "\n".join(item.get("text", "") for item in content)
    return json.dumps(content, ensure_ascii=False)


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _export_table(data: dict) -> str:
    events = data.get("events") or []
    if not events:
        return "События по текущему фильтру не найдены."
    columns = data.get("columns") or list(dict.fromkeys(key for row in events for key in row))
    header = "| " + " | ".join(_markdown_cell(column) for column in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| " + " | ".join(_markdown_cell(row.get(column)) for column in columns) + " |"
        for row in events
    ]
    return "\n".join([header, divider, *rows])


async def node_export(state: State) -> dict:
    """Полная выгрузка текущего фильтра без повторного анализа через LLM."""
    previous = _export_source(state)
    context = state.get("context") or {}
    args = {
        "time_from": context.get("time_from"),
        "time_to": context.get("time_to"),
        "filter": previous.get("filter"),
        "columns": previous.get("columns"),
        "order": previous.get("order") or "asc",
    }
    evidence_id = f"export-{uuid4().hex}"
    try:
        if extract_data_tool is None:
            raise RuntimeError("Инструмент extract_data недоступен")
        raw = await asyncio.wait_for(
            extract_data_tool.ainvoke(args),
            timeout=settings.export_timeout_seconds,
        )
        text = _export_text(raw)
        data = json.loads(text)
        if not isinstance(data, dict):
            data = {"result": data}
    except TimeoutError:
        data = {"ok": False, "error": "Технический таймаут: выгрузка остановлена из-за слишком долгого ответа."}
    except Exception as exc:
        data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    status = _result_status(data)
    evidence = EvidenceRecord(
        id=evidence_id,
        investigation_id=context.get("id"),
        step_id=evidence_id,
        tool="extract_data",
        arguments=args,
        window=data.get("window") or {
            "from": args["time_from"],
            "to": args["time_to"],
            "source": "investigation-context",
        },
        status=status,
        complete=status == "ok",
        result_ref=f"evidence://{context.get('id')}/{evidence_id}",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        result=data,
        error=data.get("error"),
    ).model_dump()
    limitations: list[str] = []
    if data.get("ok") is False:
        answer = f"Не удалось выгрузить события: {data.get('error')}"
        limitations.append(str(data.get("error")))
    else:
        table = _export_table(data)
        rows = data.get("returned_rows", len(data.get("events") or []))
        answer = f"Выгружено событий: {rows}.\n\n{table}"
        if not data.get("complete", False):
            limitation = data.get("hint") or f"Выгрузка неполная: получено {rows} событий."
            limitations.append(limitation)
            answer = f"Внимание: {limitation}\n\n{answer}"

    structured = llm_mod.ReporterResult(
        answer=answer,
        claims=[] if data.get("ok") is False else [{
            "text": f"Выгрузка extract_data вернула {data.get('returned_rows', 0)} событий.",
            "evidence_ids": [evidence_id],
        }],
        limitations=limitations,
    )
    update: dict = {
        "evidence": [evidence],
        "report_result": structured.model_dump(),
    }
    artifacts: list[str] = []
    if data.get("ok") is not False and data.get("events"):
        filename, url, _ = build_report("Выгрузка событий", answer)
        artifacts.append(url)
        update["last_report"] = {"title": "Выгрузка событий", "markdown": answer, "filename": filename}
    update["artifacts"] = artifacts
    update["progress"] = done_progress("Готово с ограничениями" if limitations else "Готово")
    update["suggestions"] = report_suggestions(
        {**state, "evidence": [*list(state.get("evidence") or []), evidence]},
        exported=True,
        has_report=bool(artifacts),
    )
    update.update(_agent_final(state, AIMessage(content=answer), "reporter", artifacts=artifacts))
    return update
