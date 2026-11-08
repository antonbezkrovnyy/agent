"""Структурированные критики доказательств и финального отчёта."""

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

import llm as llm_mod
from execution_audit import audit_messages
from control_overrides import with_prompt_override
from config import Settings
from contracts import CriticIssue
from evidence import claim_evidence_ids
from observability import trace_config
from progress import agent_progress, done_progress, simple_progress
from prompts import (
    compose,
    context_block,
    evidence_block,
    inv_summary_block,
    last_report_block,
    structured_block,
    system_prompt,
)
from state import State

settings = Settings()

CRITIC_MSG_LIMIT = settings.critic_message_limit
CRITIC_DIALOG_LIMIT = settings.critic_dialog_limit


def _findings_block(state: State) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Записи расследования: отсутствуют."
    return structured_block("Записи расследования", {"items": findings})


def _intent_block(state: State) -> str:
    intent = state.get("intent") or {}
    if not intent.get("label"):
        return "Интент: не определён."
    line = f"Интент текущего запроса: {intent['label']}"
    if intent.get("rationale"):
        line += f" — {intent['rationale']}"
    return line


def _user_requests_block(state: State) -> str:
    users = [m for m in state.get("messages") or [] if isinstance(m, HumanMessage)]
    if not users:
        return "Сообщений пользователя нет."
    lines = []
    for i, msg in enumerate(users, start=1):
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"{i}. {text[:CRITIC_MSG_LIMIT]}")
    return "Сообщения пользователя (в хронологии):\n" + "\n".join(lines)[:CRITIC_DIALOG_LIMIT]


def _critic_evidence_ids(state: State, prompt: str) -> list[str]:
    records = state.get("evidence") or []
    ids = claim_evidence_ids(state)
    if prompt == "critic_report":
        report_text = "\n".join(_report_texts(state))
        ids.extend(
            record["id"] for record in records
            if record.get("id") and record["id"] in report_text
        )
    if ids:
        return list(dict.fromkeys(ids))
    return [record["id"] for record in records[-20:] if record.get("id")]


def _critic_messages(state: State, prompt: str, extra_block: str) -> list[BaseMessage]:
    evidence_ids = _critic_evidence_ids(state, prompt)
    parts = [
        compose("base", prompt),
        _intent_block(state),
        _user_requests_block(state),
        context_block(state),
        structured_block("Scope и текущий work item", {
            "scope": state.get("investigation_scope") or {},
            "item": state.get("active_work_item") or {},
        }),
        evidence_block(state, full=True, evidence_ids=evidence_ids),
        _findings_block(state),
        structured_block("Журнал гипотез", {"items": state.get("hypotheses") or []}),
        extra_block,
    ]
    return [
        SystemMessage(content=system_prompt(*parts)),
        HumanMessage(content="Проверь работу по критериям из системного промпта."),
    ]


def _report_texts(state: State) -> list[str]:
    texts = []
    for result_key, text_key in (("inv_result", "summary"), ("report_result", "answer")):
        result = state.get(result_key) or {}
        texts.append(str(result.get(text_key) or ""))
        texts.extend(str(claim.get("text") or "") for claim in result.get("claims") or [])
    texts.append(str((state.get("last_report") or {}).get("markdown") or ""))
    return texts


def _arithmetic_issues(state: State) -> list[CriticIssue]:
    import re

    number = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)"
    pattern = re.compile(
        rf"(?<!\d)({number}(?:\s*\+\s*{number})+)\s*=\s*(?:\*\*)?\s*[~≈]?\s*({number})"
    )
    issues = []
    for text in _report_texts(state):
        for match in pattern.finditer(text):
            terms = [
                int(term.replace(" ", "").replace("\u00a0", ""))
                for term in match.group(1).split("+")
            ]
            actual = int(match.group(2).replace(" ", "").replace("\u00a0", ""))
            expected = sum(terms)
            before = text[:match.start()].rstrip()
            after = text[match.end():].lstrip()
            if before.endswith("+") or after.startswith("+"):
                continue
            if actual != expected:
                expression = " + ".join(str(term) for term in terms)
                issues.append(CriticIssue(
                    claim=f"{expression} = {actual}",
                    reason=f"Явная сумма не сходится: {expression} = {expected}, в отчёте {actual}.",
                    suggested_action="rework_report",
                ))
    return issues


