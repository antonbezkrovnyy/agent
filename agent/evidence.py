"""Evidence Bundle, компактные ссылки и цепочка текущего расследования."""

from typing import Iterable

from contracts import json_dumps


def evidence_record(state: dict, evidence_id: str) -> dict | None:
    return next((item for item in state.get("evidence") or [] if item.get("id") == evidence_id), None)


def referenced_evidence_ids(state: dict) -> list[str]:
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
    return list(dict.fromkeys(ids))


def claim_evidence_ids(state: dict) -> list[str]:
    available = {item.get("id") for item in state.get("evidence") or []}
    return [item for item in referenced_evidence_ids(state) if item in available]


def _compact_value(value, limit: int = 2000):
    encoded = json_dumps(value, ensure_ascii=False, default=str)
    return value if len(encoded) <= limit else encoded[:limit] + "…[truncated]"


def _result_overview(result: dict) -> dict:
    overview = {}
    for key, value in result.items():
        if key == "events" and isinstance(value, list):
            overview["events_count"] = len(value)
            overview["events_preview"] = [_compact_value(item) for item in value[:2]]
        else:
            overview[key] = _compact_value(value)
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
        "если preview недостаточно:\n" + json_dumps(items, ensure_ascii=False)
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
        "investigation_scope": state.get("investigation_scope") or {},
        "base_window": context.get("base_window"),
        "focus_window": context.get("focus_window"),
        "items": records,
    }


def _prompt_value(value, *, max_items: int = 50, max_string: int = 10000):
    if isinstance(value, dict):
        return {key: _prompt_value(item, max_items=max_items, max_string=max_string) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_prompt_value(item, max_items=max_items, max_string=max_string) for item in value[:max_items]]
        if len(value) > max_items:
            items.append(f"… ещё {len(value) - max_items} элементов; полные данные в evidence.jsonl")
        return items
    if isinstance(value, str) and len(value) > max_string:
        return value[:max_string] + "…[truncated]"
    return value


def full_evidence_block(state: dict, evidence_ids: Iterable[str] | None = None) -> str:
    bundle = evidence_bundle(state, evidence_ids)
    if not bundle["items"]:
        return "Доказательства: отсутствуют."
    bundle["items"] = [
        {**record, "result": _prompt_value(record.get("result") or {})}
        for record in bundle["items"]
    ]
    title = (
        "Evidence Bundle для LLM-проверки; большие коллекции сокращены, полные данные — в evidence.jsonl"
        if evidence_ids is None else
        "Evidence, связанные с claims/findings/Markdown; большие коллекции сокращены"
    )
    return title + ":\n" + json_dumps(bundle, ensure_ascii=False)


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
            "parent_call_id": item.get("parent_call_id") or "",
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
    reporter_finished = key == "not_checked" and bool(state.get("report_result"))
    result_keys = ("report_result",) if reporter_finished else ("inv_result", "report_result")
    for result_key in result_keys:
        values.extend((state.get(result_key) or {}).get(key) or [])
    if key == "not_checked":
        if reporter_finished:
            for item in state.get("work_items") or []:
                if item.get("status") not in ("failed", "skipped"):
                    continue
                result = item.get("result") or {}
                values.extend(result.get("not_checked") or [])
                values.append(item.get("goal") or item["id"])
        for run in state.get("skill_runs") or []:
            values.extend(run.get(key) or [])
    return list(dict.fromkeys(values))


def investigation_chain(state: dict) -> dict:
    context = state.get("context") or {}
    human_messages = [
        getattr(item, "text", item.content) for item in state.get("messages") or []
        if getattr(item, "type", None) == "human" and getattr(item, "text", item.content)
    ]
    return {
        "investigation_id": context.get("id"),
        "task": state.get("case_task") or (human_messages[-1] if human_messages else ""),
        "work_mode": state.get("work_mode"),
        "investigation_scope": state.get("investigation_scope") or {},
        "current_window": {
            "time_from": context.get("time_from"),
            "time_to": context.get("time_to"),
        },
        "base_window": context.get("base_window"),
        "anchors": list(state.get("anchors") or []),
        "visited_anchors": list(state.get("visited_anchors") or []),
        "investigation_plan": state.get("investigation_plan") or {},
        "work_items": [{
            "id": item.get("id"),
            "goal": item.get("goal"),
            "acceptance": item.get("acceptance") or item.get("goal"),
            "priority": item.get("priority") or "medium",
            "estimated_cost": item.get("estimated_cost") or "medium",
            "depends_on": item.get("depends_on") or [],
            "required": item.get("required", True),
            "status": item.get("status"),
            "attempt": item.get("attempt") or 0,
            "started_at": item.get("started_at") or "",
            "finished_at": item.get("finished_at") or "",
            "requested_window": item.get("requested_window") or {},
            "result": item.get("result") or {},
            "metrics": item.get("metrics") or {},
            "critic": item.get("critic") or {},
        } for item in state.get("work_items") or []],
        "run_metrics": list(state.get("run_metrics") or []),
        "hypotheses": list(state.get("hypotheses") or []),
        "events": case_events(state),
        "limitations": _result_values(state, "limitations"),
        "not_checked": _result_values(state, "not_checked"),
    }


