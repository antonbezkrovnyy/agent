"""Case Store и Context Memory поверх отдельной предметной схемы Postgres."""

import json
from datetime import UTC, datetime, timedelta

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import OpenAIEmbeddings
from langgraph.types import Overwrite

import llm as llm_mod
from artifact_store import artifact_url
from artifacts import create_report_artifacts
from config import Settings
from evidence import claim_evidence_ids, evidence_overview_block, investigation_chain
from memory_models import (
    CaseRecord,
    ContextFact,
    ContextFactDraft,
    ContextMemoryConsolidation,
    ContextMemoryOperation,
    MemoryOperationRecord,
)
from memory_store import MemoryStore, safe_json, safe_text
from observability import trace_config
from progress import agent_progress
from prompts import compose, system_prompt
from reporting import memory_audit_markdown
from state import State


class InvestigationMemory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = MemoryStore(
            settings.agent_memory_database_url,
            settings.agent_memory_embedding_dimensions,
        )
        self._embeddings = None

    @property
    def enabled(self) -> bool:
        return self.store.enabled

    def _get_embeddings(self):
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(
                model=self.settings.agent_memory_embedding_model,
                base_url=self.settings.agent_memory_embedding_base_url or self.settings.llm_base_url,
                api_key=self.settings.agent_memory_embedding_token or self.settings.llm_token,
            )
        return self._embeddings

    def _embed(self, text: str) -> list[float]:
        return self._get_embeddings().embed_query(safe_text(text))

    def save_case(self, user_id: str, case: dict) -> None:
        record = CaseRecord.model_validate(case)
        text = "\n".join(filter(None, [
            record.task,
            record.signature,
            record.summary,
            " ".join(str(item.get("value") or "") for item in record.entities),
        ]))
        try:
            embedding = self._embed(text) if text.strip() else None
        except Exception:
            embedding = None
        self.store.save_case(record, embedding)

    def get_case(self, user_id: str, investigation_id: str) -> dict | None:
        return self.store.get_case(user_id, investigation_id)

    def recent_cases(self, user_id: str, limit: int) -> list[dict]:
        return self.store.recent_cases(user_id, limit)

    def search_cases(self, user_id: str, query: str, limit: int, exclude_id: str = "") -> list[dict]:
        if not self.enabled or not query.strip():
            return []
        return self.store.search_cases(
            user_id,
            self._embed(query),
            limit,
            self.settings.agent_memory_similarity_threshold,
            exclude_id,
        )

    def search_facts(self, scope: str, query: str, limit: int) -> list[dict]:
        if not self.enabled or not query.strip():
            return []
        return self.store.search_facts(
            scope,
            self._embed(query),
            limit,
            self.settings.agent_memory_similarity_threshold,
        )

    def recent_facts(self, scope: str, limit: int) -> list[dict]:
        if not self.enabled:
            return []
        return self.store.recent_facts(scope, limit)

    def find_fact_by_statement(self, scope: str, statement: str) -> ContextFact | None:
        return self.store.find_fact_by_statement(scope, statement)

    def put_fact(self, fact: ContextFact) -> None:
        self.store.put_fact(fact, self._embed(fact.statement))

    def supersede_fact(self, target: ContextFact, fact: ContextFact) -> None:
        self.store.supersede_fact(target, fact, self._embed(fact.statement))


settings = Settings()
memory = InvestigationMemory(settings)


def _identity(config: RunnableConfig) -> tuple[str, str]:
    configurable = (config.get("configurable") or {}) if isinstance(config, dict) else {}
    return str(configurable.get("user_id") or "anonymous"), str(configurable.get("thread_id") or "")


