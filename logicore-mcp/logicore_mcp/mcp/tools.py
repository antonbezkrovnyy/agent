"""Инструменты MCP: тонкие обработчики над flows. Назначение тула и формат ответа —
в docstring, контракты параметров — в contracts.py (pydantic) и Field(description)
(TD-0001, TD-0005…TD-0008, TD-0021…TD-0023)."""

import asyncio
import functools
import time
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

from logicore_mcp.domain.window import server_time_payload
from logicore_mcp.mcp.contracts import (
    COLUMNS_PARAM,
    DUPLICATES_LIMIT,
    EXTRACT_MAX_ROWS,
    FIELD_PARAM,
    FIELDS_PARAM,
    FILTER_PARAM,
    GROUPS_PARAM,
    KEY_FIELDS_PARAM,
    LOOKUP_FIELDS_PARAM,
    ORDER_BY_PARAM,
    ORDER_PARAM,
    QUERY_LIMIT_PARAM,
    SEARCH_LIMIT,
    TIME_FROM_PARAM,
    TIME_TO_PARAM,
    TOP_VALUES_LIMIT,
)
from logicore_mcp.mcp.flows import (
    READ_ONLY,
    count_events_flow,
    duplicates_flow,
    enum_lookup_flow,
    event_card_flow,
    field_fill_rates_flow,
    field_lookup_flow,
    search_events_flow,
    timeline_flow,
    top_values_batch_flow,
    top_values_flow,
)
from logicore_mcp.mcp.extract import extract_data_flow
from logicore_mcp.mcp.registry import mcp
from logicore_mcp.mcp.runtime import rate_limiter, schema_fields_catalog, settings, use_client

UNEXPECTED_TOOL_HINT = (
    "Неожиданное исключение сервера (не ошибка Earth и не ошибка аргументов): "
    "повторите вызов; при стойком повторении сузьте окно/фильтр и сообщите владельцу — "
    "трейс записан в журнал stderr."
)

_ToolFn = TypeVar("_ToolFn", bound=Callable[..., Any])


