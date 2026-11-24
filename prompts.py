RESEARCH_PROMPT = """Проанализируй одну строку лога между тегами <log>. Не предполагай
заранее производителя, наличие конверта или конкретный формат. Конверт может
отсутствовать, payload может быть рекурсивно вложенным и экранированным,
например syslog(json(xml)). Не изменяй RAW незаметно.

Это независимый запуск: предыдущего диалога нет, но есть постоянная SQLite-база.
Сначала вызови semantic_search_parser_families и semantic_search_grok_patterns.
Формируй запрос по смыслу события, конверту, payload и нужному атому, а не только по
шумным IP/hex-значениям. Score используется только для ранжирования кандидатов.
Для поиска конкретной ревизии используй semantic_search_parsers. Если семантический
поиск пуст или не даёт уверенности, сделай fallback на list_parser_families и
search_grok_patterns; отсутствие похожих описаний само по себе не доказывает new_family.

Для каждой потенциально подходящей Family получи её через get_parser_family с нужной
revision и без изменений исполни на RAW: Grok через execute_grok_parser и
validate_grok_result, pipeline через execute_pipeline_parser и validate_pipeline_result.
Окончательный выбор делай только по фактическому совпадению.

Если существующая Family полностью подходит, выбери reuse: используй её parser без
изменений, не создавай новую ревизию и сохрани только результат исследования.
Если ни одна не подходит, выбери ровно одно решение по failed_layer и captures:
- extend_layer: минимально расширить один слой существующей Family;
- new_variant: тот же стабильный идентификатор сообщения, но другая грамматика тела;
- new_family: новый идентификатор сообщения или другой конверт;
- unknown: недостаточно доказательств для надёжного разбора.
Не ослабляй правила через .* без необходимости и не переписывай работающие слои.

Используй duckduckgo_search только для разрешения неоднозначностей. Документация —
дополнительное доказательство, а не обязательное условие анализа: для неизвестных
и кастомных источников допустимы обоснованные гипотезы и unknown.

Верни результат строго в схеме Response:

1. essence — что происходит в событии: участники, действие и результат. Укажи также
   выбранное решение reuse/extend_layer/new_variant/new_family/unknown и его причину.

2. structure — упорядоченные слои от RAW к значениям. Для каждого укажи path,
   format, role (envelope/payload/value), transform или null и confidence
   (observed/inferred/hypothesis). Отрази только обоснованную вложенность.

3. strategy — только проектные решения строками, без обоснований и альтернатив:
   разделение конверта и payload, рекурсивный разбор, диспетчеризация при наличии
   стабильного идентификатора, сохранение RAW и границы нормализации. Не требуй
   msg_id, если он не обнаружен.

4. record_schema — минимально полный контракт полей одной Family:
   - static — стабильный элемент грамматики Family, dynamic — значение наблюдения;
   - severity, action и protocol всегда dynamic;
   - structure содержит слои и якоря, record_schema — только выходные поля;
   - raw всегда dynamic и required=true;
   - msg_id static и required=true только если он обнаружен и выбран ключом Family;
   - required=true только если поле обязательно для всех допустимых вариантов Family;
   - required не зависит от category; null не приравнивается к нулю;
   - source dynamic-поля — точный path, существующий в structure; для raw используй $;
     если нужного пути нет, добавь соответствующее значение в structure, а не придумывай
     текстовое описание в source;
   - каждое смысловое значение либо входит в record_schema, либо сохраняется в residual;
   - не добавляй derived, интеграционные, транспортные и выдуманные поля.

5. format_features — свойства формата и следствия для разбора. Отмечай наблюдение,
   вывод или гипотезу. Не добавляй эрудицию без следствия.

6. pitfalls — только неоднозначности самого формата: границы, вложенность,
   экранирование, опциональность, конфликтующие обозначения, варианты записи,
   null против нуля. Для каждой укажи механизм и разрешение. Не рассматривай
   производительность, regex-движки, интеграцию, метрики или написание кода.

7. parser — исполняемый Grok-парсер или детерминированный pipeline. Задай kind=grok
   или kind=pipeline; не смешивай modes в одной revision.

   Для структурного payload не пиши regex вместо его кодека. Конверт исследуй и
   разбирай сам через модельный grok: получи capture с payload, затем вызови
   probe_format именно на этом payload (или на RAW, если конверта нет) и проверь
   выбранный кодек через try_parse. Не используй готовый конверт вместо своего Grok.
   В parser.kind=pipeline задай stages: первый grok source=raw с params
   {{pattern, patterns}}, следующие кодеки получают source
   stages.<имя_предыдущей>.fields.<capture>. Для cef params пустые, для csv/kv задай
   разделители и правила подписей из probe/try_parse. Все стадии обязательны.
   Имена выходных полей record_schema должны совпадать с fields стадий либо быть
   смысловыми псевдонимами с корректным source для последующего маппинга.
   Исполни pipeline через execute_pipeline_parser и проверь validate_pipeline_result.

   Для kind=grok встроенных Grok-шаблонов нет:
   каждое используемое имя, включая INT, IPV4, MONTH и WORD, должно быть определено
   в patterns или получено из сохранённых. В patterns задай список объектов
   name/definition/description; создавай осмысленные составные конструкции и
   переиспользуй сохранённые. Для каждой новой конструкции задай минимум один полный
   positive_examples и один negative_examples: они исполняются независимо. Не записывай
   зависимости только прозой: ссылки %{{NAME}} вычисляются автоматически.
   В layers задай name/source/pattern: первый source=raw,
   каждый следующий source — capture предыдущего слоя. Static-элементы должны быть
   литералами или стабильными шаблонами, dynamic — captures; опциональность должна
   соответствовать required. Syslog PRI ограничен 0..191, порты — 0..65535.

   В constraints задай межполевые правила, которые нельзя выразить атомом без ложных
   комбинаций. equals фиксирует стабильное значение dispatch-поля (особенно для
   детерминированного pipeline); allowed_when ограничивает присутствие поля условиями других captures,
   required_when делает его обязательным при этих условиях. Не разрешай независимые
   опциональные атомы образовывать семантически неверные комбинации: например,
   ICMP-поля допустимы только при protocol=icmp. Если межполевых правил нет, оставь
   constraints пустым.

   Для kind=grok обязательно исполни parser через execute_grok_parser и проверь через
   validate_grok_result. Для отладки используй execute_grok. При ошибке исправь
   определения или слои и повтори. Не завершай с ok-парсером, пока не достигнуто
   полное совпадение, обязательные поля и пустой residual; иначе явно опиши ошибку.

Для нового или изменённого parser обязательно вызови evaluate_candidate для Grok или
evaluate_pipeline_candidate для pipeline:
- extend_layer проверяй на новой строке и всей Evidence затронутой Family;
- new_variant и new_family проверяй на новой строке, а прежнюю Family независимо
  передай в baseline с её собственной Evidence;
- проверь captures, обязательные поля и residual;
- не сохраняй кандидата, если регрессия не прошла.

После успешной проверки сохрани новые пользовательские конструкции через
save_grok_pattern, Grok-Family через save_parser_family, а pipeline-Family через
save_pipeline_family. Для Grok
description должен раскрывать назначение, положительные и отрицательные примеры,
captures, зависимости и границы совпадения. В save_parser_family обязательно передай
два разных развёрнутых описания: family_description — суть событий, конверт, dispatch,
границы применимости и исключения; parser_description — вход, слои/стадии, выходной контракт,
причину ревизии, регрессию и ограничения. Не копируй одно описание в другое.

Проверь сохранение через get_grok_pattern и get_parser_family. При reuse не дублируй
Family и конструкции. Полный финальный Response runner сохранит сам после валидации JSON.

Для kind=grok в parser.patterns всегда возвращай полный набор конструкций, необходимых
для исполнения layers, включая переиспользованные, а не только вновь созданные.
Для kind=pipeline оставь patterns и layers пустыми, а stages верни полностью.

Перед ответом проверь согласованность блоков, источники полей в structure,
required против format_features/pitfalls и отсутствие потери envelope-значений.
Python-код, тесты, маппинги в таксономии и интеграционные решения не нужны.

<log>
{log}
</log>"""

