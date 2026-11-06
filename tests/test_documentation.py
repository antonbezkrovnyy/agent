from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke
from documentation import documentation_block


def test_documentation_contains_full_project_files():
    block = documentation_block()

    for path in ("README.md", "docs/DEV.md", "docs/OBSERVABILITY.md", "docs/ROADMAP.md", "logicore-mcp/README.md", "AGENTS.md"):
        assert f"# Файл: {path}" in block
    assert "logicore-default" in block
    assert "finalization" in block
    assert "UntranslatableCharacter" in block


def test_chat_receives_full_documentation_for_architecture_question(patch_llm):
    agent = FakeAgent({"Ты — чат-агент": [ai("Используется LiteLLM с алиасами.")]})
    patch_llm(agent, FakeStructured(intent("simple", new=False)))

    invoke(build_graph(), "Как настроен LiteLLM?")

    convo = "\n".join(message.content for message in agent.convos[0])
    assert "Полная актуальная проектная документация" in convo
    assert "# Файл: docs/DEV.md" in convo
    assert "# Файл: docs/OBSERVABILITY.md" in convo
    assert "logicore-default" in convo


def test_chat_receives_documentation_even_for_small_talk(patch_llm):
    agent = FakeAgent({"Ты — чат-агент": [ai("Привет!")]})
    patch_llm(agent, FakeStructured(intent("simple", new=False)))

    invoke(build_graph(), "привет")

    convo = "\n".join(message.content for message in agent.convos[0])
    assert "Полная актуальная проектная документация" in convo
    assert "# Файл: README.md" in convo
