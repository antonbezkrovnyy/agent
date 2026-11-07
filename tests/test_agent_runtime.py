import asyncio
import base64
import io
import json
import zipfile

import httpx
from openai import APIConnectionError

from conftest import FakeAgent, FakeStructured, agents_mod, ai, build_graph, intent, invoke, tc
from contracts import InvestigationPlan, WorkItem
from langchain_core.messages import HumanMessage, ToolMessage
from llm import CriticEvidence
from reporting import build_investigation_package


class SlowFakeAgent(FakeAgent):
    always_slow: bool = False
    delay: float = 2.0
    model_attempts: int = 0
    delayed_once: bool = False

    def __init__(self, by_role, *, always_slow=False, delay=2.0):
        super().__init__(by_role)
        self.always_slow = always_slow
        self.delay = delay
        self.model_attempts = 0
        self.delayed_once = False

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.model_attempts += 1
        should_delay = self.model_attempts > 1 and (self.always_slow or not self.delayed_once)
        if should_delay:
            self.delayed_once = True
            await asyncio.sleep(self.delay)
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


class FailingAgent:
    def __init__(self):
        self.calls = 0

    async def astream(self, agent_input, config=None, stream_mode=None):
        self.calls += 1
        yield "values", agent_input
        raise APIConnectionError(request=httpx.Request("POST", "http://litellm.test/v1/chat/completions"))


def runtime_state():
    return {
        "messages": [HumanMessage(content="проверь события")],
        "callback_url": "",
        "artifacts": [],
        "findings": [],
        "evidence": [],
        "hypotheses": [],
        "context": {
            "id": "case-runtime",
            "time_from": "2026-11-04T00:00:00+00:00",
            "time_to": "2026-11-05T00:00:00+00:00",
        },
        "last_report": {},
        "inv_summary": "",
        "inv_result": {},
        "scout_note": "",
        "scout_result": {},
        "active_work_item": {
            "id": "item-1",
            "goal": "Проверить события",
            "acceptance": "Получить количество и зафиксировать результат",
            "attempt": 0,
        },
        "investigation_scope": {},
        "skill_runs": [],
        "anchors": [],
        "visited_anchors": [],
        "critic": {},
        "rework": 0,
        "tool_usage": {},
        "progress": {},
    }


def configure_timeouts(monkeypatch, *, soft=0.2, hard=15, attempt=0.5, attempts=10):
    monkeypatch.setattr(agents_mod.settings, "agent_finalize_after_seconds", soft)
    monkeypatch.setattr(agents_mod.settings, "agent_timeout_seconds", hard)
    monkeypatch.setattr(agents_mod.settings, "agent_finalize_attempt_timeout_seconds", attempt)
    monkeypatch.setattr(agents_mod.settings, "agent_finalize_attempts", attempts)


def unzip(url):
    data = base64.b64decode(url.split("base64,", 1)[1].split("#", 1)[0])
    return zipfile.ZipFile(io.BytesIO(data))


def test_soft_timeout_preserves_state_and_finalizes_with_local_tools(patch_llm, monkeypatch):
    agent = SlowFakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("log_finding", {
                "claim": "Количество получено",
                "evidence_ids": [],
                "next_action": "завершить",
            })]),
            ai("Результат по собранным данным"),
        ],
    })
    patch_llm(agent, FakeStructured(intent()))
    configure_timeouts(monkeypatch)

    result, new_messages, update = asyncio.run(agents_mod._run_agent(
        "investigator", runtime_state(), "inv_msgs",
    ))

    assert result["agent_phase"] == "finalize"
    assert result["termination_reason"] == "soft_timeout"
    assert [item["tool"] for item in result["evidence"]] == ["count_events"]
    assert [item["claim"] for item in result["findings"]] == ["Количество получено"]
    assert result["structured_response"].summary == "Результат по собранным данным"
    assert sum(call["name"] == "count_events" for message in new_messages for call in getattr(message, "tool_calls", [])) == 1
    assert update["run_metrics"][0]["tool_calls"] == 3
    assert update["run_metrics"][0]["tool_calls_by_name"] == {
        "count_events": 1,
        "log_finding": 1,
        "InvestigationResult": 1,
    }

    package_state = {
        **runtime_state(),
        "evidence": result["evidence"],
        "findings": result["findings"],
    }
    _, url, _ = build_investigation_package("Timeout", "# Частичный результат", package_state)
    with unzip(url) as bundle:
        records = [json.loads(line) for line in bundle.read("evidence.jsonl").decode().splitlines()]
        manifest = json.loads(bundle.read("manifest.json"))
    assert [item["tool"] for item in records] == ["count_events"]
    assert manifest["evidence_count"] == 1


