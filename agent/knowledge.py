import httpx

from config import Settings

settings = Settings()


def knowledge_request(method: str, path: str, **kwargs) -> dict:
    if not settings.knowledge_service_url:
        return {"ok": False, "error": "Knowledge service не настроен"}
    try:
        with httpx.Client(timeout=settings.knowledge_timeout_seconds) as client:
            response = client.request(
                method,
                f"{settings.knowledge_service_url.rstrip('/')}{path}",
                **kwargs,
            )
        data = response.json()
        if response.status_code >= 400:
            return {"ok": False, "error": data.get("detail") or f"Knowledge service HTTP {response.status_code}"}
        return data
    except Exception as exc:
        return {"ok": False, "error": f"Knowledge service недоступен: {type(exc).__name__}"}


def wiki_search(query: str, limit: int = 3) -> dict:
    return knowledge_request("GET", "/wiki/search", params={"query": query, "limit": max(1, min(limit, 10))})


def wiki_read(page_id: str, section: str = "") -> dict:
    return knowledge_request("GET", "/wiki/read", params={"page_id": page_id, "section": section})


def publish_report_card(card: dict) -> dict:
    return knowledge_request("POST", "/reports/publish", json=card)
