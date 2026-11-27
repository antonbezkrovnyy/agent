# Исследования интерпретации логов

Серия экспериментов по автоматическому пониманию RAW-логов reasoning-моделью. Во всех экспериментах сохраняются входные данные, точный prompt, финальный ответ и оценка результата. Внутренний reasoning модели не входит в исследовательские артефакты.

## Эксперименты

| ID | Исследовательский вопрос | Основной результат | Статус |
|---|---|---|---|
| [R001](001-unconstrained-cisco-interpretation.md) | Что модель выведет без ограничений? | Cisco ASA и ACL deny распознаны, но добавлены догадки и рекомендации | Гипотеза подтверждена частично |
| [R002](002-focused-cisco-interpretation.md) | Можно ли сохранить только поля и суть события? | Два требуемых раздела сохранены, рекомендации и предположения устранены | Цель достигнута на одном примере |
| [R003](003-structured-cisco-interpretation.md) | Можно ли получить typed response вместо Markdown? | Получены `essence` и 16 полей `value/essence`; схема соблюдена, но hex снова получил неподтверждённую трактовку | Структура готова, семантика требует проверки |
| [R004](004-web-validated-cisco-interpretation.md) | Поможет ли DuckDuckGo проверять значения полей? | Модель использовала поиск и стала явно обозначать неопределённость; hex получил community-трактовку с оговоркой | Поиск работает, нужны источники на уровне полей |
| [R005](005-generic-web-validated-cisco-interpretation.md) | Сохранится ли результат без Cisco-specific prompt? | Модель сама распознала ASA и вернула 19 полей, но выполнила несколько десятков поисковых вызовов | Качество сохранено, поиск неэффективен |
| [R006](006-parser-design-review.md) | Может ли модель спроектировать контракт парсера без генерации кода? | Получены 6 решений, 52 поля, 10 особенностей и 9 грабель; есть ошибки классификации и ECS | Сильный черновик, требуется независимая проверка |
| [R007](007-structure-and-strategy-review.md) | Может ли модель строить структуру без vendor-specific prompt и выдавать стратегию только решениями? | 17 слоёв, 12 решений, 20 полей; лишняя вложенность и интеграция отсутствуют | Лучший baseline, категории полей требуют уточнения |
| [R008](008-static-dynamic-fields-review.md) | Упростит ли схему ограничение только static/dynamic? | `derived` исчез, но `severity` ошибочно static, envelope потерян, required противоречив | Диагностический результат, не новый baseline |
| [R009](009-consistent-static-dynamic-review.md) | Устранят ли явные cross-block правила потери и противоречия? | 19 полей, envelope и RAW сохранены, порты исправлены; ошибки остались только у raw и msg_id | Ближайший к рабочей спецификации результат |
| [R010](010-parser-ready-contract-review.md) | Устранят ли точечные правила raw и msg_id оставшиеся ошибки? | Обе ошибки исправлены; 17 полей, 23 слоя, 11 решений; residual сохранён | Текущий рабочий baseline |
| [R011](011-grok-parser-review.md) | Может ли модель дополнить контракт человекочитаемым Grok-парсером? | 15 конструкций и 9 слоёв получены; найдены ошибки `%%` и `second burst` | Направление верное, требуется исполнение |
| [R012](012-executed-grok-parser-review.md) | Исправит ли модель Grok после исполнения и валидации? | Исходная строка полностью разобрана, captures и residual проверены независимо; ошибка осталась в interval variant | Рабочий baseline для текущей строки |
| [R014](014-sqlite-backed-parser-review.md) | Может ли система сохранять и получать конструкции из постоянной БД? | 6 Grok и parser family сохранены в SQLite; загруженный из БД парсер успешно исполнен | Первый системный baseline |
| [R015](015-second-family-review.md) | Сможет ли система обработать вторую неизвестную строку, не сломав baseline? | Создана `syslog_acl_deny_by_group@1`; две конструкции переиспользованы, четыре добавлены; обе Family проходят регрессию | Подход подтверждён на второй строке |
| [R016](016-unified-independent-runs-review.md) | Можно ли обработать две строки независимыми запусками с одним prompt? | Два процесса без истории создали две Family; 6 конструкций; runner независимо проверяет parser и сохранение | Независимые запуски работают |
| [R017](017-semantic-search-review.md) | Можно ли находить Family, parser revisions и Grok по смыслу? | Добавлены развёрнутые описания и `semantic_embeddings_v2`; GPT-Lab `embedding` правильно ранжирует кандидатов; обе строки завершились `reuse` | Semantic search работает |
| [R018](018-cisco-corpus-review.md) | Можно ли покрыть весь Cisco corpus с пустого Registry и нуля встроенных Grok? | 1763/1763 покрыто; 9 ReAct-исследований; 3 Family, 9 revisions, 48 Grok; resume после перезагрузки успешен | Текущий системный baseline |
| [R019](019-model-quality-audit.md) | Насколько хороши parser, Grok, описания и revisions после corpus run? | Сильная архитектура и совместимость; найдены negative-case проблемы, 66 source mismatches, ошибка PRI и расхождения описаний | Аудит качества R018 |
| [R020](020-quality-gates-review.md) | Устранят ли детерминированные quality gates дефекты R019 без потери corpus? | 1763/1763 покрыто; 0 source mismatches; 49 Grok с examples; constraints, save gate и latest-first | Quality gates работают |
| [R021](021-juniper-review.md) | Перенесётся ли система на новый текстовый источник? | 26/26 Junos; 3 ReAct-исследования; новая Family; 64 Grok; устранён global/Family drift | Новый источник покрыт |
| [R022](022-unix-review.md) | Справится ли система с разнородными Unix-событиями? | 143/143; 14 ReAct-исследований; 12 новых Families; 118 Grok; независимый аудит без repair | Unix corpus покрыт |
| [R023](023-solaris-review.md) | Выдержит ли система разнообразие Solaris sshd/audit? | 67/67; 50 ReAct-исследований; 44 новых Families; 290 Grok; metadata repair и независимый аудит | Текущий исследовательский baseline |
| [R024](024-communigate-review.md) | Перенесётся ли система на CommuniGate? | Остановлено пользователем: 12/24, 11 ReAct-исследований, 7 новых Families; независимый аудит завершённых результатов пройден | Частичный эксперимент; baseline остаётся R023 |
| [R025](025-cef-pipeline-review.md) | Сможет ли модель снять envelope своим Grok и передать CEF в детерминированный codec? | Остановлено после доказательства: 66/1132, 6 ReAct-исследований, 7 pipeline-Families; 60 reuse; полный разбор 17/39 полей | Structured pipeline подтверждён частично |
| [R026](026-symantec-kv-review.md) | Выберет ли модель структурный codec для Symantec positional payload? | 1/1; выбран `grok → kv`; 26 captures; расширенные CSV/KV params проверены на 997 строках | KV pipeline подтверждён на одном наблюдении |
| [R027](027-cisco-normalization-review.md) | Можно ли добавить нормализацию после parser без изменения captures? | 18 описаний, normalization rev2; 4/4 evidence; 30/30 нормализаций; parser 1763/1763; исходный Response передан нормализатору | Минимальная normalization pipeline работает |
| [R028](028-cisco-mp-siem-mapping-review.md) | Сможет ли отдельный mapper по описаниям и semantic taxonomy собрать событие MP SIEM? | Mapping rev4: 29 mappings, 4/4 evidence, runner 1/1; 212 полей, 232 values, 222 категории; полная регрессия не запускалась | Parser → normalization → MP SIEM работает на scoped validation |
| [R029](029-cisco-pipeline-architecture-review.md) | Как объединить три агента в один pipeline и перенести исполнение в Rust+VRL? | Единый runner работает; semantic core совпал с production-событием; определены versioned Family Bundle и control/data planes | Итоговая исследовательская архитектура |
| [R030](030-rust-data-plane-review.md) | Можно ли исполнять утверждённые revisions в Rust и быстро выбирать Family? | Native Rust pipeline и Aho–Corasick shortlist работают; 10/10 tests, 4/4 exact Python parity; определён dispatch design для 10 000 Families | Rust execution baseline; масштабный индекс требует следующего этапа |
| [R031](031-cisco-end-to-end-pipeline-review.md) | Как одна Cisco-строка проходит весь путь от пустого Registry до выхода Rust? | Подробно восстановлены parser/normalizer/mapper, Bundle export, Rust startup, dispatch, 21 capture, 18 normalization descriptions, 29 mappings и финальный event | Сквозной методический отчёт на фактическом Bundle 4/2/4 |
| [R032](032-cisco-full-rust-regression-review.md) | Пройдёт ли весь Cisco corpus через Rust? | 1763/1763 mapped, failed=0; полный Python/Rust parity по captures/normalized/event без mismatches; исправлены nested duplicate captures | Полный Rust execution baseline для текущего Cisco corpus |
| [R033](033-juniper-full-rust-review.md) | Перенесётся ли полный Rust pipeline на Juniper? | 26/26 mapped, golden 3/3, полный parity без mismatches; mapping rev2 предпочитает concrete fields: username → subject.account.name, numeric protocol → protocol | Rust pipeline подтверждён на втором источнике |
| [R034](034-symantec-full-rust-review.md) | Выдержит ли Rust сложный Symantec KV corpus? | 997/997 mapped, три KV Families, golden 8/8, полный parity без mismatches; evidence-driven rev3 закрыл UDP/Outbound/Blocked/IPv6 | Полный KV execution baseline; staged learning рекомендован для corpus |
| [R035](035-self-learning-architecture-review.md) | Как система может сообщать об ограничениях и самостоятельно расширять свои capabilities? | Проект самообучающихся Registries, Taxonomy, Operations/parsing tools, reconcile, безопасной генерации кода и критериев полной автономности | Базовый Feedback Registry реализован; остальное остаётся архитектурным проектом |
| [R036](036-cisco-batch-agent-review.md) | Даст ли Drain3 + batch agent выигрыш над построчным R020 без изменения одиночного режима? | 1763/1763 покрыто; Research 9 против 10, но 7 Family вместо 3, 903 строки требуют historical fallback; найдены regression и потерянный Research | Прототип работает; преимущество и production-качество не подтверждены |
| [R037](037-logicore-mp-normalizer-production-review.md) | Как существующему агенту выпускать TEXT-правила `mp_normalizer` и fallback CONF для Saturn? | Осмотрены 8 репозиториев; зафиксированы VRL/MP capabilities, TEXT DSL, Syslog lifecycle, runtime differences, AxelNAC shadowing и отладка через saturn-tools | Подтверждён production contract и обязательные native/end-to-end gates |