def test_finalize_exhaustion_keeps_partial_state_after_ten_attempts(patch_llm, monkeypatch):
    agent = SlowFakeAgent({
        "Ты — инвестигейтор": [ai(calls=[tc("count_events", {})])],
    }, always_slow=True)
    patch_llm(agent, FakeStructured(intent()))
    configure_timeouts(monkeypatch)

    result, _, update = asyncio.run(agents_mod._run_agent(
        "investigator", runtime_state(), "inv_msgs",
    ))

    instructions = [
        message for message in result["messages"]
        if isinstance(message, HumanMessage) and "Техническая фаза завершения" in message.content
    ]
    assert len(instructions) == 10
    assert result["agent_phase"] == "fallback"
    assert result["termination_reason"] == "finalize_exhausted"
    assert result["structured_response"].status == "partial"
    assert [item["tool"] for item in result["evidence"]] == ["count_events"]
    assert update["evidence"][0]["tool"] == "count_events"
    assert update["run_metrics"][0]["tool_calls"] == 1


def check_role_finalize(patch_llm, monkeypatch, role, tool_name, work_key):
    role_prompt = {"scout": "Ты — скаут", "reporter": "Ты — репортёр"}[role]
    agent = SlowFakeAgent({role_prompt: [ai(calls=[tc(tool_name, {})]), ai("Финализация завершена")]})
    patch_llm(agent, FakeStructured(intent()))
    configure_timeouts(monkeypatch)

    result, new_messages, _ = asyncio.run(agents_mod._run_agent(role, runtime_state(), work_key))

    assert result["agent_phase"] == "finalize"
    assert [item["tool"] for item in result["evidence"]] == [tool_name]
    assert sum(
        call["name"] == tool_name
        for message in new_messages for call in getattr(message, "tool_calls", [])
    ) == 1


def test_scout_finalize_disables_new_mcp_calls(patch_llm, monkeypatch):
    check_role_finalize(patch_llm, monkeypatch, "scout", "count_events", "scout_msgs")


def test_reporter_finalize_disables_new_mcp_calls(patch_llm, monkeypatch):
    check_role_finalize(patch_llm, monkeypatch, "reporter", "extract_data", "rep_msgs")


def structured_result(text):
    return ai(calls=[tc("InvestigationResult", {
        "summary": text,
        "claims": [],
        "limitations": [],
        "not_checked": [],
    })])


def test_retry_reuses_identical_mcp_evidence_and_receives_continuation(patch_llm):
    planner = FakeStructured(InvestigationPlan(
        goal="Проверить retry",
        items=[WorkItem(id="main", goal="Проверить retry")],
    ))
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("count_events", {})]),
            structured_result("Первая попытка"),
            ai(calls=[tc("count_events", {})]),
            structured_result("Продолжение завершено"),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    critic = FakeStructured(results=[
        CriticEvidence(verdict="rework_data", feedback="Нужна ещё одна проверка"),
        CriticEvidence(verdict="ok", feedback=""),
    ])
    patch_llm(
        agent,
        FakeStructured(intent(mode="deep")),
        critic_evidence=critic,
        planner=planner,
    )

    result = invoke(build_graph(), "проверь retry", thread_id="runtime-retry")

    assert [item["tool"] for item in result["evidence"]] == ["count_events"]
    reused = [
        message for message in result["inv_msgs"]
        if isinstance(message, ToolMessage) and '"reused": true' in message.content
    ]
    assert len(reused) == 1
    assert result["work_items"][0]["attempt"] == 2
    assert result["work_items"][0]["result"]["summary"] == "Продолжение завершено"
    assert any("Continuation того же work item" in system for system in agent.systems)
    assert any(
        isinstance(message, HumanMessage) and "Продолжи main с сохранённого состояния" in message.content
        for convo in agent.convos for message in convo
    )


