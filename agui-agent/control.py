"""Валидированные операции управления состоянием Aegra thread."""

from typing import Literal

from pydantic import BaseModel, Field

from contracts import InvestigationPlan, WorkItem
from aegra_client import AegraClient


class StateEdit(BaseModel):
    operation: Literal["update_task", "update_plan", "update_prompt", "set_step_mode"]
    checkpoint_id: str | None = None
    task: str = ""
    prompt: str = ""
    role: str = ""
    enabled: bool = False
    items: list[dict] = Field(default_factory=list)


def _checkpoint(state: dict, requested: str | None) -> str | None:
    if requested:
        return requested
    checkpoint = state.get("checkpoint") or {}
    return checkpoint.get("checkpoint_id") or state.get("checkpoint_id")


def _update_task(values: dict, task: str) -> dict:
    if not task.strip():
        raise ValueError("Задача не должна быть пустой")
    messages = values.get("messages") or []
    latest = next((message for message in reversed(messages) if (message.get("type") or message.get("role")) in ("human", "user")), None)
    if not latest or not latest.get("id"):
        raise ValueError("В текущем состоянии нет пользовательской задачи для редактирования")
    updated = {**latest, "type": "human", "role": None, "content": task.strip()}
    updated.pop("role", None)
    return {"messages": [updated]}


def _update_plan(values: dict, edits: list[dict]) -> dict:
    current_items = values.get("work_items") or (values.get("investigation_plan") or {}).get("items") or []
    by_id = {item.get("id"): item for item in current_items}
    if not by_id:
        raise ValueError("Planner ещё не создал work items")
    edits_by_id = {str(edit.get("id") or ""): edit for edit in edits}
    unknown = set(edits_by_id) - set(by_id)
    if unknown:
        raise ValueError(f"Неизвестные work items: {', '.join(sorted(unknown))}")
    updated = []
    for current in current_items:
        item_id = str(current.get("id") or "")
        edit = edits_by_id.get(item_id, {})
        if edit and current.get("status") != "pending":
            raise ValueError(f"Нельзя переписывать выполненный или активный work item: {item_id}")
        allowed = {"goal", "acceptance", "priority", "estimated_cost", "depends_on", "required"}
        updated.append(WorkItem.model_validate({
            **current,
            **{key: value for key, value in edit.items() if key in allowed},
            "id": item_id,
            "status": current.get("status"),
        }).model_dump())
    ids = [item["id"] for item in updated]
    if len(ids) != len(set(ids)):
        raise ValueError("ID work items должны быть уникальны")
    if sum(bool(item.get("required")) for item in updated) > 2:
        raise ValueError("Не более двух work items могут быть обязательными")
    for item in updated:
        for dependency in item.get("depends_on") or []:
            if dependency not in ids:
                raise ValueError(f"Неизвестная зависимость {dependency} у {item['id']}")
            if dependency == item["id"]:
                raise ValueError(f"Work item {item['id']} зависит от самого себя")
    by_updated_id = {item["id"]: item for item in updated}
    visited: set[str] = set()

    def check_cycle(item_id: str, path: set[str]) -> None:
        if item_id in path:
            raise ValueError(f"Циклическая зависимость у work item {item_id}")
        if item_id in visited:
            return
        for dependency in by_updated_id[item_id].get("depends_on") or []:
            check_cycle(dependency, {*path, item_id})
        visited.add(item_id)

    for item_id in ids:
        check_cycle(item_id, set())
    plan = InvestigationPlan.model_validate({**(values.get("investigation_plan") or {}), "items": updated})
    return {"investigation_plan": plan.model_dump(), "work_items": [item.model_dump() for item in plan.items]}


def _update_prompt(values: dict, role: str, prompt: str) -> dict:
    allowed = {"intent", "scope", "planner", "scout", "investigator", "reporter", "critic_evidence", "critic_report", "chat"}
    if role not in allowed:
        raise ValueError(f"Неизвестная роль: {role}")
    if not prompt.strip():
        raise ValueError("Prompt override не должен быть пустым")
    overrides = {**(values.get("control_overrides") or {}), role: {"prompt": prompt}}
    return {"control_overrides": overrides}


def _update_step(values: dict, enabled: bool) -> dict:
    overrides = {
        **(values.get("control_overrides") or {}),
        "_step": {"tools": enabled},
    }
    return {"control_overrides": overrides}


async def edit_state(client: AegraClient, thread_id: str, edit: StateEdit) -> dict:
    active = [run for run in await client.runs(thread_id) if run.get("status") in ("pending", "running")]
    if active:
        raise ValueError("Сначала приостановите активный run")
    state = await client.state(thread_id)
    values = state.get("values") or {}
    if edit.operation == "update_task":
        update = _update_task(values, edit.task)
    elif edit.operation == "update_plan":
        update = _update_plan(values, edit.items)
    elif edit.operation == "set_step_mode":
        update = _update_step(values, edit.enabled)
    else:
        update = _update_prompt(values, edit.role, edit.prompt)
    await client.request("POST", f"/threads/{thread_id}/state", json={
        "values": update,
        "checkpoint_id": _checkpoint(state, edit.checkpoint_id),
    })
    return await client.state(thread_id)
