from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke


def test_chat_has_logi_core_persona_and_refusal_boundary(patch_llm):
    agent = FakeAgent({"Ты — чат-агент": [ai("Я расследую только события LogiCore.")]})
    intent_llm = FakeStructured(intent("simple", new=False))
    patch_llm(agent, intent_llm)

    result = invoke(build_graph(), "расскажи, как и почему ты работаешь")

    system = agent.systems[0]
    assert "LogiCore Investigator" in system
    assert "почему" in system
    assert "посторонние вопросы" in system
    assert "Evidence" in system
    assert "проверяемости выводов" in system
    assert "посторонний вопрос" in intent_llm.systems[0]
    assert result["evidence"] == []
    assert result["messages"][-1].content == "Я расследую только события LogiCore."


def test_off_topic_request_does_not_start_investigation(patch_llm):
    agent = FakeAgent({"Ты — чат-агент": [ai("Это вне моей специализации.")]})
    patch_llm(agent, FakeStructured(intent("simple", new=False)))

    result = invoke(build_graph(), "напиши стихотворение про кофе")

    assert result["report_result"] == {}
    assert result["scout_msgs"] == []
    assert result["inv_msgs"] == []
    assert result["evidence"] == []
    assert result["messages"][-1].name == "chat"
