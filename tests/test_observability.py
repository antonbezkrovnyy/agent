from observability import settings, trace_config


def test_trace_config_contains_only_observability_fields():
    state = {
        "work_mode": "deep",
        "investigation_scope": {"mode": "discovery"},
        "context": {"id": "case-1"},
        "active_work_item": {"id": "duplicates"},
        "active_skill": {"id": "event_drilldown"},
        "messages": [{"content": "secret-user-payload"}],
    }

    config = trace_config("investigator", state)

    assert config["tags"] == ["role:investigator", "mode:deep", "scope:discovery"]
    assert config["metadata"] == {
        "agent_role": "investigator",
        "work_mode": "deep",
        "scope_mode": "discovery",
        "prompt_version": settings.agent_prompt_version,
        "investigation_id": "case-1",
        "work_item_id": "duplicates",
        "skill_id": "event_drilldown",
    }
    assert "secret-user-payload" not in str(config)


def test_trace_config_handles_empty_state():
    config = trace_config("chat", {})
    assert config["tags"] == ["role:chat", "mode:unknown", "scope:unknown"]
    assert config["metadata"]["investigation_id"] == ""
    assert config["metadata"]["work_item_id"] == ""