def _case_from_state(state: State, config: RunnableConfig) -> dict:
    state = safe_json(state)
    user_id, thread_id = _identity(config)
    chain = investigation_chain(state)
    report = state.get("report_result") or {}
    entities = list({
        f"{item.get('type', 'custom')}:{str(item.get('value')).strip().lower()}": {
            "type": item.get("type") or "custom",
            "value": str(item.get("value")).strip(),
        }
        for item in state.get("anchors") or []
        if item.get("value")
    }.values())
    case = CaseRecord(
        investigation_id=chain["investigation_id"],
        user_id=user_id,
        thread_id=thread_id,
        task=chain["task"],
        summary=report.get("answer") or state.get("inv_summary") or "",
        signature=state.get("inv_summary") or (state.get("scout_result") or {}).get("answer_candidate") or "",
        entities=entities,
        updated_at=datetime.now(UTC),
        context=state.get("context") or {},
        work_mode=state.get("work_mode") or "quick",
        depth_history=list(state.get("depth_history") or []),
        evidence=list(state.get("evidence") or []),
        findings=list(state.get("findings") or []),
        hypotheses=list(state.get("hypotheses") or []),
        skill_runs=list(state.get("skill_runs") or []),
        anchors=list(state.get("anchors") or []),
        visited_anchors=list(state.get("visited_anchors") or []),
        inv_summary=state.get("inv_summary") or "",
        inv_result=state.get("inv_result") or {},
        last_report=state.get("last_report") or {},
        memory_operations=list(state.get("memory_operations") or []),
        investigation_scope=state.get("investigation_scope") or {},
        investigation_plan=state.get("investigation_plan") or {},
        work_items=list(state.get("work_items") or []),
        run_metrics=list(state.get("run_metrics") or []),
        chain=chain,
    )
    return case.model_dump(mode="json")


def node_memory_context(state: State, config: RunnableConfig) -> dict:
    if state.get("context"):
        return {"memory_candidates": [], "memory_error": ""}
    user_id, _ = _identity(config)
    try:
        cases = memory.recent_cases(user_id, settings.agent_memory_recent_limit)
        candidates = [{
            "investigation_id": item["investigation_id"],
            "task": item.get("task") or "",
            "summary": item.get("summary") or "",
            "updated_at": item.get("updated_at") or "",
        } for item in cases]
        return {"memory_candidates": candidates, "memory_error": ""}
    except Exception as exc:
        return {"memory_candidates": [], "memory_error": f"Память расследований недоступна: {type(exc).__name__}."}


def _current_task(state: State) -> str:
    messages = [
        item.content for item in state.get("messages") or []
        if isinstance(item, HumanMessage) and isinstance(item.content, str)
    ]
    return messages[-1] if messages else ""


def memory_overview(
    state: State,
    config: RunnableConfig,
    query: str = "",
    limit: int = 5,
) -> dict:
    """Read-only снимок памяти для пользователя и внутреннего инструмента."""
    user_id, _ = _identity(config)
    limit = max(1, min(limit, 10))
    errors: list[str] = []
    try:
        facts = (
            memory.search_facts(settings.agent_memory_scope, query, limit)
            if query.strip()
            else memory.recent_facts(settings.agent_memory_scope, limit)
        )
    except Exception as exc:
        facts = []
        errors.append(f"Context Memory недоступна: {type(exc).__name__}.")
    try:
        cases = memory.recent_cases(user_id, limit)
    except Exception as exc:
        cases = []
        errors.append(f"Case Store недоступен: {type(exc).__name__}.")
    context = state.get("context") or {}
    return {
        "ok": not errors,
        "current_case": {
            "id": context.get("id"),
            "work_mode": state.get("work_mode") or "",
            "evidence_count": len(state.get("evidence") or []),
            "findings_count": len(state.get("findings") or []),
            "hypotheses_count": len(state.get("hypotheses") or []),
            "last_report_title": (state.get("last_report") or {}).get("title") or "",
        } if context else None,
        "facts": facts,
        "cases": [{
            "investigation_id": item.get("investigation_id"),
            "task": item.get("task") or "",
            "summary": item.get("summary") or "",
            "updated_at": item.get("updated_at") or "",
            "work_mode": item.get("work_mode") or "",
        } for item in cases],
        "errors": errors,
        "notice": "Факты памяти не являются свежими Evidence и перепроверяются перед использованием.",
    }


