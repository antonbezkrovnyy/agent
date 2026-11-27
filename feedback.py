import storage


def runner_problem(
    agent: str,
    category: str,
    message: str,
    recovery: str | None = None,
    evidence: list[str] | None = None,
    details: object = None,
    **references,
) -> dict:
    return storage.report_feedback(
        source="runner",
        agent=agent,
        category=category,
        summary=message,
        needed=recovery,
        evidence=evidence,
        details=details,
        blocking=True,
        **references,
    )


def resolve_recovered(feedback_ids: list[int], resolution: str) -> None:
    for feedback_id in dict.fromkeys(feedback_ids):
        storage.resolve_feedback(feedback_id, resolution)
