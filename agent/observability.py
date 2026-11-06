"""Наследуемые metadata/tags для OpenInference без собственного tracer."""

from config import Settings

settings = Settings()


def trace_config(role: str, state: dict) -> dict:
    context = state.get("context") or {}
    item = state.get("active_work_item") or {}
    skill = state.get("active_skill") or {}
    scope = state.get("investigation_scope") or {}
    mode = state.get("work_mode") or "unknown"
    scope_mode = scope.get("mode") or "unknown"
    metadata = {
        "agent_role": role,
        "work_mode": mode,
        "scope_mode": scope_mode,
        "prompt_version": settings.agent_prompt_version,
        "investigation_id": str(context.get("id") or ""),
        "work_item_id": str(item.get("id") or ""),
        "work_item_attempt": (item.get("attempt") or 0) + 1 if item.get("id") else 0,
        "agent_phase": state.get("agent_phase") or "investigate",
        "continuation": bool(state.get("continuation")),
        "termination_reason": state.get("termination_reason") or "running",
        "skill_id": str(skill.get("id") or ""),
    }
    return {
        "tags": [f"role:{role}", f"mode:{mode}", f"scope:{scope_mode}"],
        "metadata": metadata,
    }