def test_restore_stream_state_is_idempotent():
    evidence = {"id": "call-1", "tool": "count_events", "status": "ok", "result": {"count": 1}}
    message = ToolMessage(content="{}", tool_call_id="call-1", name="count_events")
    latest = {"messages": [], "evidence": [], "findings": [], "artifacts": []}
    events = {"call-1": {"messages": [message], "evidence": [evidence]}}

    restored = agents_mod._restore_stream_state(latest, events)
    restored_again = agents_mod._restore_stream_state(restored, events)

    assert restored_again["evidence"] == [evidence]
    assert restored_again["messages"] == [message]


def test_stream_does_not_replace_newer_structured_snapshot_with_stale_nested_state():
    structured = object()
    latest = {"messages": [HumanMessage(content="one"), HumanMessage(content="two")], "structured_response": structured}
    stale = {"messages": [HumanMessage(content="one")], "structured_response": None}

    assert agents_mod._prefer_stream_state(latest, stale) is latest
    assert agents_mod._prefer_stream_state(stale, latest) is latest


def test_provider_connection_error_preserves_partial_state(patch_llm, monkeypatch):
    patch_llm(FakeAgent({}), FakeStructured(intent()))
    configure_timeouts(monkeypatch)
    failing = FailingAgent()
    agents_mod._agents["investigator"] = failing
    state = runtime_state()
    state["evidence"] = [{"id": "ev-before-error", "tool": "count_events", "status": "ok", "result": {"count": 17}}]
    state["findings"] = [{"claim": "Данные получены", "evidence_ids": ["ev-before-error"]}]

    result, _, _ = asyncio.run(agents_mod._run_agent("investigator", state, "inv_msgs"))

    assert failing.calls == 11
    assert result["agent_phase"] == "fallback"
    assert result["termination_reason"] == "provider_error"
    assert result["structured_response"].status == "partial"
    assert result["structured_response"].limitations == [
        "Временная ошибка LLM-провайдера: этап завершён по сохранённым данным."
    ]
    assert result["evidence"] == state["evidence"]
    assert result["findings"] == state["findings"]


def test_first_attempt_reuses_identical_mcp_evidence(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("count_events", {})]),
            structured_result("Готово"),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    result = invoke(build_graph(), "проверь повтор", thread_id="runtime-first-reuse")

    assert len(result["evidence"]) == 1
    assert len(result["tool_calls"]) == 2
    assert [item["reused"] for item in result["tool_calls"]] == [False, True]
    assert result["tool_calls"][0]["arguments_effective"]["time_from"]
    assert result["tool_calls"][1]["evidence_id"] == result["evidence"][0]["id"]
    assert result["work_items"][0]["attempt"] == 1
    assert sum(
        '"reused": true' in message.content
        for message in result["inv_msgs"] if isinstance(message, ToolMessage)
    ) == 1


def test_singleflight_shares_only_identical_calls_and_preserves_pages():
    from tools import normalize_tool_result

    class Request:
        def __init__(self, call_id, args):
            self.tool_call = {"name": "search_events", "args": args, "id": call_id}
            self.state = {
                "context": {"id": "case-singleflight"},
                "evidence": [],
                "progress": {},
                "run_tool_call_count": {},
            }

        def override(self, tool_call):
            self.tool_call = tool_call
            return self

    calls = []

    async def handler(request):
        page = request.tool_call["args"]["page"]
        calls.append(page)
        await asyncio.sleep(0.02)
        return ToolMessage(
            content=json.dumps({"ok": True, "complete": True, "page": page, "events": []}),
            tool_call_id=request.tool_call["id"],
            name="search_events",
        )

    async def run():
        same = await asyncio.gather(
            normalize_tool_result.awrap_tool_call(Request("same-1", {"page": 1}), handler),
            normalize_tool_result.awrap_tool_call(Request("same-2", {"page": 1}), handler),
        )
        pages = await asyncio.gather(
            normalize_tool_result.awrap_tool_call(Request("page-1", {"page": 1}), handler),
            normalize_tool_result.awrap_tool_call(Request("page-2", {"page": 2}), handler),
        )
        return same, pages

    same, pages = asyncio.run(run())

    assert calls == [1, 1, 2]
    assert same[0].update["evidence"][0]["id"] == "same-1"
    assert same[1].update["evidence"][0]["id"] == "same-1"
    assert '"reused": true' in same[1].update["messages"][0].content
    assert [item.update["evidence"][0]["id"] for item in pages] == ["page-1", "page-2"]
