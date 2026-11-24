# R024 — частичный CommuniGate на Registry R023

> Эксперимент остановлен пользователем из-за длительности. Завершены 12 из 24 строк: 11 ReAct-исследований и одно повторное покрытие, `failed=0`. Это частичный результат, а не новый полный corpus baseline.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R024` |
| Дата | 24 ноября 2026 г. |
| Baseline | Копия `research/r023-solaris.db` |
| Вход | `C:\Users\aa.bezkrovnyy\Downloads\opensecuritydatalake\parser-react\data\mail_communigate.log` |
| Обработано | 12 из 24 |
| ReAct-исследования | 11 |
| Покрыто без модели | 1 |
| Итог | Остановлено пользователем перед завершением строки 13 |

## Ход обработки

Созданы семь новых Families:

- `mail_account_login_session` — два варианта успешного входа;
- `mail_account_login_failure` — incorrect password и too many failed logins;
- `mail_account_message_delivered`;
- `mail_localrule_message_stored` — успешное сохранение и quota exceeded;
- `mail_enqueuer_rule_message_stored`;
- `airsync_client_ping_request`;
- `mail_account_size_added`.

Строка 11 покрылась `airsync_client_ping_request@1` без нового исследования. Остальные завершённые непокрытые грамматики получили собственные revisions. Всего добавлено 13 parser revisions.

## Независимая проверка

Для всех 11 research responses подтверждено:

- каждый dynamic `record_schema.source` существует в `structure`;
- parser исполняется на исходной RAW без ошибок валидации;
- точный Response сохранён в указанной historical revision;
- более поздние revisions не делают старый response ошибочным: `check_response` сравнивает только с latest, поэтому аудит выполнен по всем revisions.

Все 12 завершённых строк дают ровно одно совпадение parser. Все 356 сохранённых Grok проходят dependencies и positive/negative examples.

## Остановка строки 13 и repair

Остановка произошла во время исследования IMAP secure connection failure. Модель успела сохранить восемь Grok-черновиков, но не создала parser revision и research result. Черновики сохранены в Registry и явно не считаются завершённой Family.

Первый аудит обнаружил неверный negative example у `IMAP_TLS_ACCEPT_FAIL_BODY`: суффикс `trailing` входит в свободный текст ошибки и поэтому является валидным входом. Negative example заменён на тело без обязательного `. Error Code=...`; definition и positive examples не изменялись. Описание и semantic embedding синхронизированы. Повторная проверка всех 356 Grok прошла без ошибок.

## Итоговый Registry

| Объект | Количество |
|---|---:|
| Family | 67 |
| Parser revisions | 99 |
| Grok | 356 |
| Research results | 88 |
| Semantic embeddings | 554 |

## Ограничения

- Строки 13–24 не обработаны; покрытие 24/24 не заявляется.
- Восемь Grok от прерванной строки 13 проверены как атомы, но не как законченный parser.
- Нет независимого golden-эталона смысловой правильности captures.
- Проверяется только парсинг; нормализация не исследуется.
- **R023 остаётся последним полным исследовательским baseline.**

## Артефакты

- `research/r024-communigate.db` — частичный Registry.
- `research/r024-communigate.summary.json` — машинная сводка со статусом `stopped_by_user`.
- `research/r024-communigate.progress.jsonl` — 12 завершённых строк, остановка и quality audit.
- `research/024-communigate-review.md` — настоящий отчёт.

[← R023](023-solaris-review.md) · [Все исследования](README.md)
