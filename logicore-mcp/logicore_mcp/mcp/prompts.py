"""MCP prompts: шаблоны вопросов из справочника обкаток (TD-0019).

Тексты несут канонические указания, выверенные живыми прогонами ses_fdb7…ses_fdc3:
объёмы — count_events, профили — timeline, явные окна при сравнениях, ноль — расследовать.
Каждый текст содержит примеры вызовов инструментов — JSON со статичными
плейсхолдерами <имя>; сырые аргументы промпта в JSON не подставляются
(анти-инъекция, спека 2026-07-28 server/prompts Security).
"""

from typing import Any

from mcp.server.mcpserver.prompts.base import UserMessage

from logicore_mcp.mcp.registry import mcp


def _msg(text: str) -> list[Any]:
    return [UserMessage(text)]


@mcp.prompt(
    name="explore",
    title="Разведка: что происходит в Логикоре",
    description="Короткий рассказ о событиях окна: объём, источники, заполненность, примеры",
)
def explore(time_from: str | None = None, time_to: str | None = None) -> list[Any]:
    """time_from — ISO-8601 с зоной, обязателен; time_to опционален (по умолчанию сейчас)."""
    window = (
        f"окно {time_from} — {time_to}"
        if time_from and time_to
        else "последние 15 минут (time_from на 15 минут раньше)"
    )
    return _msg(
        f"Посмотри события Логикора за {window} и расскажи коротко, что происходит: "
        "объём (count_events), топ источников и теги (top_values_batch), какие поля заполнены "
        "(field_fill_rates при сомнении) и пара примеров событий. 5–7 фактов с числами. "
        "time_from передавай всегда (обязателен); если time_to опущен — сервер считает до «сейчас»."
        ' Примеры вызовов: count_events {"time_from": "<time_from>", "time_to": "<time_to>"}; '
        'top_values_batch {"fields": ["generator", "importance"], "time_from": "<time_from>"}; '
        'search_events {"time_from": "<time_from>", "limit": 3}.'
    )


@mcp.prompt(
    name="count",
    title="Подсчёт событий",
    description="Точное число событий под условием за окно — одним числом",
)
def count(
    condition: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
) -> list[Any]:
    """condition — условие словами (напр. 'action = elevate от administrator')."""
    scope = f"с условием: {condition}" if condition else "без условия (весь объём окна)"
    window = (
        f"окно {time_from} — {time_to}" if time_from and time_to else "последние 15 минут (time_to = сейчас сервера)"
    )
    return _msg(
        f"Посчитай события {scope} за {window}. Используй count_events — total поиска ненадёжен "
        "(насыщается на больших окнах). Фильтр — JsonLogic по именам полей из схемы; null в == "
        "означает IS NULL. В ответе: число, эхо применённого фильтра и окно. Если вышел ноль, "
        "а данные ожидались — расследуй: какие значения поля реально есть (top_values), "
        "не уходи в пустой ответ."
        ' Пример: count_events {"time_from": "<time_from>", "filter": '
        '{"==": [{"var": "<поле>"}, "<значение>"]}}.'
    )


@mcp.prompt(
    name="periodicity",
    title="Суточная периодичность потока",
    description="Профиль «час → число»: есть ли днём/ночью разница, повторяется ли неделя к неделе",
)
def periodicity(time_from: str | None = None, time_to: str | None = None) -> list[Any]:
    """time_from/time_to — полные сутки для профиля; без них — последние сутки."""
    window = f"окно {time_from} — {time_to}" if time_from and time_to else "последние полные сутки"
    return _msg(
        f"Построй профиль объёма событий по часам за {window} (инструмент timeline — ровные бакеты; "
        "границы выравниваются автоматически). Скажи: выражена ли суточная периодичность "
        "(днём много/ночью мало) с числами; сравни с тем же днём неделю назад — повторяется ли. "
        "Если профиль плоский — дай диагноз: машинный генератор (проверь top_values по generator) "
        "или норма; 'плоско = подозрительно' — только если ожидался человеческий трафик."
        ' Примеры: timeline {"time_from": "<сутки>", "time_to": "<сутки+24ч>"} и вторым окном '
        'неделю назад; top_values {"field": "generator", "time_from": "<сутки>"}.'
    )


@mcp.prompt(
    name="source_health",
    title="Здоровье источников",
    description="Идут ли данные с источника, кто замолчал, лаг потока, изменение плотности",
)
def source_health(source: str | None = None) -> list[Any]:
    """source — IP/имя приёмника или генератора (напр. 10.28.15.179); без него — все источники."""
    target = f"с источника {source}" if source else "по всем источникам"
    return _msg(
        f"Проверь здоровье потока {target}: (1) идут ли данные — count_events с фильтром "
        "recv_ipv4/event_src_host за последние 15 минут; (2) не замолчал ли — сравни список "
        "источников (top_values) за последний час и сутки назад по двум ЯВНЫМ окнам; "
        "(3) лаг — последнее событие против «сейчас» сервера; (4) изменение плотности — "
        "час к часу. Вывод: кто жив, кто замолчал, аномалии с числами. Ноль по источнику — "
        "проверь, в каких полях вообще есть адреса, прежде чем говорить 'молчит'."
        ' Примеры: count_events {"time_from": "<15 мин назад>", '
        '"filter": {"==": [{"var": "recv_ipv4"}, "<source>"]}}; '
        'top_values {"field": "event_src_host", "time_from": "<час назад>", "time_to": "<сейчас>"}.'
    )