def _memory_overview_markdown(overview: dict) -> str:
    lines = ["## Что находится в моей памяти"]
    current = overview.get("current_case")
    if current:
        lines.extend([
            "\n### Текущее дело в state треда",
            f"- ID: `{current['id']}`; режим: {current['work_mode'] or 'не задан'}.",
            f"- Evidence: {current['evidence_count']}; находок: {current['findings_count']}; гипотез: {current['hypotheses_count']}.",
        ])
        if current.get("last_report_title"):
            lines.append(f"- Последний отчёт: {current['last_report_title']}.")
    else:
        lines.append("\nВ этом треде активного дела нет.")
    facts = overview.get("facts") or []
    lines.append(f"\n### Долговременные факты ({len(facts)})")
    if facts:
        for fact in facts:
            lines.append(
                f"- {fact.get('statement')} "
                f"[{fact.get('status')}, {fact.get('category')}; ID `{fact.get('id')}`; "
                f"действует до: {fact.get('valid_to') or 'без срока'}]."
            )
    else:
        lines.append("Активные факты не найдены.")
    cases = overview.get("cases") or []
    lines.append(f"\n### Последние сохранённые дела ({len(cases)})")
    if cases:
        for case in cases:
            lines.append(
                f"- **{case.get('task') or 'Без названия'}** — ID `{case.get('investigation_id')}`, "
                f"режим {case.get('work_mode') or 'не задан'}, обновлено {case.get('updated_at') or 'неизвестно'}."
            )
    else:
        lines.append("Сохранённые дела не найдены.")
    lines.extend(["", *(overview.get("errors") or []), overview.get("notice") or ""])
    return "\n".join(line for line in lines if line is not None)


def node_memory_inspect(state: State, config: RunnableConfig) -> dict:
    from progress import done_progress

    intent = state.get("intent") or {}
    overview = memory_overview(state, config, intent.get("memory_query") or "")
    return {
        "messages": [AIMessage(content=_memory_overview_markdown(overview), name="chat")],
        "progress": done_progress("Показал содержимое памяти"),
        "memory_error": " ".join(overview.get("errors") or []),
    }


def node_memory_retrieve(state: State, config: RunnableConfig) -> dict:
    if not (state.get("intent") or {}).get("needs_new_data"):
        return {"context_memories": [], "similar_cases": []}
    query = "\n".join(filter(None, [
        _current_task(state),
        state.get("inv_summary") or "",
        " ".join(str(item.get("value") or "") for item in state.get("anchors") or []),
    ]))
    errors = []
    try:
        facts = memory.search_facts(
            settings.agent_memory_scope,
            query,
            settings.agent_memory_retrieval_limit,
        )
    except Exception as exc:
        facts = []
        errors.append(f"Context Memory недоступна: {type(exc).__name__}.")
    user_id, _ = _identity(config)
    try:
        cases = memory.search_cases(
            user_id,
            query,
            settings.agent_memory_case_retrieval_limit,
            (state.get("context") or {}).get("id") or "",
        )
    except Exception as exc:
        cases = []
        errors.append(f"Поиск похожих дел недоступен: {type(exc).__name__}.")
    return {
        "context_memories": facts,
        "similar_cases": cases,
        "memory_error": " ".join(errors),
    }


def node_memory_load(state: State, config: RunnableConfig) -> dict:
    intent = dict(state.get("intent") or {})
    investigation_id = intent.get("related_investigation_id")
    user_id, _ = _identity(config)
    try:
        case = memory.get_case(user_id, investigation_id)
    except Exception as exc:
        case = None
        error = f"Память расследований недоступна: {type(exc).__name__}."
    else:
        error = ""
    if not case:
        from investigation import _new_context

        intent["related_investigation_id"] = None
        return {
            "intent": intent,
            "context": _new_context(intent),
            "memory_error": error or "Прошлый случай не найден.",
        }

    intent.update({
        "label": "continuation",
        "needs_new_data": True,
        "work_mode": "deep",
        "memory_control_required": True,
    })
    restored_context = case["context"]
    if intent.get("window_requested"):
        from investigation import _focus_context

        restored_context = _focus_context(restored_context, intent)
    return {
        "context": restored_context,
        "work_mode": "deep",
        "depth_history": list(case.get("depth_history") or []),
        "evidence": list(case.get("evidence") or []),
        "findings": list(case.get("findings") or []),
        "hypotheses": list(case.get("hypotheses") or []),
        "skill_runs": list(case.get("skill_runs") or []),
        "anchors": list(case.get("anchors") or []),
        "visited_anchors": list(case.get("visited_anchors") or []),
        "inv_summary": case.get("inv_summary") or "",
        "inv_result": case.get("inv_result") or {},
        "last_report": case.get("last_report") or {},
        "memory_operations": list(case.get("memory_operations") or []),
        "investigation_scope": case.get("investigation_scope") or {},
        "investigation_plan": case.get("investigation_plan") or {},
        "work_items": list(case.get("work_items") or []),
        "intent": intent,
        "transition": "continue",
        "memory_loaded": investigation_id,
        "memory_candidates": [],
        "context_memories": [],
        "similar_cases": [],
        "memory_error": "",
        "progress": agent_progress(state, "investigator", "Проверяю прошлый случай", "investigator"),
    }


