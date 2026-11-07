from conftest import FakeStructured
from critics import _critic_run, _deterministic_issues
from llm import CriticReport


def test_claim_without_evidence_is_rejected():
    state = {
        "evidence": [{"id": "ev-1", "tool": "count_events", "status": "ok"}],
        "report_result": {
            "answer": "Вывод",
            "claims": [{"text": "Неподтверждённый вывод", "evidence_ids": []}],
        },
    }

    issues = _deterministic_issues(state)

    assert any("не содержит Evidence ID" in issue["reason"] for issue in issues)


def test_answer_with_evidence_requires_structured_claims():
    state = {
        "evidence": [{"id": "ev-1", "tool": "count_events", "status": "ok"}],
        "report_result": {"answer": "Вывод без машинных ссылок", "claims": []},
    }

    issues = _deterministic_issues(state)

    assert any("не содержит structured claims" in issue["reason"] for issue in issues)


def test_ok_verdict_with_issues_is_changed_to_rework():
    model = FakeStructured(CriticReport(
        verdict="ok",
        feedback="",
        issues=[{
            "claim": "Неверный вывод",
            "evidence_ids": ["ev-1"],
            "reason": "Evidence не подтверждает вывод",
            "suggested_action": "rework_report",
        }],
    ))
    state = {
        "messages": [],
        "evidence": [{"id": "ev-1", "tool": "count_events", "status": "ok"}],
        "report_result": {
            "answer": "Вывод",
            "claims": [{"text": "Вывод", "evidence_ids": ["ev-1"]}],
        },
    }

    result = _critic_run(
        state,
        "critic_report",
        model,
        ("ok", "rework_report", "rework_data"),
        "",
    )

    assert result["critic"]["verdict"] == "rework_report"
    assert len(result["critic"]["issues"]) == 1
