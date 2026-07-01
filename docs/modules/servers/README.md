# Модуль `servers` — Реестр серверов

Статус: `spec-ready` · Исполнитель: backend

## Scope
CRUD реестра серверов: создание (с запуском провижининга), список (с метриками), **редактирование `name`**, **перестановка порядка (drag-and-drop)**, статус, удаление. Модель — [03-data-model.md](../../03-data-model.md), контракт — [04-api.md](../../04-api.md#servers).

## Out of scope
Редактирование `ip`/`ssh_user`/`ssh_password`/`exporter_port` и переустановка агента (PATCH меняет **только `name`**), повторный запуск провижининга, soft-delete/аудит ([TD-001](../../100-known-tech-debt.md), [TD-003](../../100-known-tech-debt.md)).

## Backend — ТЗ

### Endpoints
- `GET /api/servers[?status=]` → список с метриками (через модуль `monitoring`) + `provision_status` + `online` + `position`. Сортировка `position ASC, created_at DESC, id`. Graceful degradation при недоступности Prometheus (`metrics=null`, статус `200`).
- `POST /api/servers {name,ip,ssh_user,ssh_password}` → `202`; валидация, шифрование пароля (Fernet, модуль crypto/infra), `INSERT status=pending` (`position` = `DEFAULT 0`), запуск фоновой задачи провижининга ([modules/provisioning](../provisioning/README.md)). Дубликат `ip` → `409 server_conflict`; невалидный IP → `422`.
- `PATCH /api/servers/{id} {name}` (JWT) → `200`; меняет **только `name`** (1–64), обновляет `updated_at`. `ip`/SSH/провижининг не трогаются. Нет записи → `404`; пустое/длинное `name` → `400`. Контракт — [04-api.md](../../04-api.md#patch-apiserversid).
- `PATCH /api/servers/order {ids}` (JWT) → `204`; `ids` — полная перестановка множества серверов, backend в одной транзакции присваивает `position = 0..N-1`. Прецеденция кодов: битое тело → `400`; **любой несуществующий `id` → `404` (проверяется до полноты)**; только если все `id` существуют, но список не полная перестановка → `422`. Контракт и полное правило — [04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-обоих-order-эндпоинтов).
- `GET /api/servers/{id}/metrics` (JWT) → текущие метрики; Prometheus down → `502 prometheus_unavailable`.
- `GET /api/servers/{id}/status` (JWT) → `{provision_status,error_message,updated_at}`.
- `DELETE /api/servers/{id}` (JWT) → `204`; удалить `targets/<id>.json`, удалить запись; повтор → `404`.

### Требования
1. Слои: router → service → repository (SQLAlchemy async). Pydantic-схемы запросов/ответов = контракт.
2. Пароль НИКОГДА не возвращается в ответах и не логируется.
3. Валидация `ip` через `IPvAnyAddress`; нормализация перед сравнением/уникальностью.
4. Обработка `UNIQUE(ip)` → `409`.
5. `updated_at` обновляется при смене статуса.
6. Recovery-hook: при старте backend «зависшие» `installing` старше `ANSIBLE_TIMEOUT_SEC` → `error` ([ADR-006](../../adr/ADR-006-async-provisioning-bez-brokera.md)).
7. **Каждая Alembic-миграция обязана иметь рабочий `downgrade()`** (основа отката релиза — [07-deployment.md](../../07-deployment.md#откат-миграций-бд), [03-data-model.md](../../03-data-model.md)).
8. **Колонка `position`** (`integer NOT NULL DEFAULT 0`) — миграция `0003_add_position` (`down_revision=0002_create_ai_keys`) с backfill по `created_at DESC` и рабочим `downgrade()` ([03-data-model.md](../../03-data-model.md#миграция-0003_add_position-концепт)). Reorder присваивает `position` в одной транзакции; валидация полной перестановки — иначе `422`.
9. Переименование сервера (`PATCH name`) не требует немедленной перезаписи file_sd-таргета (скрейп идёт по `instance`; label `name` информативный, обновится при следующей записи таргета).

### Переиспользуемый контракт репозитория и модели (нормативно)

Объявляется здесь как единственный источник; на него опираются read-path и [modules/notifier](../notifier/README.md):

- `ServerRepository.list_online() -> list[Server]` — серверы с `provision_status == online`. Используется notifier для опроса и read-path при необходимости.
- `Server.instance` (property) = `f"{ip}:{exporter_port}"` — целевой `instance` для PromQL/Prometheus file_sd. Единственное место формирования строки `instance`; модуль `monitoring` (`fetch_for_instances`) и notifier принимают именно её.

## DoD
- [ ] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md).
- [ ] Пароль зашифрован в БД, отсутствует в ответах/логах.
- [ ] Интеграционные тесты ([06-testing-strategy.md](../../06-testing-strategy.md)) зелёные.
- [ ] Lint/type-check/format проходят.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
- 2026-07-01: добавлены `PATCH /api/servers/{id}` (edit `name`) и `PATCH /api/servers/order` (reorder); колонка `position` + миграция `0003`; редактирование `name` переведено из out-of-scope в scope ([ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md)).
