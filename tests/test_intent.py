import json
from datetime import UTC, datetime

from conftest import FakeStructured, intent
from investigation import _intent_fallback, _intent_structured
import llm as llm_mod
from llm import IntentDecision, WindowSpec, build_intent_llm
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda


def test_intent_window_is_json_serializable(monkeypatch):
    moment = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
    decision = IntentDecision(
        intent="complex",
        rationale="Проверка окна",
        needs_new_data=True,
        window=WindowSpec(time_from=moment),
    )
    monkeypatch.setattr(llm_mod, "intent_llm", FakeStructured(decision))

    result = _intent_structured({"messages": [HumanMessage(content="С 6 утра")]})

    assert datetime.fromisoformat(result["window"]["time_from"].replace("Z", "+00:00")) == moment
    json.dumps(result)


def test_intent_uses_json_mode_fallback():
    primary = FakeStructured(error=RuntimeError("function calling unavailable"))
    fallback = FakeStructured(intent())

    class Model:
        def with_structured_output(self, schema, method):
            assert schema is IntentDecision
            if method == "function_calling":
                return RunnableLambda(primary.invoke)
            if method == "json_mode":
                return RunnableLambda(fallback.invoke)
            raise AssertionError(method)

    result = build_intent_llm(Model()).invoke([HumanMessage(content="test")])

    assert result.needs_new_data is True
    assert primary.calls == 1
    assert fallback.calls == 1


def test_intent_fallback_does_not_guess_skills_from_keywords():
    result = _intent_fallback({"context": {}, "messages": []})

    assert result["needs_new_data"] is True
    assert result["work_mode"] == "deep"
    assert result["skill_id"] is None
    assert result["related_investigation_id"] is None
