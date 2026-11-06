"""Полная проектная документация для system prompt Chat."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOCUMENTS = (
    "README.md",
    "docs/DEV.md",
    "docs/OBSERVABILITY.md",
    "docs/ROADMAP.md",
    "logicore-mcp/README.md",
    "AGENTS.md",
)


def documentation_block() -> str:
    parts = [
        "Полная актуальная проектная документация. Отвечай по ней предметно, "
        "указывай файл и раздел источника. Не выдумывай отсутствующие возможности; "
        "ROADMAP описывает только невыполненные работы."
    ]
    for path in _DOCUMENTS:
        content = (_ROOT / path).read_text(encoding="utf-8").strip()
        parts.append(f"\n\n# Файл: {path}\n\n{content}")
    return "".join(parts)
