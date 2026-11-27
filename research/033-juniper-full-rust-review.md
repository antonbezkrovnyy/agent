# R033 — Juniper через Rust: 26/26

> Цель: перенести полный parser → normalization → MP SIEM mapping pipeline на Juniper после Cisco R032. Итог: все 26 строк `artifact_juniper.log` исполняются в Rust со `status=mapped`; полный Python/Rust parity не имеет mismatches.

## 1. Итог

```text
Всего RAW:         26
Mapped Rust:       26
Failed:            0
Golden evidence:   3/3
Parity mismatches: 0
```

Используется один Family Bundle:

| Компонент | Значение |
|---|---|
| Family | `juniper_junos_rt_flow_session_deny` |
| Parser revision | 6 |
| Normalization revision | 1 |
| Mapping revision | 2 |
| Compatibility hash | `e01a9c40efb43fa2274b3ba7c5478c9e5bb8243f42ef57c375875ecb7b511986` |

Новые parser revisions не создавались. R021 уже покрывал весь corpus одной грамматикой `RT_FLOW_SESSION_DENY`.

## 2. Исходные данные и Registry

Вход:

```text
C:\Users\aa.bezkrovnyy\Downloads\opensecuritydatalake\parser-react\data\artifact_juniper.log
```

Формат — позиционный Junos, а не KV/JSON/CEF/CSV:

```text
<14>Feb  1 16:39:18 aurdcfwj1-s RT_FLOW: RT_FLOW_SESSION_DENY:
session denied <src>/<port>-><dst>/<port> <service> <protocol>(<detail>)
<verdict> <src_vrf> <dst_vrf> UNKNOWN UNKNOWN <username> <interface>
```

Создана новая рабочая копия `research/r033-juniper-mapping.db` на основе R021. Исходный `research/r021-juniper.db` не изменялся.

## 3. Normalization

Normalizer создал revision 1:

- 19 NormalizationField для record_schema, кроме raw;
- три parser evidence;
- PRI, ports, protocol и ICMP detail преобразуются в integer;
- source/destination IP нормализуются как IPv4;
- timestamp преобразуется в naive ISO;
- смысловые описания сохраняются для последующего semantic mapping.

В Junos timestamp отсутствуют год и timezone:

```text
Feb  1 16:39:18
Feb 12 13:21:28
```

Применены внешние лабораторные настройки:

```text
source_default_year=2023
source_timezone=UTC
```

Год подставляется только через `parse_timestamp.default_year`; он не выводится моделью из RAW. Реальные год и timezone источника неизвестны. Для production их необходимо передавать в ingestion metadata, а не использовать лабораторное значение.

Пример результата:

```text
"Feb  1 16:39:18"
 → normalization
"2023-02-01T16:39:18"
 → mapping с source_timezone=UTC
"2023-02-01T16:39:18Z"
```

## 4. Mapping

Финальная revision 2 создана после semantic taxonomy search, preview и полного evaluation на трёх evidence.

Сохраняются:

- обязательный MP SIEM каркас;
- Juniper/Junos/Firewall как источник;
- verdict и network interaction;
- `subject=account` и `subject.account.name` из `username`;
- source/destination IP и ports;
- строковый `protocol` через полный `value_map` числовых кодов, включая 6→tcp и 17→udp;
- host и подсистема `RT_FLOW`;
- VRF, service, policy/application placeholders, interface и PRI в доступных standard/data/number fields.

Первая revision ошибочно оставляла `username` в `datafield5`, несмотря на наличие конкретного account-поля. В `MAPPING_PROMPT` добавлен явный приоритет: сначала конкретные поля и атрибуты таксономии, а `datafield/numfield` — только после исключения подходящих конкретных targets. Неизвестные opaque-значения по-прежнему запрещено насильно маппить.

Revision 2 исправила это:

```text
subject=account
subject.account.name=alice
```

Также числовой protocol перенесён из generic number field в конкретное строковое `protocol`. Скобочная деталь `icmp_type` обоснованно осталась в `numfield2`: она равна 0 и для TCP, и для UDP, поэтому не выдаётся за подтверждённый ICMP type.

