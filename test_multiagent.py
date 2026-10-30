"""Локальные тесты многоагентного графа: фейки LLM и MCP, реальный ToolNode.

Покрыто:
1. simple → скаут (параллельно) + чат-агент, без тулз и отчётов
2. complex → инвестигейтор (log_finding ×2) → критик улик → репортёр
   (send_report в реальный локальный callback) → критик отчёта
3. continuation с last_report → репортёр правит отчёт, findings живут
4. критик отчёта возвращает на доработку (лимит 1 итерация)
5. критик выключен — инвестигейтор сразу в репортёр
6. сбой структурированного интента → JSON-fallback на обычной модели

Фейк LLM возвращает ответы по ролям (system-промпт), а не по позиции в
очереди — параллельные ветки (intent/scout) не гоняются за скриптом.

Запуск из my-agent-stack/:
uv run --with "langchain==1.3.10" --with "langgraph==1.2.16" --with "langchain-openai" \
  --with "pydantic-settings" --with "httpx" --with "tenacity" python test_multiagent.py
"""

import asyncio
import http.server
import itertools
import json
import os
import sys
import threading
import types

os.environ.setdefault("LLM_BASE_URL", "http://localhost:9/v1")
os.environ.setdefault("LLM_TOKEN", "test-token")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent"))

from langchain_core.tools import tool as lc_tool


@lc_tool
def server_time() -> str:
    """Текущее время сервера."""
    return "2026-10-30T12:00:00Z"


@lc_tool
def search_events(query: str) -> str:
    """Поиск событий в журнале."""
    return "event-42: скачок CPU на host-1 в 11:58"


@lc_tool
def count_events(window: str) -> str:
    """Количество событий за окно."""
    return "17 событий"


_mcp_stub = types.ModuleType("mcp_client")
_mcp_stub.get_mcp_tools = lambda: [server_time, search_events, count_events]
sys.modules["mcp_client"] = _mcp_stub

import graph as graph_mod
import llm as llm_mod
from llm import CriticEvidence, CriticReport, IntentDecision
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver


# ── Фейки ────────────────────────────────────────────────────────

_id = itertools.count(1)

# Роль-маркеры в system-промптах (проверка в этом порядке)
ROLES = ("Ты — скаут", "Ты — инвестигейтор", "Ты — репортёр", "Ты — чат-агент", "классификатор интента")


def AI(content=None, calls=None):
    if calls:
        return AIMessage(content="", tool_calls=calls)
    return AIMessage(content=content or "")


def tc(name, args):
    return {"name": name, "args": args, "id": f"call_{next(_id)}"}