def _envelope_unexpected(func: _ToolFn) -> _ToolFn:
    """Единый перехват неожидаемых исключений тула: конверт с именем класса вместо SDK-обёртки
    «Error executing tool …: {e}», теряющей текст при пустом str(exc); трейс — в stderr.
    CancelledError — репрайз: отмена хоста не конвертируется (PLAN-0007 п.3, TD-0008).
    Всем конвертам добавляет duration_s — длительность вызова в секундах (ревизия TD-0001
    от 2026-08-25: после планировщика стоимость окна невидима модели — ses_fc7a, 477 с)."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        name = getattr(func, "__name__", "unknown")
        started = time.monotonic()
        limiter = rate_limiter()
        if limiter is not None:
            decision = limiter.check(started)
            if not decision.allowed:
                retry = int(decision.retry_after_s or 0) + 1
                logger.warning("tool={} rate limited, retry_after={:.0f}s", name, decision.retry_after_s or 0.0)
                return {
                    "ok": False,
                    "error": "Лимит вызовов инструментов исчерпан (R-0008, TD-0039)",
                    "hint": f"Подождите ~{retry} с и повторите осознанно. Стенд защищён от монопольных циклов агента.",
                    "retry_after": retry,
                    "duration_s": 0.0,
                }
        try:
            result = await func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("tool={} unexpected exception", name)
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "hint": UNEXPECTED_TOOL_HINT,
                "duration_s": round(time.monotonic() - started, 1),
            }
        ok = not (isinstance(result, dict) and result.get("ok") is False)
        took = time.monotonic() - started
        logger.info("tool={} ok={} took={:.1f}s", name, ok, took)
        if isinstance(result, dict):
            result["duration_s"] = round(took, 1)
        return result

    return wrapper  # type: ignore[return-value]


FILTER = (
    "Готовое IQL-выражение MARS для data.query.filters, строкой и без JsonLogic. "
    "Передаётся в MARS как есть: AND/OR/NOT, сравнения, IN, LIKE, IS [NOT] NULL, "
    "has() и другие ClickHouse-выражения. Это только условие, не полный SELECT и не "
    "несколько statements. MCP не парсит IQL: синтаксические ошибки приходят в error/hint. "
    "Имена полей — из logicore://schema/fields; окно — time_from/time_to. "
    "Пример: \"action = 'login' AND status IS NOT NULL\". Без фильтра — все события окна."
)
FILTER_SHORT = (
    "Готовое IQL-выражение MARS, как в search_events; передаётся без JsonLogic-трансляции. "
    "Окно задаётся time_from/time_to. Без фильтра — все события окна."
)


def _payload(items: Any) -> Any:
    if items is None:
        return None
    return [item.model_dump() for item in items]


@mcp.tool(annotations=READ_ONLY, title="Поиск событий", structured_output=False)
@_envelope_unexpected
async def search_events(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER)] = None,
    columns: COLUMNS_PARAM = None,
    page: Annotated[int, Field(description="Страница выборки, от 1.")] = 1,
    limit: SEARCH_LIMIT = 20,
    order: ORDER_PARAM = "desc",
    groups: GROUPS_PARAM = None,
    order_by: ORDER_BY_PARAM = None,
    query_limit: QUERY_LIMIT_PARAM = None,
    include_activity: Annotated[
        bool,
        Field(
            description=(
                "Добавить activity — распределение по до 20 плотнейшим моментам. "
                "Только для анализа динамики во времени: дороже и шумнее."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Ищет события в журнале Логикора за указанный период и возвращает их
    списком: одна строка = одно событие, в строке — выбранные поля.
    До 100 строк за вызов.

    Когда брать: нужны сами события (кто, что, откуда, когда) — этот тул.
    Нужно только количество — count_events: он быстрее и точнее, а поле total
    этого тула при большой выдаче недостоверно или отсутствует. Полные детали
    одного события — get_event по uuid из строки выдачи. Что значит поле в
    результатах — field_lookup.

    Полезно знать: по умолчанию (order=desc) строки идут от новых к старым,
    первая строка — самое свежее событие; order=asc меняет порядок на обратный.
    Для одинаковых timestamp применяется tie-breaker uuid, поэтому пагинация стабильна.
    Поле message обрезается до 1000 символов; полный текст — через get_event.

    Пример: {"time_from": "2026-08-21T10:00:00Z", "time_to": "2026-08-21T12:00:00Z",
    "iql": "action = 'login'", "limit": 20}
    Без фильтра — все события окна: {"time_from": "2026-08-21T10:00:00Z"}

    Ответ: окно, фактический iql, события и пагинация. has_more=true только если
    look-ahead-запрос следующей страницы вернул строки; false — если она пустая
    или текущая страница неполная; null — если проверить продолжение не удалось,
    тогда смотрите pagination_error. next_page указывается только при has_more=true.
    groups — дополнительные равенства, order_by — полная сортировка вместо order.
    query_limit ограничивает весь результат: при достижении complete=false и has_more=false.
    Общего total при усечении нет, точное число даёт count_events.

    Общее поле ответов: duration_s — сколько секунд шёл вызов (широкие окна и
    LIKE по message дороже)."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await search_events_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            page=page,
            limit=limit,
            schema=schema,
            columns=columns,
            default_columns=settings().default_columns,
            include_activity=include_activity,
            order=order,
            groups=_payload(groups),
            order_by=_payload(order_by),
            query_limit=query_limit,
        )


@mcp.tool(annotations=READ_ONLY, title="Выгрузка событий", structured_output=False)
@_envelope_unexpected
async def extract_data(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER_SHORT)] = None,
    columns: COLUMNS_PARAM = None,
    max_rows: EXTRACT_MAX_ROWS = None,
    page: Annotated[int, Field(description="Начальная страница, от 1; для продолжения — next_page из прошлого ответа.", ge=1)] = 1,
    order: ORDER_PARAM = "asc",
    groups: GROUPS_PARAM = None,
    order_by: ORDER_BY_PARAM = None,
    query_limit: QUERY_LIMIT_PARAM = None,
) -> dict[str, Any]:
    """Выгружает события постранично, без ручной пагинации через LLM.

    Когда брать: нужна полная таблица или файл по событием, а не агрегат.
    Возвращает events, returned_rows, complete и next_page. complete=false —
    полнота не доказана: достигнут max_rows/query_limit, частичное покрытие или повтор страницы.
    После max_rows продолжите с page=next_page и теми же окном, iql и order/order_by;
    следующая пустая страница подтверждает завершение. При query_limit следующей страницы нет.
    Для агрегатов используйте count_events/top_values/timeline.
    """
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await extract_data_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            columns=columns,
            max_rows=max_rows or settings().extract_max_rows,
            page=page,
            order=order,
            default_columns=settings().default_columns,
            schema=schema,
            groups=_payload(groups),
            order_by=_payload(order_by),
            query_limit=query_limit,
        )


@mcp.tool(annotations=READ_ONLY, title="Подсчёт событий", structured_output=False)
@_envelope_unexpected
async def count_events(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER_SHORT)] = None,
    groups: GROUPS_PARAM = None,
    query_limit: QUERY_LIMIT_PARAM = None,
) -> dict[str, Any]:
    """Возвращает точное число событий в журнале за период, при необходимости — под условием фильтра.
    Одним числом, без самих событий.

    Когда брать: нужен объём — «сколько событий», «сколько за час», «сколько
    с action = login». Это канонический способ считать: total из search_events
    насыщается на больших окнах и не годится. Нужны сами строки — search_events.

    Пример: {"time_from": "2026-08-21T10:00:00Z"}

    Ответ: {ok, count, window{from,to,source}, filter_applied, iql}.
    groups — дополнительные равенства. При count >= query_limit результат помечается
    complete=false: это потолок запроса, а не полное число событий окна.

    Общее поле ответов: duration_s — сколько секунд шёл вызов; широкие окна
    считаются заметно дольше."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await count_events_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            schema=schema,
            groups=_payload(groups),
            query_limit=query_limit,
        )


