import json

from conftest import FakeStructured
from langchain_core.messages import HumanMessage

import llm as llm_mod
import memory as memory_mod
from memory_models import (
    ContextFact,
    ContextFactDraft,
    ContextMemoryConsolidation,
    ContextMemoryOperation,
)
from reporting import build_investigation_package, memory_audit_markdown
from test_context_memory import FakeMemory, draft


def state_for_memory():
    return {
        "messages": [HumanMessage(content="Проверь srv-auth-01")],
        "context": {"id": "case-audit"},
        "evidence": [{"id": "ev-new"}],
        "report_result": {"answer": "Проверено"},
    }


def test_audit_covers_all_operation_outcomes(monkeypatch):
    existing = ContextFact(
        id="fact-old",
        statement="Старое описание",
        status="observed",
        source_kind="investigation",
        evidence_ids=["ev-old"],
    )
    fake = FakeMemory([existing.model_dump(mode="json")])
    monkeypatch.setattr(memory_mod, "memory", fake)
    operations = [
        ContextMemoryOperation(action="insert", fact=draft(), reason="новое наблюдение"),
        ContextMemoryOperation(
            action="update",
            target_id="fact-old",
            fact=draft(evidence=["ev-new"]),
        ),
        ContextMemoryOperation(
            action="supersede",
            target_id="fact-old",
            fact=draft(source="user", status="confirmed"),
        ),
        ContextMemoryOperation(action="ignore", target_id="fact-old", reason="не атомарный факт"),
        ContextMemoryOperation(
            action="insert",
            fact=draft(status="confirmed", evidence=["missing"]),
        ),
    ]
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=operations)),
    )

    result = memory_mod.node_memory_save(state_for_memory(), {})
    audit = result["memory_operations"]

    assert [item["status"] for item in audit] == [
        "saved", "saved", "saved", "ignored", "rejected",
    ]
    assert result["memory_facts_saved"] == 3
    assert audit[-1]["error"].startswith("ValueError:")
    assert audit[3]["reason"] == "не атомарный факт"
    saved_case = fake.cases[("anonymous", "case-audit")]
    assert len(saved_case["memory_operations"]) == 5
    report = memory_audit_markdown(audit, result["memory_error"])
    assert "Память расследования" in report
    assert "отклонено" in report
    assert result["messages"][0].content == report


def test_duplicate_insert_records_actual_update(monkeypatch):
    existing = ContextFact(
        id="fact-duplicate",
        statement="Старое описание",
        status="confirmed",
        source_kind="user",
    ).model_dump(mode="json")
    existing["similarity"] = 0.96
    fake = FakeMemory([existing])
    monkeypatch.setattr(memory_mod, "memory", fake)
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[
            ContextMemoryOperation(action="insert", fact=draft(source="user", status="confirmed")),
        ])),
    )

    result = memory_mod.node_memory_save(state_for_memory(), {})

    assert result["memory_operations"][0]["requested_action"] == "insert"
    assert result["memory_operations"][0]["action"] == "update"
    assert result["memory_operations"][0]["fact_id"] == "fact-duplicate"


def test_empty_and_failed_consolidation_have_explicit_audit(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation()),
    )
    empty = memory_mod.node_memory_save(state_for_memory(), {})
    assert empty["memory_operations"] == []
    assert "не предложила" in empty["messages"][0].content

    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(error=RuntimeError("unavailable")),
    )
    failed = memory_mod.node_memory_save(state_for_memory(), {})
    assert failed["memory_operations"] == []
    assert failed["memory_facts_saved"] == 0
    assert "Context Memory недоступна" in failed["memory_error"]
    assert "Аудит консолидации недоступен" in failed["messages"][0].content


def test_manifest_contains_memory_audit():
    state = state_for_memory()
    state["memory_operations"] = [{
        "action": "insert",
        "requested_action": "insert",
        "statement": "Наблюдение",
        "target_id": None,
        "fact_id": "fact-1",
        "status": "saved",
        "reason": "подтверждено",
        "error": "",
    }]
    state["memory_error"] = ""

    _, url, _ = build_investigation_package("Отчёт", "Вывод", state)
    import base64
    import io
    import zipfile

    data = base64.b64decode(url.split("base64,", 1)[1].split("#", 1)[0])
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["memory_operations"][0]["fact_id"] == "fact-1"
