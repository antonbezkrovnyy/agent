import asyncio
import itertools
import json
import os
import sys
import types

import pytest

os.environ.setdefault("LLM_BASE_URL", "http://localhost:9/v1")
os.environ.setdefault("LLM_TOKEN", "test-token")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver


def json_blocks(payload):
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


@tool
def server_time() -> list[dict]:
    """Текущее время сервера."""
    return json_blocks({"ok": True, "now": "2026-10-30T12:00:00Z"})


@tool
def search_events(query: str = "", **kwargs) -> list[dict]:
    """Поиск событий в журнале."""
    return json_blocks({
        "ok": True,
        "window": {"from": kwargs.get("time_from"), "to": kwargs.get("time_to"), "source": "explicit"},
        "events": [{"uuid": "event-42", "message": "скачок CPU"}],
    })


@tool
def count_events(window: str = "", **kwargs) -> list[dict]:
    """Количество событий за окно."""
    return json_blocks({
        "ok": True,
        "count": 17,
        "window": {"from": kwargs.get("time_from"), "to": kwargs.get("time_to"), "source": "explicit"},
    })


@tool
def broken_tool(time_from: str, time_to: str | None = None) -> str:
    """Ошибка для проверки recovery."""
    raise ValueError("invalid test arguments")


_mcp_stub = types.ModuleType("mcp_client")
_mcp_stub.get_mcp_tools = lambda: [server_time, search_events, count_events, broken_tool]
sys.modules["mcp_client"] = _mcp_stub

import agents as agents_mod
import graph as graph_mod
import llm as llm_mod
from llm import CriticEvidence, CriticReport, IntentDecision


_ids = itertools.count(1)
ROLES = ("Ты — скаут", "Ты — инвестигейтор", "Ты — репортёр", "Ты — чат-агент")


def ai(content=None, calls=None):
    return AIMessage(content=content or "", tool_calls=calls or [])


def tc(name, args):
    return {"name": name, "args": args, "id": f"call_{next(_ids)}"}


class FakeAgent(FakeMessagesListChatModel):
    by_role: dict = {}
    calls: int = 0
    systems: list = []
    convos: list = []

    def __init__(self, by_role):
        super().__init__(responses=[AIMessage(content="")])
        self.by_role = {role: list(queue) for role, queue in by_role.items()}
        self.calls = 0
        self.systems = []
        self.convos = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, system):
        for role in ROLES:
            if role not in system:
                continue
            if not self.by_role[role]:
                break
            message = self.by_role[role].pop(0)
            if not message.content:
                return message
            if role == "Ты — скаут":
                return ai(calls=[tc("ScoutResult", {
                    "note": message.content, "signals": [], "evidence_ids": [],
                })])
            if role == "Ты — инвестигейтор":
                return ai(calls=[tc("InvestigationResult", {
                    "summary": message.content, "claims": [], "limitations": [],
                })])
            if role == "Ты — репортёр":
                return ai(calls=[tc("ReporterResult", {
                    "answer": message.content, "claims": [], "limitations": [],
                })])
            return message
        raise AssertionError(f"скрипт исчерпан: {system[:100]!r}")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self.systems.append(messages[0].content)
        self.convos.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._next(messages[0].content))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class FakeStructured:
    def __init__(self, result=None, results=None, error=None):
        self.result = result
        self.results = list(results) if results else []
        self.error = error
        self.calls = 0
        self.systems = []

    def invoke(self, messages):
        self.calls += 1
        self.systems.append(messages[0].content)
        if self.error:
            raise self.error
        return self.results.pop(0) if self.results else self.result


def intent(label="complex", new=True, edit=False):
    return IntentDecision(
        intent=label,
        rationale="тест",
        needs_new_data=new,
        edit_existing_report=edit,
    )


def critics():
    return (
        FakeStructured(CriticEvidence(verdict="ok", feedback="")),
        FakeStructured(CriticReport(verdict="ok", feedback="")),
    )


@pytest.fixture
def patch_llm(monkeypatch):
    def apply(agent, intent_llm, critic_evidence=None, critic_report=None):
        default_evidence, default_report = critics()
        monkeypatch.setattr(llm_mod, "llm", agent)
        monkeypatch.setattr(llm_mod, "intent_llm", intent_llm)
        monkeypatch.setattr(llm_mod, "critic_evidence_llm", critic_evidence or default_evidence)
        monkeypatch.setattr(llm_mod, "critic_report_llm", critic_report or default_report)
        agents_mod.build_agents()
    return apply


def invoke(graph, text, thread_id="test", **extra):
    return asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content=text)], **extra},
        {"configurable": {"thread_id": thread_id}},
    ))


def build_graph():
    return graph_mod.build_graph(InMemorySaver())


def texts(result):
    return [m.content for m in result["messages"]]


def assert_clean(result):
    for message in result["messages"]:
        assert isinstance(message, HumanMessage) or (
            isinstance(message, AIMessage) and message.name in ("chat", "reporter")
        )
        assert not getattr(message, "tool_calls", None)