SYSTEM_PROMPT = (
    "После завершения исследования верни только JSON-объект без markdown "
    "по следующей схеме Response: "
)


NORMALIZATION_PROMPT = """Нормализуй существующую parser Family {family_id}@{parser_revision}.
Это отдельный этап после парсинга: parser и его captures изменять нельзя. Маппинг в
таксономии, переименование полей, enrichment и классификация не нужны.

Сначала получи Family через get_parser_family, затем исходный полный Response через
get_parser_research. Используй essence, structure, strategy, format_features и pitfalls
парсинг-агента как исходную семантику, а не восстанавливай её заново. Если research не
найден, используй Family descriptions и явно отметь это ограничение. Изучи record_schema,
descriptions, constraints и все evidence. Для каждого поля record_schema кроме raw создай один
NormalizationField. source должен точно совпадать с именем поля record_schema; target
не существует, нормализованное значение сохраняет исходное имя.

description каждого поля — главный артефакт для будущего семантического маппинга.
Опиши развёрнуто: смысл, участника и направление, единицы, допустимые значения,
обязательность, null против нуля и неопределённости. Не выдумывай назначение opaque
значений. Для hit_cnt учитывай агрегацию ASA, для hit_interval — разные формы записи,
для хешей — неподтверждённый смысл. Поля без operations тоже обязательно опиши.

Доступные operations:
- trim;
- null_if с params values;
- to_integer;
- lowercase;
- normalize_ip с version=ipv4|ipv6|any;
- validate_range с min/max после числового преобразования;
- parse_timestamp с format/formats, default_year при необходимости,
  input_timezone/output_timezone только при наличии оснований.

Применяй минимально необходимые operations. Не превращай 0 в null. Не назначай UTC
или текущий год без доказательств. Для Cisco timestamp с годом и без timezone выводи
naive ISO, если timezone источника неизвестна. Не разбивай и не извлекай новые поля:
это относится к parser, а не к этой минимальной нормализации.

Сохрани parser evidence без изменений в NormalizationRevision.evidence. Сначала вызови
preview_normalization на реальной строке, затем evaluate_normalization на всех evidence.
Исправляй конкретные ошибки инструментов. После успешной проверки вызови
save_normalization с точным parser_revision. Если прежней normalization нет, используй
revision=1; если она уже существует, получи её и используй следующий свободный номер.
Затем вызови get_normalization для сохранённой revision.

Верни только полный JSON NormalizationRevision без markdown: family_id, revision,
parser_revision, fields, evidence, description. Внутренний reasoning не нужен.
"""

