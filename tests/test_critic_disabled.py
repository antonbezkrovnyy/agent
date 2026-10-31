from conftest import (
    FakeAgent,
    FakeStructured,
    ai,
    build_graph,
    critics,
    graph_mod,
    intent,
    invoke,
    tc,
    texts,
)


def test_critic_can_be_disabled(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("17 событий")],
        "Ты — репортёр": [ai("Ответ")],
    })
    critic_evidence, critic_report = critics()
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)
    graph_mod.settings.critic_enabled = False

    try:
        result = invoke(build_graph(), "проверь события")
    finally:
        graph_mod.settings.critic_enabled = True

    assert texts(result) == ["проверь события", "Ответ"]
    assert critic_evidence.calls == 0
    assert critic_report.calls == 0
    assert result["critic"] == {}
