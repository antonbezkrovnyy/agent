import json
from types import SimpleNamespace

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from critics import _deterministic_issues
from evidence import chain_markdown, investigation_chain
from export import _export_source
from state import merge_hypotheses
from tools import get_hypothesis, list_hypotheses, update_hypothesis


def runtime(state):
    return SimpleNamespace(state=state, tool_call_id="call-hyp")


def test_hypothesis_lifecycle_and_report_history():
    state = {
        "evidence": [{"id": "ev-1", "status": "ok", "window": {"from": "a", "to": "b"}}],
        "hypotheses": [],
        "messages": [],
        "context": {"id": "case-1"},
    }
    created = update_hypothesis.func(
        text="Сбой связан с deploy",
        status="open",
        reason="Нужно проверить время deploy",
        evidence_ids=[],
        runtime=runtime(state),
    )
    state["hypotheses"] = merge_hypotheses(state["hypotheses"], created.update["hypotheses"])
    assert created.update["messages"][1].name == "hypothesis"
    assert created.update["messages"][1].content.startswith("Гипотеза: Сбой связан с deploy")
    hypothesis_id = state["hypotheses"][0]["id"]

    updated = update_hypothesis.func(
        text="Сбой связан с deploy",
        status="supported",
        reason="Время deploy совпало с первым сбоем",
        evidence_ids=["ev-1"],
        hypothesis_id=hypothesis_id,
        runtime=runtime(state),
    )
    state["hypotheses"] = merge_hypotheses(state["hypotheses"], updated.update["hypotheses"])

    assert len(state["hypotheses"]) == 1
    assert state["hypotheses"][0]["status"] == "supported"
    assert [item["status"] for item in state["hypotheses"][0]["history"]] == ["open", "supported"]
    assert len(investigation_chain(state)["hypotheses"]) == 1
    report = chain_markdown(state)
    assert "## Гипотезы" in report
    assert "open" in report and "supported" in report


def test_terminal_hypothesis_requires_valid_evidence():
    state = {"evidence": [], "hypotheses": []}

    result = update_hypothesis.func(
        text="Сбой связан с deploy",
        status="rejected",
        reason="Нет доказательств",
        evidence_ids=[],
        runtime=runtime(state),
    )

    assert result.update["messages"][0].status == "error"
    assert "evidence_id" in result.update["messages"][0].content
    assert "hypotheses" not in result.update


def test_critic_flags_unsupported_hypothesis():
    state = {
        "evidence": [],
        "hypotheses": [{
            "id": "hyp-1",
            "text": "Неподтверждённая гипотеза",
            "status": "supported",
            "evidence_ids": ["missing"],
        }],
    }

    issues = _deterministic_issues(state)

    assert any(issue["suggested_action"] == "rework_data" for issue in issues)


def test_export_prefers_recommended_query():
    state = {
        "inv_result": {
            "recommended_export_query": {
                "filter": {"service": "auth"},
                "columns": ["timestamp", "message"],
                "order": "desc",
            }
        },
        "report_result": {},
        "evidence": [],
    }

    assert _export_source(state) == {
        "filter": {"service": "auth"},
        "columns": ["timestamp", "message"],
        "order": "desc",
    }


def test_hypothesis_tool_crosses_subgraph_delta(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("update_hypothesis", {
                "text": "Сбой связан с deploy",
                "status": "open",
                "reason": "Нужна проверка",
                "evidence_ids": [],
            })]),
            ai("Гипотеза сформирована"),
        ],
        "Ты — репортёр": [ai("Ответ")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    result = invoke(build_graph(), "Проверь причину сбоя")

    assert len(result["hypotheses"]) == 1
    assert result["hypotheses"][0]["status"] == "open"
    assert result["hypotheses"][0]["text"] == "Сбой связан с deploy"


def test_list_and_get_hypothesis_read_local_state():
    state = {"hypotheses": [{
        "id": "hyp-1",
        "text": "Сбой связан с deploy",
        "status": "open",
        "reason": "Нужна проверка",
        "evidence_ids": [],
        "history": [{"status": "open", "reason": "Нужна проверка"}],
    }]}
    tool_runtime = runtime(state)

    listed = json.loads(list_hypotheses.func(runtime=tool_runtime))
    fetched = json.loads(get_hypothesis.func("hyp-1", runtime=tool_runtime))
    missing = json.loads(get_hypothesis.func("missing", runtime=tool_runtime))

    assert listed["hypotheses"][0]["id"] == "hyp-1"
    assert "history" not in listed["hypotheses"][0]
    assert fetched["hypothesis"]["history"][0]["status"] == "open"
    assert missing["ok"] is False
    assert missing["available"] == ["hyp-1"]
