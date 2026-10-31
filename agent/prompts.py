"""Сборка системных промптов: тексты из prompts/*.md + динамические блоки.

Промпты — данные, не код: инструкции лежат в отдельных файлах,
сюда подмешиваются только данные (отчёт, подсказки, замечания критика).
"""

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).with_name("prompts")

REPORT_INJECT_LIMIT = 8000
EVIDENCE_INJECT_LIMIT = 20000


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
        "Зафиксированный контекст расследования:\n"
        f"id: {context.get('id')}\n"
        f"time_from: {context['time_from']}\n"
        f"time_to: {context['time_to']}\n"
        "Все запросы выполняются только в этих границах. Не пересчитывай now и не меняй окно."
    )


def evidence_block(state: dict) -> str:
    records = state.get("evidence") or []
    if not records:
        return "Доказательства: пока отсутствуют."
    compact = []
    for record in records:
        item = {key: value for key, value in record.items() if key != "result"}
        result = dict(record.get("result") or {})
        events = result.get("events")
        if isinstance(events, list) and len(events) > 3:
            result["events"] = events[:3]
            result["events_in_prompt"] = 3
            result["events_total_in_result"] = len(events)
        item["result"] = result
        compact.append(item)
    text = json.dumps(compact, ensure_ascii=False)
    if len(text) > EVIDENCE_INJECT_LIMIT:
        text = text[:EVIDENCE_INJECT_LIMIT] + "\n[Доказательства обрезаны]"
    return "Фактические результаты инструментов (Evidence):\n" + text


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
    return (
        "Состояние диалога: "
        f"последний отчёт — {'есть' if has_report else 'нет'}, "
        f"записей расследования — {len(findings)}."
    )


def critic_feedback_block(state: dict) -> str:
    """Замечания критика, если агента уже отправляли на доработку."""
    critic = state.get("critic") or {}
    if state.get("rework", 0) > 0 and critic.get("feedback"):
        return f"Критик вернул работу на доработку. Замечания: {critic['feedback']}"
    return ""