def _timeline_issues(state: State) -> list[CriticIssue]:
    issues = []
    for record in state.get("evidence") or []:
        if record.get("tool") != "timeline":
            continue
        timeline = (record.get("result") or {}).get("timeline") or {}
        buckets = timeline.get("buckets") or []
        total = timeline.get("total")
        if total is None:
            continue
        computed = sum(int(bucket.get("value") or 0) for bucket in buckets)
        if int(total) != computed:
            issues.append(CriticIssue(
                evidence_ids=[record["id"]],
                reason=f"Timeline.total={total} не совпадает с суммой бакетов {computed}.",
                suggested_action="rework_report",
            ))
    return issues


def _markdown_reference_issues(state: State) -> list[CriticIssue]:
    import re

    records = {item.get("id") for item in state.get("evidence") or []}
    pattern = re.compile(r"\bcall-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b(?!-)")
    texts = [
        str((state.get("report_result") or {}).get("answer") or ""),
        str((state.get("last_report") or {}).get("markdown") or ""),
    ]
    referenced = set(pattern.findall("\n".join(texts)))
    unknown = sorted(referenced - records)
    if not unknown:
        return []
    return [CriticIssue(
        claim="Markdown содержит ссылки на отсутствующие Evidence ID.",
        evidence_ids=unknown,
        reason="Evidence ID из отчёта отсутствует в state расследования.",
        suggested_action="rework_report",
    )]


def _iter_claims(state: State):
    records = {item.get("id"): item for item in state.get("evidence") or []}
    for result_key in ("inv_result", "report_result"):
        for claim in (state.get(result_key) or {}).get("claims") or []:
            yield claim, records


def _partial_sample_issues(state: State) -> list[CriticIssue]:
    markers = ("выборк", "partial", "неполн", "частич", "пример", "ограничен", "не доказан")
    issues = []
    for claim, records in _iter_claims(state):
        text = str(claim.get("text") or "")
        partial_ids = [
            item for item in claim.get("evidence_ids") or []
            if item in records
            and records[item].get("tool") == "search_events"
            and records[item].get("status") == "partial"
        ]
        if partial_ids and not any(marker in text.lower() for marker in markers):
            issues.append(CriticIssue(
                claim=text,
                evidence_ids=partial_ids,
                reason="Claim по partial-выборке search_events не содержит локальной оговорки о выборке.",
                suggested_action="rework_report",
            ))
    return issues


def _completeness_issues(state: State) -> list[CriticIssue]:
    categorical = ("ровно", "все ", "полный", "полная", "полные", "каждое", "каждый")
    cautious = ("не полн", "не выполнялся", "не доказан", "ограничен", "частич", "partial")
    issues = []
    for claim, records in _iter_claims(state):
        text = str(claim.get("text") or "")
        lowered = text.lower()
        if not any(word in lowered for word in categorical) or any(word in lowered for word in cautious):
            continue
        incomplete = []
        for item in claim.get("evidence_ids") or []:
            if item not in records or records[item].get("tool") != "search_events":
                continue
            result = records[item].get("result") or {}
            if result.get("has_more") is True or result.get("complete") is False:
                incomplete.append((item, "has_more=true"))
            elif result.get("has_more") is None and result.get("pagination_error"):
                incomplete.append((item, "has_more=null"))
        if incomplete:
            unknown = any(kind == "has_more=null" for _, kind in incomplete)
            reason = (
                "has_more=null с pagination_error означает неизвестную полноту, а не доказанное продолжение или завершение."
                if unknown else
                "has_more=true подтверждает следующую страницу и не позволяет называть выборку полной, даже при совпадении count."
            )
            issues.append(CriticIssue(
                claim=text,
                evidence_ids=[item for item, _ in incomplete],
                reason=reason,
                suggested_action="rework_report",
            ))
    return issues