def _existing_facts(state: State) -> list[dict]:
    query = "\n".join(filter(None, [_current_task(state), state.get("inv_summary") or ""]))
    found = memory.search_facts(
        settings.agent_memory_scope,
        query,
        settings.agent_memory_consolidation_candidates,
    )
    merged = {
        item["id"]: item
        for item in [*(state.get("context_memories") or []), *found]
        if item.get("id")
    }
    return list(merged.values())


def _consolidation_input(state: State, existing_facts: list[dict]) -> dict:
    evidence_ids = set(claim_evidence_ids(state))
    relevant_evidence = [
        item for item in state.get("evidence") or [] if item.get("id") in evidence_ids
    ]
    user_messages = [
        item.content for item in state.get("messages") or []
        if isinstance(item, HumanMessage) and isinstance(item.content, str)
    ]
    return {
        "case_id": (state.get("context") or {}).get("id"),
        "task": _current_task(state),
        "user_messages": user_messages[-10:],
        "investigation_result": state.get("inv_result") or {},
        "report_result": state.get("report_result") or {},
        "findings": state.get("findings") or [],
        "hypotheses": state.get("hypotheses") or [],
        "evidence_overview": evidence_overview_block({**state, "evidence": relevant_evidence}),
        "existing_facts": existing_facts,
    }


def _fact_from_draft(
    draft: ContextFactDraft,
    case_id: str,
    evidence_ids: set[str],
) -> ContextFactDraft:
    draft = draft.model_copy(deep=True)
    draft.source_case_id = draft.source_case_id or case_id
    draft.evidence_ids = list(dict.fromkeys(
        item for item in draft.evidence_ids if item in evidence_ids
    ))
    if draft.source_kind == "investigation" and draft.status == "confirmed" and not draft.evidence_ids:
        raise ValueError("confirmed investigation fact requires valid evidence_ids")
    return draft


def _valid_to(draft: ContextFactDraft) -> datetime | None:
    if draft.valid_to is not None:
        return draft.valid_to
    if draft.status == "observed" and settings.agent_memory_observed_ttl_hours > 0:
        return datetime.now(UTC) + timedelta(hours=settings.agent_memory_observed_ttl_hours)
    return None


def _new_fact(draft: ContextFactDraft) -> ContextFact:
    return ContextFact(
        **{
            **draft.model_dump(),
            "valid_from": draft.valid_from or datetime.now(UTC),
            "valid_to": _valid_to(draft),
        },
        scope=settings.agent_memory_scope,
        source_case_ids=[draft.source_case_id] if draft.source_case_id else [],
    )


def _updated_fact(target: ContextFact, draft: ContextFactDraft) -> ContextFact:
    now = datetime.now(UTC)
    return ContextFact(
        **{
            **target.model_dump(),
            **draft.model_dump(),
            "id": target.id,
            "scope": target.scope,
            "source_case_ids": list(dict.fromkeys([
                *(target.source_case_ids or []),
                *([draft.source_case_id] if draft.source_case_id else []),
            ])),
            "evidence_ids": list(dict.fromkeys([*target.evidence_ids, *draft.evidence_ids])),
            "valid_to": _valid_to(draft),
            "created_at": target.created_at,
            "updated_at": now,
            "superseded_by": None,
        }
    )


def _audit_record(
    operation: ContextMemoryOperation,
    status: str,
    **extra,
) -> dict:
    return MemoryOperationRecord(
        action=operation.action,
        requested_action=operation.action,
        statement=operation.fact.statement if operation.fact else "",
        target_id=operation.target_id,
        status=status,
        reason=operation.reason,
        **extra,
    ).model_dump(mode="json")


