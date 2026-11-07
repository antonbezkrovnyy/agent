from conftest import (
    FakeAgent,
    FakeStructured,
    ai,
    assert_clean,
    build_graph,
    critics,
    intent,
    invoke,
    tc,
    texts,
)


def test_simple_skips_scout(patch_llm):
    agent = FakeAgent({"Ты — чат-агент": [ai("Привет!")]})
    patch_llm(agent, FakeStructured(intent("simple", new=False)))

    result = invoke(build_graph(), "привет")

    assert texts(result) == ["привет", "Привет!"]
    assert agent.calls == 1
    assert result["scout_msgs"] == []
    assert result["evidence"] == []
    assert_clean(result)


def test_plain_answer_gets_report_critic(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("За час 17 событий")],
        "Ты — репортёр": [ai("За последний час 17 событий")],
    })
    critic_evidence, critic_report = critics()
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)

    result = invoke(build_graph(), "что в логикоре за последний час")

    assert texts(result) == ["что в логикоре за последний час", "За последний час 17 событий"]
    assert result["report_result"]["answer"] == "За последний час 17 событий"
    assert result["artifacts"] == []
    assert critic_evidence.calls == 1
    assert critic_report.calls == 1
    assert "Evidence, связанные с claims/findings/Markdown" in critic_report.systems[0]
    assert_clean(result)
