# Модуль `servers` — Реестр серверов

Статус: `spec-ready` · Исполнитель: backend

## Scope
CRUD реестра серверов: создание (с запуском провижининга), список (с метриками), статус, удаление. Модель — [03-data-model.md](../../03-data-model.md), контракт — [04-api.md](../../04-api.md#servers).

## Out of scope
Редактирование сервера (PATCH), повторный запуск провижининга, soft-delete/аудит ([TD-001](../../100-known-tech-debt.md), [TD-003](../../100-known-tech-debt.md)).

## Backend — ТЗ

### Endpoints
- `GET /api/servers[?status=]` → список с метриками (через модуль `monitoring`) + `provision_status` + `online`. Graceful degradation при недоступности Prometheus (`metrics=null`, статус `200`).
- `POST /api/servers {name,ip,ssh_user,ssh_password}` → `202`; валидация, шифрование пароля (Fernet, модуль crypto/infra), `INSERT status=pending`, запуск фоновой задачи провижининга ([modules/provisioning](../provisioning/README.md)). Дубликат `ip` → `409 server_conflict`; невалидный IP → `422`.
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
