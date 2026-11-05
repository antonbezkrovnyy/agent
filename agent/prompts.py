"""Сборка системных промптов: тексты из prompts/*.md + динамические блоки.

Промпты — данные, не код: инструкции лежат в отдельных файлах,
сюда подмешиваются только данные (отчёт, подсказки, замечания критика).
"""

from pathlib import Path

from config import Settings
from contracts import json_dumps
from evidence import evidence_overview_block, full_evidence_block

PROMPTS_DIR = Path(__file__).with_name("prompts")

settings = Settings()
REPORT_INJECT_LIMIT = settings.report_inject_limit


def load(name: str) -> str:
    """Текст промпта по имени (без расширения .md)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def compose(*names: str) -> str:
    """Склейка нескольких промптов в один текст."""
    return "\n\n".join(load(n) for n in names)


def system_prompt(*parts: str) -> str:
    """Финальный системный промпт: конкатенация непустых частей."""
    return "\n\n".join(part for part in parts if part)


def scout_block(state: dict) -> str:
    """Быстрая проверка скаута — контекст для следующих агентов."""
    note = (state.get("scout_note") or "").strip()
    if not note:
        return ""
    return f"Быстрая проверка скаута: {note}"


def context_block(state: dict) -> str:
    context = state.get("context") or {}
    if not context.get("time_from") or not context.get("time_to"):
        return ""
    return (
        "Контекст расследования:\n"
        f"id: {context.get('id')}\n"
        f"активное окно: {context['time_from']} — {context['time_to']}\n"
        f"base_window: {context.get('base_window') or {}}\n"
        f"focus_window: {context.get('focus_window')}\n"
        "По умолчанию используй активное окно. Для deep-анализа меняй его через "
        "update_window; явные границы обычного запроса не перезаписываются."
    )


def scope_block(state: dict) -> str:
    scope = state.get("investigation_scope") or {}
    if not scope.get("objective"):
        return ""
    return structured_block(
        "InvestigationScope: границы текущего этапа. Optional сейчас не выполняется",
        scope,
    )


def work_item_block(state: dict) -> str:
    item = state.get("active_work_item") or {}
    if not item.get("id"):
        return ""
    return (
        "Текущий work item. Выполни только его цель, не расширяй работу до всего расследования. "
        "requested_window имеет приоритет над общим активным окном. Заверши structured response "
        "с claims и handoff именно для этого item. Acceptance — критерий остановки, а не список "
        "всех возможных проверок. Replan разрешён только при acceptance_failed, conclusion_invalid "
        "или missing_critical_evidence; новую самостоятельную аномалию не разбирай в этом item:\n"
        + json_dumps({
            key: value for key, value in item.items()
            if key in ("id", "goal", "acceptance", "priority", "estimated_cost", "depends_on", "required", "requested_window", "attempt")
        }, ensure_ascii=False)
    )


def work_items_block(state: dict) -> str:
    items = state.get("work_items") or []
    completed = [item for item in items if item.get("status") == "done" and item.get("result")]
    if not completed:
        return ""
    handoff = [{
        "id": item["id"],
        "goal": item.get("goal"),
        "acceptance": item.get("acceptance") or item.get("goal"),
        "priority": item.get("priority") or "medium",
        "estimated_cost": item.get("estimated_cost") or "medium",
        "required": item.get("required", True),
        "result": item["result"],
        "critic": item.get("critic") or {},
    } for item in completed]
    return "Компактные handoff завершённых work items. Используй их вместо повторного анализа всей истории:\n" + json_dumps(
        handoff, ensure_ascii=False
    )


def skill_block(state: dict) -> str:
    skill = state.get("active_skill") or {}
    if not skill.get("id"):
        return ""
    return (
        "Активный версионируемый skill. Следуй strategy и stop_rules, используй "
        "только allowed_tools и верни structured skill_run с теми же id/version:\n"
        + json_dumps(skill, ensure_ascii=False)
    )


def evidence_block(state: dict, *, full: bool = False, evidence_ids=None) -> str:
    if full:
        return full_evidence_block(state, evidence_ids)
    return evidence_overview_block(state)


def structured_block(title: str, value: dict | None) -> str:
    if not value:
        return ""
    return f"{title}:\n{json_dumps(value, ensure_ascii=False, default=lambda value: value.isoformat() if hasattr(value, 'isoformat') else str(value))}"


def inv_summary_block(inv_summary: str) -> str:
    """Резюме инвестигейтора — для репортёра и критика."""
    return f"Резюме инвестигейтора:\n{inv_summary.strip()}"


def last_report_block(last_report: dict) -> str:
    """Блок «Последний созданный отчёт» для промпта репортёра."""
    markdown = str(last_report.get("markdown") or "")
    if len(markdown) > REPORT_INJECT_LIMIT:
        markdown = (
            markdown[:REPORT_INJECT_LIMIT]
            + "\n\n[Отчёт обрезан: полный текст — в аргументах save_report/send_report в истории]"
        )
    filename = last_report.get("filename") or "report"
    return f"Последний созданный отчёт ({filename}):\n```markdown\n{markdown}\n```"


def intent_hint_block(state: dict) -> str:
    """Подсказка классификатору: что уже есть в стейте."""
    has_report = bool(state.get("last_report"))
    findings = state.get("findings") or []
    text = (
        "Состояние диалога: "
        f"последний отчёт — {'есть' if has_report else 'нет'}, "
        f"записей расследования — {len(findings)}."
    )
    candidates = state.get("memory_candidates") or []
    if candidates:
        text += "\nПрошлые случаи из долговременной памяти (memory_candidates):\n" + json_dumps(
            candidates, ensure_ascii=False
        )
    if state.get("memory_error"):
        text += "\n" + state["memory_error"]
    return text


def memory_block(state: dict) -> str:
    parts = []
    if state.get("memory_loaded"):
        parts.append(
            f"Из Case Store загружено дело {state['memory_loaded']}. "
            "Прежде чем использовать старый ответ, выполни контрольный запрос к LogiCore "
            "и проверь ключевой вывод на фактических данных."
        )
    facts = state.get("context_memories") or []
    if facts:
        parts.append(
            "Релевантная Context Memory. Это организационный контекст и гипотезы для проверки, "
            "а не свежие Evidence текущего расследования. При влиянии на вывод перепроверь через LogiCore. "
            "Для поиска по новым сущностям используй search_context_memory:\n"
            + json_dumps(facts, ensure_ascii=False)
        )
    cases = state.get("similar_cases") or []
    if cases:
        parts.append(
            "Похожие прошлые дела. Это компактные карточки для построения гипотез, "
            "а не continuation и не Evidence текущего дела. Не загружай их state; "
            "проверяй выводы свежими запросами. При новых сущностях используй search_similar_cases:\n"
            + json_dumps(cases, ensure_ascii=False)
        )
    if state.get("memory_error"):
        parts.append(state["memory_error"] + " Текущая работа продолжается в state треда.")
    return "\n\n".join(parts)


def critic_feedback_block(state: dict) -> str:
    """Замечания критика, если агента уже отправляли на доработку."""
    critic = state.get("critic") or {}
    if state.get("rework", 0) > 0 and critic.get("feedback"):
        return f"Критик вернул работу на доработку. Замечания: {critic['feedback']}"
    return ""
