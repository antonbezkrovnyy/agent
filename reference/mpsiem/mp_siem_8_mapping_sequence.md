# MaxPatrol SIEM 8.0: последовательность маппинга распарсенного события

Схема полей и значения Enum: `mp_siem_8_taxonomy_reference.md`.

Иерархия категорий: `mp_siem_8_event_categories.md`.

Ниже — практический порядок заполнения схемы. Официальная документация не требует выполнять маппинг именно в такой последовательности.

Кратко:

```text
1. Обязательные поля
2. Категория события
3. Субъект и его атрибуты
4. Атрибуты объекта
5. Сетевые src / dst
6. Дополнительные event_src
7. Остальные параметры взаимодействия
8. Информационные и нестандартные поля
9. Автоматические поля не заполнять вручную
10. Проверить результат
```

## Шаг 1. Заполнить обязательный каркас

### 1.1. Идентифицировать источник события

Заполнить:

- `event_src.vendor` — производитель в нижнем регистре без пробелов;
- `event_src.title` — продукт/приложение в нижнем регистре без пробелов;
- `event_src.category` — точное значение из основного справочника.

Пример:

```text
event_src.vendor   = "microsoft"
event_src.title    = "windows"
event_src.category = "Operating system"
```

Здесь фиксируется то, **какой продукт зарегистрировал событие**, а не участники описанного в нём действия.

### 1.2. Заполнить время и идентификатор

- `time` — время события в `DateTime`, UTC+0;
- `id` — идентификатор по формату правила нормализации или корреляции.

Для правила нормализации:

```text
<Пространство имен>_<Производитель>_<Название источника>_<Транспорт>_<Тип события>
```

Поле `original_time` в схеме 8.0 помечено как автоматически заполняемое.

### 1.3. Определить действие, объект и результат

Заполнить совместно, исходя из смысла события:

- `action` — что произошло;
- `object` — над чем произошло действие;
- `status` — `success`, `failure` или `ongoing`.

Примеры смысловых связок:

```text
Успешный вход:
action = "login"
object = "session"
status = "success"

Неуспешная аутентификация:
action = "login"
object = "session"
status = "failure"

Обнаружение вредоносного ПО:
action = "detect"
object = "malware"
status = "success"

Запрет сетевого соединения:
action = "deny"
object = "connection"
status = "success"
```

`status` относится к результату описываемого действия. Например, при событии «firewall запретил соединение» действие запрета выполнено успешно, поэтому `status=success`, хотя само соединение не состоялось.

### 1.4. Заполнить важность

`importance`:

- `info` — информационное событие;
- `low` — низкая важность;
- `medium` — средняя важность;
- `high` — высокая важность.

После этого шага должны быть заполнены все девять обязательных полей:

```text
time
id
event_src.vendor
event_src.title
event_src.category
action
object
status
importance
```

## Шаг 2. Заполнить категорию события

Если категория известна, выбрать согласованную иерархию из `mp_siem_8_event_categories.md`:

1. `category.generic` — верхний уровень;
2. `category.high` — ветвь внутри `generic`;
3. `category.low` — конкретный вид события внутри `high`, если он предусмотрен таблицей.

Пример:

```text
category.generic = "Access"
category.high    = "Authentication"
category.low     = "Remote"
```

Эти поля не являются обязательными. Они не заменяют `event_src.category`: `event_src.category` классифицирует источник, а `category.*` — само событие.

## Шаг 3. Заполнить субъект действия

Если субъект известен:

1. Заполнить `subject` значением `account`, `application`, `host`, `process`, `rule`, `service` или `system`.
2. Заполнить общие атрибуты: `subject.id`, `subject.name`, `subject.domain`, `subject.group`, `subject.privileges`, `subject.state`, `subject.type`, `subject.version`.
3. Для учётной записи использовать `subject.account.*`.
4. Для процесса использовать `subject.process.*` и, при наличии данных, `subject.process.parent.*`.

Пример:

```text
subject = "account"
subject.account.domain = "corp"
subject.account.name = "alice"
subject.account.session_id = "0x3e7"
```

## Шаг 4. Заполнить атрибуты объекта действия

Обязательный `object` уже выбран на шаге 1. Теперь заполняются его детали:

- общие: `object.id`, `object.name`, `object.path`, `object.fullpath`, `object.type`, `object.state`, `object.vendor`, `object.version`;
- учётная запись: `object.account.*`;
- процесс: `object.process.*` и `object.process.parent.*`;
- хранилище: `object.storage.*`;
- изменение свойства: `object.property`, `object.value`, `object.new_value`, `object.num_value`;
- запрос: `object.query`.

Пример изменения файла:

```text
object = "file"
object.path = "/var/www/html"
object.name = "index.html"
object.fullpath = "/var/www/html/index.html"
```

## Шаг 5. Заполнить сетевые стороны взаимодействия

Если в событии есть сетевые участники:

- источник: `src.ip`, `src.hostname`, `src.fqdn`, `src.port`, `src.mac`, `src.asset`;
- получатель: `dst.ip`, `dst.hostname`, `dst.fqdn`, `dst.port`, `dst.mac`, `dst.asset`;
- назначенные/NAT-адреса: `assigned_src_*`, `assigned_dst_*`;
- геоданные: `src.geo.*`, `dst.geo.*`.

`src.host` и `dst.host` заполняются автоматически на основании IP или hostname.

`src/dst` заполняются только для сторон описываемого взаимодействия. Узел, который просто записал событие в журнал, относится к `event_src.*`.

## Шаг 6. Дополнить сведения об источнике события

Заполнить известные необязательные поля:

- `event_src.ip`, `event_src.hostname`, `event_src.fqdn`;
- `event_src.id`, `event_src.asset`;
- `event_src.rule`, `event_src.subsys`.

`event_src.host` заполняется автоматически на основании `event_src.hostname`, `event_src.ip`, `recv_ipv4` или `recv_ipv6`.

## Шаг 7. Заполнить остальные параметры взаимодействия

В зависимости от содержимого события:

- протоколы: `protocol`, `protocol.layer7`;
- направление: `direction`;
- длительность: `duration` в секундах;
- начало последовательности: `start_time`;
- связь событий: `chain_id`;
- аутентификация: `logon_auth_method`, `logon_service`, `logon_type`;
- причина: `reason`.

Пример сетевого события:

```text
protocol = "tcp"
protocol.layer7 = "ssh"
direction = "outbound"
duration = 55
```

## Шаг 8. Заполнить информационные и нестандартные поля

При наличии соответствующих данных:

- `msgid` — идентификатор типа события у источника;
- `count.bytes`, `count.bytes_in`, `count.bytes_out`;
- `count.packets`, `count.packets_in`, `count.packets_out`;
- `interface`, `tcp_flag`;
- `nas_ip`, `nas_fqdn`;
- `external_link`.

Для данных, которым нет стандартного поля:

- строки — `datafield1` … `datafield10`;
- целые числа — `numfield1` … `numfield5`.

## Шаг 9. Не заполнять вручную автоматические поля

К автоматическим относятся, в частности:

```text
uuid
normalized
body
recv_host
recv_ipv4
recv_ipv6
recv_time
original_time
incorrect_time
asset_ids
count
taxonomy_version
agent_id
input_id
job_id
task_id
origin_app_id
primary_siem_app_id
siem_id
site_id
scope_id
tenant_id
```

Остальные автоматические поля отмечены «Авто» в основном справочнике.

## Шаг 10. Проверить итоговое событие

1. Присутствуют все девять обязательных полей.
2. `time` имеет формат `DateTime` и приведено к UTC.
3. `event_src.vendor` и `event_src.title` — в нижнем регистре без пробелов.
4. `event_src.category`, `action`, `object`, `status`, `subject`, `importance` содержат только допустимые значения.
5. `category.generic/high/low` соответствуют одной ветви официальной иерархии.
6. IP, MAC, UUID, Number, StringList и UUIDList соответствуют своим типам.
7. Атрибуты субъекта относятся к `subject.*`, атрибуты объекта — к `object.*`.
8. Источник регистрации события не перепутан с сетевым источником взаимодействия.
9. Поля, отмеченные «Авто», не заполнены вручную.
10. JSON использует плоские имена ключей, например `subject.account.name`, а не вложенные объекты.
