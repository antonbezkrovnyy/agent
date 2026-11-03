"""Сборка системных промптов: тексты из prompts/*.md + динамические блоки.

Промпты — данные, не код: инструкции лежат в отдельных файлах,
сюда подмешиваются только данные (отчёт, подсказки, замечания критика).
"""

import json
from pathlib import Path

from config import Settings
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


def skill_block(state: dict) -> str:
    skill = state.get("active_skill") or {}
    if not skill.get("id"):
        return ""
    return (
        "Активный версионируемый skill. Следуй strategy и stop_rules, используй "
        "только allowed_tools и верни structured skill_run с теми же id/version:\n"
        + json.dumps(skill, ensure_ascii=False)
    )


def evidence_block(state: dict, *, full: bool = False, evidence_ids=None) -> str:
    if full:
        return full_evidence_block(state, evidence_ids)
    return evidence_overview_block(state)


def structured_block(title: str, value: dict | None) -> str:
    if not value:
        return ""
    return f"{title}:\n{json.dumps(value, ensure_ascii=False)}"


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
        text += "\nПрошлые случаи из долговременной памяти (memory_candidates):\n" + json.dumps(
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
            + json.dumps(facts, ensure_ascii=False)
        )
    cases = state.get("similar_cases") or []
    if cases:
        parts.append(
            "Похожие прошлые дела. Это компактные карточки для построения гипотез, "
            "а не continuation и не Evidence текущего дела. Не загружай их state; "
            "проверяй выводы свежими запросами. При новых сущностях используй search_similar_cases:\n"
            + json.dumps(cases, ensure_ascii=False)
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
