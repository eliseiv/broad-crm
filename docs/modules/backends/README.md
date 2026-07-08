# Модуль `backends` — Реестр бэков (сервисов) с healthcheck по домену и Telegram-алертами

Статус: `implemented` (Спринт 2) · Исполнитель: backend, frontend

## Scope

Управление списком бэков (backend-сервисов): добавление, список, **редактирование** (`code`/`name`/`domain`), удаление, **перестановка порядка (drag-and-drop, единый список)** и **периодическая автоматическая проверка доступности** каждого бэка запросом `GET https://{domain}/health` с уведомлением администратора в Telegram при недоступности (🔴) и восстановлении (🟢). Каждый бэк описывается тремя публичными полями — **Код** (`code`, уникален) / **Название** (`name`) / **Домен** (`domain`). Модель — [03-data-model.md](../../03-data-model.md#таблица-backends), API-контракт — [04-api.md](../../04-api.md#backends), решения — [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md), [ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md) (drag-and-drop/`position`).

Образец модуля целиком — **прокси** ([modules/proxies](../proxies/README.md), [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)): та же схема «модель со статусом в БД + отдельный фоновый монитор + собственный `TelegramClient`». Отличия — см. [«Отличия от прокси»](#отличия-от-прокси-нормативно).

## Out of scope (Этап 1)

- Ручной триггер «проверить сейчас», настраиваемый интервал проверки, per-backend health-path/схема ([TD-029](../../100-known-tech-debt.md)).
- Windowed-детект доступности (сглаживание транзиентных всплесков за окно, по образцу [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)) — на Этапе 1 транзиентность гасится ретраями внутри одной проверки ([TD-029](../../100-known-tech-debt.md)).
- Карточка «Бэки» на «Дашборде» (сводный счётчик) — [TD-029](../../100-known-tech-debt.md).
- Конфигурируемый путь healthcheck (фиксирован `/health`) и схема (фиксирована `https://`).
- Измерение задержки/латентности, тела ответа, парсинг JSON `/health`, аутентификация к `/health`.

## Отличия от прокси (нормативно)

| Аспект | Прокси | Бэки |
|--------|--------|------|
| Секрет | `password` (опц., Fernet, `password_encrypted`) | **нет секрета** — все поля публичны, Fernet/crypto не задействованы |
| Идентификатор | `id` (UUID); `name` не уникален | `code` — **уникальный** бизнес-код (дубликат → `409 backend_code_taken`) |
| Поля | `name`/`proxy_type`/`host`/`port`/`username`/`password` | **`code`/`name`/`domain`** (три публичных поля) |
| Проверка | `GET` эталонного URL **через** `httpx.AsyncClient(proxy=...)` | `GET https://{domain}/health` **напрямую** (без прокси-туннеля), строго `2xx` |
| Эталонный URL | `PROXY_CHECK_URL` (конфиг) | путь **фиксирован** `/health`, URL = `https://{domain}/health` |
| Исход `unknown` | нет | **нет** (как у прокси) |
| Интервал (default) | `PROXY_CHECK_INTERVAL_SEC=60` | `BACKEND_CHECK_INTERVAL_SEC=60` |
| Overall-deadline проверки | `PROXY_CHECK_DEADLINE_SEC=30` | `BACKEND_CHECK_DEADLINE_SEC=30` ([ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) |
| Модель алерта недоступности | **немедленно** при первом `error` | **grace-порог 30 мин** непрерывной недоступности перед 🔴 (`BACKEND_ALERT_AFTER_SEC=1800`, поля `error_since`/`alert_sent` — [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) |
| Re-check при `PATCH` | смена `proxy_type`/`host`/`port`/`username`/`password` | смена **`domain`** (только оно связано с подключением) |

Всё остальное (статус в БД, `error→working` recovery, монитор стартует всегда, Telegram гейтится `notifier_enabled`, немедленная проверка при создании, единый список с reorder как у серверов) — как у прокси. **Отличие модели алерта:** у бэков 🔴 откладывается на grace-порог 30 мин (у прокси — немедленно); `check_status` в обоих случаях меняется сразу (реальность в UI).

## Безопасность (нормативно)

- У бэка **нет секрета**: `code`, `name`, `domain` — публичные поля, хранятся plaintext, возвращаются в API как есть. Fernet/`FERNET_KEY`/`crypto.py` не используются.
- В логах монитора собранный URL (`https://{domain}/health`) не секретен, но лог ошибок остаётся без чувствительных данных (образец structlog-фильтра, [05-security.md](../../05-security.md)). Логируется `backend_check_error` (warning) с `code`/`domain`/причиной — без тел ответов.

## Backend — ТЗ

Слои и стек — как в модулях `servers`/`proxies`: router → service → repository (SQLAlchemy async), Pydantic-схемы = контракт. Образцы для переиспользования: `app/api/*`, `app/services/proxy_service.py`, `app/services/proxy_monitor_service.py`, `app/infra/proxy_check.py`, `app/infra/telegram.py`, `app/domain/notifications.py`, `app/repositories/*`, `app/models/*`, `app/schemas/*`; фоновая задача — паттерн `asyncio.create_task` + сильная ссылка (как прокси-монитор в `app/main.py` lifespan).

### Endpoints (все под JWT, префикс `/api`)

- `GET /api/backends` → список `BackendListItem`. Сортировка `position ASC, created_at DESC, id`. Единый плоский список. Пагинации нет. См. [04-api.md](../../04-api.md#get-apibackends).
- `POST /api/backends {code, name, domain}` → `202`; валидация, нормализация домена, проверка уникальности `code` (дубль → `409 backend_code_taken`), `INSERT check_status='pending'` (`position` = `DEFAULT 0`), запуск **немедленной фоновой проверки** (`asyncio.create_task`). Возвращает созданный `BackendListItem` (`check_status:"pending"`). См. [04-api.md](../../04-api.md#post-apibackends).
- `PATCH /api/backends/order {ids}` → `204`; перестановка **единого списка** (как `PATCH /api/servers/order`), `position = 0..N-1` в одной транзакции. Прецеденция кодов — [04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов).
- `PATCH /api/backends/{id} {code?, name?, domain?}` → `200`; редактирование. Уникальность `code` и **триггер re-check при смене `domain`** — см. [«Редактирование бэка»](#редактирование-бэка-patch-нормативно) и [04-api.md](../../04-api.md#patch-apibackendsid).
- `GET /api/backends/{id}/status` → `{id, check_status, error_message, last_checked_at}`. Лёгкий endpoint для polling статуса после добавления/редактирования.
- `DELETE /api/backends/{id}` → `204`; hard delete. Повтор → `404 backend_not_found`.

Коды ошибок и точные схемы — [04-api.md](../../04-api.md#backends). Невалидный формат `domain` → `422 unprocessable`; дубликат `code` → `409 backend_code_taken`.

### Редактирование бэка (`PATCH`, нормативно)

`PATCH /api/backends/{id}` принимает `{code?, name?, domain?}` (все опциональны). «Переданное поле» определяется по множеству заданных полей запроса (`model_dump(exclude_unset=True)` / `__pydantic_fields_set__` в Pydantic v2).

1. **`code`** — если передан, заменяет значение (с валидацией длины). **Уникальность повторно проверяется**: смена на `code`, занятый **другим** бэком → `409 backend_code_taken`. Не передан — не меняется.
2. **`name`** — если передан, заменяет (валидация длины). Не передан — не меняется.
3. **`domain`** — если передан, **нормализуется** (см. ниже) и заменяет (невалидный формат → `422`). Не передан — не меняется.
4. **Re-check триггерится, если изменился `domain`** (единственное поле, связанное с подключением): `check_status='pending'`, `error_message=NULL`, запуск немедленной фоновой проверки (тот же путь, что `POST`; первый переход считается от `prev_status='pending'`). Первая неуспешная проверка после edit шлёт **🔴** (как для нового бэка), успешная — молча (`pending→working`).
5. **Смена только `code`/`name`** — `check_status` не трогается, проверка не перезапускается.
6. `updated_at` обновляется всегда при изменении хотя бы одного поля. `last_checked_at` при re-check не сбрасывается.

### Перестановка (единый список, нормативно)

- Бэки — **единый список** (без группировки), reorder по образцу **серверов/прокси** ([04-api.md](../../04-api.md#patch-apiserversorder)). `PATCH /api/backends/order {ids}` принимает полный упорядоченный список `id` и в одной транзакции присваивает `position = 0..N-1`.
- Прецеденция ошибок — общая для всех order-эндпоинтов ([04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)): битое тело → `400`; любой несуществующий `id` → `404 backend_not_found`; только если все `id` существуют — неполная перестановка → `422`.
- Правило сортировки и присвоения `position` — общее с серверами ([03-data-model.md](../../03-data-model.md#колонка-position-порядок-карточек)).

### Требования

1. `code` уникален (`UNIQUE`); дубликат при `POST`/`PATCH` → `409 backend_code_taken` (детерминированно, из `IntegrityError`/предварительной проверки). Прецеденция: схемная валидация (`400`/`422`) до `409`.
2. `check_status` ∈ {`pending`,`working`,`error`}, default `pending`. `error_message` — русскоязычная причина при `error`, иначе `NULL`.
3. `updated_at`/`last_checked_at` обновляются при каждой проверке с конклюзивным исходом (`working`/`error`) атомарным `UPDATE`. (У бэков исхода `unknown` нет — любой провал после ретраев конклюзивен.)
4. **Каждая Alembic-миграция обязана иметь рабочий `downgrade()`** ([07-deployment.md](../../07-deployment.md#откат-миграций-бд), [03-data-model.md](../../03-data-model.md)).
5. Таблица создаётся миграцией **`0007_create_backends`** (`down_revision="0006_create_proxies"` — текущая голова цепочки), с уникальным индексом `uq_backends_code`, колонкой `position` (`integer NOT NULL DEFAULT 0`) и индексом `ix_backends_position` ([03-data-model.md](../../03-data-model.md#миграция-0007_create_backends-концепт)).

### Нормализация домена и проверка (нормативно)

**Нормализация домена** (на входе `POST`/`PATCH`, чистая функция, тестируется без сети):

1. Trim пробелов.
2. Снять схему `http://` / `https://`, если присутствует (регистронезависимо).
3. Снять всё, начиная с первого `/` (путь/query/fragment) — оставить только authority `host[:port]`.
4. Привести host к нижнему регистру.
5. Результат (`host[:port]`) — сохраняется в `domain`.

**Валидация формата** после нормализации: непустой `host` из валидных DNS-меток (буквы/цифры/дефис, точки-разделители) с опциональным `:port` (`1..65535`); в результате нет пробелов и `/`. Невалидный → `422 unprocessable` (`details:[{field:"domain", ...}]`). Примеры: `https://api.example.com/` → `api.example.com`; `API.Example.com:8443` → `api.example.com:8443`; `http://x/health` → `x`.

**Проверка доступности** = `GET https://{domain}/health`. HTTP-клиент — `httpx.AsyncClient(timeout=<явный httpx.Timeout по всем фазам>, verify=True, follow_redirects=False)` с ограниченными ретраями на транзиентные ошибки (backoff-паттерн `app/infra/ai_provider.py` / проверки прокси). Путь `/health` и схема `https://` **фиксированы**.

> **Анти-зависание (нормативно, [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)).** Как у прокси: `BACKEND_CHECK_TIMEOUT_SEC` применяется как явный `httpx.Timeout(connect=t, read=t, write=t, pool=t)` (не одиночный float), а проверка одного бэка (`check_one`, вкл. все ретраи) оборачивается `asyncio.wait_for(..., BACKEND_CHECK_DEADLINE_SEC)` (default **30 с**) → при превышении исход `error` «Таймаут подключения». Проверка всегда завершается конклюзивно; `deadline` (30 с) < интервал (60 с).

**Маппинг результата → исход проверки:**

| Ответ / событие | Исход | `check_status` | `error_message` (рус.) |
|-----------------|-------|----------------|-------------------------|
| `2xx` | `working` | `working` | `NULL` |
| Таймаут (после ретраев) | `error` | `error` | **«Таймаут подключения»** |
| Сетевая/DNS/TLS/транспортная ошибка (после ретраев) | `error` | `error` | **«Бэк недоступен»** |
| Не-2xx ответ (`3xx`/`4xx`/`5xx`) | `error` | `error` | **«Ошибка бэка (HTTP N)»** (N = код статуса) |
| Прочая ошибка httpx | `error` | `error` | **«Ошибка бэка»** |

- **Строго `2xx`** → `working` (в отличие от прокси, где принимаются и `3xx`): `/health` должен отвечать `200`; редиректы не следуются (`follow_redirects=False`), `3xx` = ошибка здоровья.
- **Нет исхода `unknown`** (как у прокси, [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md)): недоступность бэка и есть отслеживаемое событие. Чтобы единичный сетевой всплеск не давал ложный флип, проверка делает **ограниченные ретраи внутри себя** (backoff, ≈3 попытки) и только затем заключает `error`.
- Причины (`error_message`) — русскоязычные, приходят в API готовыми; frontend показывает их как есть.

### Фоновый монитор `BackendMonitorService` (нормативно)

Отдельная фоновая asyncio-задача (**по образцу `ProxyMonitorService`**, [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md), [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)). Состояние переходов берётся из БД `backends.check_status` (персистентно, переживает рестарт).

- **Запуск:** в `lifespan` (`app/main.py`), рядом с прокси- и AI-монитором. Монитор **стартует ВСЕГДА** (не гейтится Telegram) — обновление `check_status` для UI работает независимо от бота. Telegram-клиент передаётся как `None` при отключённом боте.
- **Остановка:** отмена задачи при shutdown (`task.cancel()` + `suppress(CancelledError)`).
- **Цикл:** бесконечный `while True`: одна итерация проверки всех бэков → `asyncio.sleep(BACKEND_CHECK_INTERVAL_SEC)` (default 60 с). Необработанное исключение внутри итерации логируется и **не валит задачу**.
- **Итерация (`poll_once`):** открыть короткоживущую сессию БД, получить снимок всех бэков (`id, code, name, domain, prev_status=check_status`), закрыть сессию. Для каждого бэка (под семафором ограничения конкурентности, образец прокси-монитора): собрать URL `https://{domain}/health`, выполнить проверку, вычислить исход; **при конклюзивном исходе** — обновить БД (`check_status`, `error_message`, `last_checked_at`, `updated_at`) отдельным атомарным `UPDATE`; вычислить переход относительно `prev_status`, при необходимости отправить алерт (если `notifier_enabled`).
- **Немедленная проверка при создании (`POST`)** и при re-check (`PATCH` со сменой `domain`): та же логика проверки одного бэка (`check_one`) запускается фоново сразу после `INSERT`/`UPDATE`. Первый переход считается от `prev_status='pending'`.

### Переходы статуса и алерты (нормативно)

**Grace-порог 30 минут перед 🔴 (нормативно, [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)).** `check_status` переходит в `error` **немедленно** (реальность в UI сразу), но Telegram-🔴 шлётся только если бэк недоступен **непрерывно ≥ `BACKEND_ALERT_AFTER_SEC` (default 1800 с = 30 мин)** — устраняет ложные алерты при штатных перезагрузках бэка (1–2 мин). Состояние эпизода — персистентные поля `backends.error_since` (начало недоступности) и `backends.alert_sent` (был ли 🔴) ([03-data-model.md](../../03-data-model.md#таблица-backends), миграция `0013_backends_alert_grace`).

Чистая функция перехода (time-aware) `evaluate_transition(prev_status, result, error_since, alert_sent, now) -> (new_status, error_message, new_error_since, new_alert_sent, alert)`, `alert ∈ {None, "error", "recovery"}`:

| `prev` | `cur` (result) | `new_status` | `error_since` | `alert_sent` | Алерт |
|--------|----------------|--------------|---------------|--------------|-------|
| `pending` / `working` | `error` | `error` | ← `now` (старт эпизода) | остаётся `false` | **нет** (grace-окно только началось) |
| `error` | `error` (прошло `< 30 мин`) | `error` | без изменений | `false` | молча (обновляется `error_message`) |
| `error` | `error` (прошло `≥ 30 мин`, `alert_sent=false`) | `error` | без изменений | → `true` | **🔴 «Бэк не работает»** |
| `error` | `error` (`alert_sent=true`) | `error` | без изменений | `true` | молча (уже слали) |
| `error` | `working` (`alert_sent=true`) | `working` | → `NULL` | → `false` | **🟢 «Бэк снова работает»** (отбой) |
| `error` | `working` (`alert_sent=false`) | `working` | → `NULL` | `false` | молча (🔴 не слали, напр. рестарт < 30 мин) |
| `pending` / `working` | `working` | `working` | `NULL` | `false` | молча |

- Telegram-отправка выполняется **только если** `settings.notifier_enabled` (`TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` заданы). Иначе переход только фиксируется в БД (статус + `error_since`/`alert_sent` для UI/grace), лог `backend_alert_suppressed_no_telegram` (info) — не ошибка.
- `check_status`/`error_since`/`alert_sent` в БД обновляются **всегда**, независимо от `notifier_enabled` и результата отправки Telegram.
- Персистентность (`check_status` + `error_since` + `alert_sent`) переживает рестарт backend: grace-отсчёт и признак отправки корректны между рестартами (сломанный бэк не переоткрывает окно, нет дубль-🔴; recovery-🟢 шлётся только если 🔴 был отправлен).

### Формат сообщений Telegram (точно, нормативно — источник истины)

Метки — как у серверов/прокси/AI-ключей ([modules/notifier](../notifier/README.md), [domain/notifications](../proxies/README.md#формат-сообщений-telegram-точно)). Текст — plain (без parse_mode/Markdown). Имя бэка — в двойных кавычках, код — в квадратных скобках, домен — как есть. Билдеры (чистые функции, в `app/domain/notifications.py`, рядом с `build_proxy_error`/`build_proxy_recovery`):

```
build_backend_error(code: str, name: str, domain: str, reason: str) -> str
build_backend_recovery(code: str, name: str, domain: str) -> str
```

Блок идентификации (внутренний хелпер `_backend_block(code, name, domain)`):

```
Бэк "<name>" [<code>] <domain>
```

**🔴 Бэк не работает** (переход `pending|working → error`), `build_backend_error(code, name, domain, reason)`:

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Бэк "<name>" [<code>] <domain>
Бэк не работает: "<reason>"
```

`<reason>` = актуальный `error_message` («Таймаут подключения» / «Бэк недоступен» / «Ошибка бэка (HTTP N)» / «Ошибка бэка»).

**🟢 Бэк восстановлен** (переход `error → working`), `build_backend_recovery(code, name, domain)`:

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Бэк "<name>" [<code>] <domain>
Бэк снова работает
```

Доставка — через тот же `TelegramClient.send_message` (best-effort, at-least-once, ограниченные ретраи; секреты не логируются) — см. [modules/notifier](../notifier/README.md#доставка-в-telegram).

### Backend — ориентиры реализации (структура — на усмотрение)

1. **Настройки** (`config.py`): `backend_check_interval_sec: int = 60`, `backend_check_timeout_sec: float = 10.0`, **`backend_check_deadline_sec: float = 30.0`** (overall-deadline, анти-зависание — [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)), **`backend_alert_after_sec: int = 1800`** (grace-порог 30 мин перед 🔴). `notifier_enabled` переиспользуется. Путь `/health` и схема `https://` — константы, не конфиг. Монитор ведёт grace-состояние в `backends.error_since`/`alert_sent`.
2. **Проверка бэка** (`infra/`, напр. `backend_check.py`): чистый результат `BackendCheckResult{outcome, reason}` (`working`/`error`) — маппинг тестируется без сети (моки httpx). Нормализация домена и сборка URL — отдельные чистые функции.
3. **Билдеры сообщений** (`domain/notifications.py`): `build_backend_error(code, name, domain, reason)` / `build_backend_recovery(code, name, domain)` → строка. qa проверяет побайтовое совпадение формата.
4. **BackendMonitorService** (`services/`): цикл + **чистая функция перехода** `evaluate_transition(prev_status, result) -> (new_status, error_message, alert)` (образец прокси-монитора) для тестируемости матрицы без сети/БД. Исхода `unknown` нет.
5. **Роутер/сервис/репозиторий** (`api/`, `services/`, `repositories/`, `models/`, `schemas/`): CRUD по образцу серверов/прокси. Уникальность `code` — `UNIQUE`-констрейнт + маппинг `IntegrityError` → `409 backend_code_taken`.
6. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` монитора при старте (всегда, рядом с прокси-монитором), отмена при shutdown.

## Frontend — ТЗ

Зеркалит страницу `proxies`/`servers` (единый список карточек, drag-and-drop, клик=edit); детальный UI-гайд — [08-design-system.md](../../08-design-system.md#страница-бэки). Реализация строк — русский словарь ([08-design-system.md](../../08-design-system.md#локализация-страницы-бэки)).

### Навигация

- Добавить вкладку **«Бэки»** (`/backends`) в `AppLayout` — [08-design-system.md](../../08-design-system.md#навигация-категории-дропдауны-applayout). Защищённый маршрут внутри `AppLayout`, не-full-bleed ветка. (Со Спринта B «Бэки» — пункт категории «Мониторинг», [ADR-022](../../adr/ADR-022-teams-nav-categories.md).)

### Страница `BackendsPage`

- Адаптивная сетка карточек (`grid-cols-1 md:grid-cols-2 xl:grid-cols-3`, gap 24px), как «Серверы»/«Прокси». Единый список (без секций), сортировка по `position`. Ячейки: `BackendCard` на каждый бэк + `AddBackendCard`.
- `BackendCard`: имя (`name`), код (`code`, моношрифт/чип), домен (`domain`, моношрифт), статус-бейдж (**Работает** / **Не работает** / **Проверка…**), причина ошибки при `error`, **единственная** кнопка **Удалить** (одна на карточку; дубль в error/недоступном состоянии **не рендерится** — [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)).
- **Клик по карточке = редактирование** (короткий клик открывает `AddBackendModal` в режиме edit). **Зажатие ~200 мс + движение = перетаскивание** (@dnd-kit, [08-design-system.md](../../08-design-system.md#перестановка-карточек-drag-and-drop)). Кнопка **Удалить** — `stopPropagation`.
- `AddBackendCard` → `AddBackendModal` (Radix Dialog) в режиме **add**: поля **Код** (`Input`), **Название** (`Input`), **Домен** (`Input`). Кнопки **Отмена** / **Добавить**. Ошибка `409 backend_code_taken` — пофилдово под полем «Код» («Код занят»).
- **Режим edit `AddBackendModal`:** префил `code`/`name`/`domain`. Кнопка действия — **Сохранить**. Отправляются только изменённые поля. После смены `domain` карточка возвращается в **Проверка…** и polling статуса возобновляется.
- **Перестановка:** единый `SortableContext`; на `onDragEnd` — оптимистичное обновление + `PATCH /api/backends/order {ids}`; при ошибке — откат и инвалидация `GET /api/backends`.
- Данные и polling — через feature-слой `features/backends` (`api.ts`, `hooks.ts`) на TanStack Query, по образцу `features/proxies`/`features/servers`. Типы — в `types/api.ts`. Статус `pending` → «Проверка…», лёгкий polling `GET /api/backends/{id}/status` до выхода из `pending`.

### Состояния UI

Loading (skeleton), empty (только `AddBackendCard` + подсказка), pending («Проверка…», спиннер), error (акцентная граница + причина + «Удалить»), toast «Бэк добавлен» / «Бэк обновлён» / «Бэк удалён», `409` «Код занят» пофилдово, обработка `422`/сетевых ошибок — по образцу прокси/серверов ([08-design-system.md](../../08-design-system.md#состояния-ui-обязательны)).

## DoD

- [x] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md#backends); `code` уникален (дубль → `409 backend_code_taken`); прецеденция `400`/`422` → `409`.
- [x] Нормализация домена (с/без схемы, завершающий `/`, path → authority) и валидация формата (`422` на невалидном); в БД хранится «голый» `host[:port]`.
- [x] Проверка идёт `GET https://{domain}/health` (`follow_redirects=False`, `verify=True`); строго `2xx` → `working`; таймаут/сеть/не-2xx (после ретраев) → `error` с рус. причиной, содержащей код статуса при не-2xx.
- [x] Формат обоих сообщений Telegram побайтово соответствует спецификации (`build_backend_error`/`build_backend_recovery`, блок `Бэк "<name>" [<code>] <domain>`).
- [x] `PATCH /api/backends/{id}`: смена `code` проверяет уникальность (`409`); re-check только при смене `domain`; смена `code`/`name` статус не трогает.

**Правки [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)/[ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md) (spec-ready, требуют реализации):**
- [ ] Grace-порог: `check_status→error` немедленно, **🔴 только после непрерывной недоступности ≥ `BACKEND_ALERT_AFTER_SEC` (30 мин)**; recovery `error→working` шлёт 🟢 **только если 🔴 был отправлен** (`alert_sent`). `evaluate_transition` — time-aware (пять аргументов: `+error_since, alert_sent, now`).
- [ ] Проверка не зависает: явный `httpx.Timeout` по всем фазам + overall-deadline `BACKEND_CHECK_DEADLINE_SEC` (`asyncio.wait_for`) → таймаут-исход.
- [ ] Монитор пишет `check_status`/`error_since`/`alert_sent` независимо от бота; grace-состояние переживает рестарт.
- [ ] Alembic-миграция `0013_backends_alert_grace` (`error_since`/`alert_sent`) с рабочим `downgrade()`.
- [ ] Единственная кнопка «Удалить» на карточке (дубль в error-состоянии убран).
- [x] `PATCH /api/backends/order`: перестановка единого списка; полная перестановка валидируется (иначе `422`); несуществующий `id` → `404 backend_not_found`.
- [x] Frontend: вкладка «Бэки» в `AppLayout`, `BackendsPage` (единый список), `BackendCard`/`AddBackendCard`/`AddBackendModal` (add+edit), `409` пофилдово, drag-and-drop (клик=edit / зажатие=drag), все состояния UI, русские строки из словаря.
- [x] Coverage ≥90 % для функций нормализации/проверки/перехода/билдеров сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [x] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-08: **спецификация правок [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)/[ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)** (architect, требуют реализации): overall-deadline проверки `BACKEND_CHECK_DEADLINE_SEC`=30 (анти-зависание) + явный `httpx.Timeout` по всем фазам; **grace-порог 30 мин** непрерывной недоступности перед 🔴 (`BACKEND_ALERT_AFTER_SEC`=1800, поля `error_since`/`alert_sent`, миграция `0013_backends_alert_grace`, time-aware `evaluate_transition`); единственная кнопка «Удалить» на карточке. Устраняет ложные алерты при перезагрузке бэка (1–2 мин). Модель алерта у бэков теперь отличается от прокси (немедленно) — grace-порог.
- 2026-07-07: модуль реализован (Спринт 2) — backend + frontend + qa завершены, reviewer approve/production_ready. Статус `implemented`, DoD выполнен. Косметические minor architect-reviewer (полный список причин `error_message` в 03-data-model/04-api, переход `pending → [*]: DELETE` в state-диаграмме) синхронизированы (architect).
- 2026-07-07: backend-реализация (backend). Модель `Backend`/миграция `0007_create_backends`; схемы/репозиторий/сервис (CRUD + 409 `backend_code_taken` + нормализация домена → 422 + re-check при смене `domain`); `infra/backend_check.py` (нормализация/валидация домена, `GET https://{domain}/health`, строго 2xx); `BackendMonitorService` (стартует всегда, Telegram гейтится `notifier_enabled`); билдеры `build_backend_error`/`build_backend_recovery`. Frontend — отдельной задачей.
- 2026-07-07: спецификация создана (architect). Решение об отдельном in-backend-мониторе healthcheck бэков (по образцу прокси [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)), без секрета/Fernet, с уникальным `code`, фиксированным `GET https://{domain}/health` и строгим `2xx` — [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md). Отложенные пункты — [TD-029](../../100-known-tech-debt.md).