def _distribution_issues(state: State) -> list[CriticIssue]:
    import re

    issues = []
    for claim, records in _iter_claims(state):
        text = str(claim.get("text") or "")
        if "%" not in text or not any(word in text.lower() for word in ("распредел", "доля", "доли")):
            continue
        for evidence_id in claim.get("evidence_ids") or []:
            record = records.get(evidence_id) or {}
            if record.get("tool") not in ("top_values", "top_values_batch"):
                continue
            result = record.get("result") or {}
            entries = result.get("fields") if isinstance(result.get("fields"), list) else [result]
            for entry in entries:
                field = str(entry.get("field") or "")
                has_null = any(
                    item.get("value") is None and (item.get("count") or 0) > 0
                    for item in entry.get("top") or []
                )
                if not field or not has_null:
                    continue
                for match in re.finditer(re.escape(field), text, re.IGNORECASE):
                    local = text[max(0, match.start() - 30):match.end() + 220].lower()
                    if not any(word in local for word in ("null", "остат", "прочие", "прочих")):
                        issues.append(CriticIssue(
                            claim=text,
                            evidence_ids=[evidence_id],
                            reason=f"Распределение {field} опускает null-группу или остаток из Evidence.",
                            suggested_action="rework_report",
                        ))
                    break
    return issues


def _artifact_claim_issues(state: State) -> list[CriticIssue]:
    import re

    package = (state.get("last_report") or {}).get("package_filename")
    if not package:
        return []
    negative = re.compile(
        r"(?:zip[^\n.]{0,120}(?:не формируется|не создан|не удалось создать|отсутствует|нет средства)|"
        r"(?:не формируется|не создан|не удалось создать|нет средства)[^\n.]{0,120}zip)",
        re.IGNORECASE,
    )
    if not any(negative.search(text) for text in _report_texts(state)):
        return []
    return [CriticIssue(
        claim="Текст отчёта противоречит наличию ZIP-пакета.",
        reason=f"В state создан пакет {package}; нельзя утверждать, что ZIP отсутствует или не формируется.",
        suggested_action="rework_report",
    )]


def _aggregate_support_issues(state: State) -> list[CriticIssue]:
    aggregate_tools = {
        "count_events", "timeline", "top_values", "top_values_batch",
        "aggregate_events", "analysis_sql",
    }
    issues = []
    for claim, records in _iter_claims(state):
        if claim.get("evidence_class") != "агрегатный":
            continue
        ids = [item for item in claim.get("evidence_ids") or [] if item in records]
        if not ids or any(records[item].get("tool") in aggregate_tools for item in ids):
            continue
        issues.append(CriticIssue(
            claim=claim.get("text") or "",
            evidence_ids=ids,
            reason=(
                "Агрегатный claim обобщает наблюдения без агрегатного Evidence. "
                "Получи count/aggregate за то же окно и фильтр либо понизь вывод до наблюдения по выборке."
            ),
            suggested_action="rework_data",
        ))
    return issues


def _evidence_strength_issues(state: State) -> list[CriticIssue]:
    issues = []
    for claim, records in _iter_claims(state):
        if claim.get("evidence_class") != "полный":
            continue
        weak = [
            evidence_id for evidence_id in claim.get("evidence_ids") or []
            if evidence_id not in records
            or records[evidence_id].get("status") != "ok"
            or records[evidence_id].get("tool") == "field_fill_rates"
        ]
        if weak:
            issues.append(CriticIssue(
                claim=claim.get("text") or "",
                evidence_ids=weak,
                reason="Класс «полный» недопустим по partial/error или прокси field_fill_rates; понизьте класс evidence и силу формулировки.",
                suggested_action="rework_report",
            ))
    return issues


