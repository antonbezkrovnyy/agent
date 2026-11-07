from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
import memory as memory_mod


class FakeMemory:
    def __init__(self):
        self.cases = {}

    def save_case(self, user_id, case):
        self.cases[(user_id, case["investigation_id"])] = case

    def get_case(self, user_id, investigation_id):
        return self.cases.get((user_id, investigation_id))

    def recent_cases(self, user_id, limit):
        cases = [case for (owner, _), case in self.cases.items() if owner == user_id]
        return sorted(cases, key=lambda item: item["updated_at"], reverse=True)[:limit]

    def search_facts(self, scope, query, limit):
        return []

    def search_cases(self, user_id, query, limit, exclude_id=""):
        return []


def investigation_agent():
    return FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("Данные проверены")],
        "Ты — репортёр": [ai("Ответ")],
    })


def test_case_moves_to_new_thread_with_control_query(patch_llm, monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    patch_llm(investigation_agent(), FakeStructured(intent()))
    graph = build_graph()
    first = invoke(graph, "проверь события", thread_id="memory-first")

    assert first["memory_saved"] == first["context"]["id"]
    assert fake.cases

    decision = intent("continuation", new=True, mode="deep")
    decision.related_investigation_id = first["context"]["id"]
    decision.window_requested = True
    decision.window.last_minutes = 30
    agent2 = FakeAgent({
        "Ты — инвестигейтор": [ai("Старый вывод подтверждён")],
        "Ты — репортёр": [ai("Прошлый случай проверен")],
    })
    patch_llm(agent2, FakeStructured(decision))
    second = invoke(graph, "продолжи прошлый случай", thread_id="memory-second")

    assert second["memory_loaded"] == first["context"]["id"], (second.get("memory_error"), second["intent"])
    assert second["context"]["id"] == first["context"]["id"]
    assert second["context"]["focus_window"] is not None
    assert second["intent"]["memory_control_required"] is True
    assert second["inv_summary"] == "Старый вывод подтверждён"
    assert len(second["evidence"]) == len(first["evidence"])


def test_memory_failure_does_not_stop_dialog(patch_llm, monkeypatch):
    class BrokenMemory:
        def recent_cases(self, user_id, limit):
            raise RuntimeError("database down")

    monkeypatch.setattr(memory_mod, "memory", BrokenMemory())
    agent = FakeAgent({"Ты — чат-агент": [ai("Привет")]})
    patch_llm(agent, FakeStructured(intent("simple", new=False)))

    result = invoke(build_graph(), "привет")

    assert result["messages"][-1].content == "Привет"
    assert "недоступна" in result["memory_error"]


def test_memory_save_failure_is_explicit(patch_llm, monkeypatch):
    class BrokenMemory:
        def save_case(self, user_id, case):
            raise RuntimeError("database down")

    monkeypatch.setattr(memory_mod, "memory", BrokenMemory())
    patch_llm(investigation_agent(), FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")

    assert result["memory_saved"] == ""
    assert "недоступна" in result["memory_error"]
    assert result["report_result"]["answer"] == "Ответ"


def test_case_snapshot_restores_audit_analysis_and_verification(monkeypatch):
    from langchain_core.messages import HumanMessage

    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    state = {
        "messages": [HumanMessage(content="Проверь события")],
        "context": {"id": "case-full", "time_from": "2026-01-01", "time_to": "2026-01-02"},
        "work_mode": "deep",
        "evidence": [{"id": "ev-1"}],
        "tool_calls": [{"id": "call-1"}],
        "analysis_datasets": [{"id": "dataset-1", "rows": [{"value": 1}]}],
        "analysis_results": [{"id": "analysis-1"}],
        "verification_checks": [{"id": "check-1"}],
        "independent_verification": {"status": "passed"},
        "run_metrics": [{"agent": "investigator"}],
        "report_result": {"answer": "Готово", "claims": []},
        "tenant": "tenant-1",
        "source_endpoint": "https://source.test",
        "schema_version": "schema-1",
        "time_semantics": "event_time",
        "time_axes": {"event": "timestamp"},
    }
    case = memory_mod._case_from_state(state, {"configurable": {"thread_id": "first"}})
    fake.cases[("anonymous", "case-full")] = case

    restored = memory_mod.node_memory_load(
        {"intent": {"related_investigation_id": "case-full"}},
        {"configurable": {"thread_id": "second"}},
    )

    for key in (
        "tool_calls", "analysis_datasets", "analysis_results", "verification_checks",
        "independent_verification", "run_metrics", "report_result", "tenant",
        "source_endpoint", "schema_version", "time_semantics", "time_axes",
    ):
        assert restored[key] == state[key]