def chain_block(state: dict) -> str:
    return "Детерминированная цепочка расследования:\n" + json_dumps(
        investigation_chain(state), ensure_ascii=False
    )


def evidence_query_block(record: dict) -> str:
    iql = record.get("iql")
    if iql:
        return f"\n\n```iql\n{iql}\n```"
    arguments = json_dumps(record.get("arguments") or {}, ensure_ascii=False)
    return f"\n\n```json\n{arguments}\n```"


def queries_markdown(state: dict) -> str:
    lines = ["# Запросы расследования", ""]
    for index, record in enumerate(state.get("evidence") or [], start=1):
        lines.extend([
            f'<a id="query-{index}"></a>',
            "",
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
        "## Охват текущего этапа",
        "",
    ]
    scope = chain.get("investigation_scope") or {}
    if scope:
        lines.extend([
            f"- Тип: `{scope.get('mode')}`; цель: {scope.get('objective')}",
            f"- Политика данных: {scope.get('sampling_policy')}",
            "- Выполняется сейчас:",
            *(f"  - {item}" for item in scope.get("included") or []),
        ])
        optional = scope.get("optional") or []
        if optional:
            lines.append("- Не выполняется сейчас; варианты следующего этапа:")
            lines.extend(f"  - {item.get('objective')}: {item.get('rationale')}" for item in optional)
    else:
        lines.append("Scope не зафиксирован.")
    lines.extend([
        "",
        "## Ход работы",
        "",
    ])
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
    if chain["work_items"]:
        lines.extend(["", "## План расследования", ""])
        for item in chain["work_items"]:
            lines.append(
                f"- `{item['id']}` — **{item['status']}**, попытка {item['attempt']}: {item['goal']} "
                f"Зависимости: {item['depends_on']}. Окно: `{item['requested_window']}`."
            )
            lines.append(
                f"  Acceptance: {item['acceptance']}; приоритет: {item['priority']}; "
                f"стоимость: {item['estimated_cost']}."
            )
            if item.get("started_at"):
                lines.append(f"  Начато: {item['started_at']}; завершено: {item.get('finished_at') or 'ещё выполняется'}.")
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
    calls = list(state.get("tool_calls") or [])
    if calls:
        lines.extend([
            "",
            "## Аудит всех вызовов",
            "",
            "Полные аргументы и результаты: `tool_calls.jsonl`; SQL и датасеты — в `analysis_results/` и `datasets/`.",
            "",
            "| # | Tool | Статус | Аргументы | Результат | Evidence |",
            "|---|---|---|---|---|---|",
        ])
        for index, call in enumerate(calls, start=1):
            arguments = json_dumps(call.get("arguments_effective") or {}, ensure_ascii=False)
            result = json_dumps(call.get("result"), ensure_ascii=False)
            evidence_link = call.get("evidence_id") or "—"
            if call.get("reused"):
                evidence_link += " (reused)"
            cells = [
                str(index),
                str(call.get("tool") or ""),
                str(call.get("status") or ""),
                arguments[:500] + ("…" if len(arguments) > 500 else ""),
                result[:300] + ("…" if len(result) > 300 else ""),
                evidence_link,
            ]
            lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    return "\n".join(lines)


def with_investigation_chain(markdown: str, state: dict) -> str:
    if not (state.get("evidence") or state.get("findings")):
        return markdown
    if "# Цепочка расследования" in markdown:
        return markdown
    return markdown.rstrip() + "\n\n---\n\n" + chain_markdown(state) + "\n"
