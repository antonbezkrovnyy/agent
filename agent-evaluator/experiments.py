"""Golden-сценарии и штатные Phoenix Datasets/Experiments.

Без флагов только валидирует локальный JSON. Живые runs выполняются лишь с --execute.
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    if not 20 <= len(scenarios) <= 30:
        raise ValueError("Нужно 20–30 сценариев")
    ids = [item["id"] for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("ID сценариев должны быть уникальны")
    for item in scenarios:
        if not isinstance(item.get("input"), dict) or not isinstance(item.get("expected"), dict):
            raise ValueError(f"Сценарий {item['id']} должен содержать input и expected")
    return scenarios


def dataset_examples(scenarios: list[dict]) -> list[dict]:
    return [{
        "input": item["input"],
        "output": item["expected"],
        "metadata": {"scenario_id": item["id"], "category": item["category"]},
    } for item in scenarios]


async def _live_task(payload: dict) -> dict:
    base_url = os.environ.get("AEGRA_API_URL", "http://localhost:2026").rstrip("/")
    thread_id = "exp-" + uuid4().hex
    prompt = payload["task"]
    if payload.get("window"):
        prompt += "\nОкно: " + json.dumps(payload["window"], ensure_ascii=False, default=str)
    for key in ("mode", "scope_mode"):
        if payload.get(key):
            prompt += f"\nПользователь явно выбрал {key}={payload[key]}."
    if payload.get("alert"):
        prompt += "\nSIEM alert payload:\n" + json.dumps(payload["alert"], ensure_ascii=False, default=str)
    timeout = int(os.environ.get("EXPERIMENT_TIMEOUT_SECONDS", "900"))
    async with httpx.AsyncClient(timeout=60) as client:
        created = await client.post(f"{base_url}/threads", json={
            "threadId": thread_id,
            "ifExists": "raise",
            "metadata": {"kind": "phoenix-experiment", "created_at": datetime.now(UTC).isoformat()},
        })
        if created.status_code not in (200, 201):
            return {"status": "error", "error": f"thread: {created.status_code}"}
        run = await client.post(f"{base_url}/threads/{thread_id}/runs", json={
            "assistant_id": os.environ.get("AEGRA_ASSISTANT", "agent"),
            "input": {"messages": [{"role": "human", "content": prompt}]},
            "config": {"configurable": {"user_id": "phoenix-experiment"}},
            "on_disconnect": "continue",
        })
        if run.status_code not in (200, 201, 202):
            return {"status": "error", "error": f"run: {run.status_code}"}
        status = "pending"
        for _ in range(timeout // 2):
            await asyncio.sleep(2)
            runs = await client.get(f"{base_url}/threads/{thread_id}/runs")
            items = runs.json() if runs.status_code == 200 else []
            if isinstance(items, dict):
                items = items.get("runs") or []
            latest = items[0] if items else {}
            status = latest.get("status") or "unknown"
            if status in ("success", "error", "failed", "cancelled", "timeout"):
                break
        else:
            status = "timeout"
            run_payload = run.json()
            run_id = run_payload.get("run_id") or run_payload.get("runId")
            if run_id:
                await client.post(f"{base_url}/threads/{thread_id}/runs/{run_id}/cancel", json={"action": "interrupt"})
        state_response = await client.get(f"{base_url}/threads/{thread_id}/state")
        values = state_response.json().get("values") or {} if state_response.status_code == 200 else {}
    evidence = values.get("evidence") or []
    claims = (values.get("report_result") or {}).get("claims") or []
    report = values.get("last_report") or {}
    report_text = "\n".join([
        str((values.get("report_result") or {}).get("answer") or ""),
        str(report.get("markdown") or ""),
    ])
    return {
        "kind": "run",
        "status": status,
        "thread_id": thread_id,
        "work_mode": values.get("work_mode") or "",
        "scope_mode": (values.get("investigation_scope") or {}).get("mode") or "",
        "plan_items": len((values.get("investigation_plan") or {}).get("items") or []),
        "evidence_ids": [item.get("id") for item in evidence],
        "claims": claims,
        "tools": list(dict.fromkeys(item.get("tool") for item in evidence if item.get("tool"))),
        "repeated_tool_calls": sum(item.get("repeated_tool_calls") or 0 for item in values.get("run_metrics") or []),
        "analysis_dataset_create_calls": sum(
            1 for item in values.get("tool_calls") or [] if item.get("tool") == "analysis_dataset_create"
        ),
        "package_sha256": report.get("package_sha256") or "",
        "manifest_sha256": report.get("manifest_sha256") or "",
        "false_zip_denial": bool(report.get("package_filename")) and "ZIP-архив не формируется" in report_text,
        "claims_missing_evidence_class": sum(1 for item in claims if not item.get("evidence_class")),
    }


def experiment_task(input: dict) -> dict:
    if "state" in input:
        return {"kind": "state", "state": input["state"], "trace": input.get("trace") or {}}
    return asyncio.run(_live_task(input))


def completed(output: dict) -> float:
    return float(output.get("kind") == "state" or output.get("status") == "success")


def scope_compliance(output: dict, expected: dict) -> float:
    if output.get("kind") == "state":
        from evaluator import deterministic_checks

        issues, _ = deterministic_checks(output["state"], output["trace"])
        has_scope_issue = any(item.category == "scope_creep" for item in issues)
        return float(has_scope_issue == ("scope_creep" in expected.get("issue_categories", [])))
    checks = [
        not expected.get("work_mode") or output.get("work_mode") == expected["work_mode"],
        not expected.get("scope_mode") or output.get("scope_mode") == expected["scope_mode"],
        output.get("plan_items", 0) <= expected.get("max_items", 5),
    ]
    return float(all(checks))


def evidence_compliance(output: dict, expected: dict) -> float:
    if output.get("kind") == "state":
        from evaluator import deterministic_checks

        issues, _ = deterministic_checks(output["state"], output["trace"])
        actual = {item.category for item in issues}
        return float(actual == set(expected.get("issue_categories") or []))
    available = set(output.get("evidence_ids") or [])
    claims_valid = all(set(claim.get("evidence_ids") or []) <= available for claim in output.get("claims") or [])
    return float(
        len(available) >= expected.get("min_evidence", 0)
        and set(expected.get("required_tools") or []) <= set(output.get("tools") or [])
        and claims_valid
    )


def efficiency(output: dict, expected: dict) -> float:
    maximum = expected.get("max_repeated_tool_calls")
    return float(maximum is None or output.get("repeated_tool_calls", 0) <= maximum)


def artifact_compliance(output: dict, expected: dict) -> float:
    if output.get("kind") == "state":
        return 1.0
    maximum = expected.get("max_analysis_dataset_create")
    checks = [
        maximum is None or output.get("analysis_dataset_create_calls", 0) <= maximum,
        not expected.get("require_detached_hashes") or bool(
            output.get("package_sha256") and output.get("manifest_sha256")
        ),
        not expected.get("forbid_false_zip_denial") or not output.get("false_zip_denial"),
        not expected.get("require_evidence_classes") or output.get("claims_missing_evidence_class", 0) == 0,
    ]
    return float(all(checks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Выполнить живые runs; без флага только validation")
    parser.add_argument("--upload-only", action="store_true", help="Создать dataset, но не запускать runs")
    parser.add_argument("--dataset-name", default="logicore-agent-golden-v1")
    parser.add_argument("--variant", default="current")
    parser.add_argument("--dry-run", type=int, default=0, help="Число examples без записи experiment; runs всё равно живые")
    args = parser.parse_args()
    scenarios = load_scenarios()
    if not args.execute and not args.upload_only:
        print(f"OK: {len(scenarios)} сценариев; сетевых действий не выполнено")
        return

    from phoenix.client import Client

    client = Client(base_url=os.environ.get("PHOENIX_URL", "http://localhost:6006"))
    try:
        dataset = client.datasets.get_dataset(dataset=args.dataset_name)
    except Exception:
        dataset = client.datasets.create_dataset(
            name=args.dataset_name,
            examples=dataset_examples(scenarios),
            dataset_description="LogiCore Agent: live и фиксированные golden-сценарии",
        )
    print(f"Dataset: {args.dataset_name}")
    if args.upload_only or not args.execute:
        return
    result = client.experiments.run_experiment(
        dataset=dataset,
        task=experiment_task,
        evaluators={
            "completed": completed,
            "scope_compliance": scope_compliance,
            "evidence_compliance": evidence_compliance,
            "efficiency": efficiency,
            "artifact_compliance": artifact_compliance,
        },
        experiment_name=f"logicore-{args.variant}",
        experiment_metadata={"variant": args.variant, "prompt_version": os.environ.get("AGENT_PROMPT_VERSION", "")},
        dry_run=args.dry_run or False,
        timeout=int(os.environ.get("EXPERIMENT_TIMEOUT_SECONDS", "900")) + 60,
        print_summary=True,
    )
    print(result)


if __name__ == "__main__":
    main()
