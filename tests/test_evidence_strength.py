import pytest
from pydantic import ValidationError

from critics import _deterministic_issues
from llm import Claim
from verification import _claim_statuses, verification_block


def test_claim_evidence_class_is_typed():
    claim = Claim(text="Не менее 10 пар", evidence_ids=["ev-1"], evidence_class="агрегатный")

    assert claim.evidence_class == "агрегатный"
    with pytest.raises(ValidationError):
        Claim(text="test", evidence_class="догадка")


def test_full_class_is_rejected_for_partial_and_proxy_evidence():
    state = {
        "evidence": [{
            "id": "ev-partial",
            "tool": "analysis_sql",
            "status": "partial",
        }, {
            "id": "ev-proxy",
            "tool": "field_fill_rates",
            "status": "ok",
        }],
        "inv_result": {
            "limitations": ["Часть данных неполна"],
            "claims": [{
                "text": "Точно 989 пар",
                "evidence_ids": ["ev-partial"],
                "evidence_class": "полный",
            }, {
                "text": "Это replay",
                "evidence_ids": ["ev-proxy"],
                "evidence_class": "полный",
            }],
        },
        "report_result": {},
    }

    issues = _deterministic_issues(state)

    assert len(issues) == 2
    assert all("Класс «полный» недопустим" in issue["reason"] for issue in issues)
    assert all(issue["suggested_action"] == "rework_report" for issue in issues)


def test_aggregate_claim_requires_aggregate_evidence():
    state = {
        "evidence": [{"id": "ev-sample", "tool": "search_events", "status": "ok"}],
        "inv_result": {
            "claims": [{
                "text": "Весь всплеск является replay",
                "evidence_ids": ["ev-sample"],
                "evidence_class": "агрегатный",
            }],
        },
        "report_result": {},
    }

    issues = _deterministic_issues(state)

    assert len(issues) == 1
    assert issues[0]["suggested_action"] == "rework_data"
    assert "без агрегатного Evidence" in issues[0]["reason"]


def test_aggregate_claim_accepts_count_for_same_population():
    state = {
        "evidence": [{"id": "ev-count", "tool": "count_events", "status": "ok"}],
        "inv_result": {
            "claims": [{
                "text": "989 событий с признаком replay",
                "evidence_ids": ["ev-count"],
                "evidence_class": "агрегатный",
            }],
        },
        "report_result": {},
    }

    assert _deterministic_issues(state) == []


def test_claim_matrix_and_report_expose_evidence_class_and_query_links():
    claims = [{
        "text": "Не менее 989 пар",
        "evidence_ids": ["ev-1"],
        "evidence_class": "агрегатный",
    }]
    evidence = [{"id": "ev-1", "status": "ok"}]
    rows = _claim_statuses(claims, evidence, [])
    block = verification_block("consistent_with_evidence", [], claim_statuses=rows)

    assert rows[0]["evidence_class"] == "агрегатный"
    assert rows[0]["query_refs"] == [{
        "evidence_id": "ev-1",
        "label": "Q1",
        "url": "queries.md#query-1",
    }]
    assert "Класс evidence | Проверка" in block
    assert "Не менее 989 пар | Связано с успешным Evidence | агрегатный | [Q1](queries.md#query-1)" in block


def test_critic_rejects_text_that_denies_existing_zip():
    state = {
        "last_report": {
            "package_filename": "investigation.zip",
            "markdown": "ZIP-архив не формируется.",
        },
        "report_result": {"answer": "ZIP-архив не формируется.", "claims": []},
        "evidence": [],
    }

    issues = _deterministic_issues(state)

    assert len(issues) == 1
    assert issues[0]["suggested_action"] == "rework_report"
    assert "investigation.zip" in issues[0]["reason"]


def test_artifact_claim_check_does_not_fire_without_zip():
    state = {
        "last_report": {"markdown": "ZIP-архив не формируется."},
        "report_result": {"answer": "ZIP-архив не формируется.", "claims": []},
        "evidence": [],
    }

    assert _deterministic_issues(state) == []