class FakeAgent(FakeMessagesListChatModel):
    """Скриптованная LLM: очередь ответов на роль (system-промпт)."""

    by_role: dict = {}
    calls: int = 0
    systems: list = []

    def __init__(self, by_role: dict):
        super().__init__(responses=[AIMessage(content="")])
        self.by_role = {role: list(queue) for role, queue in by_role.items()}
        self.calls = 0
        self.systems = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, system: str) -> AIMessage:
        for role in ROLES:
            if role in system:
                if self.by_role[role]:
                    return self.by_role[role].pop(0)
                break
        raise AssertionError(f"скрипт исчерпан для system: {system[:80]!r}")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self.systems.append(messages[0].content)
        return ChatResult(generations=[ChatGeneration(message=self._next(messages[0].content))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class FakeStructured:
    """Фейк с with_structured_output: возвращает готовый объект схемы."""

    def __init__(self, result=None, results=None, error=None):
        self.result = result
        self.results = list(results) if results else []
        self.error = error
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results.pop(0) if self.results else self.result


def patch(agent_llm, intent, crit_ev, crit_rep):
    llm_mod.llm = agent_llm
    llm_mod.intent_llm = intent
    llm_mod.critic_evidence_llm = crit_ev
    llm_mod.critic_report_llm = crit_rep


def contents(result):
    return [m.content if isinstance(m.content, str) else "" for m in result["messages"]]


# ── Callback-сервер для send_report ──────────────────────────────

POSTS: list = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        POSTS.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


_srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
CB = f"http://127.0.0.1:{_srv.server_address[1]}/callback"


# ── Сценарии ─────────────────────────────────────────────────────

async def scenario_1_simple():
    agent = FakeAgent({
        "Ты — скаут": [
            AI(calls=[tc("server_time", {})]),
            AI("Скаут: время 12:00, аномалий нет."),
        ],
        "Ты — чат-агент": [
            AI("Привет! Я аналитик Логикора."),
        ],
    })
    intent = FakeStructured(IntentDecision(intent="simple", rationale="приветствие"))
    crit_ev = FakeStructured(CriticEvidence(verdict="ok", feedback=""))
    crit_rep = FakeStructured(CriticReport(verdict="ok", feedback=""))
    patch(agent, intent, crit_ev, crit_rep)

    g = graph_mod.build_graph(InMemorySaver())
    result = await g.ainvoke(
        {"messages": [HumanMessage(content="Привет, что ты умеешь?")]},
        {"configurable": {"thread_id": "t-simple"}},
    )

    assert intent.calls == 1
    assert agent.calls == 3, f"ожидается 3 LLM-вызова (скаут×2, чат), было {agent.calls}"
    assert crit_ev.calls == 0 and crit_rep.calls == 0, "критик не должен вызываться на simple"
    assert result["intent"]["label"] == "simple"
    assert result["artifacts"] == []

    texts = contents(result)
    assert any("Скаут:" in t for t in texts), "ответ скаута не в диалоге"
    assert "Привет! Я аналитик Логикора." in texts
    assert texts.index("Скаут: время 12:00, аномалий нет.") < texts.index("Привет! Я аналитик Логикора."), \
        "скаут должен завершиться до чат-агента (барьер dispatch)"
    assert isinstance(result["messages"][2], ToolMessage), "tool-ответ server_time не в диалоге"
    assert not any("save_report" in t or "send_report" in t for t in texts)

    assert "Ты — чат-агент" in agent.systems[-1]
    assert any("Ты — скаут" in s for s in agent.systems)


async def scenario_2_3_report_and_revision():
    # run 1 — complex: скаут → инвестигейтор → критик → репортёр (send_report)
    agent1 = FakeAgent({
        "Ты — скаут": [
            AI(calls=[tc("search_events", {"query": "host-1 CPU"})]),
            AI("Скаут: вижу скачок CPU на host-1 в 11:58."),
        ],
        "Ты — инвестигейтор": [
            AI(calls=[tc("search_events", {"query": "host-1 11:50-12:00"})]),
            AI(calls=[tc("log_finding", {"finding": "Скачок CPU на host-1 в 11:58", "next_action": "считаю события"})]),
            AI(calls=[tc("count_events", {"window": "1h"})]),
            AI(calls=[tc("log_finding", {"finding": "17 событий за час, пик в 11:58", "next_action": "делаю вывод"})]),
            AI("Резюме: скачок CPU на host-1 в 11:58, 17 событий за час."),
        ],
        "Ты — репортёр": [
            AI(calls=[tc("send_report", {"title": "Расследование host-1",
                                        "markdown": "# Расследование host-1\nСкачок CPU в 11:58.",
                                        "callback_url": CB})]),
            AI("Отчёт доставлен."),
        ],
    })
    intent1 = FakeStructured(IntentDecision(intent="complex", rationale="расследование аномалии"))
    crit_ev1 = FakeStructured(CriticEvidence(verdict="ok", feedback=""))
    crit_rep1 = FakeStructured(CriticReport(verdict="ok", feedback=""))
    patch(agent1, intent1, crit_ev1, crit_rep1)

    g = graph_mod.build_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-inv"}}
    result1 = await g.ainvoke(
        {"messages": [HumanMessage(content="Расследуй аномалию CPU на host-1")], "callback_url": CB},
        cfg,
    )

    assert agent1.calls == 9, f"ожидается 9 LLM-вызовов, было {agent1.calls}"
    assert crit_ev1.calls == 1 and crit_rep1.calls == 1
    assert len(result1["findings"]) == 2, f"findings: {result1['findings']}"
    assert len(result1["artifacts"]) == 1 and result1["artifacts"][0].startswith("data:text/markdown")
    assert result1["last_report"]["title"] == "Расследование host-1"

    sent = [m for m in result1["messages"] if isinstance(m, ToolMessage) and m.name == "send_report"]
    assert sent and '"ok": true' in sent[0].content, f"send_report: {sent and sent[0].content}"

    assert len(POSTS) == 1, f"callback должен получить ровно 1 POST, было {len(POSTS)}"
    assert POSTS[0]["event"] == "investigation_report"
    assert POSTS[0]["thread_id"] == "t-inv"

    final = result1["messages"][-1]
    assert getattr(final, "artifacts", None) and len(final.artifacts) == 1, \
        "artifacts не переехали на финальное сообщение"

    # run 2 — continuation: правка отчёта тем же тредом
    agent2 = FakeAgent({
        "Ты — скаут": [
            AI(calls=[tc("count_events", {"window": "1h"})]),
            AI("Скаут: событий по-прежнему 17."),
        ],
        "Ты — репортёр": [
            AI(calls=[tc("save_report", {"title": "host-1 (коротко)",
                                        "markdown": "# Вывод\nСкачок CPU в 11:58."})]),
            AI("Готово, короткий отчёт сохранён."),
        ],
    })
    intent2 = FakeStructured(IntentDecision(intent="continuation", rationale="правка отчёта"))
    crit_rep2 = FakeStructured(CriticReport(verdict="ok", feedback=""))
    patch(agent2, intent2, crit_ev1, crit_rep2)

    result2 = await g.ainvoke(
        {"messages": [HumanMessage(content="Сделай отчёт короче, только выводы")]},
        cfg,
    )

    assert agent2.calls == 4, f"ожидается 4 LLM-вызова (скаут×2, репортёр×2), было {agent2.calls}"
    assert not any("Ты — инвестигейтор" in s for s in agent2.systems), \
        "правки должен делать репортёр, не инвестигейтор"
    assert any("Последний созданный отчёт (" in s for s in agent2.systems), \
        "last_report не подмешан в промпт репортёра"
    assert result2["last_report"]["markdown"] == "# Вывод\nСкачок CPU в 11:58."
    assert len(result2["artifacts"]) == 1, "artifacts нового run'а должны быть только от save_report"
    assert len(result2["findings"]) == 2, "findings прошлого run'а должны жить весь тред"


async def scenario_4_rework():
    agent = FakeAgent({
        "Ты — скаут": [
            AI(calls=[tc("count_events", {"window": "1h"})]),
            AI("Скаут: 17 событий."),
        ],
        "Ты — инвестигейтор": [
            AI(calls=[tc("search_events", {"query": "host-1"})]),
            AI(calls=[tc("log_finding", {"finding": "Скачок CPU в 11:58", "next_action": "делаю вывод"})]),
            AI("Резюме: скачок CPU в 11:58."),
        ],
        "Ты — репортёр": [
            AI(calls=[tc("save_report", {"title": "host-1", "markdown": "# Отчёт\nСкачок CPU."})]),
            AI("Отчёт готов."),
            AI(calls=[tc("save_report", {"title": "host-1", "markdown": "# Отчёт\nСкачок CPU.\nВывод: проблема в host-1."})]),
            AI("Отчёт доработан."),
        ],
    })
    intent = FakeStructured(IntentDecision(intent="complex", rationale="аномалия"))
    crit_ev = FakeStructured(CriticEvidence(verdict="ok", feedback=""))
    crit_rep = FakeStructured(results=[
        CriticReport(verdict="rework_report", feedback="Добавьте в отчёт итоговый вывод"),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch(agent, intent, crit_ev, crit_rep)

    g = graph_mod.build_graph(InMemorySaver())
    result = await g.ainvoke(
        {"messages": [HumanMessage(content="Расследуй аномалию на host-1")]},
        {"configurable": {"thread_id": "t-rework"}},
    )

    assert agent.calls == 9, f"ожидается 9 LLM-вызовов, было {agent.calls}"
    assert crit_rep.calls == 2, "критик отчёта должен сработать дважды (rework → ok)"
    assert result["rework"] == 1, f"rework: {result['rework']}"
    assert len(result["artifacts"]) == 2, "две версии отчёта (оригинал + доработка)"
    assert any("Критик вернул работу на доработку" in s for s in agent.systems), \
        "замечания критика не подмешаны в промпт при доработке"


async def scenario_5_critic_disabled():
    agent = FakeAgent({
        "Ты — скаут": [
            AI(calls=[tc("count_events", {"window": "1h"})]),
            AI("Скаут: 17 событий."),
        ],
        "Ты — инвестигейтор": [
            AI("Резюме: всё в норме."),
        ],
        "Ты — репортёр": [
            AI(calls=[tc("save_report", {"title": "host-1", "markdown": "# Отчёт\nВсё в норме."})]),
            AI("Готово."),
        ],
    })
    intent = FakeStructured(IntentDecision(intent="complex", rationale="проверка данных"))
    crit_ev = FakeStructured(CriticEvidence(verdict="ok", feedback=""))
    crit_rep = FakeStructured(CriticReport(verdict="ok", feedback=""))
    patch(agent, intent, crit_ev, crit_rep)

    graph_mod.settings.critic_enabled = False
    try:
        g = graph_mod.build_graph(InMemorySaver())
        result = await g.ainvoke(
            {"messages": [HumanMessage(content="Проверь события за час")]},
            {"configurable": {"thread_id": "t-nocrit"}},
        )
    finally:
        graph_mod.settings.critic_enabled = True

    assert agent.calls == 5, f"ожидается 5 LLM-вызовов, было {agent.calls}"
    assert crit_ev.calls == 0 and crit_rep.calls == 0, "критик выключен, но вызывался"
    assert len(result["artifacts"]) == 1
    assert result["critic"] == {}, "критик не должен писать в стейт"


async def scenario_6_intent_fallback():
    agent = FakeAgent({
        "классификатор интента": [
            AI('{"intent": "complex", "rationale": "аномалия в вопросе"}'),
        ],
        "Ты — скаут": [
            AI(calls=[tc("count_events", {"window": "1h"})]),
            AI("Скаут: 17 событий."),
        ],
        "Ты — инвестигейтор": [
            AI("Резюме: 17 событий за час."),
        ],
        "Ты — репортёр": [
            AI(calls=[tc("save_report", {"title": "Проверка", "markdown": "# Проверка\n17 событий."})]),
            AI("Готово."),
        ],
    })
    intent = FakeStructured(error=RuntimeError("boom: structured output недоступен"))
    crit_ev = FakeStructured(CriticEvidence(verdict="ok", feedback=""))
    crit_rep = FakeStructured(CriticReport(verdict="ok", feedback=""))
    patch(agent, intent, crit_ev, crit_rep)

    g = graph_mod.build_graph(InMemorySaver())
    result = await g.ainvoke(
        {"messages": [HumanMessage(content="Расследуй аномалию счётчика")]},
        {"configurable": {"thread_id": "t-fallback"}},
    )

    assert intent.calls == 1, "структурированный вызов должен был совершиться (и упасть)"
    assert result["intent"]["label"] == "complex", f"intent: {result['intent']}"
    # 1 — сам fallback-вызов, остальные — скаут×2, инвестигейтор, репортёр×2
    assert agent.calls == 6, f"ожидается 6 LLM-вызовов, было {agent.calls}"
    assert len(result["artifacts"]) == 1


async def main():
    await scenario_1_simple()
    print("OK 1: simple → скаут + чат")
    await scenario_2_3_report_and_revision()
    print("OK 2/3: complex → отчёт → правка (continuation)")
    await scenario_4_rework()
    print("OK 4: критик → доработка отчёта (лимит 1)")
    await scenario_5_critic_disabled()
    print("OK 5: критик выключен")
    await scenario_6_intent_fallback()
    print("OK 6: fallback интента (JSON)")
    print("ALL PASS")


asyncio.run(main())