def _deterministic_issues(state: State) -> list[dict]:
    records = {item.get("id"): item for item in state.get("evidence") or []}
    issues: list[CriticIssue] = []
    for result_key, text_key in (("inv_result", "summary"), ("report_result", "answer")):
        text = str((state.get(result_key) or {}).get(text_key) or "")
        if text.strip().startswith("Tool call limit reached:"):
            issues.append(CriticIssue(
                claim=text,
                reason="Служебное сообщение ToolCallLimitMiddleware выдано вместо результата этапа.",
                suggested_action="rework_report" if result_key == "report_result" else "rework_data",
            ))
    for result_key, text_key in (("inv_result", "summary"), ("report_result", "answer")):
        result = state.get(result_key) or {}
        claims = result.get("claims") or []
        text = str(result.get(text_key) or "").strip()
        if records and text and not claims and result.get("status") not in ("partial", "failed"):
            issues.append(CriticIssue(
                claim=text[:500],
                reason="Ответ по собранным Evidence не содержит structured claims с явными ссылками.",
                suggested_action="rework_report" if result_key == "report_result" else "rework_data",
            ))
        for claim in claims:
            evidence_ids = claim.get("evidence_ids") or []
            if not evidence_ids:
                issues.append(CriticIssue(
                    claim=claim.get("text") or "",
                    reason="Фактический claim не содержит Evidence ID.",
                    suggested_action="rework_report",
                ))
                continue
            unknown = [item for item in evidence_ids if item not in records]
            if unknown:
                issues.append(CriticIssue(
                    claim=claim.get("text") or "",
                    evidence_ids=unknown,
                    reason="Утверждение ссылается на отсутствующие Evidence ID.",
                    suggested_action="rework_report",
                ))
    for hypothesis in state.get("hypotheses") or []:
        evidence_ids = hypothesis.get("evidence_ids") or []
        unknown = [item for item in evidence_ids if item not in records]
        if unknown or (hypothesis.get("status") != "open" and not evidence_ids):
            issues.append(CriticIssue(
                claim=hypothesis.get("text") or "",
                evidence_ids=unknown,
                reason="Статус гипотезы не подтверждён существующими Evidence.",
                suggested_action="rework_data",
            ))
    used = [records[item] for item in claim_evidence_ids(state) if item in records]
    for record in used:
        window = record.get("window") or {}
        bounds = [window.get("from"), window.get("to")]
        if any(bounds) and not all(bounds):
            issues.append(CriticIssue(
                evidence_ids=[record["id"]],
                reason="Evidence содержит неполное временное окно.",
                suggested_action="rework_data",
            ))
    limitations = [
        item
        for key in ("inv_result", "report_result")
        for item in (state.get(key) or {}).get("limitations") or []
    ]
    degraded = [item for item in used if item.get("status") != "ok"]
    if degraded and not limitations:
        issues.append(CriticIssue(
            evidence_ids=[item["id"] for item in degraded],
            reason="Partial/error Evidence использованы без явных limitations.",
            suggested_action="rework_report",
        ))
    issues.extend(_markdown_reference_issues(state))
    issues.extend(_arithmetic_issues(state))
    issues.extend(_timeline_issues(state))
    issues.extend(_partial_sample_issues(state))
    issues.extend(_completeness_issues(state))
    issues.extend(_distribution_issues(state))
    issues.extend(_aggregate_support_issues(state))
    issues.extend(_evidence_strength_issues(state))
    issues.extend(_artifact_claim_issues(state))
    unique = {}
    for issue in issues:
        key = (issue.claim, tuple(sorted(issue.evidence_ids)), issue.reason)
        unique.setdefault(key, issue.model_dump())
    return list(unique.values())


def _critic_run(state: State, prompt: str, model: Any, allowed_verdicts: tuple, extra_block: str) -> dict:
    """Детерминированные проверки и один LLM-ход критика."""
    verdict, feedback = "critic_unavailable", "Критик не вернул корректный вердикт."
    deterministic = _deterministic_issues(state)
    issues = list(deterministic)
    messages = with_prompt_override(state, prompt, _critic_messages(state, prompt, extra_block))
    audit_messages(prompt, state, messages)
    try:
        from deadline import run_expired

        if run_expired(state):
            raise TimeoutError("Investigation run deadline exceeded")
        result = model.invoke(
            messages,
            config=trace_config(prompt, state),
        )
        if result is not None and result.verdict in allowed_verdicts:
            verdict = result.verdict
            feedback = getattr(result, "feedback", "") or ""
            for issue in getattr(result, "issues", []) or []:
                payload = issue.model_dump() if hasattr(issue, "model_dump") else dict(issue)
                if payload not in issues:
                    issues.append(payload)
    except Exception as exc:
        feedback = f"Критик недоступен: {type(exc).__name__}."
    if deterministic:
        if "rework_data" in allowed_verdicts and any(
            item["suggested_action"] == "rework_data" for item in deterministic
        ):
            verdict = "rework_data"
        elif "rework_report" in allowed_verdicts:
            verdict = "rework_report"
        elif "rework_data" in allowed_verdicts:
            verdict = "rework_data"
        reasons = " ".join(dict.fromkeys(item["reason"] for item in deterministic))
        if not feedback or feedback.startswith("Критик недоступен"):
            feedback = f"{feedback} {reasons}".strip()
    if verdict == "ok" and issues:
        if "rework_data" in allowed_verdicts and any(
            item.get("suggested_action") == "rework_data" for item in issues
        ):
            verdict = "rework_data"
        elif "rework_report" in allowed_verdicts:
            verdict = "rework_report"
        elif "rework_data" in allowed_verdicts:
            verdict = "rework_data"
        if not feedback:
            feedback = "Вердикт ok отклонён: критик вернул незакрытые issues."
    rework = state.get("rework", 0) + (1 if verdict.startswith("rework_") else 0)
    return {"critic": {"verdict": verdict, "feedback": feedback, "issues": issues}, "rework": rework}


