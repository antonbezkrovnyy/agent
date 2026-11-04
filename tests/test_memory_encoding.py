from memory_models import CaseRecord, ContextFact
from memory_store import MemoryStore, safe_json, safe_text


def test_lone_surrogates_are_replaced_before_postgresql():
    value = "Наблюдение \ud83d без парного символа"

    assert safe_text(value).encode("utf-8")
    assert safe_text(value) == "Наблюдение ? без парного символа"
    assert safe_json({"statement": value})["statement"] == safe_text(value)


def test_fact_is_sanitized_at_store_boundary():
    class Cursor:
        params = None

        def execute(self, query, params):
            self.params = params

    fact = ContextFact.model_construct(
        statement="Событие \ud83d с неполным Unicode",
        status="observed",
        source_kind="investigation",
    )
    cursor = Cursor()

    MemoryStore("", 1)._write_fact(cursor, fact, [0.1])

    assert cursor.params[2] == "Событие ? с неполным Unicode"
    cursor.params[2].encode("utf-8")


def test_case_snapshot_is_sanitized_at_store_boundary():
    case = CaseRecord.model_construct(
        investigation_id="case-unicode",
        user_id="anonymous",
        task="Задача \ud83d",
    )
    safe_case = CaseRecord.model_validate(safe_json(case.model_dump(mode="json")))
    assert safe_case.task == "Задача ?"


def test_consolidation_sanitizes_llm_dict_before_pydantic(monkeypatch):
    from conftest import FakeStructured
    from langchain_core.messages import HumanMessage
    import llm as llm_mod
    import memory as memory_mod

    class Memory:
        enabled = True

        def save_case(self, *args):
            pass

        def search_facts(self, *args):
            return []

        def find_fact_by_statement(self, *args):
            return None

        def put_fact(self, fact):
            assert fact.statement == "Наблюдение ?"

    monkeypatch.setattr(memory_mod, "memory", Memory())
    monkeypatch.setattr(llm_mod, "context_memory_llm", FakeStructured({"operations": [{
        "action": "insert",
        "fact": {
            "statement": "Наблюдение \ud83d",
            "status": "confirmed",
            "source_kind": "user",
        },
    }]}))
    state = {
        "messages": [HumanMessage(content="Проверь")],
        "context": {"id": "case-unicode"},
        "evidence": [{"id": "ev-1"}],
        "report_result": {"answer": "Готово"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_facts_saved"] == 1
    assert result["memory_error"] == ""
