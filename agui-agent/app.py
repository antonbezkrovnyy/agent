"""AG-UI адаптер над Aegra runs API."""

import sys

sys.path.insert(0, "/app/agent")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder

from aegra_client import AegraClient
from agui_mapper import map_stream, message_text
from artifact_store import artifact_url
from execution_audit import list_execution_events
from control import StateEdit, edit_state
from manual_tools import ManualToolCall, execute_manual_tool, list_manual_tools
from dashboard import dashboard

app = FastAPI(title="LogiCore Agent — AG-UI / Aegra adapter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AegraClient()


def graph_messages(input_data: RunAgentInput) -> list[dict]:
    result = []
    roles = {"user": "human", "assistant": "ai", "system": "system", "developer": "system"}
    for message in input_data.messages:
        role = str(message.role)
        if role not in roles or getattr(message, "subagent_run_id", None) is not None:
            continue
        if role == "assistant" and getattr(message, "tool_calls", None):
            continue
        content = message_text(message.content)
        if not content:
            continue
        item = {"role": roles[role], "content": content, "id": message.id}
        name = getattr(message, "name", None)
        if name:
            item["name"] = name
        result.append(item)
    return result


@app.post("/agent")
async def agent_endpoint(input_data: RunAgentInput, request: Request):
    encoder = EventEncoder(accept=request.headers.get("accept"))
    known_ids = {message.id for message in input_data.messages if message.id}
    messages = graph_messages(input_data)

    async def event_generator():
        try:
            forwarded = input_data.forwarded_props or {}
            attach_id = forwarded.get("aegra_run_id") or forwarded.get("aegraRunId") or ""
            checkpoint_id = str(forwarded.get("checkpoint_id") or forwarded.get("checkpointId") or "")
            before = forwarded.get("interrupt_before") or forwarded.get("interruptBefore") or []
            after = forwarded.get("interrupt_after") or forwarded.get("interruptAfter") or []
            if attach_id:
                source = client.attach_run(
                    input_data.thread_id,
                    str(attach_id),
                    str(forwarded.get("last_event_id") or forwarded.get("lastEventId") or ""),
                )
            elif forwarded.get("resume") or checkpoint_id:
                source = client.resume_run(
                    input_data.thread_id,
                    checkpoint_id=checkpoint_id or None,
                    interrupt_before=before,
                    interrupt_after=after,
                )
            else:
                await client.ensure_thread(input_data.thread_id)
                source = client.stream_run(
                    input_data.thread_id,
                    messages,
                    interrupt_before=before,
                    interrupt_after=after,
                )
            async for event in map_stream(input_data.thread_id, input_data.run_id, known_ids, source):
                yield encoder.encode(event)
        except Exception as exc:
            from ag_ui.core import EventType, RunErrorEvent

            yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=str(exc)))

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "backend": "aegra"}


@app.get("/dashboard")
async def analyst_dashboard() -> dict:
    try:
        return await dashboard(client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Dashboard: {exc}") from exc


@app.get("/threads/{thread_id}/state")
async def thread_state(thread_id: str) -> dict:
    try:
        return await client.state(thread_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/threads/{thread_id}/state/{checkpoint_id}")
async def checkpoint_state(thread_id: str, checkpoint_id: str) -> dict:
    try:
        return await client.state(thread_id, checkpoint_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/threads/{thread_id}/state")
async def update_thread_state(thread_id: str, edit: StateEdit) -> dict:
    try:
        return await edit_state(client, thread_id, edit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/threads/{thread_id}/history")
async def thread_history(thread_id: str, limit: int = 100) -> list[dict]:
    try:
        return await client.history(thread_id, max(1, min(limit, 1000)))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/threads/{thread_id}/runs")
async def thread_runs(thread_id: str) -> list[dict]:
    try:
        return await client.runs(thread_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_run(thread_id: str, run_id: str) -> dict:
    try:
        return await client.cancel(thread_id, run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/threads/{thread_id}/events")
async def execution_events(
    thread_id: str,
    run_id: str = "",
    role: str = "",
    event_type: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    try:
        return list_execution_events(
            thread_id,
            run_id=run_id,
            role=role,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Execution journal: {exc}") from exc


@app.get("/tools")
async def manual_tools() -> list[dict]:
    return list_manual_tools()


@app.post("/threads/{thread_id}/tools")
async def call_manual_tool(thread_id: str, call: ManualToolCall) -> dict:
    try:
        return await execute_manual_tool(client, thread_id, call)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/graph")
async def graph_topology() -> dict:
    try:
        return await client.graph()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/artifacts/{key:path}")
async def download_artifact(key: str) -> RedirectResponse:
    if not key.startswith("investigations/") or ".." in key.split("/"):
        raise HTTPException(status_code=404, detail="artifact not found")
    return RedirectResponse(artifact_url({"storage": "s3", "key": key}))
