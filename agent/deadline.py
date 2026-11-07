from datetime import UTC, datetime


def remaining_seconds(state: dict) -> float | None:
    value = state.get("run_deadline_at")
    if not value:
        return None
    try:
        deadline = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return max(0.0, (deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def run_expired(state: dict) -> bool:
    return remaining_seconds(state) == 0


def capped_timeout(state: dict, timeout: float) -> float:
    remaining = remaining_seconds(state)
    if remaining is None:
        return timeout
    return max(0.001, min(timeout, remaining))
