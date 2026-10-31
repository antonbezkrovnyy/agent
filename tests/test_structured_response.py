from conftest import agents_mod, ai, llm_mod, tc


def test_recovers_structured_call_followed_by_plain_text():
    result = {
        "messages": [
            ai(calls=[tc("InvestigationResult", {
                "summary": "17 событий", "claims": [], "limitations": [],
            })]),
            ai("Обычный текст после structured output"),
        ]
    }

    structured = agents_mod._structured_response(result)

    assert structured["summary"] == "17 событий"
    assert structured["claims"] == []


def test_plain_text_becomes_schema_with_limitation():
    result = {"messages": [ai("17 событий")]}

    structured = agents_mod._structured_response(result, llm_mod.InvestigationResult)

    assert structured["summary"] == "17 событий"
    assert structured["limitations"]
