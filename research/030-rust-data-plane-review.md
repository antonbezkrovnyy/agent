# R030 — Rust data plane и быстрый Family dispatch

> Цель: исполнять утверждённые parser → normalization → mapping revisions без LLM и определить архитектуру dispatch для масштаба порядка 10 000 Families. Реализован native Rust runtime, versioned Family Bundle, Aho–Corasick candidate index и golden parity с Python на Cisco evidence.

## 1. Результат

Для `cisco_asa_acl_verdict` собран исполняемый Bundle:

- parser revision 4;
- normalization revision 2;
- mapping revision 4;
- taxonomy `mp_siem_8.0`;
- compatibility SHA-256 `2e8ada129cbb9b140563346ad400bfb96df09850873a55b12f6604b3a7d5f490`.

Rust принимает RAW и возвращает captures, normalized values и финальное плоское MP SIEM event. Во время обработки не используются Python, LLM, SQLite или сеть.

Проверенный release-сценарий:

```text
candidate_count=1
family=cisco_asa_acl_verdict@4
status=mapped
captures=21
normalized=21
revisions=4/2/4
```

## 2. Граница control plane и data plane

Rust не читает SQLite. Control plane выполняет `bundle.py`:

```text
SQLite mapping revision
 → связанная normalization revision
 → связанная parser revision
 → taxonomy и dispatch metadata
 → Family Bundle JSON
```

Bundle содержит всё необходимое для исполнения и валидации:

- patterns, layers/stages, record schema и constraints;
- normalization fields и operations;
- mappings, source timezone и taxonomy identifier;
- поля, enum values и категории таксономии;
- evidence и descriptions;
- dispatch anchors;
- compatibility hash.

Hash вычисляется по каноническому JSON без поля `compatibility_hash`. При загрузке Rust повторно вычисляет его и отклоняет изменённый Bundle. Также проверяются schema version, Family identity и согласованность revisions.

Golden JSON — отдельный тестовый артефакт. Он не нужен runtime.

## 3. Загрузка и исполнение

При запуске Rust:

1. читает Bundle JSON;
2. проверяет hash и revision compatibility;
3. раскрывает `%{NAME:capture}` в regex;
4. компилирует Grok layers/stages;
5. строит общий Aho–Corasick automaton из dispatch anchors;
6. хранит скомпилированные правила в памяти.

На каждую RAW-строку:

```text
Aho–Corasick shortlist
 → полный parser кандидатов
 → ровно один accepted Family
 → captures и record/constraints validation
 → normalization operations
 → mappings, conditions и value_map
 → MP SIEM taxonomy validation
 → итоговый JSON
```

Ноль принятых Families даёт `no_family`, несколько — `ambiguous_family`. Первое случайное совпадение не выбирается.

Поддержаны текущие codecs: Grok, CEF, CSV и KV. Нормализация сохраняет исходные captures и записывает преобразованные значения отдельно. Неизвестные значения `value_map` дают ошибку. Timestamp без timezone требует внешний `source_timezone` только при преобразовании в UTC.

## 4. Что сохраняется

| Артефакт | Хранение |
|---|---|
| Исходные revisions | SQLite |
| Исполняемый versioned Bundle | JSON-файл |
| Скомпилированные regex | Только память |
| Aho–Corasick automaton | Только память |
| Golden results | JSON для тестов |
| Итоговые события | Возвращаются CLI; автоматически не сохраняются |

При перезапуске Rust повторно загружает Bundle и компилирует правила. На каждую запись повторной компиляции нет. Бинарный cache Registry пока не реализован.

## 5. Dispatch: что построено

Текущий индекс — **trie из литеральных якорей с fail-ссылками Aho–Corasick**, а не общее дерево полных regex.

Экспортёр извлекает из первого Grok-слоя устойчивые литералы и добавляет значения required static-полей, одинаковые во всех успешно разобранных evidence. Остаются только якоря, найденные в каждом evidence Family.

Для Cisco:

```json
{
  "strategy": "aho_corasick_all_anchors",
  "anchors": ["106100", ": %ASA-"],
  "fallback": false
}
```

Family попадает в shortlist только при наличии всех обязательных якорей. Полный Grok затем подтверждает candidate и извлекает captures.

Aho–Corasick выполняет поиск за `O(N + число найденных совпадений)`, а не за независимое от совпадений время. Это устраняет применение всех полных regex к каждой записи, но текущая фильтрация наборов якорей всё ещё проходит по загруженным Families и поэтому не является окончательным решением для 10 000 Families.

## 6. CLI

Одна строка:

```bash
log-data-plane run \
  --bundle research/r030-cisco-acl-verdict.bundle.json \
  --raw '<132>Feb 12 2023 ...'
```

