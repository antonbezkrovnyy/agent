"""Публичный статус текущего run и быстрые follow-up действия."""

TOOL_LIMITS = {"scout": 10, "investigator": 30, "reporter": 60}


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
    if state.get("evidence"):
        suggestions.append({
            "label": "Продолжить расследование",
            "value": "Продолжи расследование и углуби анализ",
        })
    return suggestions
