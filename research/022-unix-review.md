# R022 — Unix sshd/sudo/PAM на Registry R021

> Третий независимый источник после Cisco и Juniper. Все 143 строки Unix corpus обработаны: 14 ReAct-исследований, 12 новых Families, остальные 129 строк покрыты сохранёнными parser revisions. Quality gates и полная независимая проверка пройдены без repair.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R022` |
| Дата | 23 ноября 2026 г. |
| Baseline | Копия `research/r021-juniper.db` |
| Вход | `C:\Users\aa.bezkrovnyy\Downloads\opensecuritydatalake\parser-react\data\cybercamp_art_unix.log` |
| Строк | 143 |
| Тип формата | Обычные текстовые sshd, sudo, PAM, systemd, su |
| ReAct-исследования | 14 |
| Итог | 143/143 покрыто, `failed=0` |

## Значение эксперимента

Этот corpus содержит не один повторяющийся шаблон, а серию разных событий одного Unix-сеанса:

- PAM open/close;
- SSH publickey и disconnect;
- sudo command;
- systemd login/logout/remove;
- su switch user.

Поэтому он проверил и построение новых Families, и переиспользование базовых syslog-атомов, и расширение похожих грамматик.

## Новые Families

| Строка | Family | Назначение |
|---:|---|---|
| 1 | `pam_unix_session_closed` | PAM session close |
| 2 | `sshd_accepted_publickey` | SSH publickey auth с алгоритмом и fingerprint |
| 3 | `pam_unix_session_opened` | PAM session open |
| 4 | `systemd_logind_new_session` | Создание systemd session |
| 5 | `pam_env_deprecated_user_environment` | Устаревшее PAM env сообщение |
| 8 | `sudo_command_session` | Команда через sudo |
| 63 | `sshd_received_disconnect` | Received disconnect |
| 64 | `sshd_disconnected_from_user` | Disconnected by user |
| 66 | `systemd_logind_session_logged_out` | Logout session |
| 121 | `sshd_connection_closed_by` | Connection closed by peer |
| 124 | `su_switch_user_session` | Переключение пользователя через su |
| 129 | `systemd_logind_removed_session` | Удаление session |

Дополнительно расширены:

- `pam_unix_session_closed` до revision 2 на CRON-варианте;
- `pam_unix_session_opened` до revision 2 на варианте `by (uid=1000)`.

## Покрытие

| Показатель | Значение |
|---|---:|
| Всего строк | 143 |
| ReAct-исследования | 14 |
| Покрыто без модели | 129 |
| Непокрыто | 0 |
| Matches latest на строку | 1 |

После появления нужных revisions повторные PAM/SSH-события обрабатывались детерминированно. Одна длинная пауза на строке 9 не привела к ошибке: после её завершения сохранённые Families покрыли всё до строки 62.

## Итоговый Registry

| Объект | Количество |
|---|---:|
| Family | 16 |
| Parser revisions | 32 |
| Grok | 118 |
| Research results | 27 |
| Semantic embeddings | 182 |

Добавлено по сравнению с R021:

- 12 Families;
- 14 revisions;
- 54 Grok;
- 14 research results;
- 82 embeddings.

## Независимая проверка

Для каждой из 12 новых Families вызван `check_response` на latest response. Подтверждено:

- parser исполняется на исходной RAW;
- source paths корректны;
- сохранённая Family совпадает с Response;
- global Grok definitions не конфликтуют;
- examples проходят;
- latest coverage — ровно один match на каждую из 143 строк.

Дополнительно:

- все 14 новых research responses не имеют source mismatches;
- все 118 Grok проходят positive/negative examples и dependency validation;
- repair не потребовался;
- журнал дополнен событием `quality_audit`.

## Оценка модели

Сильная сторона — модель не пыталась объединить все Unix-сообщения в одну универсальную regex. Разные события получили отдельные Families, а близкие PAM-варианты оформлены revisions.

Также видна цена работы: 14 исследований на 143 строки и около 48 минут второго запуска. Это больше, чем на повторяющихся Cisco-строках, но ожидаемо для разнообразного набора событий.

## Ограничения

- Проверен один Unix corpus; другие сообщения sshd/PAM могут потребовать новых variants.
- Нет независимого golden-эталона смысла всех captures.
- Некоторые базовые атомы создаются повторно для разных Families; вопрос дальнейшей дедупликации остаётся.
- Проверяется только парсинг, не нормализация.

## Вывод

**R022 — текущий исследовательский baseline.** Система подтвердила работу уже на трёх разных corpus: Cisco, Juniper и Unix. Общий объём — 1932 строки, 27 ReAct-исследований, 16 Families.

Главное отличие R022 — не просто покрытие, а успешная обработка разнородных текстовых событий с последующей независимой проверкой всех новых Families и всех 118 Grok.

## Артефакты

- `research/r022-unix.db` — итоговый Registry.
- `research/r022-unix.summary.json` — машинная сводка.
- `research/r022-unix.progress.jsonl` — corpus и `quality_audit`.
- `research/022-unix-review.md` — настоящий отчёт.

[← R021](021-juniper-review.md) · [Все исследования](README.md)