@mcp.prompt(
    name="user_activity",
    title="Профиль пользователя",
    description="Что делала учётка: действия с расшифровками, повышение привилегий, примеры",
)
def user_activity(account: str, time_from: str | None = None, time_to: str | None = None) -> list[Any]:
    """account — имя учётки (subject_account_name, напр. administrator или ua-karpenko)."""
    window = f"окно {time_from} — {time_to}" if time_from and time_to else "последние сутки нормализованных данных"
    return _msg(
        f"Построй профиль активности учётки «{account}» за {window}: распределение действий "
        "(top_values по action с фильтром subject_account_name = '{account}'), суммарный объём "
        "(count_events), были ли повышения привилегий (elevate) и примеры событий с uuid. "
        "Значения action расшифровывай через enum_lookup; если значения нет в словаре — скажи "
        "об этом прямо, не выдумывай."
        ' Примеры: top_values {"field": "action", "time_from": "<time_from>", '
        '"filter": {"==": [{"var": "subject_account_name"}, "<account>"]}}; '
        'search_events {"time_from": "<time_from>", '
        '"filter": {"==": [{"var": "subject_account_name"}, "<account>"]}, "limit": 5}.'
    )


@mcp.prompt(
    name="correlation",
    title="События корреляции",
    description="Найти события корреляционного правила и показать карточку",
)
def correlation(rule: str) -> list[Any]:
    """rule — имя правила или его часть (напр. Unix_Sensitive_File_Read)."""
    return _msg(
        f"Найди события корреляции по правилу «{rule}»: точное имя посмотри в top_values по "
        "correlation_rule (оно бывает с суффиксом «event: …»), затем счёт (count_events), "
        "и карточку одного события (get_event по паре uuid+timestamp из выборки). В карточке "
        "обрати внимание на chain_id, subject/object, важность. Если правило не нашлось — "
        "покажи список реально существующих правил."
        ' Примеры: top_values {"field": "correlation_rule", "time_from": "<time_from>", "limit": 20}; '
        'count_events {"time_from": "<time_from>", '
        '"filter": {"contains": [{"var": "correlation_rule"}, "<rule>"]}}; '
        'get_event {"uuid": "<uuid>", "timestamp": "<timestamp>"}.'
    )


@mcp.prompt(
    name="field_fill",
    title="Заполненность полей",
    description="Какие поля заполнены и чем — карта, в т.ч. в разрезе фильтра",
)
def field_fill(filter_description: str | None = None) -> list[Any]:
    """filter_description — разрез словами (напр. 'только TEXT-события'); без него — все события окна."""
    scope = f" в разрезе: {filter_description}" if filter_description else ""
    return _msg(
        f"Покажи карту заполненности полей{scope} за последние сутки (field_fill_rates; при "
        "медленном ответе — курсор offset из ответа). Выведи таблицу «поле / заполненность / "
        "топ-значение» для существенного и назови общее число заполненных (filled_fields в ответе). Пропущенные "
        "поля — одной строкой списком. Не перечисляй пустые поля поимённо."
        ' Пример: field_fill_rates {"time_from": "<сутки назад>"}; продолжение при '
        'complete=false — тот же вызов с "offset": N из ответа.'
    )


@mcp.prompt(
    name="duplicates",
    title="Дубликаты событий",
    description="Задваиваются ли события — группы повторов по ключу",
)
def duplicates(time_from: str | None = None, time_to: str | None = None) -> list[Any]:
    """time_from — обязателен; time_to опционален (по умолчанию сейчас)."""
    window = (
        f"окно {time_from} — {time_to}"
        if time_from and time_to
        else "последние 15 минут (time_from на 15 минут раньше)"
    )
    return _msg(
        f"Проверь, задваиваются ли события за {window}: инструмент duplicates (ключ message по "
        "умолчанию; для нормализованных — ключ [generator, event_type, timestamp] или свой "
        "состав). Выведи группы повторов с числом или явное «дублей нет» с указанием ключа. "
        "Для пары из группы — карточки, покажи отличия."
        ' Примеры: duplicates {"time_from": "<time_from>", "key_fields": ["message"]}; '
        'для нормализованных {"time_from": "<time_from>", '
        '"key_fields": ["generator", "event_type", "timestamp"]}.'
    )
