"""Playbook ограниченного pivot по связанным сущностям."""

from skills.contracts import SkillDefinition


ENTITY_PIVOT = SkillDefinition(
    id="entity_pivot",
    version=1,
    triggers=[
        "pivot",
        "связанные сущности",
        "проследи сущность",
        "проверь связи события",
    ],
    allowed_tools=[
        "server_time",
        "search_events",
        "count_events",
        "top_values",
        "top_values_batch",
        "get_event",
        "get_evidence",
        "update_window",
        "log_finding",
        "list_findings",
        "get_finding",
    ],
    strategy=[
        "Начни с типизированных anchors из state и skill_run: ip, account, host, hash, process, event.",
        "Канонизируй ключ как type:value в нижнем регистре; не исследуй повторно visited_anchors.",
        "Выбирай ветки по релевантности исходной задаче, максимум 4 новых anchors и глубина не более 2.",
        "Для каждой ветки делай focused-запросы с сохранением исходных фильтров и явным evidence_id.",
        "После исследования добавляй ключ в visited_anchors; непосещённые ветки перечисляй в NOT_CHECKED с причиной.",
        "Меняй окно только при необходимости проверки гипотезы и не создавай новое расследование.",
    ],
    stop_rules=[
        "Не более 4 новых anchors, глубина 2 и 4 изменения окна за один skill_run.",
        "Нет повторного посещения type:value в текущем и предыдущих skill_runs.",
        "Остановись при отсутствии новых релевантных anchors или diminishing returns.",
        "При лимите или ошибке верни partial и перечисли непроверенные ветки в NOT_CHECKED.",
    ],
    max_window_updates=4,
    max_depth=2,
    max_branches=4,
)
