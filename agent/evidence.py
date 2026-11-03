"""Evidence Bundle, компактные ссылки и цепочка текущего расследования."""

import json
from typing import Iterable


def evidence_record(state: dict, evidence_id: str) -> dict | None:
    return next((item for item in state.get("evidence") or [] if item.get("id") == evidence_id), None)


def claim_evidence_ids(state: dict) -> list[str]:
    ids: list[str] = []
    for key in ("scout_result", "inv_result", "report_result"):
        result = state.get(key) or {}
        for evidence_id in result.get("evidence_ids") or []:
            ids.append(evidence_id)
        for claim in result.get("claims") or []:
            ids.extend(claim.get("evidence_ids") or [])
        skill_run = result.get("skill_run") or {}
        ids.extend(skill_run.get("evidence_ids") or [])
        for anchor in skill_run.get("anchors") or []:
            ids.extend(anchor.get("evidence_ids") or [])
    for finding in state.get("findings") or []:
        ids.extend(finding.get("evidence_ids") or [])
    for hypothesis in state.get("hypotheses") or []:
        ids.extend(hypothesis.get("evidence_ids") or [])
    available = {item.get("id") for item in state.get("evidence") or []}
    return list(dict.fromkeys(item for item in ids if item in available))


def _result_overview(result: dict) -> dict:
    overview = {}
    for key in ("ok", "count", "returned_rows", "total", "complete", "next_page", "truncated", "error", "hint"):
        if key in result:
            overview[key] = result[key]
    events = result.get("events")
    if isinstance(events, list):
        overview["events_count"] = len(events)
    overview["result_keys"] = sorted(result)
    return overview


def evidence_overview_block(state: dict) -> str:
    records = state.get("evidence") or []
    if not records:
        return "Доказательства: пока отсутствуют."
    items = []
    for record in records:
        item = {key: value for key, value in record.items() if key != "result"}
        item["result_preview"] = _result_overview(record.get("result") or {})
        items.append(item)
    return (
        "Обзор Evidence. Полную запись читай через get_evidence(evidence_id), "
        "если preview недостаточно:\n" + json.dumps(items, ensure_ascii=False)
    )


def evidence_bundle(state: dict, evidence_ids: Iterable[str] | None = None) -> dict:
    records = state.get("evidence") or []
    if evidence_ids is not None:
        wanted = set(evidence_ids)
        records = [item for item in records if item.get("id") in wanted]
    context = state.get("context") or {}
    return {
        "investigation_id": context.get("id"),
        "work_mode": state.get("work_mode"),
        "base_window": context.get("base_window"),
        "focus_window": context.get("focus_window"),
        "items": records,
    }


def full_evidence_block(state: dict, evidence_ids: Iterable[str] | None = None) -> str:
    bundle = evidence_bundle(state, evidence_ids)
    if not bundle["items"]:
        return "Доказательства: отсутствуют."
    return "Фактические результаты инструментов (полный Evidence Bundle):\n" + json.dumps(
        bundle, ensure_ascii=False
    )


def case_events(state: dict) -> list[dict]:
    events = []
    for item in (state.get("context") or {}).get("window_history") or []:
        events.append({"at": item.get("at"), "type": "window", **item})
    for item in state.get("depth_history") or []:
        events.append({"at": item.get("at"), "type": "depth", **item})
    for item in state.get("evidence") or []:
        events.append({
            "at": item.get("created_at"),
            "type": "evidence",
            "evidence_id": item.get("id"),
            "tool": item.get("tool"),
            "status": item.get("status"),
            "window": item.get("window"),
            "arguments": item.get("arguments") or {},
            "iql": item.get("iql"),
        })
    for item in state.get("skill_runs") or []:
        events.append({"at": item.get("recorded_at"), "type": "skill", **item})
    for item in state.get("findings") or []:
        events.append({"at": item.get("timestamp"), "type": "finding", **item})
    for hypothesis in state.get("hypotheses") or []:
        history = hypothesis.get("history") or [{
            "at": hypothesis.get("updated_at"),
            "status": hypothesis.get("status"),
            "reason": hypothesis.get("reason"),
            "evidence_ids": hypothesis.get("evidence_ids") or [],
        }]
        for event in history:
            events.append({
                "type": "hypothesis",
                "hypothesis_id": hypothesis.get("id"),
                "text": hypothesis.get("text"),
                **event,
            })
    return sorted(events, key=lambda item: item.get("at") or "")


def _result_values(state: dict, key: str) -> list[str]:
    values = []
    for result_key in ("inv_result", "report_result"):
        values.extend((state.get(result_key) or {}).get(key) or [])
    if key == "not_checked":
        for run in state.get("skill_runs") or []:
            values.extend(run.get(key) or [])
    return list(dict.fromkeys(values))


def investigation_chain(state: dict) -> dict:
    context = state.get("context") or {}
    human_messages = [
        item.content for item in state.get("messages") or []
        if getattr(item, "type", None) == "human" and isinstance(item.content, str)
    ]
    return {
        "investigation_id": context.get("id"),
        "task": human_messages[-1] if human_messages else "",
        "work_mode": state.get("work_mode"),
        "current_window": {
            "time_from": context.get("time_from"),
            "time_to": context.get("time_to"),
        },
        "base_window": context.get("base_window"),
        "anchors": list(state.get("anchors") or []),
        "visited_anchors": list(state.get("visited_anchors") or []),
        "hypotheses": list(state.get("hypotheses") or []),
        "events": case_events(state),
        "limitations": _result_values(state, "limitations"),
        "not_checked": _result_values(state, "not_checked"),
    }


