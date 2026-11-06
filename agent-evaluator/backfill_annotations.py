"""Дозаписать run-summary аннотации для уже оценённых runs.

Quality-аннотацию (logicore_agent_quality) не трогает: только run_type, scope_mode,
plan_execution, claims_support, hypothesis_coverage и critic_review. Запуск:

    docker compose exec agent-evaluator python backfill_annotations.py --limit 50
"""

import argparse
import asyncio
import logging

import httpx

from app import phoenix, settings, store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def _state(client: httpx.AsyncClient, thread_id: str) -> dict:
    response = await client.get(f"{settings.aegra_api_url}/threads/{thread_id}/state")
    response.raise_for_status()
    payload = response.json()
    return payload.get("values") or payload


async def _latest_run_matches(client: httpx.AsyncClient, thread_id: str, run_id: str) -> bool:
    runs = await client.get(f"{settings.aegra_api_url}/threads/{thread_id}/runs", params={"limit": 1})
    runs.raise_for_status()
    latest = (runs.json() or [{}])[0]
    return (latest.get("run_id") or latest.get("runId")) == run_id


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not store.enabled:
        raise SystemExit("agent_memory database is not configured")
    records = store.list_traced(args.limit)
    async with httpx.AsyncClient(timeout=60) as client:
        for run_id, thread_id, trace_id in records:
            try:
                if not await _latest_run_matches(client, thread_id, run_id):
                    logging.info("Skip run=%s: в треде есть более новый run", run_id)
                    continue
                state = await _state(client, thread_id)
            except Exception:
                logging.exception("State unavailable: run=%s", run_id)
                continue
            written = phoenix.annotate_run_summary(trace_id, state)
            logging.info("Backfilled run=%s trace=%s... annotations=%s", run_id, trace_id[:13], written)


if __name__ == "__main__":
    asyncio.run(main())