def _apply_operation(
    operation: ContextMemoryOperation,
    existing: dict[str, dict],
    case_id: str,
    evidence_ids: set[str],
) -> dict:
    if operation.action == "ignore":
        return _audit_record(operation, "ignored")
    if operation.fact is None:
        raise ValueError(f"{operation.action} requires fact")
    draft = _fact_from_draft(operation.fact, case_id, evidence_ids)
    target = existing.get(operation.target_id or "")
    if operation.action in ("update", "supersede") and target is None:
        raise ValueError("target_id is not present in existing_facts")

    actual_action = operation.action
    if operation.action == "insert":
        duplicate = max(
            existing.values(),
            key=lambda item: float(item.get("similarity") or 0),
            default=None,
        )
        if duplicate is not None and float(duplicate.get("similarity") or 0) >= settings.agent_memory_dedup_threshold:
            target = duplicate
        else:
            exact = memory.find_fact_by_statement(settings.agent_memory_scope, draft.statement)
            target = exact.model_dump(mode="json") if exact is not None else None
        if target is None:
            fact = _new_fact(draft)
            memory.put_fact(fact)
            return _audit_record(operation, "saved", fact_id=fact.id)
        actual_action = "update"

    if actual_action == "update":
        fact = _updated_fact(ContextFact.model_validate(target), draft)
        memory.put_fact(fact)
    else:
        fact = _new_fact(draft)
        memory.supersede_fact(ContextFact.model_validate(target), fact)
    record = _audit_record(operation, "saved", fact_id=fact.id)
    record["target_id"] = target.get("id")
    record["action"] = actual_action
    return record


def _consolidate(state: State) -> tuple[int, list[str], list[dict]]:
    existing_facts = _existing_facts(state)
    payload = _consolidation_input(state, existing_facts)
    messages = [
        SystemMessage(content=system_prompt(compose("memory_consolidation"))),
        HumanMessage(content=json.dumps(safe_json(payload), ensure_ascii=False, default=str)),
    ]
    response = llm_mod.context_memory_llm.invoke(
        messages,
        config=trace_config("memory_consolidation", state),
    )
    result = response if isinstance(response, ContextMemoryConsolidation) else ContextMemoryConsolidation.model_validate(safe_json(response))
    existing = {item["id"]: item for item in existing_facts}
    evidence_ids = {item.get("id") for item in state.get("evidence") or []}
    saved = 0
    errors: list[str] = []
    operations: list[dict] = []
    for operation in result.operations:
        try:
            record = _apply_operation(
                operation,
                existing,
                payload["case_id"],
                evidence_ids,
            )
            saved += int(record["status"] == "saved")
        except Exception as exc:
            errors.append(f"{operation.action}: {type(exc).__name__}")
            record = _audit_record(
                operation,
                "rejected",
                error=f"{type(exc).__name__}: {exc}",
            )
        operations.append(record)
    return saved, errors, operations


def node_memory_save(state: State, config: RunnableConfig) -> dict:
    empty = {"memory_operations": []}
    if not (state.get("context") or {}).get("id"):
        return empty
    if not (state.get("evidence") or state.get("findings") or state.get("last_report")):
        return empty
    if not getattr(memory, "enabled", True):
        return {**empty, "memory_saved": "", "memory_facts_saved": 0}
    user_id, _ = _identity(config)
    case_id = state["context"]["id"]
    try:
        memory.save_case(user_id, _case_from_state(state, config))
    except Exception as exc:
        return {
            **empty,
            "memory_saved": "",
            "memory_facts_saved": 0,
            "memory_error": f"Память расследований недоступна: {type(exc).__name__}.",
        }

    if not (state.get("inv_result") or state.get("findings") or state.get("report_result")):
        return {**empty, "memory_saved": case_id, "memory_facts_saved": 0, "memory_error": ""}
    try:
        saved, rejected, operations = _consolidate(state)
        error = f"Часть фактов Context Memory отклонена: {', '.join(rejected)}." if rejected else ""
    except Exception as exc:
        saved = 0
        operations = []
        error = f"Context Memory недоступна: {type(exc).__name__}."
    audit = memory_audit_markdown(operations, error)
    report_state = {**state, "memory_operations": operations, "memory_error": error}
    extra: dict = {}
    message = AIMessage(content=audit, name="reporter")
    if state.get("last_report"):
        report = dict(state["last_report"])
        markdown = report.get("markdown") or ""
        if audit not in markdown:
            markdown = markdown.rstrip() + "\n\n" + audit
        title = report.get("title") or "Расследование"
        report, artifacts = create_report_artifacts(title, markdown, report_state)
        report_state["last_report"] = report
        extra = {"last_report": report, "artifacts": Overwrite(artifacts)}
        message.artifacts = [artifact_url(item) for item in artifacts]
    try:
        memory.save_case(user_id, _case_from_state(report_state, config))
    except Exception as exc:
        error = " ".join(filter(None, [error, f"Журнал памяти не сохранён: {type(exc).__name__}."]))
    return {
        "memory_saved": case_id,
        "memory_facts_saved": saved,
        "memory_operations": operations,
        "memory_error": error,
        "messages": [message],
        **extra,
    }
