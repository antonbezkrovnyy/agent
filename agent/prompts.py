"""Сборка системных промптов: тексты из prompts/*.md + динамические блоки.

Промпты — данные, не код: инструкции лежат в отдельных файлах,
сюда подмешиваются только данные (отчёт, подсказки, замечания критика).
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).with_name("prompts")

# Сколько символов отчёта подмешивать в промпт
REPORT_INJECT_LIMIT = 8000


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