@mcp.tool(annotations=READ_ONLY, title="Таймлайн событий", structured_output=False)
@_envelope_unexpected
async def timeline(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER_SHORT)] = None,
    groups: GROUPS_PARAM = None,
) -> dict[str, Any]:
    """Строит гистограмму объёма событий за период: ровные бакеты «интервал → число событий», без самих событий.

    Когда брать: анализ динамики во времени — «есть ли суточная
    периодичность», «когда был всплеск», «равномерно ли течёт поток».
    Сумма бакетов равна count_events того же окна, точный объём в моменте —
    тоже он.

    Полезно знать: потолок окна — 8 дней; границы выравниваются до сетки Earth
    (до минут, при спане ≥24 ч — до часов, ≥7 сут — до 3 часов), поэтому задачи
    с границами вне сетки не завершаются — сверяйте фактическое окно с
    возвращённым window, а не с запрошенным. Последний бакет может быть
    вырожденным (from == to, value 0/null) — это артефакт Earth, не падение
    потока. Вычисление асинхронное. Если поток закрылся без done, ответ будет
    complete=false с hint — это неполный ряд, а не доказанный ноль/падение потока.

    Пример: {"time_from": "2026-08-21T10:00:00Z", "time_to": "2026-08-21T12:00:00Z"}

    Ответ: {ok, window, filter_applied, iql, complete?, timeline{buckets[{from,to,value}],
    bucket_count, granularity_seconds, total}}."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await timeline_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            schema=schema,
            groups=_payload(groups),
        )


@mcp.tool(annotations=READ_ONLY, title="Топ значений поля", structured_output=False)
@_envelope_unexpected
async def top_values(
    time_from: TIME_FROM_PARAM,
    field: FIELD_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER_SHORT)] = None,
    limit: TOP_VALUES_LIMIT = 5,
) -> dict[str, Any]:
    """Показывает самые частые и самые редкие значения одного поля за период: какие action, importance, хосты и т.п.
    вообще есть в данных.

    Когда брать: обзор «что в данных» — какие значения принимает поле и как
    часто. Несколько полей подряд — top_values_batch, он делает то же одним
    вызовом. Что значит само поле — field_lookup, не угадывай по имени.

    Пример: {"field": "action", "time_from": "2026-08-21T10:00:00Z", "limit": 10}

    Ответ: distinct — число прочитанных значений (при complete=false это только
    нижняя граница, распределение потолком 100 не доказано полным; bottom тогда не
    возвращается). null_count — ЧИСЛО null-ГРУПП, не null-событий.
    top/bottom[{value,count,share}] — share в % от суммы прочитанных групп;
    при complete=false это НЕ доля всех событий окна. Отсутствие значения в
    выдаче ≠ его ноль. Точная заполненность — два count_events (все и field != null)."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await top_values_flow(
            client,
            field=field,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            limit=limit,
            schema=schema,
        )


