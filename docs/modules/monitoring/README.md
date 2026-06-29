# Модуль `monitoring` — Метрики из Prometheus

Статус: `spec-ready` · Исполнитель: backend

## Scope
Клиент Prometheus HTTP API, выполнение PromQL и маппинг результатов в схему метрик карточки (CPU/RAM/SSD/uptime/online + зоны). Prometheus — единственный источник ([ADR-003](../../adr/ADR-003-prometheus-istochnik-metrik.md)). Запросы — [02-promql.md](02-promql.md).

## Backend — ТЗ
1. Async-клиент на httpx к `${PROMETHEUS_URL}/api/v1/query`, таймаут `PROM_QUERY_TIMEOUT_SEC`.
2. Для сервера выполнять запросы по `instance="<ip>:<exporter_port>"` (см. [02-promql.md](02-promql.md)). Эффективно: батч-запросы для списка серверов (по возможности один запрос на метрику с фильтром по нескольким instance).
3. Маппинг в схему `metrics` ([04-api.md](../../04-api.md)): `usage_percent` (округление до 1 знака), `zone` по порогам, `detail {value,total,unit}`.
4. Вычисление `zone`: `>90→red`, `>=80 && <=90→yellow`, `<80→green` — единый модуль порогов (совпадает с frontend).
5. `online` = `up == 1`. `uptime_seconds` = `node_time_seconds - node_boot_time_seconds`.
6. Деградация — строго по таблице [«Доступность метрик»](../../04-api.md#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down) в 04-api.md, два независимых случая:
   - **(а) Prometheus недоступен** (ошибка соединения/таймаут к самому Prometheus): `GET /api/servers` → graceful degradation, `metrics=null`/`online=false`, статус `200`; `GET /api/servers/{id}/metrics` → `502 prometheus_unavailable`.
   - **(б) Prometheus доступен, но `up==0` ИЛИ конкретная метрика отсутствует** в ответе: и `GET /api/servers`, и `GET /api/servers/{id}/metrics` → `200` с `online=false` и `null`-полями (`detail.value`/`detail.total`/`usage_percent` не подставляются ложными значениями). **`502` в этом случае НЕ возвращается.**
   - Итог: `502` — ТОЛЬКО при недоступности самого Prometheus (случай «а»), а не при пустом/частичном валидном ответе (случай «б»).
7. Единицы и CPU `detail` ([Q-MON-1](../../99-open-questions.md) обновлён): RAM — `unit:"GB"` (Used/Total); SSD — `unit:"GB"` (Used/Total по `/`). **CPU `detail` ВСЕГДА `unit:"cores"`, `value:null`, `total`=число логических ядер** (ветка частоты/`GHz` исключена — [TD-013](../../100-known-tech-debt.md)); если число ядер недоступно → `total:null`. `unit` — технический идентификатор в API (`"cores"`/`"GB"`), локализация отображения (`ядра`/`ГБ`) — на frontend. Точные запросы — [02-promql.md](02-promql.md).

## DoD
- [ ] PromQL и маппинг соответствуют [02-promql.md](02-promql.md).
- [ ] Граничные тесты зон (79.9/80/90/90.1), деградация при недоступности ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [ ] Coverage ≥90 % для маппинга/зон.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
