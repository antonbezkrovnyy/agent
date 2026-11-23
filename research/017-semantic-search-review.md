# R017 — семантический поиск по Registry через embedding API

> Для Family, parser revisions и Grok добавлены развёрнутые описания и семантический поиск. Локальный Qwen3-Embedding-0.6B заменён на GPT-Lab `embedding` после проверки качества ранжирования. Семантика отбирает кандидатов, окончательное решение по-прежнему принимается исполнением parser.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R017` |
| Дата | 23 ноября 2026 г. |
| Baseline | `R016`, две Family и 6 Grok |
| Первый вариант | Локальный `Qwen/Qwen3-Embedding-0.6B`, 1024 dimensions |
| Итоговый вариант | GPT-Lab `embedding`, 2560 dimensions |
| API | OpenAI-совместимый `POST /embeddings` |
| Индекс | `semantic_embeddings_v2` в SQLite |
| Сущности | Family, parser revisions, Grok patterns |
| Статус | Поиск работает; обе строки независимо завершились правильным `reuse` |

## Исследовательский вопрос

Можно ли заменить выбор кандидатов по точному имени и SQL `LIKE` на семантический поиск, не превращая similarity в доказательство применимости parser?

**Результат: да. Embeddings ранжируют кандидатов, затем `execute_grok_parser` и независимая валидация подтверждают или отклоняют их.**

## Почему провайдер заменён

Локальная Qwen3-Embedding-0.6B была скачана и реально исполнена: векторы размерности 1024 получены. Однако на близких parser descriptions модель нестабильно ранжировала нужную revision, а пользователь отметил низкое практическое качество локального варианта.

В `/models` GPT-Lab доступны `embedding`, `embed-ru`, `embed-ru-balanced`, `embed-ru-fast` и другие модели. В `.env` уже настроена `embedding`; smoke test подтвердил размерность 2560. Согласно [документации GPT-Lab](https://gpt.jet.su/agents.md), это профиль для смешанных RU/EN/code embeddings, что соответствует техническим описаниям Registry.

Локальные `torch`, `transformers` и `sentence-transformers` удалены из проекта после переключения.

## Развёрнутые описания

### FamilyDescription

Сохраняет стабильную смысловую идентичность Family:

- summary;
- event semantics;
- envelope and payload;
- dispatch;
- applicability;
- exclusions.

### ParserDescription

Описывает конкретную ревизию:

- summary;
- input contract;
- layering;
- output contract;
- revision reason;
- regression and limitations.

### Grok description

При сохранении требуется раскрывать назначение, положительные и отрицательные примеры, captures, зависимости и границы совпадения.

Старые записи не потеряны: для них строится технический fallback из Evidence, layers и record_schema. Такие описания менее информативны, поэтому новые сохранения обязаны содержать полные структуры.

## Индекс и модельные версии

Добавлена таблица `semantic_embeddings_v2`:

```text
entity_type + entity_id + index_version
content + content_hash
model_name + dimension
vector_json + created_at
```

Это отдельный индекс, а не смешение векторов Qwen и GPT-Lab. Поиск фильтруется текущей моделью. При смене содержимого или модели embeddings пересчитываются.

Векторы нормализуются, score вычисляется как cosine similarity. Отдельный векторный сервер не требуется: корпус хранится и сканируется в SQLite.

## Новые инструменты

| Инструмент | Область поиска | Результат |
|---|---|---|
| `semantic_search_parser_families` | Последняя revision каждой Family | `family_id`, revision, Evidence, description, score |
| `semantic_search_parsers` | Все parser revisions | `family_id`, revision, Evidence, description, score |
| `semantic_search_grok_patterns` | Все сохранённые Grok | name, definition, description, score |

Инструменты возвращают кандидатов с score без жёсткого порога. `get_parser_family` дополнен опциональной `revision`, чтобы можно было загрузить найденную не последнюю ревизию.

Прежние `list_parser_families` и `search_grok_patterns` оставлены как fallback.

## Контур выбора

```text
RAW
→ смысловой поисковый запрос без концентрации на IP/hex-шуме
→ semantic search Family / parser / Grok
→ fallback на полный каталог или LIKE при необходимости
→ get_parser_family(family_id, revision)
→ execute_grok_parser на RAW
→ validate_grok_result
→ reuse / extend_layer / new_variant / new_family / unknown
→ регрессия и независимая проверка runner-ом
```

Similarity не является условием успеха и не заменяет полное совпадение Grok.

## Контрольные запросы через API

| Поиск | Первый результат | Score | Следующий результат | Score |
|---|---|---:|---|---:|
| Family `106023`, без host, src/dst | `asa_106023_acl_deny` | 0.828938 | `asa_106100_acl_hit` | 0.690582 |
| Parser `106023`, access-group, ASAIFACE/ASAQSTRING | `asa_106023_acl_deny@1` | 0.761389 | `asa_106100_acl_hit@1` | 0.725369 |
| Непустое содержимое двойных кавычек | `ASAQSTRING` | 0.821394 | — | — |

Все три утверждения о первом кандидате подтверждены программно.

## Полные ReAct-прогоны

Проверка выполнена на отдельной временной копии R016, чтобы не менять исследовательский baseline.

| Вход | Semantic top | Решение | Исполнение | Дубликат |
|---|---|---|---|---|
| `ASA-106100` | `asa_106100_acl_hit`, 0.819354 | `reuse@1` | Полное совпадение, residual пуст | Нет |
| `ASA-106023` | `asa_106023_acl_deny`, 0.824967 | `reuse@1` | Полное совпадение, residual пуст | Нет |

Во втором прогоне альтернативная Family получила близкий score 0.798956, но окончательный выбор определён фактическим исполнением, а не относительной близостью описаний.

После проверки временные БД удалены.

## Обработка конфигурации и ошибок

- Используются `EMBED_API_KEY`, `EMBED_API_BASE_URL`, `EMBED_API_MODEL`.
- Ключ не выводился и не записывался в код или артефакты.
- Модель не выдумывалась: доступность подтверждена `GET /models` и реальным `POST /embeddings`.
- OpenAI client настроен с timeout и повторами только штатных временных ошибок.
- Прямая зависимость `openai>=3.57.0` добавлена в `pyproject.toml`.

## Ограничения

- Корпус мал: две Family и 6 Grok; статистической оценки retrieval quality нет.
- Близкие score нельзя интерпретировать как вероятность правильного parser.
- Старые fallback-описания слабее новых структурированных описаний.
- На реальных ReAct-прогонах проверен `reuse`, а не создание новой Family через semantic search.
- Полные `FamilyDescription` и `ParserDescription` проверены на уровне инструментов и контрольного Registry, но новая реальная Family после R017 ещё не создавалась.
- Детерминированный `execute_all_families`, corpus regression и жизненный цикл `candidate → validated → published` отсутствуют.

## Вывод

**R017 — текущий системный baseline.** Registry получил смысловые описания и версионируемый semantic index. GPT-Lab `embedding` показал правильное ранжирование на контрольных запросах, а два независимых запуска подтвердили практический `reuse` без дублирования Family.

Надёжность контура сохраняется за детерминированной границей: найти кандидата можно семантически, принять parser — только через исполнение и регрессию.

## Артефакты

- `research/017-semantic-search-results.json` — измеренные score и результаты `reuse` без reasoning.
- `research/017-semantic-search-review.md` — настоящий отчёт.
- `semantic.py` — embedding API, индексация и поиск.
- `schemas.py` — `FamilyDescription` и `ParserDescription`.
- `tools.py` — инструменты semantic search и сохранения.
- `storage.py` — SQLite schema и `semantic_embeddings_v2`.
- `prompts.py` — единый prompt с semantic-first поиском и fallback.

[← R016](016-unified-independent-runs-review.md) · [Все исследования](README.md)
