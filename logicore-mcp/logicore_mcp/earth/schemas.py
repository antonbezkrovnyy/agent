"""Pydantic-формы и builders тел запросов Earth (RS-0003, TD-0002, TD-0005).

Ответы типизированы свободно: контракт — снимок со стенда, не документация (R-0001);
фактические формы зафиксированы в reference/earth-api.md.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def format_utc(moment: datetime) -> str:
    """Формат времени Earth в запросах: YYYY-MM-DD HH:MM:SS, вызов — с UTC-датой (TD-0002)."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class LoginRequest(BaseModel):
    login: str
    password: str


class LoginData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str
    user: dict[str, Any]


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: bool
    message: str = ""
    data: LoginData


class EventsMeta(BaseModel):
    """meta ответа поиска; from/schema — псевдонимы зарезервированных имён.

    from/to — None при пустой выдаче (стенд, reference/earth-api.md); total при пустой
    выдаче мусорный — интерпретация в domain/projection (TD-0004).
    """

    model_config = ConfigDict(populate_by_name=True)

    current_page: int
    last_page: int
    per_page: int
    total: int
    from_: int | None = Field(alias="from")
    to: int | None
    schema_: list[dict[str, Any]] = Field(default_factory=list, alias="schema")


class EventsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[dict[str, Any]]
    meta: EventsMeta
    result: bool
    message: str = ""


class EventCardResponse(BaseModel):
    """Ответ GET /events/single: data — список из одной карточки (~266 полей, снимок стенда) (TD-0006)."""

    model_config = ConfigDict(extra="ignore")

    data: list[dict[str, Any]]
    result: bool
    message: str = ""


def build_events_request(
    *,
    filters: str | None,
    time_from: datetime,
    time_to: datetime,
    tenant_ids: list[int],
    columns: tuple[str, ...],
    page: int = 1,
    per_page: int = 20,
    order: str = "desc",
) -> dict[str, Any]:
    """Тело POST /api/v1/events, mode: IQL — маппинг TD-0002; проекция (TD-0007); порядок (TD-0025)."""
    query: dict[str, Any] = {
        "columns": list(columns),
        "timestamp": {"fromTime": format_utc(time_from), "toTime": format_utc(time_to)},
        "order": [{"column": "timestamp", "action": order.upper()}],
    }
    if filters is not None:
        query["filters"] = filters
    return {
        "mode": "IQL",
        "data": {
            "pagination": {"page": page, "perPage": per_page},
            "query": query,
            "tenantIds": tenant_ids,
            "timezone": "UTC",
        },
    }


def build_count_request(
    *,
    filters: str | None,
    time_from: datetime,
    time_to: datetime,
    tenant_ids: list[int],
) -> dict[str, Any]:
    """Тело POST /api/v1/events/count — число событий окна под фильтром (TD-0017)."""
    query: dict[str, Any] = {
        "columns": ["uuid"],
        "timestamp": {"fromTime": format_utc(time_from), "toTime": format_utc(time_to)},
    }
    if filters is not None:
        query["filters"] = filters
    return {
        "mode": "IQL",
        "data": {"query": query, "tenantIds": tenant_ids, "timezone": "UTC"},
    }


def build_timeline_request(
    *,
    filters: str | None,
    time_from: datetime,
    time_to: datetime,
    tenant_ids: list[int],
) -> dict[str, Any]:
    """Тело POST /mars/api/v1/events/timeline — EventsTimelineRequest, SSE-поток (TD-0018, Mars API)."""
    query: dict[str, Any] = {"timestamp": {"fromTime": format_utc(time_from), "toTime": format_utc(time_to)}}
    if filters is not None:
        query["filters"] = filters
    return {
        "data": {"query": query, "tenantIds": tenant_ids, "timezone": "UTC"},
    }


def build_single_params(*, uuid: str, timestamp: str, tenant_ids: list[int]) -> list[tuple[str, Any]]:
    """Query-параметры GET /api/v1/events/single: ключ карточки uuid + timestamp (TD-0006)."""
    params: list[tuple[str, Any]] = [("tenant_ids[]", tenant) for tenant in tenant_ids]
    params.extend((("uuid", uuid), ("timestamp", timestamp)))
    return params


def build_groups_request(
    *,
    columns: list[str],
    filters: str | None,
    time_from: datetime,
    time_to: datetime,
    tenant_ids: list[int],
    limit: int = 100,
) -> dict[str, Any]:
    """Тело POST /mars/api/v1/events/groups — IQLGroupsRequest (tenantIds, limit внутри query)."""
    query: dict[str, Any] = {
        "columns": columns,
        "timestamp": {"fromTime": format_utc(time_from), "toTime": format_utc(time_to)},
        "limit": limit,
    }
    if filters is not None:
        query["filters"] = filters
    return {"data": {"query": query, "tenantIds": tenant_ids, "timezone": "UTC"}}
