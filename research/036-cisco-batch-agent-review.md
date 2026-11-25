# R036 — пакетное исследование Cisco corpus через Drain3 и batch agent

> Цель: расширить существующего однострочного агента поддержкой батчей без изменения прежнего режима, затем с чистого Registry обработать `data/artifact_cisco_firewall.log`. Итог: пакетный механизм работает и все 1763 строки имеют parser, но результат не готов к production. По сравнению с R020 число Research уменьшилось только с 10 до 9, Family стало 7 вместо 3, одна revision потеряла обратную совместимость, а ещё одна осталась без Research Response.

## 1. Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | R036 |
| Дата | 25 ноября 2026 г. |
| Вход | `data/artifact_cisco_firewall.log`, 1763 непустые строки |
| Начальное состояние | Новая пустая SQLite БД |
| Batch Registry | `research/cisco-batch-clean.db` |
| Baseline | `research/r020-corpus.db` |
| Кластеризация | `drain3.TemplateMiner`, полностью в RAM |
| Параметры Drain3 | `similarity_threshold=0.4`, `depth=4`, `persistence_handler=None` |
| Samples | До 5 оригинальных RAW на кластер, Farthest Point Sampling |
| Предобработка RAW | Отсутствует: без масок, нормализации и отсечения заголовков |
| Batch response | `1..N FamilyResearchGroup` + `unresolved_sample_ids` |
| Normalization/mapping | Не запускались |
| Итоговое покрытие | 1763/1763, uncovered=0, ambiguous Family matches=0 |
| Статус | Эксперимент завершён; найдены дефекты качества, production readiness не подтверждена |

## 2. Выбор кластеризатора

Предварительный замер выполнялся только на CPU. Для всех вариантов использовался один Farthest Point Sampling: первая уникальная строка, затем жадный выбор максимального минимального косинусного расстояния до уже выбранных.

| Подход | Кластеров | Строк для агента | Комментарий |
|---|---:|---:|---|
| IPLoM через `logparser3` | 23 | 93 | Дробление по timestamp и вариантам envelope |
| Spell через `logparser3` | 18 | 82 | Меньше дробления, но нужны временные файлы адаптера |
| Drain через `logparser3` | 11 | 49 | Одно смешение severity |
| TF-IDF + Agglomerative, исходный token pattern, порог 0.35 | 1674 | 1763 | Практически отсутствие кластеризации |
| TF-IDF + Agglomerative, новый token pattern, порог 0.45 | 19 | 78 | Рабочий запасной вариант, одно объединение ASA/PIX |
| **Drain3, полные RAW, RAM** | **10** | **45** | Лучший предварительный результат |

Финальный TF-IDF token pattern для кластеризации:

```python
r"[a-zA-Z_%-][a-zA-Z0-9_%-]*"
```

Он игнорирует чисто числовые токены, но сохраняет коды `%ASA-4-106100` и токены, начинающиеся с буквы или `%`. Для diversity sampling сохранён `r"\S+"`, чтобы IP, порты и числовые хвосты влияли на контрастность.

Для основного эксперимента выбран Drain3. Замер кластеризации и sampling на полных 1763 RAW занял около 0,138 секунды. Это время не включает исследования LLM.

## 3. Реализованная архитектура

Прежние `run_observation()`, `RESEARCH_PROMPT`, `Response`, `make_agent()` и `corpus.py` сохранены. Пакетный режим добавлен отдельно:

```text
Все RAW
→ dispatch существующих parser families
→ только непокрытые строки
→ Drain3 + diversity sampling
→ ResearchBatch(sample_id, raw, cluster metadata)
→ один batch LLM call
→ ResearchBatchResponse с 1..N parser groups
→ независимая проверка каждой группы на всех её samples
→ сохранение обычного Response для каждой группы
→ dispatch всех member lines
→ опциональные построчные normalization/mapping
```

Основные компоненты:

- `schemas.py`: `LogSample`, `ResearchBatch`, `FamilyResearchGroup`, `ResearchBatchResponse`;
- `agent.py`: `make_batch_agent()`, `run_batch_step()`, `run_research_batch()`;
- `prompts.py`: отдельный batch prompt с минимизацией числа Family и полным назначением `sample_id`;
- `validation.py`: `check_batch_response()` и поддержка `str | list[str]` в `check_response()`;
- `log_batching_drain3.py`: `member_indices` и `sample_indices` для сохранения происхождения RAW и дублей;
- `batch_corpus.py`: отдельный runner с resume, fingerprint батчей, полной финальной проверкой и downstream-флагами;
- `tests/test_batch_research.py`: распределение samples, несколько Family, unresolved, сохранение групп и lineage дублей.

Каждый `sample_id` обязан ровно один раз попасть в группу или `unresolved_sample_ids`. Каждая группа сохраняет обычный `Response`, поэтому существующие parser Registry и downstream-модель не требуют нового формата Family.

