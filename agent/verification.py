"""Статус проверки отчёта и его публикация в Markdown, state и чате."""

from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.types import Overwrite

from artifact_store import artifact_url
from artifacts import VERIFICATION_HEADING, create_report_artifacts, strip_verification
from progress import done_progress

_LABELS = {
    "consistent_with_evidence": "Текст согласован с имеющимися Evidence и прошёл детерминированные проверки.",
    "unverified": "Текст отчёта не проверен LLM-критиком: critic_unavailable.",
    "issues_open": "Не все замечания критика устранены.",
    "update_failed": "Reporter не сохранил исправленную версию; прежние замечания остаются открытыми.",
}
_PROGRESS = {
    "consistent_with_evidence": "Готово",
    "unverified": "Готово без проверки критика",
    "issues_open": "Готово с неустранёнными замечаниями критика",
    "update_failed": "Не удалось исправить отчёт",
}
_CLAIM_LABELS = {
    "confirmed_in_archive": "Подтверждено в архиве",
    "probable": "Вероятно / ограничено Evidence",
    "disputed": "Спорно",
    "not_confirmed": "Не подтверждено",
}


def _independent_verification(state: dict) -> dict:
    value = state.get("independent_verification") or {}
    return {
        "status": value.get("status") or "not_performed",
        "method": value.get("method") or "",
        "source": value.get("source") or "",
        "checked_at": value.get("checked_at") or "",
    }


def _claim_statuses(claims: list[dict], evidence: list[dict], issues: list[dict]) -> list[dict]:
    records = {item.get("id"): item for item in evidence}
    rows = []
    for claim in claims:
        text = str(claim.get("text") or "")
        ids = list(claim.get("evidence_ids") or [])
        linked_issues = [
            issue for issue in issues
            if str(issue.get("claim") or "") == text or set(issue.get("evidence_ids") or []).intersection(ids)
        ]
        if not ids or any(item not in records for item in ids):
            status = "not_confirmed"
        elif linked_issues or any(records[item].get("status") == "error" for item in ids):
            status = "disputed"
        elif any(records[item].get("status") == "partial" for item in ids):
            status = "probable"
        else:
            status = "confirmed_in_archive"
        rows.append({
            "claim": text,
            "status": status,
            "label": _CLAIM_LABELS[status],
            "evidence_ids": ids,
        })
    return rows


def verification_block(
    status: str,
    issues: list[dict],
    detail: str = "",
    independent: dict | None = None,
    claim_statuses: list[dict] | None = None,
) -> str:
    independent = independent or {"status": "not_performed", "method": "", "source": "", "checked_at": ""}
    claim_statuses = claim_statuses or []
    lines = [
        VERIFICATION_HEADING,
        f"- **Статус:** {_LABELS[status]}",
        "- **Фактические данные:** `queries.md`, `evidence.jsonl` и `events.csv` содержат результаты вызовов инструментов, их аргументы и окна. Это не самостоятельное подтверждение первичной системы; ограничения `partial/error` остаются в силе. `has_more=true` подтверждает продолжение, `has_more=null` с `pagination_error` — неизвестную полноту.",
    ]
    independence = independent["status"]
    if independence == "not_performed":
        lines.append("- **Независимая проверка первичных данных:** не выполнялась.")
    else:
        lines.append(f"- **Независимая проверка первичных данных:** {independence}.")
        if independent.get("method"):
            lines.append(f"  - Метод: {independent['method']}")
        if independent.get("source"):
            lines.append(f"  - Источник: {independent['source']}")
        if independent.get("checked_at"):
            lines.append(f"  - Время проверки: {independent['checked_at']}")
    if detail:
        lines.append(f"- **Что не получилось:** {detail}")
    if status != "consistent_with_evidence":
        lines.append("- **Текстовые выводы:** не считать полностью проверенными; особенно утверждения, связанные с открытыми замечаниями ниже.")
    else:
        lines.append("- **Текстовые выводы:** согласованы с Evidence в этом пакете; это не доказательство событий в первичной системе.")
    if issues:
        lines.append("- **Открытые замечания:**")
        for issue in issues:
            claim = str(issue.get("claim") or "Утверждение не указано")
            reason = str(issue.get("reason") or "Причина не указана")
            lines.append(f"  - {claim} — {reason}")
    if claim_statuses:
        lines.extend([
            "",
            "| Утверждение | Статус |\n|---|---|",
        ])
        for row in claim_statuses:
            text = row["claim"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {text} | {row['label']} |")
    return "\n".join(lines)


def finalize_verification(state: dict, update: dict, status: str, detail: str = "") -> dict:
    current = update.get("report_result") or state.get("report_result") or {}
    critic = update.get("critic") or state.get("critic") or {}
    issues = list(critic.get("issues") or [])
    independent = _independent_verification(state)
    claim_statuses = _claim_statuses(current.get("claims") or [], state.get("evidence") or [], issues)
    verification = {
        "status": status,
        "detail": detail,
        "issues": issues,
        "independent_verification": independent,
        "claims": claim_statuses,
    }
    block = verification_block(status, issues, detail, independent, claim_statuses)
    answer = strip_verification(str(current.get("answer") or "")).rstrip()
    current["answer"] = answer if status == "consistent_with_evidence" else answer + "\n\n---\n\n" + block + "\n"
    current["verification"] = verification
    update["report_result"] = current

    previous = update.get("last_report") or state.get("last_report") or {}
    artifacts = []
    if previous:
        last_report, artifacts = create_report_artifacts(
            previous.get("title") or "Отчёт расследования",
            previous.get("markdown") or "",
            state,
            previous=previous,
            verification_block=block,
            verification=verification,
        )
        last_report["revision"] = int(previous.get("revision") or 1)
        last_report["verification"] = verification
        update["last_report"] = last_report
        update["artifacts"] = Overwrite(artifacts)

    update["progress"] = done_progress(_PROGRESS[status])
    final = AIMessage(content=current["answer"], name="reporter")
    if artifacts:
        final.artifacts = [artifact_url(item) for item in artifacts]
    previous_final = next(
        (message for message in reversed(state.get("messages") or [])
         if getattr(message, "name", None) == "reporter"),
        None,
    )
    update["messages"] = [
        *([RemoveMessage(id=previous_final.id)] if previous_final is not None else []),
        final,
    ]
    return update
