# PromQL-запросы (финализировано)

`$inst` = `<ip>:<exporter_port>` (label `instance`). Окно rate — `1m`. Все usage в процентах (0–100).

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
