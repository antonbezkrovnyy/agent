# R023 — Solaris sshd/audit на Registry R022

> Четвёртый источник: Solaris SSH, login и audit. Все исходные 67 строк обработаны: 50 ReAct-исследований, 17 покрытий без модели, `failed=0`. Система подтвердила работу на большом разнообразии позиционных текстовых грамматик, а не только на повторяющихся логах.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R023` |
| Дата | 23 ноября 2026 г. |
| Baseline | Копия `research/r022-unix.db` |
| Вход | `C:\Users\aa.bezkrovnyy\Downloads\opensecuritydatalake\parser-react\data\os_solaris.log` |
| Исходных строк | 67 |
| ReAct-исследования | 50 |
| Итог | Все 67 покрыты, `failed=0` |

## Значение эксперимента

Solaris оказался самым разнообразным corpus:

- SSH auth methods;
- login console/root/failure;
- audit syscalls;
- cron/at jobs;
- login - ssh/rlogin/telnet/local;
- reboot/poweroff/init;
- auditon/ACL/chown/chmod/bind/connect/accept;
- user management;
- rsh access;
- sudo/su;
- отдельный `WARNING` без обычного syslog-конверта.

Поэтому большое количество исследований здесь ожидаемо: модель строит parser для каждой новой грамматики, а не для каждого нового IP или имени пользователя.

## Reuse и новые грамматики

### Покрыто без модели

17 строк совпали с уже сохранёнными revisions после их создания. Например, строка 2 покрылась `solaris_sshd_received_disconnect@1`, а строка 57 — существующей `solaris_audit_modify_login_information@4`.

### Revisions

Некоторые примеры последовательного расширения:

- `solaris_sshd_failed_password@2` — добавлен `keyboard-interactive`;
- `solaris_audit_modify_login_information@1–4` — repository, old/new shell, homedir, group assignment;
- `solaris_audit_rsh_access@2` — успешный доступ после failed-варианта;
- `solaris_audit_open_access_obj@2` — `open(2) - write,trunc`;
- `solaris_reboot_notice@2` — исправление промежуточной revision через quality gates.

### Новые Families

Созданы 44 новых Families, включая:

- `solaris_sshd_accepted_keyboard_interactive`, `solaris_sshd_accepted_publickey`, `solaris_sshd_accepted_password`;
- `solaris_login_console_failure`, `solaris_login_root_login_console`;
- `solaris_audit_recvfrom_syscall`, `solaris_audit_socket_syscall`, `solaris_audit_connect_syscall`, `solaris_audit_bind_syscall`, `solaris_audit_accept_syscall`;
- `solaris_audit_login_ssh_session`, `solaris_audit_login_rlogin_session`, `solaris_audit_login_telnet_session`, `solaris_audit_login_local_session`;
- `solaris_audit_modify_login_information`, `solaris_audit_add_user_login`, `solaris_audit_delete_user_login`;
- `solaris_sudo_command_tsid`, `solaris_sudo_auth_failure_attempts`, `solaris_su_succeeded_switch`, `solaris_su_failed_switch`;
- `normalization_rule_reference_warning` для последней нестандартной WARNING-строки.

## Итоговый Registry

| Объект | Количество |
|---|---:|
| Family | 60 |
| Parser revisions | 86 |
| Grok | 290 |
| Research results | 77 |
| Semantic embeddings | 462 |

Добавлено по сравнению с R022:

- 44 Families;
- 54 revisions;
- 172 Grok;
- 50 research results;
- 280 embeddings.

## Quality repair

Независимая проверка подтвердила все 44 новые Families через `check_response`, но обнаружила metadata drift в шести глобальных Grok:

- `AUDIT_PATH`;
- `AUDIT_AUDITON_EVENT_NAME`;
- `AUDIT_MODIFY_LOGIN_TEXT_BODY`;
- `AUDIT_DELETE_USER_UAUTH_TEXT_BODY`;
- `AUDIT_RSH_ACCESS_BODY_TEXT`;
- `AUDIT_REMOTE_NODE`.

Examples синхронизированы с проверенными latest responses. Для `AUDIT_REMOTE_NODE` дополнительно исправлена граница: теперь требуется хотя бы одна буква и запрещён ведущий дефис, поэтому IPv4 и `-bad-node` не принимаются как hostname.

После repair:

- 290/290 Grok проходят examples и dependencies;
- все 50 новых research responses не имеют source mismatches;
- latest coverage — один match на каждую строку;
- definition conflicts не появились.

## Изменение исходного файла

Во время анализа исходный файл содержал 67 строк. Позднее текущий файл оказался из 66 строк: последняя `WARNING` удалена из него внешне, а не агентом.

RAW строки 67 сохранена в `r023-solaris.progress.jsonl`, parser `normalization_rule_reference_warning@1` создан и проверен. Таким образом, покрытие подтверждено и для исходных 67, и для текущих 66 строк.

## Оценка

Система подтвердила целевое поведение:

- новые грамматики исследуются моделью;
- повторяющиеся и совместимые записи переиспользуют parser;
- разные операции не склеиваются в одну универсальную regex;
- revisions расширяются по фактическим вариантам;
- source paths, examples, constraints и definition consistency проверяются независимо.

Цена эксперимента велика: 50 исследований и почти три часа второго запуска. Но это связано прежде всего с разнообразием самого corpus, а не с ошибками модели.

## Ограничения

- Нет независимого golden-эталона правильности всех captures.
- 44 новые Families показывают разнообразие grammar, но не доказывают необходимость каждой из них в долгосрочной таксономии.
- Свободные описания требуют отдельной смысловой проверки.
- Проверяется только парсинг; нормализация не исследуется.
- LEEF/CEF/JSON/CSV/KV в эксперимент не входили.

## Вывод

**R023 — текущий исследовательский baseline.** Система подтвердила работу на четырёх источниках: Cisco, Juniper, Unix и Solaris. Общий исходный объём — 1999 строк, 77 ReAct-исследований.

Главный результат — не только покрытие, а подтверждение, что модель может последовательно строить сотни атомов и revisions для разнообразных текстовых форматов с независимым контролем качества.

## Артефакты

- `research/r023-solaris.db` — итоговый Registry.
- `research/r023-solaris.summary.json` — машинная сводка.
- `research/r023-solaris.progress.jsonl` — corpus и `quality_repair`.
- `research/023-solaris-review.md` — настоящий отчёт.

[← R022](022-unix-review.md) · [Все исследования](README.md)
