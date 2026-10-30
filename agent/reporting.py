"""Механика отчётов: сборка файла (data-URL) и доставка в callback.

Чистые функции без LangGraph — их можно тестировать в отрыве от графа.
"""

import base64
import datetime
import json
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def slug(value: str, fallback: str) -> str:
    """Безопасное имя файла: только A-Za-z0-9._-."""
    segment = _SAFE_SEGMENT.sub("-", value.strip()).strip("-.")
    if not any(c.isascii() and c.isalpha() for c in segment):
        return fallback
    return segment or fallback


def build_report(title: str, markdown: str, filename: str | None = None) -> tuple[str, str, int]:
    """Собирает (имя файла, data-URL отчёта, размер в байтах)."""
    name = slug(filename, "report") if filename else slug(title, "report")
    if not name.lower().endswith(".md"):
        name += ".md"
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{stamp}-{name}"
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    body = (
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"saved_at: {now}\n"
        "---\n\n" + markdown.rstrip() + "\n"
    )
    data = body.encode("utf-8")
    url = f"data:text/markdown;base64,{base64.b64encode(data).decode('ascii')}#{fname}"
    return fname, url, len(data)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def post_report(target: str, payload: dict) -> httpx.Response:
    """POST с ретраями: 3 попытки, экспоненциальная задержка 1с → 2с → 4с."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.post(target, json=payload)