def node_critic_evidence(state: State) -> dict:
    """Критик после инвестигейтора или отдельного work item."""
    active_item = state.get("active_work_item") or {}
    if not active_item.get("id"):
        active_item = next(
            (item for item in state.get("work_items") or [] if item.get("status") == "critic_review"),
            {},
        )
    if active_item.get("id"):
        item_result = next(
            (item.get("result") or {} for item in state.get("work_items") or []
             if item.get("id") == active_item["id"] and item.get("result")),
            {},
        ) or active_item.get("result") or {}
        item_state = {
            **state,
            "inv_result": item_result,
            "inv_summary": item_result.get("summary") or "",
        }
        update = _critic_run(
            item_state,
            "critic_evidence",
            llm_mod.critic_evidence_llm,
            ("ok", "rework_data"),
            structured_block("Результат work item", item_result),
        )
        verdict = update["critic"]["verdict"]
        retry = verdict == "rework_data" and update["rework"] <= settings.max_rework
        status = "pending" if retry else ("done" if verdict in ("ok", "critic_unavailable") else "failed")
        return {
            "work_items": [{
                "id": active_item["id"],
                "status": status,
                "critic": update["critic"],
            }],
            "rework": update["rework"],
            "progress": simple_progress("planner", f"Проверена часть {active_item['id']}"),
        }
    extra = structured_block("Результат инвестигейтора", state.get("inv_result"))
    update = _critic_run(
        state,
        "critic_evidence",
        llm_mod.critic_evidence_llm,
        ("ok", "rework_data"),
        extra or inv_summary_block(state.get("inv_summary") or "(резюме пока нет)"),
    )
    if update["critic"]["verdict"] == "rework_data" and update["rework"] <= settings.max_rework:
        update["progress"] = agent_progress(state, "investigator", "Критик запросил дополнительные данные", "investigator")
        update["suggestions"] = []
    else:
        update["progress"] = agent_progress(state, "reporter", "Формирую ответ", "reporter")
    return update


def node_critic_report(state: State) -> dict:
    """Критик после репортёра: проверяет отчёт и выбирает доработку."""
    if state.get("last_report"):
        extra = last_report_block(state["last_report"])
    else:
        extra = structured_block("Финальный ответ репортёра", state.get("report_result"))
    update = _critic_run(
        state,
        "critic_report",
        llm_mod.critic_report_llm,
        ("ok", "rework_report", "rework_data"),
        extra or "(ответа пока нет)",
    )
    verdict = update["critic"]["verdict"]
    if verdict in ("rework_report", "rework_data") and update["rework"] <= settings.max_rework:
        agent = "reporter" if verdict == "rework_report" else "investigator"
        text = "Дорабатываю ответ" if agent == "reporter" else "Собираю дополнительные данные"
        update["progress"] = agent_progress(state, agent, text, agent)
        update["suggestions"] = []
        if verdict == "rework_data":
            update["replan_requested"] = True
    else:
        from verification import finalize_verification

        status = "consistent_with_evidence" if verdict == "ok" else "unverified" if verdict == "critic_unavailable" else "issues_open"
        if status == "issues_open":
            current = update.get("report_result") or state.get("report_result") or {}
            note = f"Лимит доработок исчерпан; осталось замечание критика: {update['critic']['feedback']}"
            update["report_result"] = {
                **current,
                "limitations": [*(current.get("limitations") or []), note],
            }
        update = finalize_verification(state, update, status)
    return update