@mcp.tool(annotations=READ_ONLY, title="Карточка события", structured_output=False)
@_envelope_unexpected
async def get_event(
    uuid: Annotated[str, Field(description="uuid события из строки выборки search_events.")],
    timestamp: Annotated[
        str,
        Field(
            description=(
                "Момент события — поле timestamp той же строки выборки, ISO-8601 с зоной. Не окно и не интервал."
            )
        ),
    ],
) -> dict[str, Any]:
    """Возвращает карточку одного события — все поля схемы целиком, включая полный текст message.

    Когда брать: нужны полные детали конкретного события, когда строка из
    search_events уже найдена. Пара uuid + timestamp берётся ТОЛЬКО из одной
    строки этой выдачи: живой uuid с чужим timestamp даёт found=false, как и
    несуществующий. Известен только uuid — сначала search_events с iql
    "uuid = '<uuid>'" на широком time_from, затем эта пара.

    Пример вызова: {"uuid": "0ea9d1f2-... (из строки выборки)", "timestamp": "2026-08-21T10:15:30Z"}

    Ответ: found=true — event с заполненными полями (пустые опущены, счётчики
    fields_total/fields_filled); found=false — пара не нашлась (вероятнее не та
    пара, чем удаление события; рецепт — в hint), uuid/timestamp эхом."""
    async with use_client() as client:
        return await event_card_flow(client, uuid=uuid, timestamp=timestamp)


@mcp.tool(annotations=READ_ONLY, title="Топ значений пакетом", structured_output=False)
@_envelope_unexpected
async def top_values_batch(
    time_from: TIME_FROM_PARAM,
    fields: FIELDS_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[FILTER_PARAM, Field(description=FILTER_SHORT)] = None,
    limit: TOP_VALUES_LIMIT = 5,
) -> dict[str, Any]:
    """Обходит несколько полей за один вызов: для каждого — топ-значения и заполненность.
    Заменяет серию последовательных top_values.

    Когда брать: нужен срез по нескольким полям сразу — «какие action,
    importance и типы событий есть». Одно поле — top_values. Заполненность ВСЕХ
    полей схемы, а не выборочных — field_fill_rates. Что значит поле —
    field_lookup.

    Пример: {"fields": ["action", "importance"], "time_from": "2026-08-21T10:00:00Z"}

    Ответ: window, fields — список {field, distinct, complete, null_count,
    top/bottom[{value, count, share}]}; сбой одного поля — {field, error, hint}
    в его элементе, остальные отдаются."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await top_values_batch_flow(
            client,
            fields=fields,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            limit=limit,
            schema=schema,
        )


@mcp.tool(annotations=READ_ONLY, title="Заполненность полей", structured_output=False)
@_envelope_unexpected
async def field_fill_rates(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[
        FILTER_PARAM,
        Field(description=FILTER_SHORT + " Заполненность в разрезе (напр., только mime = TEXT) — штатный сценарий."),
    ] = None,
    offset: Annotated[
        int,
        Field(description="Курсор продолжения: N из ответа предыдущего вызова при complete=false."),
    ] = 0,
) -> dict[str, Any]:
    """Сканирует все поля схемы и показывает, какие чем заполнены — одним вызовом.
    Заменяет перебор полей по одному.

    Когда брать: первый зонд по незнакомым данным — «что вообще есть в событиях»,
    прежде чем выбирать поля для фильтров и агрегаций. Точная проверка одного
    конкретного поля — top_values.

    Полезно знать: если стенд медленный, ответ придёт с complete=false —
    обязательно продолжите вызовом с offset=N из этого ответа, с тем же
    окном/фильтром; fill_rate — приближение по топу составного распределения.
    Страницы НЕ кумулятивны: каждый вызов несёт только свой фрагмент —
    объединяйте filled/empty/failed всех страниц с идентичным окном/фильтром.
    complete — только курсор полей (все страницы прочитаны), НЕ «отсутствие
    failed»: сбойные поля — в failed с hint о повторе. time_coverage —
    частичное покрытие окна бюджетом дробления: числа относятся к префиксу
    до covered_until.

    Пример: {"time_from": "2026-08-21T10:00:00Z"}

    Ответ: fields_total, fields_scanned, offset, complete, filled_fields,
    filled[{field, fill_rate, null_count, top_value}] убыв. fill_rate, empty —
    поля без значений, failed — ошибки Earth по группам полей, time_coverage —
    частичное покрытие окна (covered_until, partial_batches)."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await field_fill_rates_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            schema=schema,
            offset=offset,
        )


