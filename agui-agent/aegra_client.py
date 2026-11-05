"""Минимальный асинхронный клиент Aegra Agent Protocol."""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

AEGRA_API_URL = os.environ.get("AEGRA_API_URL", "http://aegra:2026").rstrip("/")
AEGRA_ASSISTANT = os.environ.get("AEGRA_ASSISTANT", "agent")
AGUI_USER_ID = os.environ.get("AGUI_USER_ID", "local-sandbox")


@dataclass
class SSEEvent:
    event: str
    data: Any
    event_id: str = ""


class AegraClient:
    def __init__(self, base_url: str = AEGRA_API_URL):
        self.base_url = base_url.rstrip("/")

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.RequestError as exc:
            raise RuntimeError(f"Aegra недоступна: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Aegra {method} {path}: HTTP {response.status_code}: {response.text[:800]}")
        if not response.content:
            return {}
        return response.json()

    async def ensure_thread(self, thread_id: str) -> dict:
        return await self.request("POST", "/threads", json={
            "threadId": thread_id,
            "ifExists": "do_nothing",
            "metadata": {"kind": "agui-copilot"},
        })

    async def state(self, thread_id: str, checkpoint_id: str = "") -> dict:
        suffix = f"/{checkpoint_id}" if checkpoint_id else ""
        return await self.request("GET", f"/threads/{thread_id}/state{suffix}")

    async def history(self, thread_id: str, limit: int = 100) -> list[dict]:
        return await self.request("POST", f"/threads/{thread_id}/history", json={
            "limit": limit,
            "subgraphs": True,
        })

    async def runs(self, thread_id: str) -> list[dict]:
        payload = await self.request("GET", f"/threads/{thread_id}/runs")
        return payload if isinstance(payload, list) else payload.get("runs") or []

    async def cancel(self, thread_id: str, run_id: str) -> dict:
        return await self.request("POST", f"/threads/{thread_id}/runs/{run_id}/cancel", json={"action": "interrupt"})

    async def graph(self) -> dict:
        assistants = await self.request("POST", "/assistants/search", json={})
        assistant = next(
            (item for item in assistants if item.get("graph_id") == AEGRA_ASSISTANT or item.get("assistant_id") == AEGRA_ASSISTANT),
            None,
        )
        if not assistant:
            raise RuntimeError(f"Assistant {AEGRA_ASSISTANT} не найден в Aegra")
        assistant_id = assistant["assistant_id"]
        graph, subgraphs = await self.request("GET", f"/assistants/{assistant_id}/graph"), await self.request("GET", f"/assistants/{assistant_id}/subgraphs")
        return {"assistant_id": assistant_id, "graph": graph, "subgraphs": subgraphs}

    async def stream_run(
        self,
        thread_id: str,
        messages: list[dict],
        *,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        payload = {
            "assistant_id": AEGRA_ASSISTANT,
            "input": {"messages": messages},
            "config": {"configurable": {"user_id": AGUI_USER_ID}},
            "stream_mode": ["values", "updates", "custom"],
            "stream_subgraphs": True,
            "durability": "sync",
            "multitask_strategy": "reject",
            "on_disconnect": "cancel",
            "interrupt_before": interrupt_before or [],
            "interrupt_after": interrupt_after or [],
        }
        async for event in self._stream("POST", f"/threads/{thread_id}/runs/stream", json=payload):
            yield event

    async def attach_run(self, thread_id: str, run_id: str, last_event_id: str = "") -> AsyncIterator[SSEEvent]:
        headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
        async for event in self._stream(
            "GET",
            f"/threads/{thread_id}/runs/{run_id}/stream",
            headers=headers,
        ):
            yield event

    async def resume_run(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        payload = {
            "assistant_id": AEGRA_ASSISTANT,
            "input": None,
            "checkpoint_id": checkpoint_id,
            "command": None if checkpoint_id else {"resume": True},
            "config": {"configurable": {"user_id": AGUI_USER_ID}},
            "stream_mode": ["values", "updates", "custom"],
            "stream_subgraphs": True,
            "durability": "sync",
            "multitask_strategy": "reject",
            "on_disconnect": "cancel",
            "interrupt_before": interrupt_before or [],
            "interrupt_after": interrupt_after or [],
        }
        async for event in self._stream("POST", f"/threads/{thread_id}/runs/stream", json=payload):
            yield event

    async def _stream(self, method: str, path: str, **kwargs) -> AsyncIterator[SSEEvent]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(method, f"{self.base_url}{path}", **kwargs) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise RuntimeError(
                            f"Aegra {method} {path}: HTTP {response.status_code}: {body.decode(errors='replace')[:800]}"
                        )
                    event_name = "message"
                    event_id = ""
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if not line:
                            if data_lines:
                                raw = "\n".join(data_lines)
                                try:
                                    data = json.loads(raw)
                                except json.JSONDecodeError:
                                    data = raw
                                yield SSEEvent(event=event_name, data=data, event_id=event_id)
                            event_name = "message"
                            event_id = ""
                            data_lines = []
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("id:"):
                            event_id = line[3:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
        except httpx.RequestError as exc:
            raise RuntimeError(f"Поток Aegra прерван: {exc}") from exc
