from conftest import agents_mod, ai, llm_mod, tc


def test_plain_text_after_tool_call_uses_schema_fallback_not_manual_parser():
    result = {
        "messages": [
            ai(calls=[tc("InvestigationResult", {
                "summary": "17 событий", "claims": [], "limitations": [],
            })]),
            ai("Обычный текст после structured output"),
        ]
    }

    structured = agents_mod._structured_response(result, llm_mod.InvestigationResult)

    assert structured["summary"] == "Обычный текст после structured output"
    assert structured["claims"] == []
    assert structured["limitations"]


def test_plain_text_becomes_schema_with_limitation():
    result = {"messages": [ai("17 событий")]}

    structured = agents_mod._structured_response(result, llm_mod.InvestigationResult)

    assert structured["summary"] == "17 событий"
    assert structured["limitations"]
