# PromQL-запросы (финализировано)

`$inst` = `<ip>:<exporter_port>` (label `instance`). Окно rate — `1m`. Все usage в процентах (0–100).

Запросы в разделах ниже (CPU/RAM/SSD usage %, detail, uptime, online) — **мгновенные**; они используются **UI-картами и read-path** (`GET /api/servers`, `GET /api/servers/{id}/metrics`) и соответствуют режиму `MonitoringService.fetch_for_instances(instances)` **без окна** (`window_sec=None`). Нотификатор использует те же usage-выражения, обёрнутые в `max_over_time` за окно опроса — см. [«Notifier: max-over-window»](#notifier-max-over-window-только-для-оценки-зоны-алертов) ([ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md)).

## CPU usage %
```promql
100 - (avg by(instance)(rate(node_cpu_seconds_total{instance="$inst",mode="idle"}[1m])) * 100)
```
CPU `detail` — **всегда число логических ядер** (обновление [Q-MON-1](../../99-open-questions.md); вариант с частотой убран — [TD-013](../../100-known-tech-debt.md)): `unit:"cores"`, `value:null`, `total`=число логических ядер:
```promql
count by(instance)(node_cpu_seconds_total{instance="$inst",mode="idle"}) # total = число логических ядер
```
- Считаем по `mode="idle"`: ровно одна серия на логическое ядро, поэтому `count` = число ядер.
- Если серия недоступна (нет данных) → `total:null` (UI скрывает строку абсолютных значений CPU).

Ветка частоты (`node_cpu_scaling_frequency_*hertz`, `GHz`) **исключена из scope** — частота недоступна на многих VM, что давало разнобой между серверами.

`usage_percent` для CPU считается всегда независимо от `detail`.

## RAM usage %
```promql
(1 - node_memory_MemAvailable_bytes{instance="$inst"} / node_memory_MemTotal_bytes{instance="$inst"}) * 100
```
Абсолютные (GB):
```promql
node_memory_MemTotal_bytes{instance="$inst"}                                            # total
node_memory_MemTotal_bytes{instance="$inst"} - node_memory_MemAvailable_bytes{instance="$inst"}  # used
```

## SSD (диск `/`) usage %
Единый фильтр меток для всех SSD-запросов: `instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"`.
```promql
(1 - node_filesystem_avail_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"}) * 100
```
Абсолютные (GB):
```promql
node_filesystem_size_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"}                                                                                          # total
node_filesystem_size_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"} - node_filesystem_avail_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"}    # used
```

## Uptime (секунды)
```promql
node_time_seconds{instance="$inst"} - node_boot_time_seconds{instance="$inst"}
```

## Online / offline
```promql
up{instance="$inst"}   # 1 = online, 0/нет данных = offline
```

## Конвертация единиц (backend)
- bytes → GB: `/ 1024^3`, округление до 1 знака.
- CPU cores: целое число (`int`), без конверсии.
- usage_percent: округление до 1 знака, clamp в [0,100].

## Notifier: max-over-window (только для оценки зоны алертов)

Применяется **исключительно** на пути нотификатора (`MonitoringService.fetch_for_instances(instances, window_sec=W)`), когда `window_sec` задан. UI-карты и read-path используют мгновенные запросы выше — **эти обёртки к ним не применяются** ([ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md)). Меняется только источник `usage_percent` для **CPU/RAM/SSD**; `online` (мгновенный `up`), `uptime`, `detail` — как в мгновенных запросах.

`$W` = `NOTIFIER_METRIC_WINDOW_SEC` (env, default `90`, нормативно `≥ NOTIFIER_POLL_INTERVAL_SEC`). `$step` = разрешение subquery, рекомендация `15s` (≈ scrape-интервал). Зона выводится из максимума через **неизменённый** `usage_to_zone()`.

Каждое usage-выражение оборачивается в `max_over_time((<expr>)[$W:$step])` (subquery, т.к. `<expr>` — инстант-вектор, а не «сырой» range-vector). Для наглядности при `$W=90s`, `$step=15s`:

### CPU usage % (max за окно)
```promql
max_over_time(
  (100 - (avg by(instance)(rate(node_cpu_seconds_total{instance="$inst",mode="idle"}[1m])) * 100))[90s:15s]
)
```
Внутренний `rate[1m]` уже сглаживает CPU; subquery берёт максимум минутного usage по шагам окна. Пик короче `rate`-окна (`1m`)/scrape (~15 с) усредняется внутри `rate` — принятая оговорка ADR-016 (алертим на устойчивые ~минутные пики).

### RAM usage % (max за окно)
```promql
max_over_time(
  ((1 - node_memory_MemAvailable_bytes{instance="$inst"} / node_memory_MemTotal_bytes{instance="$inst"}) * 100)[90s:15s]
)
```

### SSD (диск `/`) usage % (max за окно)
```promql
max_over_time(
  ((1 - node_filesystem_avail_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{instance="$inst",mountpoint="/",fstype!~"tmpfs|overlay"}) * 100)[90s:15s]
)
```

- **Окно и перекрытие:** `$W ≥ poll_interval` → соседние окна опросов перекрываются, любой момент попадает хотя бы в одно окно (см. обоснование в [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md#решение)). Слишком широкое окно → «залипание» алерта (max держит пик всю длину окна) — потому берётся `poll_interval + запас`, а не десятки минут.
- **online/detail/uptime не оборачиваются:** offline-детект остаётся на мгновенном `up{instance="$inst"}`; абсолютные `detail` (cores/GB) и `uptime` — мгновенные. Нотификатор всё равно использует только `zone`/`usage_percent`.
