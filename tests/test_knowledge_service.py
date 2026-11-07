import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "knowledge-service"))

from indexer import KnowledgeIndex, parse_markdown, split_sections
from memory_api import MemoryEditor


def test_markdown_parser_and_sections(tmp_path):
    path = tmp_path / "page.md"
    path.write_text(
        "---\ntitle: VPN\nentities: [source, VPN-01]\n---\n\n# VPN\n\nSummary\n\n## Time\n\nUse ingest time\n",
        encoding="utf-8",
    )

    parsed = parse_markdown(path)
    sections = split_sections(parsed["body"])

    assert parsed["metadata"]["title"] == "VPN"
    assert parsed["metadata"]["entities"] == ["source", "VPN-01"]
    assert [item["title"] for item in sections] == ["VPN", "Time"]
    assert sections[1]["content"] == "Use ingest time"


class FakeKnowledgeStore:
    def __init__(self):
        self.pages = {}
        self.removed = None

    def upsert_page(self, page, sections):
        self.pages[page["id"]] = (page, sections)

    def remove_missing(self, active):
        self.removed = active


def test_indexer_publishes_page_and_sections(tmp_path, monkeypatch):
    from config import Settings

    (tmp_path / "Runbooks").mkdir()
    (tmp_path / "Runbooks" / "VPN.md").write_text(
        "---\ntitle: VPN\n---\n\n# VPN\n\nCheck timeline\n",
        encoding="utf-8",
    )
    index = KnowledgeIndex(Settings(), str(tmp_path))
    store = FakeKnowledgeStore()
    index.store = store
    monkeypatch.setattr(index, "_embed", lambda text: [0.1, 0.2])

    result = index.index_all()

    assert result["indexed"] == 1
    page, sections = store.pages["Runbooks/VPN"]
    assert page["title"] == "VPN"
    assert page["index_status"] == "ready"
    assert sections[0]["embedding"] == [0.1, 0.2]
    assert store.removed == {"Runbooks/VPN"}


class FakeMemoryStore:
    def __init__(self):
        self.facts = {}
        self.control_updates = []

    def put_fact(self, fact, embedding):
        self.facts[fact.id] = fact

    def supersede_fact(self, target, fact, embedding):
        self.facts[fact.id] = fact

    def get_fact(self, scope, fact_id):
        return self.facts.get(fact_id)

    def update_fact_controls(self, scope, fact_id, base_revision, **kwargs):
        self.control_updates.append((fact_id, base_revision, kwargs))
        fact = self.facts[fact_id]
        fact.revision += 1
        return fact


def test_memory_editor_versions_and_reindex(monkeypatch):
    from config import Settings

    editor = MemoryEditor(Settings())
    editor.store = FakeMemoryStore()
    monkeypatch.setattr(editor, "_embed", lambda text: [0.3])

    created = editor.create_fact("VPN uses ingest time")
    updated = editor.update_fact(created["id"], "VPN uses source time", None, 1)
    controlled = editor.set_controls(updated["id"], 2, pinned=True)

    assert created["index_status"] == "ready"
    assert updated["revision"] == 2
    assert updated["id"] != created["id"]
    assert controlled["revision"] == 3
    assert editor.store.control_updates[-1][2]["pinned"] is True


def test_consolidation_increments_revision():
    from memory import _updated_fact
    from memory_models import ContextFact, ContextFactDraft

    target = ContextFact(
        statement="Старый факт",
        status="confirmed",
        source_kind="user",
        revision=4,
        pinned=True,
    )
    draft = ContextFactDraft(
        statement="Новый факт",
        status="observed",
        source_kind="investigation",
    )

    updated = _updated_fact(target, draft)

    assert updated.revision == 5
    assert updated.pinned is True


def test_wiki_tools_return_structured_results(monkeypatch):
    import knowledge
    from tools import wiki_read, wiki_search

    monkeypatch.setattr(knowledge, "wiki_search", lambda query, limit=3: {"ok": True, "items": [{"id": "Runbooks/VPN"}]})
    monkeypatch.setattr(knowledge, "wiki_read", lambda page_id, section="": {"ok": True, "page": {"id": page_id, "section": section}})

    found = json.loads(wiki_search.func("VPN"))
    page = json.loads(wiki_read.func("Runbooks/VPN", "Time"))

    assert found["items"][0]["id"] == "Runbooks/VPN"
    assert page["page"] == {"id": "Runbooks/VPN", "section": "Time"}


def test_report_artifacts_publish_card(monkeypatch):
    import knowledge
    from artifacts import create_report_artifacts
    from test_investigation_package import state_with_evidence

    published = []
    monkeypatch.setattr(knowledge, "publish_report_card", lambda card: published.append(card) or {"ok": True})
    state = state_with_evidence()

    _, artifacts = create_report_artifacts("VPN report", "# Result", state)

    assert artifacts
    assert published[0]["case_id"] == "case-package"
    assert published[0]["title"] == "VPN report"
    assert published[0]["artifacts"]