При первом preview ранней revision модель получила `invalid_string:protocol:17`, а при rev2 допустила `mapping_order_violation` для `time`; оба варианта отклонены quality gates до сохранения. Финальная revision 2 прошла 3/3 evidence.

## 5. Диагностика runner и Taxonomy Registry

Первый mapper зациклился на `save_mapping`: ToolMessage имел транспортный `status=success`, но инструмент возвращал `ok=false`, а runner скрывал содержимое ошибки.

`mapping_agent.py` теперь выводит `Tool error: ...` для JSON-результатов с `ok=false`. Это позволило увидеть фактическую причину: рабочая копия R033 не содержала taxonomy records, поэтому validator возвращал `unknown_taxonomy_field` даже для `time`, `action` и других базовых полей.

Таксономия восстановлена штатно из `reference/mpsiem`:

- 212 fields;
- 232 values;
- 222 categories;
- 666 готовых taxonomy embeddings переиспользованы из R028.

После проверки `get_taxonomy_field(time)` mapper был перезапущен и успешно сохранил revision 1. Это была проблема Registry/наблюдаемости, а не ошибка семантического выбора модели.

## 6. Полный Rust прогон

Экспорт:

```bash
uv run python bundle.py export \
  --database research/r033-juniper-mapping.db \
  --family-id juniper_junos_rt_flow_session_deny \
  --output research/r033-juniper-rt-flow.bundle.json
```

Исполнение:

```bash
./rust/log-data-plane/target/release/log-data-plane.exe run \
  --bundle research/r033-juniper-rt-flow.bundle.json \
  --input C:/Users/aa.bezkrovnyy/Downloads/opensecuritydatalake/parser-react/data/artifact_juniper.log \
  --output research/r033-juniper-rust-full.jsonl
```

Результат:

```text
results=26
mapped=26
failure_count=0
```

Каждая строка прошла Aho–Corasick dispatch, полный Grok parser, normalization, mapping и taxonomy validation без вызова Python или LLM.

## 7. Полный Python ↔ Rust parity

Для каждой из 26 строк сравнены:

```text
ok
status
captures
normalized
event
```

`research/r033-juniper-rust-full.summary.json`:

```json
{
  "ok": true,
  "total": 26,
  "mapped": 26,
  "failed": 0,
  "python_rust_parity": {
    "compared": 26,
    "mismatch_count": 0,
    "mismatches": []
  }
}
```

Это подтверждает не только успешные statuses, а идентичность всех промежуточных и финальных значений текущим Python revisions.

## 8. Совместный результат R032 + R033

```text
Cisco:    1763/1763 mapped
Juniper:    26/26 mapped
Итого:     1789/1789 mapped
Parity mismatches: 0
```

Используются четыре Family Bundles: три Cisco и один Juniper. Перенос native Rust runtime на второй источник подтверждён без изменений Rust-кода.

## 9. Артефакты

- `research/r033-juniper-mapping.db` — normalization/mapping revisions и Taxonomy Registry.
- `research/r033-juniper-rt-flow.bundle.json` — исполняемый Bundle.
- `research/r033-juniper-rt-flow.golden.json` — три Python reference results.
- `research/r033-juniper-rust-full.jsonl` — результат каждой из 26 строк.
- `research/r033-juniper-rust-full.summary.json` — counts, revisions, parity и source configuration.
- `mapping_agent.py` — вывод ошибок инструментов с `ok=false`.

## 10. Ограничения

- Проверен только `RT_FLOW_SESSION_DENY`, а не все типы сообщений Junos.
- Лабораторные год и timezone не являются восстановленными свойствами источника.
- Доказана parity с текущими Python mappings, а не независимая production-правильность каждой семантической категории.
- Масштабный dispatch и ReDoS-hardening остаются отдельными задачами.

[← R032](032-cisco-full-rust-regression-review.md) · [Все исследования](README.md)
