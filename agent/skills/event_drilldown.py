"""Playbook детерминированного разбора одного события."""

from skills.contracts import SkillDefinition


EVENT_DRILLDOWN = SkillDefinition(
    id="event_drilldown",
    version=1,
    triggers=[
        "карточка события",
        "разбери событие",
        "покажи событие целиком",
        "детали события",
    ],
    allowed_tools=[
        "get_event",
        "search_events",
        "get_evidence",
        "log_finding",
        "list_findings",
        "get_finding",
    ],
    strategy=[
        "Идентифицируй событие по uuid + timestamp из запроса или предыдущего Evidence; не подставляй другое событие.",
        "Прочитай его через get_event или существующую запись через get_evidence, не меняя окно.",
        "Опиши фактические поля, сообщение, теги и таксономию; не выдумывай смысл отсутствующих полей.",
        "Создай anchors только для явно присутствующих IP, account, host, hash, process и event.",
        "Свяжи вывод и каждый anchor с реальными evidence_id; возможные dating/pivot укажи как следующие действия.",
    ],
    stop_rules=[
        "Одно целевое событие разобрано без расширения расследования.",
        "Если uuid + timestamp не найдены или событие отсутствует, верни partial/failed и NOT_CHECKED.",
        "Не выполняй pivot и не изменяй окно внутри drilldown.",
    ],
    max_window_updates=0,
    max_depth=0,
    max_branches=4,
)
