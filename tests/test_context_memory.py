import json

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from langchain_core.messages import HumanMessage

import llm as llm_mod
import memory as memory_mod
from memory_models import (
    ContextFact,
    ContextFactDraft,
    ContextMemoryConsolidation,
    ContextMemoryOperation,
)
from tools import search_context_memory


class FakeMemory:
    enabled = True

    def __init__(self, facts=None):
        self.cases = {}
        self.facts = list(facts or [])
        self.queries = []
        self.saved_facts = []
        self.superseded = []

    def save_case(self, user_id, case):
        self.cases[(user_id, case["investigation_id"])] = case

    def recent_cases(self, user_id, limit):
        return []

    def search_facts(self, scope, query, limit):
        self.queries.append(query)
        return [dict(item) for item in self.facts[:limit]]

    def search_cases(self, user_id, query, limit, exclude_id=""):
        return []

    def find_fact_by_statement(self, scope, statement):
        return None

    def put_fact(self, fact):
        self.saved_facts.append(fact)

    def supersede_fact(self, target, fact):
        self.superseded.append((target, fact))


def investigation_agent():
    return FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("Данные проверены")],
        "Ты — репортёр": [ai("Ответ")],
    })


def draft(source="investigation", status="observed", evidence=None):
    return ContextFactDraft(
        statement="srv-auth-01 используется регламентными задачами",
        entities=[{"type": "host", "value": "srv-auth-01"}],
        category="infrastructure",
        status=status,
        source_kind=source,
        evidence_ids=evidence or [],
    )


def test_relevant_context_is_retrieved_for_new_investigation(patch_llm, monkeypatch):
    fact = ContextFact(
        id="fact-vpn",
        statement="10.20.0.0/16 используется офисным VPN",
        status="confirmed",
        source_kind="user",
    ).model_dump(mode="json")
    fake = FakeMemory([fact])
    monkeypatch.setattr(memory_mod, "memory", fake)
    agent = investigation_agent()
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь подключение из 10.20.1.8")

    assert result["context_memories"][0]["id"] == "fact-vpn"
    assert "10.20.1.8" in fake.queries[0]
    assert any("офисным VPN" in system for system in agent.systems)


def test_user_fact_is_saved_without_evidence(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    operation = ContextMemoryOperation(
        action="insert",
        fact=draft(source="user", status="confirmed"),
    )
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[operation])),
    )
    state = {
        "messages": [HumanMessage(content="Запомни назначение srv-auth-01")],
        "context": {"id": "case-user"},
        "evidence": [{"id": "ev-1"}],
        "report_result": {"answer": "Сохранил"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_saved"] == "case-user"
    assert result["memory_facts_saved"] == 1
    assert fake.saved_facts[0].source_case_ids == ["case-user"]
    assert fake.saved_facts[0].evidence_ids == []
    assert fake.saved_facts[0].valid_to is None


def test_confirmed_fact_requires_valid_evidence(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    operation = ContextMemoryOperation(
        action="insert",
        fact=draft(status="confirmed", evidence=["missing-evidence"]),
    )
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[operation])),
    )
    state = {
        "messages": [HumanMessage(content="Проверь srv-auth-01")],
        "context": {"id": "case-evidence"},
        "evidence": [{"id": "ev-1"}],
        "inv_result": {"summary": "Проверено"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_saved"] == "case-evidence"
    assert result["memory_facts_saved"] == 0
    assert "отклонена" in result["memory_error"]
    assert fake.saved_facts == []


def test_update_preserves_provenance(monkeypatch):
    existing = ContextFact(
        id="fact-existing",
        statement="Старое описание srv-auth-01",
        status="observed",
        source_kind="investigation",
        source_case_ids=["case-old"],
        evidence_ids=["ev-old"],
    )
    fake = FakeMemory([existing.model_dump(mode="json")])
    monkeypatch.setattr(memory_mod, "memory", fake)
    operation = ContextMemoryOperation(
        action="update",
        target_id=existing.id,
        fact=draft(evidence=["ev-new"]),
    )
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[operation])),
    )
    state = {
        "messages": [HumanMessage(content="Проверь srv-auth-01")],
        "context": {"id": "case-new"},
        "evidence": [{"id": "ev-new"}],
        "inv_result": {"summary": "Уточнено"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_facts_saved"] == 1
    updated = fake.saved_facts[0]
    assert updated.id == existing.id
    assert updated.source_case_ids == ["case-old", "case-new"]
    assert updated.evidence_ids == ["ev-old", "ev-new"]
    assert updated.valid_to is not None


def test_supersede_creates_new_version(monkeypatch):
    existing = ContextFact(
        id="fact-old",
        statement="Старая роль srv-auth-01",
        status="confirmed",
        source_kind="user",
    )
    fake = FakeMemory([existing.model_dump(mode="json")])
    monkeypatch.setattr(memory_mod, "memory", fake)
    operation = ContextMemoryOperation(
        action="supersede",
        target_id=existing.id,
        fact=draft(source="user", status="confirmed"),
    )
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[operation])),
    )
    state = {
        "messages": [HumanMessage(content="Теперь роль srv-auth-01 другая")],
        "context": {"id": "case-new"},
        "evidence": [{"id": "ev-new"}],
        "report_result": {"answer": "Обновлено"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_facts_saved"] == 1
    target, replacement = fake.superseded[0]
    assert target.id == existing.id
    assert replacement.id != existing.id
    assert replacement.source_case_ids == ["case-new"]


def test_search_context_memory_tool_returns_structured_facts(monkeypatch):
    fact = ContextFact(
        id="fact-vpn",
        statement="10.20.0.0/16 используется офисным VPN",
        status="confirmed",
        source_kind="user",
    ).model_dump(mode="json")
    fake = FakeMemory([fact])
    monkeypatch.setattr(memory_mod, "memory", fake)

    payload = json.loads(search_context_memory.invoke({"query": "VPN 10.20.1.8", "limit": 3}))

    assert payload["ok"] is True
    assert payload["facts"][0]["id"] == "fact-vpn"


def test_semantic_duplicate_insert_becomes_update(monkeypatch):
    existing = ContextFact(
        id="fact-duplicate",
        statement="Старое описание srv-auth-01",
        status="confirmed",
        source_kind="user",
        source_case_ids=["case-old"],
    ).model_dump(mode="json")
    existing["similarity"] = 0.96
    fake = FakeMemory([existing])
    monkeypatch.setattr(memory_mod, "memory", fake)
    operation = ContextMemoryOperation(
        action="insert",
        fact=draft(source="user", status="confirmed"),
    )
    monkeypatch.setattr(
        llm_mod,
        "context_memory_llm",
        FakeStructured(ContextMemoryConsolidation(operations=[operation])),
    )
    state = {
        "messages": [HumanMessage(content="Уточни srv-auth-01")],
        "context": {"id": "case-new"},
        "evidence": [{"id": "ev-new"}],
        "report_result": {"answer": "Обновлено"},
    }

    result = memory_mod.node_memory_save(state, {})

    assert result["memory_facts_saved"] == 1
    assert fake.saved_facts[0].id == "fact-duplicate"
    assert fake.saved_facts[0].source_case_ids == ["case-old", "case-new"]