@mcp.tool(annotations=READ_ONLY, title="Дубликаты событий", structured_output=False)
@_envelope_unexpected
async def duplicates(
    time_from: TIME_FROM_PARAM,
    time_to: TIME_TO_PARAM = None,
    iql: Annotated[
        FILTER_PARAM,
        Field(description=FILTER_SHORT + " Дубликаты в разрезе источника/фильтра."),
    ] = None,
    key_fields: KEY_FIELDS_PARAM = None,
    limit: DUPLICATES_LIMIT = 10,
) -> dict[str, Any]:
    """Ищет точные дубликаты событий за период: группы повторов по составному ключу, самые большие группы первыми.

    Когда брать: вопрос «события не задваиваются?» — этот тул отвечает прямо.
    Показать пару событий из группы — search_events по фильтру ключа в узком
    окне, их карточки — get_event.

    Пример: {"time_from": "2026-08-21T10:00:00Z",
    "key_fields": ["generator", "event_type", "timestamp"]}

    Ответ: key, duplicate_groups[{<поля ключа>, count}] убыв. count;
    duplicate_groups_total — число групп в прочитанном распределении. При
    ceiling_reached=true достигнут потолок 100: это нижняя граница, а не итог окна,
    поэтому сузьте окно/фильтр. Пустой список без ceiling_reached = дубликатов нет.
    truncated=true — показано меньше, чем прочитано (limit или потолок, см. hint).
    Группы с count>1 — кандидаты повторов, не доказательство дубля сбора:
    heartbeat законно повторяет строку."""
    schema = await schema_fields_catalog()
    async with use_client() as client:
        return await duplicates_flow(
            client,
            time_from=time_from,
            time_to=time_to,
            filter=iql,
            key_fields=key_fields,
            limit=limit,
            schema=schema,
        )


@mcp.tool(annotations=READ_ONLY, title="Справочник полей", structured_output=False)
@_envelope_unexpected
async def field_lookup(
    fields: LOOKUP_FIELDS_PARAM,
) -> dict[str, Any]:
    """Объясняет смысл полей схемы: что означает поле и чем отличается от похожих.
    Тип — из живой схемы стенда, описание — из справки MP SIEM 7.0.

    Когда брать: перед фильтрацией или агрегацией по незнакомому имени —
    убедись, что поле значит то, что ты думаешь. Поля без описания в справке —
    description: null, выдумывать смысл не нужно.

    Пример вызова: {"fields": ["action", "object"]}

    Ответ: fields[{field, type, description}], счётчики described/without_overlay."""
    return await field_lookup_flow(
        fields=fields,
        schema=await schema_fields_catalog(),
    )


@mcp.tool(annotations=READ_ONLY, title="Расшифровка перечислений", structured_output=False)
@_envelope_unexpected
async def enum_lookup(
    field: Annotated[str, Field(description="Enum-поле: action, object, status, subject, event_src_category.")],
    value: Annotated[
        str | None,
        Field(description="Точное значение для lookup. Пропущен — словарь поля целиком."),
    ] = None,
) -> dict[str, Any]:
    """Расшифровывает значения перечислений (action, object, status,
    subject, event_src_category) по таксономии MP SIEM 7.0.

    Когда брать: вопрос вида «что значит action = escalate». Расшифровка берётся
    из справки, а не генерируется; found=false — в ответе человеку расшифровку
    НЕ выдумывать (вернутся похожие значения).

    Пример вызова: {"field": "action", "value": "escalate"}

    Ответ: {field, found?, value?, description?, similar?, values?}."""
    return enum_lookup_flow(field=field, value=value)


@mcp.tool(annotations=READ_ONLY, title="Время сервера", structured_output=False)
@_envelope_unexpected
async def server_time() -> dict[str, Any]:
    """Возвращает текущее время сервера — без параметров, мгновенно, без похода в Логикор.

    Когда брать: любое «сейчас», «последние N минут», «сегодня» — считай от
    этого времени, а не прикидывай в уме (модель в этом регулярно ошибается).
    Это то же самое время, из которого сервер считает окна с source="to-now".

    Полезно знать: это время локальных часов MCP-процесса, а не чтение часов
    Earth — вывод «у источника битые часы» требует уверенности в
    синхронизации часов хоста MCP.

    Вызов без аргументов.

    Ответ: {"ok": true, "now": "2026-08-22T16:30:00+00:00", "epoch": 1787416200}."""
    return server_time_payload()
