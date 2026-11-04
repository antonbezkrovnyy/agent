"""AG-UI sandbox поверх графа LogiCore Agent.

Изолированный sidecar: импортирует тот же пакет agent/, что и aegra,
но поднимает его in-process и отдаёт события по протоколу AG-UI.
Основной стек (aegra, agent-chat-ui, agent-mcp) не затрагивается.
"""

import sys

sys.path.insert(0, "/app/agent")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langgraph.checkpoint.memory import InMemorySaver

from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint

from artifact_store import artifact_url
from graph import build_graph

app = FastAPI(title="LogiCore Agent — AG-UI sandbox")

# Sandbox: UI на другом порту (copilotkit-demo / ручной инспектор) ходит сюда из браузера.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = LangGraphAgent(
    name="agent",
    graph=build_graph(checkpointer=InMemorySaver()),
)
add_langgraph_fastapi_endpoint(app, agent, "/agent")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/artifacts/{key:path}")
async def download_artifact(key: str) -> RedirectResponse:
    """Выдать подписанную S3-ссылку только для файлов расследований."""
    if not key.startswith("investigations/") or ".." in key.split("/"):
        raise HTTPException(status_code=404, detail="artifact not found")
    return RedirectResponse(artifact_url({"storage": "s3", "key": key}))
