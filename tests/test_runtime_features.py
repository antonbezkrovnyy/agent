from conftest import agents_mod, llm_mod
from langchain.agents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware
from skills import get_skill


def test_role_model_defaults_and_override(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "llm_model_investigator", "deep-model")

    assert llm_mod.model_name_for_role("investigator") == "deep-model"
    assert llm_mod.model_name_for_role("scout") == llm_mod.settings.llm_model


def test_summarization_uses_official_middleware():
    middleware = agents_mod._middleware("scout", object())

    assert isinstance(middleware[0], SummarizationMiddleware)
    assert isinstance(middleware[-1], ToolCallLimitMiddleware)


def test_skill_run_records_model_and_duration():
    active = {
        **get_skill("incident_dating").model_dump(),
        "selected_at": "2026-01-01T00:00:00+00:00",
    }
    state = {
        "active_skill": active,
        "context": {"window_history": []},
        "evidence": [],
    }
    structured = {
        "summary": "Готово",
        "limitations": [],
        "skill_run": {
            "skill_id": "incident_dating",
            "version": 2,
            "status": "done",
            "summary": "Готово",
            "window_updates": 0,
            "evidence_ids": [],
            "not_checked": [],
        },
    }

    update = agents_mod._skill_run_update(state, {"context": {"window_history": []}, "evidence": []}, structured)
    run = update["skill_runs"][0]

    assert run["model"] == llm_mod.model_name_for_role("investigator")
    assert run["duration_ms"] >= 0