## 4. Ход реального запуска

Команда:

```bash
RESEARCH_DB=research/cisco-batch-clean.db \
  .venv/Scripts/python -u batch_corpus.py data/artifact_cisco_firewall.log
```

Первый запуск:

```text
START total=1763 covered=0 uncovered=1763
```

Завершены шесть Research, после чего ожидание LLM на `cluster_id=9` прекратило прогресс. Процесс был остановлен вручную. На момент остановки parser текущего кластера уже был сохранён, но финальный `ResearchBatchResponse` отсутствовал.

Повторный запуск с той же БД:

```text
START total=1763 covered=1753 uncovered=10
```

Оставшиеся три батча завершились:

| Research | Samples | Результат |
|---:|---|---|
| 7 | 1627–1630 | `cisco_asa_106100_acl_hit_deny@2` |
| 8 | 1760–1763 | `firepower_fdm_ipssi_connection_kv@1` |
| 9 | 201–202 | `cisco_asa_106023_deny_ip_protocol_number@1` |

Код завершения повторного запуска — 0. Его длительность составила 1393,532 секунды. Полный эксперимент с первым запуском, ожиданиями и перезапуском занял примерно 59 минут. Два заметных ожидания API были связаны с отсутствием клиентского таймаута: в модели задан `timeout=None`.

## 5. Сравнение с построчными baselines

| Метрика | R018 | R020 — основной baseline | R036 batch |
|---|---:|---:|---:|
| Строк | 1763 | 1763 | 1763 |
| Research | 9 | 10 | 9 |
| RAW, представленные модели | 9 | 10 | 45 |
| Уникальные Family | 3 | 3 | 7 |
| Parser revisions | 9 | 12 | 10 |
| Grok-конструкции | 48 | 49 | 31 |
| Pipeline revisions | 0 | 0 | 1 |
| Непокрытые строки | 0 | 0 | 0 |
| Покрытие latest revisions | Множественные matches | 1763, ровно 1 match | 860 |
| Строки, требующие historical fallback | Да | 0 | 903 |
| Длительность | 822 с, повторный запуск | 2321 с | ~59 мин с ожиданиями |

R020 выбран основным baseline, поскольку после R018 добавлены constraints, source-path validation, исполняемые Grok examples, save gate и latest-first dispatch. Сравнение только с R018 занижает требования к качеству batch-результата.

**Главный вывод сравнения:** число Research уменьшилось лишь с 10 до 9. При этом модель получила 45 RAW вместо 10, создала 7 Family вместо 3 и потеряла свойство R020, при котором latest revisions самостоятельно покрывают весь corpus ровно одним match.

Более дробная модель не обязательно означает семантическую ошибку каждой Family, но она увеличивает число независимых revisions и downstream-артефактов. Практического преимущества перед R020 на этом corpus не показано.

## 6. Почему `stages_json` заполнен только один раз

Это соответствует двум режимам `ParserDefinition`:

- девять revisions имеют `kind=grok`: заполнены `layers_json`, `stages_json=[]`;
- одна revision имеет `kind=pipeline`: заполнены `stages_json`, `layers_json=[]`.

Единственная pipeline Family:

```text
firepower_fdm_ipssi_connection_kv@1
```

Её стадии:

```text
grok → kv
```

Grok снимает envelope и извлекает payload, затем KV-кодек разбирает поля `Protocol: TCP, SrcIP: ..., DstIP: ...`. Пустой `stages_json` у Grok Families не является потерей данных.

## 7. Независимый аудит корректности

### 7.1. Подтверждённое покрытие

Все 1763 RAW повторно прошли через сохранённые revisions с проверкой required fields, constraints и пустого residual:

```text
parser_families revisions: 10
unique Family IDs:          7
research_results:            9
grok_patterns:              31
uncovered:                  []
ambiguous Family matches:   0
```

Следовательно, полное лексическое покрытие corpus действительно получено.

### 7.2. Дефект обратной совместимости `cisco_asa_106023_deny_acl@2`

Девять из десяти revisions проходят всё собственное evidence. Исключение:

```text
cisco_asa_106023_deny_acl@2
```

У этой revision 10 evidence-строк. Пять новых строк с hostname проходят, пять прежних без hostname не проходят.

Фрагменты envelope показывают причину:

```text
Revision 1:
%{ASATIMESTAMP:timestamp}: %ASA-...

Revision 2:
%{ASATIMESTAMP:timestamp}(?: %{ASAHOSTNAME:hostname} :)? %ASA-...
```

В revision 2 двоеточие попало внутрь optional-группы hostname. Если hostname отсутствует, исчезает и необходимое двоеточие после timestamp.

При этом `parser_description` и Research утверждают, что candidate успешно прошёл все 10 evidence-строк. Независимое исполнение это утверждение не подтвердило.

Последствие:

