import json
from types import SimpleNamespace

from critics import _critic_messages
from evidence import evidence_overview_block, full_evidence_block, with_investigation_chain
from tools import get_evidence


def state_with_evidence():
    return {
        "messages": [],
        "context": {
            "id": "case-1",
            "time_from": "2026-01-01T00:00:00+00:00",
            "time_to": "2026-01-02T00:00:00+00:00",
            "base_window": {
                "time_from": "2026-01-01T00:00:00+00:00",
                "time_to": "2026-01-02T00:00:00+00:00",
            },
            "window_history": [],
        },
        "work_mode": "deep",
        "evidence": [
            {
                "id": "e1",
                "tool": "search_events",
                "arguments": {},
                "window": {"from": "2026-01-01", "to": "2026-01-02"},
                "status": "ok",
                "created_at": "2026-01-02T00:00:00+00:00",
                "result": {"events": [{"uuid": f"event-{i}"} for i in range(10)]},
            },
            {
                "id": "e2",
                "tool": "count_events",
                "arguments": {},
                "window": {},
                "status": "ok",
                "result": {"count": 999, "marker": "unrelated"},
            },
        ],
        "findings": [],
        "depth_history": [],
        "inv_result": {"claims": [{"text": "Десять событий", "evidence_ids": ["e1"]}]},
        "report_result": {},
    }


def test_overview_contains_compact_results_without_dumping_all_events():
    text = evidence_overview_block(state_with_evidence())

    assert "get_evidence" in text
    assert '"events_count": 10' in text
    assert "event-0" in text
    assert "event-1" in text
    assert "event-9" not in text
    assert '"marker": "unrelated"' in text


def test_full_bundle_has_all_events_only_for_requested_claim():
    text = full_evidence_block(state_with_evidence(), ["e1"])

    assert "event-0" in text
    assert "event-9" in text
    assert "unrelated" not in text


def test_get_evidence_reads_full_record_from_state():
    state = state_with_evidence()
    runtime = SimpleNamespace(state=state)

    result = json.loads(get_evidence.func("e1", runtime=runtime))

    assert result["ok"] is True
    assert len(result["evidence"]["result"]["events"]) == 10


def test_report_chain_uses_state_facts():
    state = state_with_evidence()
    state["messages"] = [SimpleNamespace(type="human", content="Найди события")]
    state["context"]["window_history"] = [{
        "at": "2026-01-02T00:01:00+00:00",
        "action": "expand",
        "reason": "Проверка назад",
        "from": {},
        "to": {},
    }]
    state["inv_result"]["not_checked"] = ["Другой источник"]

    markdown = with_investigation_chain("# Ответ", state)

    assert "Найди события" in markdown
    assert "Проверка назад" in markdown
    assert "search_events" in markdown
    assert "Другой источник" in markdown
    assert "unrelated" not in markdown


def test_critic_receives_full_claim_evidence():
    messages = _critic_messages(state_with_evidence(), "critic_evidence", "Проверка")
    system = messages[0].content

    assert "полный Evidence Bundle" in system
    assert "event-9" in system
    assert "unrelated" not in system
