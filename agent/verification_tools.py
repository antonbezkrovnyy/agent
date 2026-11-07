import asyncio
import datetime
import json
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from contracts import json_dumps
from provenance import source_metadata, verification_summary


def _text(result: Any) -> str:
    text = getattr(result, "text", None)
    if text:
        return text
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return "\n".join(item.get("text", "") for item in content)
    return json_dumps(content, ensure_ascii=False)


def _comparable(value):
    if isinstance(value, dict):
        return {
            key: _comparable(item) for key, item in value.items()
            if key not in ("duration_s", "duration_ms", "run_id", "attempt", "max_attempts")
        }
    if isinstance(value, list):
        return [_comparable(item) for item in value]
    return value


@tool
async def verification_recheck(
    evidence_ids: list[str],
    runtime: ToolRuntime = None,
) -> Command:
    """Повторить до пяти MCP/analysis запросов; это same-source, не независимый источник."""
    from tools import _MCP_TOOLS_BY_NAME, build_mcp_evidence

    state = runtime.state if runtime else {}
    call_id = runtime.tool_call_id if runtime else "verification"
    originals = {item.get("id"): item for item in state.get("evidence") or []}
    checks: list[dict] = []
    fresh_evidence: list[dict] = []
    fresh_analysis_results: list[dict] = []
    fresh_artifacts: list[dict] = []
    for index, evidence_id in enumerate(list(dict.fromkeys(evidence_ids))[:5], start=1):
        checked_at = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
        original = originals.get(evidence_id)
        check = {
            "id": f"{call_id}-{index}",
            "source_evidence_id": evidence_id,
            "recheck_evidence_id": "",
            "method": "same_source_replay",
            "independent": False,
            "checked_at": checked_at,
            "status": "error",
            "match": False,
        }
        if original is None:
            check["error"] = "Исходный Evidence не найден"
            checks.append(check)
            continue
        name = original.get("tool") or ""
        args = dict(original.get("arguments") or {})
        recheck_id = f"{call_id}-{index}"
        if name == "analysis_sql":
            from analysis import analysis_sql

            dataset_id = args.get("dataset_id") or (original.get("result") or {}).get("dataset_id")
            dataset = next((
                item for item in state.get("analysis_datasets") or [] if item.get("id") == dataset_id
            ), None)
            if dataset is None:
                check["error"] = f"Датасет {dataset_id} для повтора analysis_sql не найден"
                checks.append(check)
                continue
            nested_state = {**state, "analysis_datasets": [dataset], "analysis_results": []}
            nested_update = await analysis_sql.coroutine(
                dataset_id=dataset_id,
                query=args.get("query") or (original.get("result") or {}).get("query"),
                max_rows=args.get("max_rows"),
                runtime=SimpleNamespace(state=nested_state, tool_call_id=recheck_id),
            )
            record = nested_update.update["evidence"][0]
            data = record.get("result") or {}
            fresh_analysis_results.extend(nested_update.update.get("analysis_results") or [])
            fresh_artifacts.extend(nested_update.update.get("artifacts") or [])
        else:
            tool = _MCP_TOOLS_BY_NAME.get(name)
            if tool is None or name in ("server_time", "source_info"):
                check["error"] = f"Повтор {name} не поддерживает содержательной проверки"
                checks.append(check)
                continue
            try:
                raw = await asyncio.wait_for(tool.ainvoke(args), timeout=120)
                data = json.loads(_text(raw))
                if not isinstance(data, dict):
                    data = {"result": data}
            except Exception as exc:
                data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            record = build_mcp_evidence(
                name,
                args,
                data,
                recheck_id,
                (state.get("context") or {}).get("id"),
                state.get("mcp_reuse_run_id") or None,
                call_id,
            )
        fresh_evidence.append(record)
        same = _comparable(original.get("result")) == _comparable(data)
        if original.get("status") == "ok" and record.get("status") == "ok" and same:
            status = "matched"
        elif record.get("status") == "error":
            status = "error"
        else:
            status = "changed"
        check.update({
            "recheck_evidence_id": recheck_id,
            "status": status,
            "match": status == "matched",
            "original_status": original.get("status"),
            "recheck_status": record.get("status"),
            "error": data.get("error"),
        })
        checks.append(check)

    all_checks = [*(state.get("verification_checks") or []), *checks]
    evidence = [*(state.get("evidence") or []), *fresh_evidence]
    summary = verification_summary(all_checks, source_metadata({**state, "evidence": evidence}).get("endpoint") or "")
    result = {
        "ok": all(item["status"] != "error" for item in checks),
        "complete": all(item["status"] == "matched" for item in checks),
        "independent": False,
        "checks": checks,
        "summary": summary,
    }
    return Command(update={
        "messages": [ToolMessage(
            content=json_dumps(result, ensure_ascii=False),
            tool_call_id=call_id,
            name="verification_recheck",
            **({"status": "error"} if not result["ok"] else {}),
        )],
        "evidence": fresh_evidence,
        "analysis_results": fresh_analysis_results,
        "artifacts": fresh_artifacts,
        "verification_checks": checks,
        "independent_verification": summary,
    })
