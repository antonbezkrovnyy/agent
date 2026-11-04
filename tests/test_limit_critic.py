from critics import _deterministic_issues


def test_critic_rejects_middleware_text_as_final_answer():
    state = {
        "evidence": [],
        "inv_result": {
            "summary": "Tool call limit reached: run limit exceeded (31/30 calls).",
            "claims": [],
        },
        "report_result": {
            "answer": "Tool call limit reached: run limit exceeded (61/60 calls).",
            "claims": [],
        },
    }

    issues = _deterministic_issues(state)

    assert len(issues) == 2
    assert {item["suggested_action"] for item in issues} == {"rework_data", "rework_report"}
    assert all("ToolCallLimitMiddleware" in item["reason"] for item in issues)