- latest revisions покрывают 860 строк;
- ещё 903 строки покрываются только через `cisco_asa_106023_deny_acl@1` historical fallback;
- общий результат 1763/1763 зависит от сохранения старой revision.

Это реальная потеря обратной совместимости и противоречие между evidence, описанием и фактическим parser.

### 7.3. Отсутствующий Research Response

Для следующей revision исходный Research отсутствует:

```text
cisco_asa_106023_deny_icmp_typecode@2
```

Parser был сохранён до остановки первого зависшего запуска, но финальный Response не вернулся. Сама revision успешно исполняется на всех 10 evidence-строках. Дефект относится к полноте handoff: normalization agent не сможет получить исходный Research через `get_parser_research()` и будет использовать Family descriptions.

### 7.4. Ограничение проверки исторических Research

При повторной проверке Research старых revisions `check_response()` может вернуть `parser_not_persisted`, даже если Research точно соответствует своей исторической revision.

Причина: проверка сохранения использует `storage.list_families()`, который возвращает только latest revisions. Это ограничение валидатора, а не доказательство ошибки старого parser. Для исторической проверки необходимо сравнивать Response с конкретной `family_id@revision`.

### 7.5. Границы доказанного

Проверены полное совпадение parser, required captures, constraints и пустой residual. Не выполнялись:

- сверка каждого capture с golden-набором;
- normalization;
- mapping в MP SIEM;
- оценка качества консолидации Family по внешнему эталону.

Поэтому отчёт подтверждает лексическое покрытие, но не эталонную правильность всех извлечённых значений.

## 8. Тесты и проверки реализации

До реального запуска:

```text
uv run pytest -q
12 passed
```

Дополнительно подтверждено:

- прежний Drain3-результат не изменился: 10 кластеров и 45 samples;
- `member_indices` и `sample_indices` указывают на правильные RAW;
- одиночный `check_response(response, raw)` продолжает работать;
- вариант `check_response(response, raws)` исполняет parser на каждом RAW;
- ошибочный sample отклоняется;
- несколько Family в одном batch response сохраняются независимо;
- batch corpus CLI импортируется и запускается с `--help`;
- `uv lock --check` и `git diff --check` проходят.

Реальный запуск выявил проблемы, которые unit-тесты не покрывали: регрессию на полном evidence сохранённой Family, потерю Research при прерванном запуске и длительное ожидание LLM без таймаута.

## 9. Итоговая оценка

Пакетный режим технически реализован и способен исследовать несколько RAW и порождать несколько Family за один вызов. Drain3 обеспечивает быструю предварительную группировку без файлов и изменения RAW, а runner поддерживает resume и финальный полный dispatch.

Однако текущий эксперимент **не демонстрирует преимущества над R020**:

- уменьшение Research только с 10 до 9;
- увеличение входного контекста с 10 до 45 RAW;
- 7 Family вместо 3;
- 903 строки зависят от historical fallback;
- одна revision противоречит своему evidence;
- одна revision не имеет Research;
- наблюдались длительные ожидания API.

Результат следует считать исследовательским прототипом, а не production-ready заменой построчного pipeline.

## 10. Необходимые действия перед следующим экспериментом

1. Исправить `cisco_asa_106023_deny_acl@2` или отозвать её как latest, сохранив корректную обратную совместимость.
2. Перед завершением batch проверять parser не только на новых samples, но и на всём evidence сохранённой revision.
3. Для `extend_layer` делать обязательной регрессию прежнего evidence; для несовместимой грамматики создавать отдельную Family/variant вместо latest extension.
4. Восстановить Research для `cisco_asa_106023_deny_icmp_typecode@2` без повторного создания дублирующей Family.
5. Разрешить проверку Research против конкретной исторической revision.
6. Добавить конечные LLM timeout/retry и понятный статус ожидания в журнал.
7. Повторить чистый запуск и потребовать latest-only покрытие 1763/1763 без увеличения Family сверх обоснованного.
8. Только после parser audit запускать normalization/mapping и golden-проверку captures.

## 11. Артефакты

### Batch Registry и журналы

- `research/cisco-batch-clean.db` — итоговый Registry;
- `research/cisco-batch-clean.batch-progress.jsonl` — события и resume fingerprints;
- `research/cisco-batch-clean.batch-summary.json` — summary повторного запуска;
- `research/036-cisco-batch-agent-review.md` — этот отчёт.

### Реализация

- `agent.py`;
- `batch_corpus.py`;
- `schemas.py`;
- `prompts.py`;
- `validation.py`;
- `log_batching_drain3.py`;
- `log_batching_tfidf.py`;
- `tests/test_batch_research.py`.

### Baselines

- `research/r018-cisco-corpus.db`;
- `research/r020-corpus.db`;
- `research/018-cisco-corpus-review.md`;
- `research/020-quality-gates-review.md`.

[Все исследования](README.md)
