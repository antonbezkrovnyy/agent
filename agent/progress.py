"""Публичный статус текущего run и быстрые follow-up действия."""

from config import Settings

settings = Settings()
TOOL_LIMITS = {
    "scout": settings.agent_tool_limit_scout,
    "investigator": settings.agent_tool_limit_investigator,
    "reporter": settings.agent_tool_limit_reporter,
}


def simple_progress(stage: str, text: str, state: str = "running") -> dict:
    return {"stage": stage, "state": state, "text": text}


def agent_progress(state: dict, stage: str, text: str, agent: str, status: str = "running") -> dict:
    limit = TOOL_LIMITS[agent]
    calls = (state.get("tool_usage") or {}).get(agent, 0)
    return {
        "stage": stage,
        "state": status,
        "text": text,
        "agent": agent,
        "tool_calls": min(calls, limit),
        "tool_limit": limit,
    }


def done_progress(text: str = "Готово") -> dict:
    return simple_progress("done", text, "done")


def report_suggestions(state: dict, *, exported: bool = False, has_report: bool = False) -> list[dict]:
    suggestions = []
    scope = state.get("investigation_scope") or {}
    for direction in (scope.get("optional") or [])[:3]:
        objective = str(direction.get("objective") or "").strip()
        if objective:
            suggestions.append({"label": objective[:80], "value": f"Продолжи текущее расследование: {objective}"})
    if state.get("evidence") and not exported:
        suggestions.append({
            "label": "Показать всё таблицей",
            "value": "Пришли все события таблицей",
        })
    if state.get("evidence") and not has_report:
        suggestions.append({
            "label": "Сохранить отчёт",
            "value": "Сохранить отчёт расследования файлом",
        })
    if state.get("evidence") and not scope.get("optional"):
        suggestions.append({
            "label": "Продолжить расследование",
            "value": "Продолжи расследование коротким следующим шагом",
        })
    return suggestions