## Общие условия ранних экспериментов

| Параметр | Значение |
|---|---|
| Модель | `deepseek-flash` |
| Температура | `0` |
| Вход | Одна встроенная строка Cisco ASA |
| Чтение файла | Не выполняется |
| Golden и внешняя документация | Не используются |
| Контур | ReAct + JSON `Response(text)` |
| Число прогонов на эксперимент | 1 |

## Сравнительный вывод

| Аспект | R001 | R002 | R003 |
|---|---|---|---|
| Понимание события | Да | Да | Да |
| Разбор по полям | Да | Да | Да, 16 объектов |
| Структурированный JSON | Нет | Нет | Да |
| Рекомендации | Да | Нет | Нет |
| Догадки о намерениях | Да | Нет | Нет |
| Догадки о конфигурации | Да | Нет | Нет |
| Осторожность с неизвестными значениями | Низкая | Выше | Низкая для hex |

**Текущий исследовательский parser baseline — R023.** Cisco, Juniper, Unix и Solaris подтверждают работу на четырёх corpus: 1999 исходных строк, 77 ReAct-исследований, 60 Families, 290 Grok. Quality gates, latest-first dispatch и global/Family consistency проходят независимый аудит.

**Текущий execution baseline — R032 + R033 + R034:** семь versioned Family Bundles исполняют Cisco, Juniper и Symantec KV в native Rust: 2786/2786 mapped, failed=0 и полный Python/Rust parity без mismatches. Масштаб 10 000 Families и production ReDoS-hardening пока не проверены; недостающие год/timezone задаются внешней лабораторной конфигурацией.

Эталонная точность всех captures, универсальность для других источников и production-качество не доказаны.
