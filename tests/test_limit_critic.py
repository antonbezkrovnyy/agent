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


def test_critic_checks_explicit_arithmetic_and_markdown_evidence_ids():
    state = {
        "evidence": [],
        "report_result": {
            "answer": "2468 + 10043 = ~12 517; call-00000000-0000-0000-0000-000000000000",
            "claims": [],
        },
    }

    issues = _deterministic_issues(state)

    assert any("2468 + 10043 = 12511" in item["reason"] for item in issues)
    assert any(item["evidence_ids"] == ["call-00000000-0000-0000-0000-000000000000"] for item in issues)


def test_critic_accepts_valid_multi_term_sum():
    state = {
        "evidence": [],
        "report_result": {"answer": "10 + 1 + 1 + 1 = 13", "claims": []},
    }

    assert not any("Явная сумма" in item["reason"] for item in _deterministic_issues(state))


def test_critic_requires_local_caveat_for_partial_event_sample():
    state = {
        "evidence": [{
            "id": "sample",
            "tool": "search_events",
            "status": "partial",
            "window": {},
            "result": {},
        }],
        "report_result": {
            "claims": [{"text": "Волна deny заполнила минуту.", "evidence_ids": ["sample"]}],
            "limitations": ["Выборка ограничена."],
        },
    }

    issues = _deterministic_issues(state)

    assert any("локальной оговорки" in item["reason"] for item in issues)
    state["report_result"]["claims"][0]["text"] = "По выборке из 20 строк видна волна deny."
    assert not any("локальной оговорки" in item["reason"] for item in _deterministic_issues(state))


def test_critic_checks_null_in_distribution_claim():
    state = {
        "evidence": [{
            "id": "tops",
            "tool": "top_values_batch",
            "status": "ok",
            "window": {},
            "result": {"fields": [{
                "field": "importance",
                "top": [
                    {"value": "info", "count": 90, "share": 90.0},
                    {"value": None, "count": 10, "share": 10.0},
                ],
            }]},
        }],
        "report_result": {
            "claims": [{
                "text": "Распределение importance: info 90.0%.",
                "evidence_ids": ["tops"],
            }],
            "limitations": [],
        },
    }

    issues = _deterministic_issues(state)

    assert any("importance опускает null" in item["reason"] for item in issues)
    state["report_result"]["claims"][0]["text"] = "Распределение importance: info 90.0%, null 10.0%."
    assert not any("опускает null" in item["reason"] for item in _deterministic_issues(state))


def test_critic_checks_timeline_total_against_buckets():
    state = {
        "evidence": [{
            "id": "timeline-1",
            "tool": "timeline",
            "status": "ok",
            "window": {},
            "result": {"timeline": {"total": 6, "buckets": [{"value": 1}, {"value": 2}]}},
        }],
        "report_result": {"claims": [], "limitations": []},
    }

    issues = _deterministic_issues(state)

    assert any("Timeline.total=6" in item["reason"] for item in issues)


def test_critic_rejects_complete_claim_when_search_has_more():
    state = {
        "evidence": [{
            "id": "search-partial",
            "tool": "search_events",
            "status": "partial",
            "window": {},
            "result": {"has_more": True},
        }],
        "report_result": {
            "claims": [{"text": "Все 13 high-событий разобраны.", "evidence_ids": ["search-partial"]}],
            "limitations": ["Выборка ограничена."],
        },
    }

    issues = _deterministic_issues(state)

    assert any("has_more=true" in item["reason"] for item in issues)
    state["report_result"]["claims"][0]["text"] = "По выборке 13 high-событий сделано наблюдение."
    assert not any("has_more=true" in item["reason"] for item in _deterministic_issues(state))


def test_critic_distinguishes_unknown_and_proven_pagination_completion():
    state = {
        "evidence": [{
            "id": "search-unknown",
            "tool": "search_events",
            "status": "partial",
            "window": {},
            "result": {"has_more": None, "pagination_error": "next page unavailable"},
        }],
        "report_result": {
            "claims": [{"text": "Все 13 high-событий разобраны.", "evidence_ids": ["search-unknown"]}],
            "limitations": [],
        },
    }

    issues = _deterministic_issues(state)

    assert any("has_more=null" in item["reason"] for item in issues)
    state["evidence"][0].update({"status": "ok", "result": {"has_more": False, "truncated": False}})
    assert not any("has_more" in item["reason"] for item in _deterministic_issues(state))


def test_critic_deduplicates_issues_regardless_of_evidence_id_order():
    state = {
        "evidence": [],
        "inv_result": {
            "claims": [{"text": "Вывод", "evidence_ids": ["missing-b", "missing-a"]}],
        },
        "report_result": {
            "claims": [{"text": "Вывод", "evidence_ids": ["missing-a", "missing-b"]}],
        },
    }

    issues = _deterministic_issues(state)

    assert len(issues) == 1
    assert issues[0]["evidence_ids"] == ["missing-b", "missing-a"]


def test_critic_does_not_match_prefix_of_suffixed_recheck_evidence_id():
    from critics import _markdown_reference_issues

    evidence_id = "call-cbc0553a-6b09-4885-b3db-2df8b01a4c35-1"
    state = {
        "evidence": [{"id": evidence_id}],
        "report_result": {"answer": f"Recheck: `{evidence_id}`"},
        "last_report": {"markdown": f"Recheck: `{evidence_id}`"},
    }

    assert _markdown_reference_issues(state) == []
