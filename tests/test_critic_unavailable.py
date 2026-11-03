from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke


def test_critic_failure_is_explicit_and_does_not_stop_run(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [ai("Данные проверены")],
        "Ты — репортёр": [ai("Ответ")],
    })
    patch_llm(
        agent,
        FakeStructured(intent(mode="deep")),
        critic_evidence=FakeStructured(error=RuntimeError("unavailable")),
        critic_report=FakeStructured(error=RuntimeError("unavailable")),
    )

    result = invoke(build_graph(), "проверь события")

    assert result["report_result"]["answer"] == "Ответ"
    assert result["critic"]["verdict"] == "critic_unavailable"
    assert result["rework"] == 0
    assert result["progress"]["text"] == "Готово без проверки критика"
