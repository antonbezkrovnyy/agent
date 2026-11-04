"""Проверка structured skill_run, anchors и фактических ограничений playbook."""

from datetime import UTC, datetime

import llm as llm_mod
from state import State


def _anchor_key(anchor: dict) -> str:
    return f"{anchor.get('type', 'custom')}:{str(anchor.get('value') or '').strip().lower()}"


def skill_run_update(state: State, result: dict, structured: dict) -> dict:
    active = state.get("active_skill") or {}
    if not active.get("id"):
        return {}
    run = structured.get("skill_run")
    if not run or run.get("skill_id") != active["id"] or run.get("version") != active["version"]:
        note = "Skill не вернул корректный structured skill_run."
        structured.setdefault("limitations", []).append(note)
        run = {
            "skill_id": active["id"],
            "version": active["version"],
            "status": "failed",
            "summary": structured.get("summary") or note,
            "window_updates": 0,
            "evidence_ids": [],
            "not_checked": [note],
        }
    available = {item.get("id") for item in result.get("evidence") or state.get("evidence") or []}
    unknown = [item for item in run.get("evidence_ids") or [] if item not in available]
    if unknown:
        run["evidence_ids"] = [item for item in run.get("evidence_ids") or [] if item in available]
        run.setdefault("not_checked", []).append("Из skill_run удалены неизвестные evidence_id.")
        run["status"] = "partial"
    before = len((state.get("context") or {}).get("window_history") or [])
    after = len((result.get("context") or {}).get("window_history") or [])
    run["window_updates"] = max(0, after - before)
    if run["window_updates"] > active.get("max_window_updates", 0):
        run.setdefault("not_checked", []).append("Превышен лимит изменений окна skill.")
        run["status"] = "partial"

    existing_anchors = list(state.get("anchors") or [])
    merged = {_anchor_key(item): dict(item) for item in existing_anchors}
    valid_anchors = []
    for anchor in run.get("anchors") or []:
        evidence_ids = [item for item in anchor.get("evidence_ids") or [] if item in available]
        if not evidence_ids:
            run.setdefault("not_checked", []).append(f"Anchor без Evidence пропущен: {_anchor_key(anchor)}.")
            run["status"] = "partial"
            continue
        anchor = {**anchor, "evidence_ids": evidence_ids}
        key = _anchor_key(anchor)
        valid_anchors.append(anchor)
        previous = merged.get(key)
        if previous:
            anchor["evidence_ids"] = list(dict.fromkeys([
                *(previous.get("evidence_ids") or []),
                *evidence_ids,
            ]))
        merged[key] = anchor
    run["anchors"] = valid_anchors
    if len(valid_anchors) > active.get("max_branches", 1):
        run.setdefault("not_checked", []).append("Превышен лимит новых anchors skill.")
        run["status"] = "partial"
    if run.get("depth", 0) > active.get("max_depth", 0):
        run.setdefault("not_checked", []).append("Превышена глубина pivot skill.")
        run["status"] = "partial"

    previous_visited = {item.strip().lower() for item in state.get("visited_anchors") or []}
    run_visited = list(dict.fromkeys(
        item.strip().lower() for item in run.get("visited_anchors") or [] if item.strip()
    ))
    if previous_visited.intersection(run_visited):
        run.setdefault("not_checked", []).append("Обнаружено повторное посещение anchors.")
        run["status"] = "partial"
    run["visited_anchors"] = run_visited
    if active["id"] == "entity_pivot":
        visited = previous_visited.union(run_visited)
        for key in sorted(set(merged).difference(visited)):
            note = f"Ветка не исследована в этом skill_run: {key}."
            if note not in run.setdefault("not_checked", []):
                run["not_checked"].append(note)
    recorded_at = datetime.now(UTC)
    run["model"] = llm_mod.model_name_for_role("investigator")
    if active.get("selected_at"):
        selected_at = datetime.fromisoformat(active["selected_at"].replace("Z", "+00:00"))
        run["duration_ms"] = max(0, int((recorded_at - selected_at).total_seconds() * 1000))
    run["recorded_at"] = recorded_at.isoformat(timespec="seconds")
    return {
        "skill_runs": [run],
        "anchors": list(merged.values()),
        "visited_anchors": sorted(previous_visited.union(run_visited)),
    }
