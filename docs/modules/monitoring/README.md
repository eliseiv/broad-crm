# Модуль `monitoring` — Метрики из Prometheus

Статус: `spec-ready` · Исполнитель: backend

## Scope
Клиент Prometheus HTTP API, выполнение PromQL и маппинг результатов в схему метрик карточки (CPU/RAM/SSD/uptime/online + зоны). Prometheus — единственный источник ([ADR-003](../../adr/ADR-003-prometheus-istochnik-metrik.md)). Запросы — [02-promql.md](02-promql.md).

## Backend — ТЗ
1. Async-клиент на httpx к `${PROMETHEUS_URL}/api/v1/query`, таймаут `PROM_QUERY_TIMEOUT_SEC`.
2. Для сервера выполнять запросы по `instance="<ip>:<exporter_port>"` (см. [02-promql.md](02-promql.md)). Эффективно: батч-запросы для списка серверов (по возможности один запрос на метрику с фильтром по нескольким instance).
3. Маппинг в схему `metrics` ([04-api.md](../../04-api.md)): `usage_percent` (округление до 1 знака), `zone` по порогам, `detail {value,total,unit}`.
4. Вычисление `zone`: `>90→red`, `>=80 && <=90→yellow`, `<80→green` — единый модуль порогов (совпадает с frontend).
5. `online` = `up == 1`. `uptime_seconds` = `node_time_seconds - node_boot_time_seconds`. Типичные причины `up==0` при «установленном» агенте (операционные, не код): неверные права file_sd (target-файлы должны быть `0644`, каталог `0755` — Prometheus читает под другим uid) и закрытый firewall `:9100`. Нормативные требования — [09-provisioning.md](../../09-provisioning.md#регистрация-таргета-file_sd) и [§Сетевая доступность](../../09-provisioning.md#сетевая-доступность-node_exporter-9100).
6. Деградация — строго по таблице [«Доступность метрик»](../../04-api.md#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down) в 04-api.md, два независимых случая:
   - **(а) Prometheus недоступен** (ошибка соединения/таймаут к самому Prometheus): `GET /api/servers` → graceful degradation, `metrics=null`/`online=false`, статус `200`; `GET /api/servers/{id}/metrics` → `502 prometheus_unavailable`.
   - **(б) Prometheus доступен, но `up==0` ИЛИ конкретная метрика отсутствует** в ответе: и `GET /api/servers`, и `GET /api/servers/{id}/metrics` → `200` с `online=false` и `null`-полями (`detail.value`/`detail.total`/`usage_percent` не подставляются ложными значениями). **`502` в этом случае НЕ возвращается.**
   - Итог: `502` — ТОЛЬКО при недоступности самого Prometheus (случай «а»), а не при пустом/частичном валидном ответе (случай «б»).
7. Единицы и CPU `detail` ([Q-MON-1](../../99-open-questions.md) обновлён): RAM — `unit:"GB"` (Used/Total); SSD — `unit:"GB"` (Used/Total по `/`). **CPU `detail` ВСЕГДА `unit:"cores"`, `value:null`, `total`=число логических ядер** (ветка частоты/`GHz` исключена — [TD-013](../../100-known-tech-debt.md)); если число ядер недоступно → `total:null`. `unit` — технический идентификатор в API (`"cores"`/`"GB"`), локализация отображения (`ядра`/`ГБ`) — на frontend. Точные запросы — [02-promql.md](02-promql.md).

## Устойчивость read-path (нормативно)

Усвоенный урок: после сбоя всплеск polling (несколько вкладок × интервал) усиливал нагрузку → Prometheus отвечал `503` → все серверы массово «офлайн». Read-path обязан гасить такие всплески:

1. **Короткий TTL-кэш + single-flight для `GET /api/servers`.** Результат агрегированного запроса кэшируется на `METRICS_CACHE_TTL_SEC` (default `5` с). Параллельные запросы за тем же ключом, пока кэш пуст, **схлопываются в один** исходящий PromQL (single-flight) — N вкладок/опросов дают 1 запрос к Prometheus, а не N.
2. **Ограничение конкурентности исходящих PromQL** — семафор (default `4` одновременных запросов к Prometheus). Защищает Prometheus от лавины при множестве серверов/клиентов.
3. **Ретраи на транзиентные ошибки** Prometheus (`429`, `5xx`, таймаут) — короткий backoff, ограниченное число попыток в пределах `PROM_QUERY_TIMEOUT_SEC`-бюджета. Единичный `503`/таймаут не должен сразу обрушать всю выдачу в «офлайн».
4. **Деградация — только при устойчивой недоступности** (после исчерпания ретраев). TTL-кэш **не маскирует** реальную недоступность дольше своего TTL: по истечении ≤`METRICS_CACHE_TTL_SEC` свежий запрос отразит реальное состояние. Семантика `online`/`up=1` ([04-api.md](../../04-api.md)) и DoD `up=1` ([09-provisioning.md](../../09-provisioning.md#definition-of-done-провижининга-нормативно)) НЕ меняются — кэш лишь сглаживает частоту обращений в пределах TTL.

Параметры: `METRICS_CACHE_TTL_SEC` (env, default 5), конкурентность семафора и политика ретраев — константы backend (рекомендация: семафор 4). Со стороны Prometheus — `--query.max-concurrency=50` ([07-deployment.md](../../07-deployment.md#конфигурация-prometheus)).

## DoD
- [ ] PromQL и маппинг соответствуют [02-promql.md](02-promql.md).
- [ ] Граничные тесты зон (79.9/80/90/90.1), деградация при недоступности ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [ ] Coverage ≥90 % для маппинга/зон.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
