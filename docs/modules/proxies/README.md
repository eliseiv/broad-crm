# Модуль `proxies` — Реестр HTTP/SOCKS-прокси с мониторингом доступности и Telegram-алертами

Статус: `implemented` (Спринт 1) · Исполнитель: backend, frontend

## Scope

Управление списком прокси (**http** / **https** / **socks5**): добавление, список, **редактирование** (`name`/`proxy_type`/`host`/`port`/`username`/`password`), удаление, **перестановка порядка (drag-and-drop, единый список)**, безопасное хранение пароля (Fernet), маскирование пароля в UI/API (флаг `has_password`) и **периодическая автоматическая проверка доступности** прокси с уведомлением администратора в Telegram при недоступности (🔴) и восстановлении (🟢). Модель — [03-data-model.md](../../03-data-model.md#таблица-proxies), API-контракт — [04-api.md](../../04-api.md#proxies), решения — [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md), [ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md) (drag-and-drop/`position`).

Образец модуля целиком — **AI-ключи** ([modules/ai-keys](../ai-keys/README.md), [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md)): та же схема «модель со статусом в БД + отдельный фоновый монитор + собственный `TelegramClient` + Fernet-секрет». Отличия — см. [«Отличия от AI-ключей»](#отличия-от-ai-ключей-нормативно).

## Out of scope (Этап 1)

- Ручной триггер «проверить сейчас», настраиваемый интервал проверки per-proxy ([TD-028](../../100-known-tech-debt.md)).
- Windowed-детект доступности (сглаживание транзиентных всплесков за окно, по образцу [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)) — на Этапе 1 транзиентность гасится ретраями внутри одной проверки ([TD-028](../../100-known-tech-debt.md)).
- Карточка «Прокси» на «Дашборде» (сводный счётчик) — [TD-028](../../100-known-tech-debt.md).
- Типы прокси кроме `http`/`https`/`socks5` (напр. SOCKS4) — расширяются добавлением в enum + сборкой URL.
- Использование прокси приложением для реальных исходящих запросов (только реестр + мониторинг доступности).
- Измерение задержки/скорости прокси, гео-инфо, ротация пула.

## Отличия от AI-ключей (нормативно)

| Аспект | AI-ключи | Прокси |
|--------|----------|--------|
| Секрет | `key` (обязателен) — весь ключ шифруется | `password` (**опционален**) — шифруется только если задан (`password_encrypted` может быть `NULL`) |
| Маска в API | `key_masked` (первые4…последние4) | `has_password: bool` (пароль не раскрывается фрагментами); `username` возвращается как есть (не секрет) |
| Группировка | по `provider` (секции OpenAI/Anthropic), reorder внутри группы | **единый список** (как серверы), reorder по всему списку |
| Проверка | `GET /v1/models` у провайдера (по ключу в заголовке) | `GET` эталонного URL **через** `httpx.AsyncClient(proxy=...)` |
| Исход `unknown` | **есть** (транзиентная ошибка провайдера ≠ ключ отозван → статус не меняется) | **нет** — недоступность прокси и есть событие; провал (после ретраев) → `error` |
| Интервал (default) | `AI_KEY_CHECK_INTERVAL_SEC=900` | `PROXY_CHECK_INTERVAL_SEC=60` |

Всё остальное (статус в БД, переходы `pending|working→error`/`error→working`, стартует всегда, Telegram гейтится `notifier_enabled`, немедленная проверка при создании) — как у AI-ключей.

## Безопасность пароля (нормативно)

- Пароль прокси шифруется **Fernet** тем же `FERNET_KEY`, что и SSH-пароли/AI-ключи ([ADR-007](../../adr/ADR-007-shifrovanie-fernet.md), [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)); в БД — `password_encrypted bytea` (`NULL`, если пароль не задан). Переиспользуются `encrypt_secret`/`decrypt_secret` из `app/infra/crypto.py`.
- **Пароль НИКОГДА** не возвращается ни в одном ответе API и не логируется. Расшифровка — только в памяти монитора непосредственно перед сборкой URL и HTTP-запросом.
- В ответах API вместо пароля — производный флаг **`has_password: bool`** (`= password_encrypted IS NOT NULL`). Фрагменты пароля НЕ хранятся и НЕ раскрываются (в отличие от `key_prefix`/`key_last4` у ключей — у прокси маски по фрагментам нет).
- **`username` — не секрет**: хранится plaintext (`text`), возвращается в API как есть, участвует в сборке URL (`user:pass@`). Детали — [05-security.md](../../05-security.md#защита-паролей-прокси).

## Backend — ТЗ

Слои и стек — как в модулях `servers`/`ai-keys`: router → service → repository (SQLAlchemy async), Pydantic-схемы = контракт. Образцы для переиспользования: `app/api/*`, `app/services/ai_key_service.py`, `app/services/ai_key_monitor_service.py`, `app/infra/ai_provider.py`, `app/infra/telegram.py`, `app/infra/crypto.py`, `app/domain/notifications.py`, `app/repositories/*`, `app/models/*`, `app/schemas/*`; фоновая задача — паттерн `asyncio.create_task` + сильная ссылка (как AI-монитор в `app/main.py` lifespan).

### Endpoints (все под JWT, префикс `/api`)

- `GET /api/proxies` → список `ProxyListItem`. Сортировка `position ASC, created_at DESC, id`. Единый плоский список (без группировки). Пагинации нет. См. [04-api.md](../../04-api.md#get-apiproxies).
- `POST /api/proxies {name, proxy_type, host, port, username?, password?}` → `202`; валидация, шифрование пароля (Fernet, только если задан), `INSERT check_status='pending'` (`position` = `DEFAULT 0`), запуск **немедленной фоновой проверки** (`asyncio.create_task`). Возвращает созданный `ProxyListItem` (`check_status:"pending"`). См. [04-api.md](../../04-api.md#post-apiproxies).
- `PATCH /api/proxies/order {ids}` → `204`; перестановка **единого списка** (как `PATCH /api/servers/order`), `position = 0..N-1` в одной транзакции. Прецеденция кодов — [04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов).
- `PATCH /api/proxies/{id} {name?, proxy_type?, host?, port?, username?, password?}` → `200`; редактирование. **Секретная семантика пароля** и **триггер re-check** — см. [«Редактирование прокси»](#редактирование-прокси-patch-нормативно) и [04-api.md](../../04-api.md#patch-apiproxiesid).
- `GET /api/proxies/{id}/status` → `{id, check_status, error_message, last_checked_at}`. Лёгкий endpoint для polling статуса после добавления/редактирования.
- `DELETE /api/proxies/{id}` → `204`; hard delete. Повтор → `404 proxy_not_found`.

Коды ошибок и точные схемы — [04-api.md](../../04-api.md#proxies). `proxy_type` вне enum / `port` вне `1..65535` → `422 unprocessable` (по аналогии с невалидным IP у серверов).

### Редактирование прокси (`PATCH`, нормативно)

`PATCH /api/proxies/{id}` принимает `{name?, proxy_type?, host?, port?, username?, password?}` (все опциональны). «Переданное поле» определяется по множеству заданных полей запроса (`model_dump(exclude_unset=True)` / `__pydantic_fields_set__` в Pydantic v2), что позволяет отличить «поле отсутствует» от «поле передано пустым».

1. **`name`/`proxy_type`/`host`/`port`** — если переданы, заменяют значение (с валидацией). Не переданы — не меняются.
2. **`username`** (не секрет): если поле **не передано** — не менять; если передано — установить (значение `null` или `""` → `username = NULL`, т.е. убрать логин).
3. **`password`** (секрет, не префилится):
   - **не передано** → `password_encrypted` НЕ меняется;
   - **`null` или `""`** → **очистить** (`password_encrypted = NULL`, `has_password=false`) — убрать пароль;
   - **непустая строка** → **заменить** (re-encrypt через `encrypt_secret`).
   Форма редактирования пароль не префилит (backend не хранит и не отдаёт plaintext) — поэтому поле «Пароль» пустое; чтобы сохранить текущий пароль, поле не отправляют.
4. **Re-check триггерится, если** изменилось хотя бы одно **связанное с подключением** поле — `proxy_type`, `host`, `port`, `username` **или** `password` (передан непустой либо явно очищен): `check_status='pending'`, `error_message=NULL`, запуск немедленной фоновой проверки (тот же путь, что `POST`; первый переход считается от `prev_status='pending'`). Первая неуспешная проверка после edit шлёт **🔴** (как для нового прокси), успешная — молча (`pending→working`).
5. **Смена только `name`** — `check_status` не трогается, проверка не перезапускается.
6. `updated_at` обновляется всегда при изменении хотя бы одного поля. `last_checked_at` при re-check не сбрасывается (остаётся временем последней конклюзивной проверки до завершения новой).

### Перестановка (единый список, нормативно)

- Прокси — **единый список** (без группировки), reorder по образцу **серверов** ([04-api.md](../../04-api.md#patch-apiserversorder)). `PATCH /api/proxies/order {ids}` принимает полный упорядоченный список `id` и в одной транзакции присваивает `position = 0..N-1`.
- Прецеденция ошибок — общая для всех order-эндпоинтов ([04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)): битое тело → `400`; любой несуществующий `id` → `404 proxy_not_found`; только если все `id` существуют — неполная перестановка → `422`.
- Правило сортировки и присвоения `position` — общее с серверами ([03-data-model.md](../../03-data-model.md#колонка-position-порядок-карточек)).

### Требования

1. Пароль (plaintext) НИКОГДА не возвращается в ответах и не логируется (structlog-фильтр секретов, [05-security.md](../../05-security.md)). `username` — не секрет, возвращается как есть.
2. `has_password` вычисляется в схеме ответа из `password_encrypted IS NOT NULL`.
3. `check_status` ∈ {`pending`,`working`,`error`}, default `pending`. `error_message` — русскоязычная причина при `error`, иначе `NULL`.
4. `updated_at`/`last_checked_at` обновляются при каждой проверке с конклюзивным исходом (`working`/`error`) атомарным `UPDATE`. (У прокси исхода `unknown` нет — любой провал после ретраев конклюзивен, см. ниже.)
5. **Каждая Alembic-миграция обязана иметь рабочий `downgrade()`** ([07-deployment.md](../../07-deployment.md#откат-миграций-бд), [03-data-model.md](../../03-data-model.md)).
6. Таблица создаётся миграцией **`0006_create_proxies`** (`down_revision="0005_create_notifier_alert_log"` — текущая голова цепочки), с колонкой `position` (`integer NOT NULL DEFAULT 0`) и индексом `ix_proxies_position` ([03-data-model.md](../../03-data-model.md#миграция-0006_create_proxies-концепт)).

### Проверка доступности прокси (нормативно)

Проверка = **доступность прокси** через эталонный запрос. HTTP-клиент — `httpx` с коротким таймаутом `PROXY_CHECK_TIMEOUT_SEC` (default 10 с) и ограниченными ретраями на транзиентные ошибки (backoff-паттерн `app/infra/ai_provider.py`). TLS verify включён.

1. Собрать URL прокси в памяти: `"<proxy_type>://[<username>[:<password>]@]<host>:<port>"`.
   - `proxy_type` = схема (`http`/`https`/`socks5`).
   - `username`/`password` включаются, только если заданы (URL-энкодятся; пароль расшифровывается `decrypt_secret` из `password_encrypted` непосредственно перед сборкой).
   - Собранная строка — только в памяти, не логируется (содержит пароль).
2. `httpx.AsyncClient(proxy=<url>, timeout=PROXY_CHECK_TIMEOUT_SEC, verify=True)` → `GET PROXY_CHECK_URL` (default `https://www.gstatic.com/generate_204`).

**Маппинг результата → исход проверки:**

| Ответ / событие | Исход | `check_status` | `error_message` (рус.) |
|-----------------|-------|----------------|-------------------------|
| `2xx`/`3xx` (в т.ч. `204`) | `working` | `working` | `NULL` |
| Таймаут (после ретраев) | `error` | `error` | **«Таймаут подключения»** |
| Сетевая/транспортная ошибка, ошибка прокси-соединения (после ретраев) | `error` | `error` | **«Прокси недоступен»** |
| `4xx`/`5xx` от эталонного URL | `error` | `error` | **«Ошибка прокси»** |
| Прочая ошибка httpx | `error` | `error` | **«Ошибка прокси»** |

- **Нет исхода `unknown`** (осознанное отличие от AI-ключей, [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)): недоступность прокси и есть отслеживаемое событие. Чтобы единичный сетевой всплеск не давал ложный флип, проверка делает **ограниченные ретраи внутри себя** (backoff `(0.2, 0.5)` с, ≈3 попытки, как `check_key`) и только затем заключает `error`.
- Пароль/собранный URL/логин **не логируются** ни при каком исходе. Логируется `proxy_check_error` (warning) без секретов.
- Причины (`error_message`) — русскоязычные, приходят в API готовыми; frontend показывает их как есть.

### Фоновый монитор `ProxyMonitorService` (нормативно)

Отдельная фоновая asyncio-задача (**по образцу `AiKeyMonitorService`**, [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md), [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md)). Состояние переходов берётся из БД `proxies.check_status` (персистентно, переживает рестарт).

- **Запуск:** в `lifespan` (`app/main.py`), рядом с AI-монитором. Монитор **стартует ВСЕГДА** (не гейтится Telegram) — обновление `check_status` для UI работает независимо от бота. Telegram-клиент передаётся как `None` при отключённом боте.
- **Остановка:** отмена задачи при shutdown (`task.cancel()` + `suppress(CancelledError)`), как AI-монитор.
- **Цикл:** бесконечный `while True`: одна итерация проверки всех прокси → `asyncio.sleep(PROXY_CHECK_INTERVAL_SEC)` (default 60 с). Необработанное исключение внутри итерации логируется и **не валит задачу**.
- **Итерация (`poll_once`):** открыть короткоживущую сессию БД, получить все прокси (снимок `id, name, proxy_type, host, port, username, password_encrypted, prev_status=check_status`), закрыть сессию. Для каждого прокси (под семафором ограничения конкурентности, образец AI-монитора): расшифровать пароль (если есть), собрать URL, выполнить проверку, вычислить исход; **при конклюзивном исходе** — обновить БД (`check_status`, `error_message`, `last_checked_at`, `updated_at`) отдельным атомарным `UPDATE`; вычислить переход относительно `prev_status`, при необходимости отправить алерт (если `notifier_enabled`).
- **Немедленная проверка при создании (`POST /api/proxies`)** и при re-check (`PATCH`): та же логика проверки одного прокси (`check_one`) запускается фоново сразу после `INSERT`/`UPDATE`. Первый переход считается от `prev_status='pending'`.

### Переходы статуса и алерты (нормативно)

`prev` — предыдущий `check_status` из БД, `cur` — исход текущей проверки. Чистая функция перехода `evaluate_transition(old_status, result) -> (new_status, error_message, alert)` (образец — `evaluate_transition` в `app/services/ai_key_monitor_service.py`), `alert ∈ {None, "error", "recovery"}`:

| `prev` | `cur` | Действие |
|--------|-------|----------|
| `pending` / `working` | `error` | **🔴 «Прокси не работает»** (в т.ч. первая проверка недоступного прокси) |
| `error` | `working` | **🟢 «Прокси снова работает»** (recovery/отбой) |
| `working` | `working` | молча |
| `pending` | `working` | молча (первая успешная проверка — не recovery) |
| `error` | `error` | молча (уже сломан; `error_message` обновляется на актуальную причину) |

- Telegram-отправка выполняется **только если** `settings.notifier_enabled` (`TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` заданы). Иначе переход только фиксируется в БД (статус для UI), лог `proxy_alert_suppressed_no_telegram` (info) — не ошибка.
- `check_status` в БД обновляется **всегда**, независимо от `notifier_enabled` и результата отправки Telegram.
- Персистентность `check_status` гарантирует: после рестарта backend сломанный прокси не переоткрывается (нет дубль-🔴), а recovery отрабатывает корректно между рестартами.

### Формат сообщений Telegram (точно)

Метки — как у серверов/AI-ключей ([modules/notifier](../notifier/README.md), [domain/notifications](../ai-keys/README.md#формат-сообщений-telegram-точно)). Текст — plain (без parse_mode/Markdown). Имя прокси — в двойных кавычках, идентификация — `<host>:<port>`. Билдеры (чистые функции, рядом с `app/domain/notifications.py`): `build_proxy_error(name, host, port, reason)` / `build_proxy_recovery(name, host, port)`.

**🔴 Прокси не работает** (переход `pending|working → error`):

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Прокси "<name>" <host>:<port>
Прокси не работает: "<reason>"
```

`<reason>` = актуальный `error_message` («Таймаут подключения» / «Прокси недоступен» / «Ошибка прокси»).

**🟢 Прокси восстановлен** (переход `error → working`):

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Прокси "<name>" <host>:<port>
Прокси снова работает
```

Доставка — через тот же `TelegramClient.send_message` (best-effort, at-least-once, ограниченные ретраи; секреты не логируются) — см. [modules/notifier](../notifier/README.md#доставка-в-telegram).

### Backend — ориентиры реализации (структура — на усмотрение)

1. **Настройки** (`config.py`): `proxy_check_interval_sec: int = 60`, `proxy_check_timeout_sec: float = 10.0`, `proxy_check_url: str = "https://www.gstatic.com/generate_204"`. `notifier_enabled` переиспользуется.
2. **Зависимость `httpx[socks]`** (обязательно для `socks5`): `httpx` из коробки проксирует HTTP/HTTPS, но `socks5://` требует экстру `httpx[socks]` (транзитивно `socksio`). Текущий `backend/pyproject.toml` объявляет `httpx>=0.27,<0.28` **без** этой экстры → **добавить `httpx[socks]`** (пометка для backend/devops, [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md); [02-tech-stack.md](../../02-tech-stack.md#backend)). Без экстры проверка SOCKS5-прокси падает.
3. **Проверка прокси** (`infra/`, напр. `proxy_checker.py`): чистый результат `ProxyCheckResult{outcome, reason}` (`working`/`error`) — маппинг тестируется без сети (моки httpx). Сборка URL — отдельная функция; пароль/URL не логируются.
4. **Билдеры сообщений** (`domain/notifications.py`): `build_proxy_error(name, host, port, reason)` / `build_proxy_recovery(name, host, port)` → строка. qa проверяет побайтовое совпадение формата.
5. **ProxyMonitorService** (`services/`): цикл + **чистая функция перехода** `evaluate_transition(prev_status, result) -> (new_status, error_message, alert)` (образец AI-монитора) для тестируемости матрицы без сети/БД. Исхода `unknown` нет.
6. **Роутер/сервис/репозиторий** (`api/`, `services/`, `repositories/`, `models/`, `schemas/`): CRUD по образцу серверов/ключей. `has_password` собирается в схеме ответа.
7. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` монитора при старте (всегда, рядом с AI-монитором), отмена при shutdown.

## Frontend — ТЗ

Зеркалит страницу `servers` (единый список карточек, drag-and-drop, клик=edit); детальный UI-гайд — [08-design-system.md](../../08-design-system.md#страница-прокси). Реализация строк — русский словарь ([08-design-system.md](../../08-design-system.md#локализация-страницы-прокси)).

### Навигация

- Добавить вкладку **«Прокси»** (`/proxies`) в `AppLayout` — [08-design-system.md](../../08-design-system.md#навигация-категории-дропдауны-applayout). Защищённый маршрут внутри `AppLayout`. (Со Спринта B «Прокси» — пункт категории «Мониторинг», [ADR-022](../../adr/ADR-022-teams-nav-categories.md).)

### Страница `ProxiesPage`

- Адаптивная сетка карточек (`grid-cols-1 md:grid-cols-2 xl:grid-cols-3`, gap 24px), как «Серверы»/«ИИ - ключи». Единый список (без секций), сортировка по `position`. Ячейки: `ProxyCard` на каждый прокси + `AddProxyCard`.
- `ProxyCard`: имя, тип (http/https/socks5), `host:port` (моношрифт), статус-бейдж (**Работает** / **Не работает** / **Проверка…**), причина ошибки при `error`, наличие логина/пароля (иконка/подпись при `username`/`has_password`), кнопка **Удалить**.
- **Клик по карточке = редактирование** (короткий клик открывает `AddProxyModal` в режиме edit). **Зажатие ~200 мс + движение = перетаскивание** (@dnd-kit, [08-design-system.md](../../08-design-system.md#перестановка-карточек-drag-and-drop)). Кнопка **Удалить** — `stopPropagation`.
- `AddProxyCard` → `AddProxyModal` (Radix Dialog) в режиме **add**: поля **Название** (`Input`), **Тип** (`Select`: http/https/socks5), **Хост** (`Input`), **Порт** (`Input`, числовой), **Логин** (`Input`, опц.), **Пароль** (`Input type=password`, toggle видимости, опц.). Кнопки **Отмена** / **Добавить**.
- **Режим edit `AddProxyModal`:** префил `name`/`proxy_type`/`host`/`port`/`username`; поле **Пароль пустое** с подсказкой «Оставьте пустым, чтобы не менять пароль». Кнопка действия — **Сохранить**. Отправляются только изменённые поля; пустой `password` не отправляется. После смены связанного с подключением поля карточка возвращается в **Проверка…** и polling статуса возобновляется.
- **Перестановка:** единый `SortableContext`; на `onDragEnd` — оптимистичное обновление + `PATCH /api/proxies/order {ids}`; при ошибке — откат и инвалидация `GET /api/proxies`.
- Данные и polling — через feature-слой `features/proxies` (`api.ts`, `hooks.ts`) на TanStack Query, по образцу `features/servers`/`features/ai-keys`. Типы — в `types/api.ts`. Статус `pending` → «Проверка…», лёгкий polling `GET /api/proxies/{id}/status` до выхода из `pending`.

### UI-примитив `Select`

- Переиспользуется существующий `Select` (нативный стилизованный `<select>`, [08-design-system.md](../../08-design-system.md#компонент-select)) — новых зависимостей нет.
- Значения: `{value:"http", label:"HTTP"}`, `{value:"https", label:"HTTPS"}`, `{value:"socks5", label:"SOCKS5"}`.

### Состояния UI

Loading (skeleton), empty (только `AddProxyCard` + подсказка), pending («Проверка…», спиннер), error (акцентная граница + причина + «Удалить»), toast «Прокси добавлен» / «Прокси обновлён» / «Прокси удалён», обработка `422`/сетевых ошибок — по образцу серверов/ключей ([08-design-system.md](../../08-design-system.md#состояния-ui-обязательны)).

## DoD

- [x] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md#proxies); пароль отсутствует в ответах/логах; `username` возвращается, `has_password` собирается из `password_encrypted`.
- [x] Пароль зашифрован Fernet (`FERNET_KEY`, `encrypt_secret`/`decrypt_secret`), `password_encrypted` = `NULL` при отсутствии пароля.
- [x] Проверка собирает URL `scheme://[user:pass@]host:port` в памяти и идёт через `httpx.AsyncClient(proxy=...)` к `PROXY_CHECK_URL`; `2xx/3xx` → working, таймаут/сеть/иное (после ретраев) → error; URL/пароль не логируются.
- [x] `httpx[socks]` добавлен в зависимости backend (SOCKS5 работает).
- [x] Матрица переходов и алерты соответствуют таблице; первая неуспешная проверка шлёт 🔴, recovery `error→working` шлёт 🟢; исхода `unknown` нет.
- [x] Формат обоих сообщений Telegram побайтово соответствует спецификации.
- [x] Монитор стартует всегда; Telegram-отправка гейтится `notifier_enabled`; `check_status` обновляется независимо от бота; переходы переживают рестарт.
- [x] Alembic-миграция `0006_create_proxies` (`down_revision="0005_create_notifier_alert_log"`) с рабочим `downgrade()`; колонка `position` + индекс `ix_proxies_position`.
- [x] `PATCH /api/proxies/{id}`: не переданный `password` = не менять; `null`/`""` = очистить; непустой = re-encrypt; re-check при смене `proxy_type`/`host`/`port`/`username`/`password`.
- [x] `PATCH /api/proxies/order`: перестановка единого списка; полная перестановка валидируется (иначе `422`); несуществующий `id` → `404`.
- [x] Frontend: вкладка «Прокси» в `AppLayout`, `ProxiesPage` (единый список), `ProxyCard`/`AddProxyCard`/`AddProxyModal` (add+edit), `Select` с тремя типами, drag-and-drop (клик=edit / зажатие=drag), все состояния UI, русские строки из словаря.
- [x] Coverage ≥90 % для функций проверки/перехода/билдеров сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [x] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-07: спецификация создана (architect). Решение об отдельном in-backend-мониторе доступности прокси (по образцу AI-ключей), Fernet для пароля, отдельных полях ввода и эталонном URL проверки — [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md). Отложенные пункты — [TD-028](../../100-known-tech-debt.md). Требуется добавить зависимость `httpx[socks]` (backend/devops).
- 2026-07-07: **Спринт 1 реализован** (backend + frontend + qa, все гейты зелёные, reviewer approve / production_ready). Закрыты все пункты DoD: модель + миграция `0006_create_proxies`, `ProxyMonitorService` (старт всегда, персистентный `check_status`), Telegram-алерты down 🔴 / recovery 🟢, CRUD API (`GET/POST/PATCH/DELETE /api/proxies`, `PATCH /api/proxies/order`, `GET /api/proxies/{id}/status`), страница `/proxies` (единый список, DnD, add/edit-модалка), `httpx[socks]`, тесты (coverage ≥90 % для проверки/перехода/билдеров). Статус модуля → `implemented`. Остаточный edge-case (алерт для прокси, удалённого в момент in-flight проверки) — [TD-028](../../100-known-tech-debt.md).