def chain_block(state: dict) -> str:
    return "Детерминированная цепочка расследования:\n" + json.dumps(
        investigation_chain(state), ensure_ascii=False
    )


def evidence_query_block(record: dict) -> str:
    iql = record.get("iql")
    if iql:
        return f"\n\n```iql\n{iql}\n```"
    arguments = json.dumps(record.get("arguments") or {}, ensure_ascii=False)
    return f"\n\n```json\n{arguments}\n```"


def queries_markdown(state: dict) -> str:
    lines = ["# Запросы расследования", ""]
    for index, record in enumerate(state.get("evidence") or [], start=1):
        lines.extend([
            f"## {index}. {record.get('tool')}",
            "",
            f"- Evidence: `{record.get('id')}`",
            f"- Статус: `{record.get('status')}`",
            f"- Окно: `{record.get('window')}`",
            f"- Результат: `evidence.jsonl`, запись {index}.",
            evidence_query_block(record),
            "",
        ])
    if not (state.get("evidence") or []):
        lines.append("Запросов не было.")
    return "\n".join(lines)


def chain_markdown(state: dict) -> str:
    chain = investigation_chain(state)
    lines = [
        "# Цепочка расследования",
        "",
        f"- ID: `{chain['investigation_id']}`",
        f"- Задача: {chain['task']}",
        f"- Режим: `{chain['work_mode']}`",
        f"- Текущее окно: `{chain['current_window']['time_from']}` — `{chain['current_window']['time_to']}`",
        "",
        "## Ход работы",
        "",
    ]
    if not chain["events"]:
        lines.append("Значимых действий пока нет.")
    evidence_positions = {
        item.get("id"): index for index, item in enumerate(state.get("evidence") or [], start=1)
    }
    for index, event in enumerate(chain["events"], start=1):
        kind = event.get("type")
        if kind == "evidence":
            evidence_id = event.get("evidence_id")
            lines.append(
                f"{index}. **{event.get('tool')}** — `{event.get('status')}`, "
                f"Evidence `{evidence_id}`, окно `{event.get('window')}`."
            )
            if evidence_id in evidence_positions:
                lines.append(f"   Результат: `evidence.jsonl`, запись {evidence_positions[evidence_id]}." + evidence_query_block(event))
        elif kind == "window":
            lines.append(
                f"{index}. **Окно: {event.get('action')}** — {event.get('reason')}. "
                f"`{event.get('from')}` → `{event.get('to')}`."
            )
        elif kind == "depth":
            lines.append(
                f"{index}. **Режим: {event.get('from')} → {event.get('to')}** — "
                f"{event.get('reason')}."
            )
        elif kind == "skill":
            metrics = ""
            if event.get("model"):
                metrics = f" Модель `{event['model']}`."
            if event.get("duration_ms") is not None:
                metrics += f" Длительность {event['duration_ms']} мс."
            lines.append(
                f"{index}. **Skill {event.get('skill_id')} v{event.get('version')}** — "
                f"`{event.get('status')}`, {event.get('summary')} "
                f"Evidence: {event.get('evidence_ids') or []}.{metrics}"
            )
        elif kind == "finding":
            lines.append(
                f"{index}. **Находка:** {event.get('claim')} "
                f"Evidence: {event.get('evidence_ids') or []}."
            )
        elif kind == "hypothesis":
            lines.append(
                f"{index}. **Гипотеза {event.get('hypothesis_id')} → {event.get('status')}:** "
                f"{event.get('text')} Причина: {event.get('reason')} "
                f"Evidence: {event.get('evidence_ids') or []}."
            )
    if chain["anchors"]:
        lines.extend(["", "## Anchors", ""])
        visited = set(chain["visited_anchors"])
        for anchor in chain["anchors"]:
            key = f"{anchor.get('type')}:{str(anchor.get('value')).lower()}"
            status = "visited" if key in visited else "not_checked"
            lines.append(f"- `{key}` — {status}; Evidence: {anchor.get('evidence_ids') or []}.")
    if chain["hypotheses"]:
        lines.extend(["", "## Гипотезы", ""])
        for hypothesis in chain["hypotheses"]:
            lines.append(
                f"- `{hypothesis.get('id')}` — **{hypothesis.get('status')}**: {hypothesis.get('text')} "
                f"Основание: {hypothesis.get('reason')} Evidence: {hypothesis.get('evidence_ids') or []}."
            )
    lines.extend(["", "## Ограничения", ""])
    lines.extend(f"- {item}" for item in chain["limitations"])
    if not chain["limitations"]:
        lines.append("Явных ограничений не зафиксировано.")
    lines.extend(["", "## NOT_CHECKED", ""])
    lines.extend(f"- {item}" for item in chain["not_checked"])
    if not chain["not_checked"]:
        lines.append("Не указано.")
    return "\n".join(lines)


def with_investigation_chain(markdown: str, state: dict) -> str:
    if not (state.get("evidence") or state.get("findings")):
        return markdown
    if "# Цепочка расследования" in markdown:
        return markdown
    return markdown.rstrip() + "\n\n---\n\n" + chain_markdown(state) + "\n"
