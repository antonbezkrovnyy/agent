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

    assert result["report_result"]["answer"].startswith("Ответ")
    assert result["report_result"]["verification"]["status"] == "unverified"
    assert result["critic"]["verdict"] == "critic_unavailable"
    assert result["rework"] == 0
    assert result["progress"]["text"] == "Готово без проверки критика"


def test_deterministic_issue_includes_reason_when_llm_critic_is_unavailable(patch_llm, monkeypatch):
    import critics as critics_mod

    monkeypatch.setattr(critics_mod.llm_mod, "critic_report_llm", FakeStructured(error=RuntimeError("down")))
    state = {
        "messages": [],
        "evidence": [],
        "report_result": {"answer": "2468 + 10043 = ~12 517", "claims": []},
        "critic": {},
        "rework": 0,
    }

    result = critics_mod.node_critic_report(state)

    assert result["critic"]["verdict"] == "rework_report"
    assert "2468 + 10043 = 12511" in result["critic"]["feedback"]
