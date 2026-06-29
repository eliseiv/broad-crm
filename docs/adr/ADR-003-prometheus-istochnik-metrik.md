# ADR-003 · Prometheus — единственный источник метрик

- Статус: accepted
- Дата: 2026-06-28

## Контекст

Нужны метрики CPU/RAM/SSD/uptime/online для карточек. Есть выбор: дублировать метрики в PostgreSQL (своя time-series) или читать напрямую из Prometheus по требованию.

## Решение

Метрики НЕ хранятся в PostgreSQL. **Prometheus — единственный источник истины для метрик.** Backend по запросу карточек выполняет PromQL к Prometheus HTTP API (`/api/v1/query`) и маппит результат в схему ответа. В БД — только реестр серверов и `provision_status`.

PromQL-запросы зафиксированы в [modules/monitoring/02-promql.md](../modules/monitoring/02-promql.md).

## Обоснование

- Prometheus уже выбран как хранилище (ТЗ) и оптимизирован для time-series.
- Дублирование привело бы к рассинхронизации, лишним таблицам, ретеншн-политике, нагрузке на БД.
- Историю и детальный анализ закрывает Grafana ([ADR-005](ADR-005-custom-gauge-vs-grafana-embed.md)).

## Последствия

- (+) Простая модель данных ([03-data-model.md](../03-data-model.md)), нет дублирования.
- (+) Единая точка правды, консистентность с Grafana.
- (−) Доступность метрик зависит от Prometheus → нужна graceful degradation: `GET /api/servers` отдаёт `metrics=null`/`online=false` при недоступности; `GET /api/servers/{id}/metrics` отдаёт `502 prometheus_unavailable`.
- (−) Каждый запрос карточек — обращение к Prometheus; при необходимости кэшировать на стороне backend (TTL ~ scrape interval) — будущая оптимизация.

## Альтернативы

- **Своя time-series в Postgres / TimescaleDB** — отвергнуто: избыточно, дублирует Prometheus.
- **Кэш метрик в Redis** — отложено: преждевременная оптимизация для Этапа 1.