Проверка dispatch:

```bash
log-data-plane route \
  --bundle research/r030-cisco-acl-verdict.bundle.json \
  --raw '<132>Feb 12 2023 ...'
```

Evidence validation и Python parity:

```bash
log-data-plane validate --bundle research/r030-cisco-acl-verdict.bundle.json
log-data-plane verify \
  --bundle research/r030-cisco-acl-verdict.bundle.json \
  --golden research/r030-cisco-acl-verdict.golden.json
```

`run --input` исполняет JSONL-поток результатов по непустым RAW-строкам файла. Несколько `--bundle` образуют общий Registry.

Экспорт и создание golden:

```bash
uv run python bundle.py export \
  --database research/r028-cisco-mapping.db \
  --family-id cisco_asa_acl_verdict \
  --output research/r030-cisco-acl-verdict.bundle.json

uv run python bundle.py golden \
  --database research/r028-cisco-mapping.db \
  --bundle research/r030-cisco-acl-verdict.bundle.json \
  --output research/r030-cisco-acl-verdict.golden.json
```

## 7. Проверки

Среда Windows не имела Rust и Visual Studio Build Tools. Локальный `stable-x86_64-pc-windows-gnu` toolchain установлен внутри проекта; глобальные настройки не изменялись.

Пройдены:

- 7 unit tests;
- 3 integration tests;
- `cargo fmt --check`;
- `cargo clippy --all-targets -- -D warnings`;
- release build;
- CLI `validate`: 4/4 evidence;
- CLI `verify`: 4/4, mismatches=0;
- release RAW → MP SIEM event.

Golden parity сравнивает `ok`, `status`, все `captures`, все `normalized` и полный `event`. Отдельно проверены lookahead, custom CEF/KV parsing, timestamp без года, timezone policy, dispatch/no-family и hash tamper detection.

Полная mapping-регрессия 1763 Cisco-строк не запускалась по решению пользователя. Benchmark производительности и нагрузочный тест 10 000 Families не выполнялись.

## 8. Нужен ли общий DFA на 10 000 Families

Общий DFA из полных парсеров не рекомендуется как первое решение:

- captures всё равно требуют отдельного извлечения;
- lookahead и подобные конструкции не представимы в обычном линейном DFA;
- объединение 10 000 полных выражений увеличит память и стоимость пересборки;
- pipeline stages и межполевые constraints не сводятся к одному regex.

Нужен общий **dispatch graph/index**, после которого остаются единицы кандидатов:

```text
Aho–Corasick literals
 → линейные routing regex / RegexSet там, где это доказуемо безопасно
 → точные vendor/message keys
 → 1–5 полных parser candidates
```

Необходимые изменения текущего baseline:

1. **Inverted anchor index.** Вместо прохода по всем Families хранить anchor → Families и считать обязательные совпадения только у затронутых Families.
2. **Lazy parser compilation.** При старте загружать dispatch metadata, компилировать полный parser при первом candidate match и кэшировать его.
3. **Формальный dispatch contract.** Явно хранить required/alternative anchors, routing regex и fallback; не полагаться только на вывод из evidence.
4. **Ограниченный fallback.** Не допускать полного перебора Families без routing key.
5. **Atomic Registry updates.** Собирать новый индекс и переключаться на него целиком.
6. **Линейный safe subset.** Использовать Rust `regex` для routing и совместимых parser patterns; `fancy-regex` оставлять только там, где нужны дополнительные возможности.

Общий binary snapshot можно добавить после измерений startup cost; он не является первоочередной заменой правильному индексу.

## 9. Ограничения и production-hardening

- Реализован native Rust runtime, не VRL.
- Полный Grok использует `fancy-regex`; lookahead может вызвать backtracking. Жёсткого timeout или полной ReDoS-защиты пока нет.
- Dispatch гарантирован на evidence, но формальное доказательство отсутствия false negatives для произвольных будущих записей требует dispatch contract.
- Все 10 000 Families одновременно не загружались; memory/startup/latency не измерены.
- Точность доказана на четырёх mapping evidence, а не на полном corpus.
- Binary compatibility cache и production ingestion API отсутствуют.

## 10. Артефакты

- `bundle.py` — экспорт Bundle и Python golden results.
- `rust/log-data-plane/` — crate, CLI, unit/integration tests.
- `research/r030-cisco-acl-verdict.bundle.json` — исполняемый Family Bundle.
- `research/r030-cisco-acl-verdict.golden.json` — четыре Python reference results.

[← R029](029-cisco-pipeline-architecture-review.md) · [Все исследования](README.md)