NORMALIZATION_SYSTEM_PROMPT = (
    "После завершения нормализации верни только JSON-объект без markdown "
    "по следующей схеме NormalizationRevision: "
)


MAPPING_PROMPT = """Спроектируй маппинг Family {family_id}@{parser_revision} и
normalization revision {normalization_revision} в таксономию MP SIEM 8.0.
Это третий независимый агент после parser и normalizer. Исходный parser и normalization
менять нельзя. Именно ты выбираешь соответствия по смыслу, а не по совпадению имён.

Сначала получи полный mp_siem_8_mapping_sequence через get_mapping_guidelines и соблюдай
его порядок в списке mappings: обязательный каркас → категории → subject → object
атрибуты → src/dst → event_src → остальное взаимодействие → information/data fields.
Затем получи Family через get_parser_family, исходный Response через
get_parser_research и normalization через get_normalization. Главные входные данные —
развёрнутые description каждого NormalizationField, исходная essence/structure/pitfalls
и примеры evidence.

Для каждого исходного поля и для обязательного каркаса MP SIEM используй
semantic_search_taxonomy по текстовому смыслу. Ищи taxonomy_field для полей,
taxonomy_value для action/object/status/importance/event_src.category и
taxonomy_category для category.generic/high/low. Затем получай точные определения через
get_taxonomy_field/get_taxonomy_values/get_taxonomy_categories. Semantic score только
ранжирует кандидатов; решение подтверждай описанием и фактическим preview.

Обязательные поля MP SIEM: time, id, event_src.vendor, event_src.title,
event_src.category, action, object, status, importance. Категории источника и события не
путай. event_src описывает регистратора, src/dst — стороны сетевого взаимодействия.
status относится к результату действия, а не обязательно к успеху соединения.
Автоматические поля не заполняй. JSON события плоский.

MappingField.source — имя normalized capture. Для констант Family используй constant с
обоснованием; не скрывай неизвестные исходные значения. Для value_map задавай все
допустимые значения без default: неизвестное значение должно вызвать ошибку.
Для разных представлений одного source используй condition {{format: ip|ipv4|ipv6|hostname}}:
например, IP-форма host может идти в event_src.ip, а обычное имя — в event_src.hostname.
Составное 'устройство/интерфейс' не является hostname: не записывай его туда без отдельного
обоснованного разбора. Не приписывай source-интерфейсу ingress-семантику без доказательств.

Не придумывай назначение opaque полей. datafield1..10 и numfield1..5 существуют в Registry:
используй их для значимых данных без стандартного поля после семантического поиска.
Не делай новый парсинг.

Внешняя настройка source_timezone для этого запуска: {source_timezone}.
Она не выведена из RAW. Если timestamp naive и настройка задана, executor использует её
для приведения time к UTC. Если не задана, не придумывай timezone и верни ошибку конфигурации.

id формируй по правилу MP SIEM: namespace_vendor_title_transport_event-type без пробелов.
Отдельный namespace обязателен: vendor/title/transport/event-type сам по себе недостаточен.
Нормализованные timestamp уже ISO; не меняй его смысл.

Сначала preview_mapping на реальной строке, затем evaluate_mapping на всех evidence.
Исправляй все missing/invalid/unknown/enum/category ошибки. После успеха вызови
save_mapping: revision=1, если прежней нет, иначе следующий свободный номер. Проверь
через get_mapping. Верни только полный JSON MappingRevision без markdown: family_id,
revision, parser_revision, normalization_revision, taxonomy=mp_siem_8.0,
source_timezone, mappings, evidence, description.
"""

MAPPING_SYSTEM_PROMPT = (
    "После завершения маппинга верни только JSON-объект без markdown "
    "по следующей схеме MappingRevision: "
)
