MP_TEXT_PROMPT = """Создай production-пакет mp_normalizer для источника {source}.
Вход — ранее неизвестные RAW-строки между тегами <corpus>. Кластеризация, если она есть,
служит только подсказкой. Не изменяй RAW и не выдумывай семантику неизвестных значений.

Сначала вызови get_mp_text_capabilities. Работай только с grok.kind=TEXT и возможностями
именно этого runtime. Не используй исследовательский %{{CUSTOM_REGEX:capture}}, обычный Grok,
Logstash Grok, JSON token или mp_json. Не пиши итоговый YAML/VRL вручную: compiler создаёт их
из структурированного MpTextExportSpec.

Для каждой стабильной Family создай одну или несколько rules:
- rule_key — стабильный snake_case идентификатор сообщения;
- pattern — MP TEXT DSL с полным совпадением; все captures только temporary $variables;
  одинаковый capture допустим в alternatives для разных представлений одного значения;
- record_schema описывает каждый capture ровно один раз, raw не добавляй;
- normalization описывает каждый capture ровно один раз; используй минимально нужные operations;
- mappings преобразует captures в MP SIEM/Logicore и обоснованные константы;
- expected_events имеет ту же длину, что evidence, и содержит проверяемые flat normalized fields;
- negative_evidence содержит похожие строки, которые правило обязано отклонить.

В mappings обязательны поля из required_event_fields. Таксономия Logicore — подмножество MP SIEM
с техническими полями. Семантику выбирай через get_mapping_guidelines, get_taxonomy_field,
semantic_search_taxonomy и get_taxonomy_values. Не создавай message, uuid, recv_time, generator,
normalized, routing tags и остальные Saturn-managed поля. Не используй придуманные поля.
Целевые имена можно записывать dotted: compiler превращает src.ip в src_ip.

Для timestamp используй подходящий DATETIME token. Для parse_timestamp нужна явная timezone;
source_timezone задаётся оператором и равна {source_timezone}. Не выдумывай год и timezone.
Если compiler возвращает unsupported_capability, не ослабляй семантику: исправь candidate на
поддерживаемую конструкцию либо зарегистрируй blocking feedback.

dispatch_contains должен содержать стабильные литералы маршрутизации источника, не шумные IP,
время и значения наблюдения. Source-specific normalizer выполняется после generic и имеет
приоритет только при собственном успешном разборе. Не полагайся на порядок rules внутри YAML.

Перед export обязательно:
1. preview_mp_text_candidate и исправление всех IR errors;
2. evaluate_mp_text_candidate до native ok=true, включая все expected_events и negatives;
3. export_mp_normalizer с тем же spec без изменений.

Если меняешь уже экспортированный candidate, увеличь revision. Не выдавай draft за готовый результат.
После export верни только MpTextCandidateResponse с фактическими путями и числом проверок.
Оставшиеся семантические допущения перечисли в assumptions и при необходимости report_feedback.
В report_feedback обязательно указывай family_id={source}. Перед финальным завершением проверь
list_open_feedback: открытых blocking проблем быть не должно. После фактического устранения
закрой их resolve_mp_feedback; accepted_uncertainty выбирай только для внешней неопределённости,
не для обхода ошибки candidate. Открытая blocking проблема запрещает готовый результат.

<corpus>
{corpus}
</corpus>"""

MP_TEXT_SYSTEM_PROMPT = (
    "Ты создаёшь только проверенные MP TEXT production rules. После export верни только JSON "
    "без markdown по схеме MpTextCandidateResponse: "
)
