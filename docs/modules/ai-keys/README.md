# Модуль `ai-keys` — Реестр AI-ключей с проверкой валидности и Telegram-алертами

Статус: `spec-ready` · Исполнитель: backend, frontend

## Scope

Управление API-ключами AI-провайдеров (**OpenAI**, **Anthropic**): добавление, список, **редактирование (`name`/`provider`/`key`)**, удаление, **перестановка порядка внутри провайдер-группы (drag-and-drop)**, безопасное хранение (Fernet), маскирование в UI/API и **периодическая автоматическая проверка валидности** ключа с уведомлением администратора в Telegram при поломке (🔴) и восстановлении (🟢). На UI ключи **сгруппированы по провайдерам** (секции OpenAI / Anthropic). Модель — [03-data-model.md](../../03-data-model.md#таблица-ai_keys), API-контракт — [04-api.md](../../04-api.md#ai-keys), решения — [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md), [ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md).

## Out of scope (Этап 1)

- Ручной триггер «проверить сейчас», настраиваемый интервал проверки per-key ([TD-021](../../100-known-tech-debt.md)).
- Перемещение ключа между провайдер-группами перетаскиванием (провайдер меняется только через `PATCH /api/ai-keys/{id}`, не drag-and-drop).
- Точный баланс/остаток средств по ключу (провайдеры не отдают биллинг по ключу — детектируем только валидность/квоту, [TD-020](../../100-known-tech-debt.md)).
- Проверка через платные эндпоинты (тратящие токены).
- Провайдеры кроме OpenAI/Anthropic (расширяются добавлением в enum + адаптер).
- Использование ключей приложением для реальных вызовов моделей (только реестр + мониторинг живости).

## Безопасность ключа (нормативно)

- Полный ключ шифруется **Fernet** тем же `FERNET_KEY`, что и SSH-пароли ([ADR-007](../../adr/ADR-007-shifrovanie-fernet.md), [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md)); в БД — `key_encrypted bytea`. Переиспользуются `encrypt_password`/`decrypt_password` из `app/infra/crypto.py`.
- **Полный ключ НИКОГДА** не возвращается ни в одном ответе API и не логируется. Расшифровка — только в памяти монитора/проверки непосредственно перед HTTP-запросом к провайдеру.
- Для маски в БД хранятся **plaintext-фрагменты**: `key_prefix` (первые 4 символа) и `key_last4` (последние 4 символа). Это осознанное раскрытие 8 символов ради UX; сам секрет из фрагментов не восстанавливается.
- В ответах API — только производное поле `key_masked` (см. [04-api.md](../../04-api.md#схема-aikeylistitem)). Детали — [05-security.md](../../05-security.md#защита-ai-ключей).

### Правило маски `key_masked`

- Длина ключа `>= 8` → `"<key_prefix>…<key_last4>"` (разделитель — символ горизонтального многоточия `…`, U+2026). Пример: `sk-p…bA3T`.
- Длина ключа `< 8` (фрагменты пересеклись бы) → **полная маска** `"********"`; `key_prefix`/`key_last4` при этом = `NULL` (не сохраняются). Реальные ключи OpenAI/Anthropic длиннее — это защитный кейс.

## Backend — ТЗ

Слои и стек — как в модуле `servers` ([modules/servers](../servers/README.md)): router → service → repository (SQLAlchemy async), Pydantic-схемы = контракт. Образцы для переиспользования: `app/api/servers.py`, `app/services/server_service.py`, `app/repositories/server_repository.py`, `app/models/server.py`, `app/schemas/server.py`; фоновая задача — паттерн `asyncio.create_task` + set сильных ссылок (как при создании сервера).

### Endpoints (все под JWT, префикс `/api`)

- `GET /api/ai-keys` → список `AiKeyListItem` + `position` + **`backend_count`** (число бэков, использующих ключ, `COUNT` по `backends.ai_key_id` — для секции «Бэки» detail-view, [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)) (см. [04-api.md](../../04-api.md#get-apiai-keys)). Сортировка `position ASC, created_at DESC, id`. Плоский список; группировка по провайдеру — на frontend. Пагинации нет.
- `POST /api/ai-keys {name, provider, key}` → `202`; валидация, шифрование ключа (Fernet), вычисление `key_prefix`/`key_last4`, `INSERT check_status='pending'` (`position` = `DEFAULT 0`), запуск **немедленной фоновой проверки** (`asyncio.create_task`). Возвращает созданный `AiKeyListItem` (`check_status:"pending"`).
- `PATCH /api/ai-keys/{id} {name?, provider?, key?}` → `200`; редактирование ключа. **Секретная семантика:** `key` пустой/отсутствует = не менять; непустой `key` → re-encrypt + пересчёт `key_prefix`/`key_last4`. **Re-check:** смена `provider` ИЛИ непустой `key` → `check_status='pending'`, `error_message=NULL`, немедленная фоновая проверка (первый переход от `prev='pending'`). Только смена `name` — без re-check. См. [«Редактирование ключа»](#редактирование-ключа-patch-нормативно) и [04-api.md](../../04-api.md#patch-apiai-keysid).
- `PATCH /api/ai-keys/order {provider, ids}` → `204`; перестановка **внутри провайдер-группы** (`WHERE provider=:provider`), `position = 0..M-1` в одной транзакции. Прецеденция кодов: битое тело / нет `provider` → `400`; `provider` вне enum → `422` (до проверки id); **любой несуществующий `id` → `404` (проверяется до полноты)**; только если все `id` существуют — неполная перестановка группы / чужой провайдер → `422`. См. [04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов).
- `GET /api/ai-keys/{id}/status` → `{id, check_status, error_message, last_checked_at}`. Лёгкий endpoint для polling статуса после добавления/редактирования.
- `DELETE /api/ai-keys/{id}` → `204`; hard delete. Повтор → `404 ai_key_not_found`.
- `GET /api/ai-keys/{id}/key` (гейт `require("ai-keys","edit")`) → `200 SecretRevealResponse {value}`; **on-demand reveal** полного ключа для detail-view ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)): `decrypt_secret(key_encrypted)` in-memory, `Cache-Control: no-store`, аудит `secret_revealed`. Нет права → `403`; нет ключа → `404 ai_key_not_found`. Контракт — [04-api.md](../../04-api.md#get-apiai-keysidkey), [05-security.md](../../05-security.md#reveal-секретов-по-требованию-adr-035).
- `GET /api/ai-keys/{id}/backends` (гейт `require("ai-keys","view")`) → `200 {backends: BackendRef[]}` (`{code,name,domain}`); **reverse-lookup** бэков, использующих ключ (`backends.ai_key_id = {id}`) — для сворачиваемой секции «Бэки» в detail-view ключа ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)). Сортировка `position ASC, created_at DESC, id`. Нет ключа → `404 ai_key_not_found`. Свёрнутый счётчик — `AiKeyListItem.backend_count`. Контракт — [04-api.md](../../04-api.md#get-apiai-keysidbackends).

Коды ошибок и точные схемы — [04-api.md](../../04-api.md#ai-keys). `provider` вне enum → `422 unprocessable` (code `unprocessable`, по аналогии с невалидным IP у серверов).

### Редактирование ключа (`PATCH`, нормативно)

`PATCH /api/ai-keys/{id}` принимает `{name?, provider?, key?}` (все опциональны). Правила:

1. **Секрет никогда не префилится и не отдаётся.** Backend не хранит plaintext-ключ в открытом виде и не возвращает его; поэтому в форме edit поле «Ключ» **пустое**. Пустое поле (`""` / отсутствие) = «оставить текущий ключ». Непустое значение = заменить.
2. **Смена `key` (непустой):** расшифровка не нужна — новый plaintext сразу шифруется (`encrypt_password`), пересчитываются `key_prefix`/`key_last4`; `key_masked` в ответе — по новому ключу. Правило маски (в т.ч. `<8` символов → `********`, `key_prefix/key_last4 = NULL`) — то же, что при создании ([правило маски](#правило-маски-key_masked)).
3. **Re-check триггерится, если** изменился `provider` **ИЛИ** передан непустой `key`: `check_status='pending'`, `error_message=NULL`, запуск немедленной фоновой проверки (тот же путь, что `POST`; `prev_status='pending'`). Первая неуспешная проверка после edit шлёт **🔴** (как для нового ключа), успешная — молча (`pending→working`).
4. **Смена только `name`** — `check_status` не трогается, проверка не перезапускается.
5. **Смена `provider` без нового `key`** — тот же секрет проверяется против нового провайдера: `key_encrypted`/маска не меняются, но `check_status='pending'` + re-check (ключ формата одного провайдера у другого, как правило, даст `error` — это корректный результат проверки, не баг).
6. `updated_at` обновляется всегда при изменении хотя бы одного поля. `last_checked_at` при re-check не сбрасывается (остаётся временем последней конклюзивной проверки до завершения новой).

### Группировка по провайдерам и перестановка (нормативно)

- **UI-группировка:** frontend делит плоский `GET /api/ai-keys` на секции по `provider` (заголовки **OpenAI** / **Anthropic**), внутри секции — порядок по `position`. Backend секции не формирует. UI-детали — [08-design-system.md](../../08-design-system.md#группировка-ии-ключей-по-провайдерам).
- **Перестановка — только внутри своей группы.** Провайдер у ключа при drag-and-drop фиксирован; между секциями карточки не перемещаются. Сменить провайдера можно только через `PATCH /api/ai-keys/{id}` (что запустит re-check).
- `PATCH /api/ai-keys/order {provider, ids}` валидирует, что `ids` — полная перестановка ключей ровно этого провайдера (иначе `422`); присваивает `position = 0..M-1` только этой группе.

### Требования

1. Ключ (plaintext) НИКОГДА не возвращается в обычных list/detail-ответах и не логируется (structlog-фильтр секретов, [05-security.md](../../05-security.md)). **Исключение:** on-demand reveal полного ключа под `ai-keys:edit` ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)).
2. `key_prefix`/`key_last4` вычисляются один раз при создании; `key_masked` собирается в схеме ответа.
3. `check_status` ∈ {`pending`,`working`,`error`}, default `pending`. `error_message` — русскоязычная причина при `error`, иначе `NULL`.
4. `updated_at`/`last_checked_at` обновляются при каждой проверке **с конклюзивным исходом** (`working`/`error`) — атомарным `UPDATE`. Транзиентный `unknown` (сеть/таймаут/`5xx`) конклюзивной проверкой **не считается** и строку `ai_keys` не трогает (см. маппинг ниже); `last_checked_at` тем самым отражает время последней конклюзивной проверки.
5. **Каждая Alembic-миграция обязана иметь рабочий `downgrade()`** (основа отката релиза — [07-deployment.md](../../07-deployment.md#откат-миграций-бд), [03-data-model.md](../../03-data-model.md)).
6. **Колонка `position`** (`integer NOT NULL DEFAULT 0`) добавляется общей миграцией `0003_add_position` (`down_revision=0002_create_ai_keys`) с backfill по `PARTITION BY provider ORDER BY created_at DESC` ([03-data-model.md](../../03-data-model.md#миграция-0003_add_position-концепт)). Reorder переставляет `position` в одной транзакции внутри провайдер-группы.

### Проверка ключа у провайдера (нормативно)

Проверка = **только валидность/блокировка**, без траты токенов. Используется лёгкий read-only `GET /v1/models`. HTTP-клиент — `httpx` с коротким таймаутом `AI_PROVIDER_TIMEOUT_SEC` (default 10 с) и ограниченными ретраями на транзиентные ошибки (паттерн `app/infra/prometheus.py`).

**OpenAI:**
- `GET {OPENAI_API_BASE}/models`, заголовок `Authorization: Bearer <key>`.

**Anthropic:**
- `GET {ANTHROPIC_API_BASE}/models`, заголовки `x-api-key: <key>` и `anthropic-version: {ANTHROPIC_API_VERSION}` (default `2023-06-01`).

**Маппинг результата → исход проверки:**

| Ответ провайдера | Исход | `check_status` | `error_message` (рус.) |
|------------------|-------|----------------|-------------------------|
| `200` | `working` | `working` | `NULL` |
| `401` | `error` | `error` | **«Ключ недействителен»** |
| `403` | `error` | `error` | **«Доступ запрещён»** |
| `429` c признаком `insufficient_quota` | `error` | `error` | **«Недостаточно средств»** |
| прочий `4xx` (в т.ч. `429` без `insufficient_quota`) | `error` | `error` | **«Ошибка провайдера»** |
| таймаут / сетевая ошибка / `5xx` | **`unknown`** | **не меняется** (строка `ai_keys` не обновляется целиком: ни `check_status`, ни `error_message`, ни `last_checked_at`) | не меняется |

- Признак `insufficient_quota` детектируется по телу ошибки провайдера (OpenAI: `error.code == "insufficient_quota"`; Anthropic — эквивалентный признак исчерпания квоты/кредитов). Если тело нераспознаваемо — трактуется как «прочий 4xx» → «Ошибка провайдера». Best-effort ([TD-020](../../100-known-tech-debt.md)).
- **`unknown` — ключевое правило устойчивости:** транзиентная недоступность провайдера НЕ флипает статус в `error` и НЕ шлёт алерт (иначе сеть/5xx провайдера = ложный «ключ сломан»). Строка `ai_keys` при `unknown` **не обновляется вообще** — включая `last_checked_at`, которое остаётся временем последней конклюзивной проверки (`working`/`error`). Логируется `ai_key_check_unknown` (warning). Только 4xx-ответы авторизации/квоты меняют статус.

### Фоновый монитор `AiKeyMonitorService` (нормативно)

Отдельная фоновая asyncio-задача (**НЕ** state-машина нотификатора серверов — [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md)). Состояние переходов берётся из БД `check_status` (персистентно, переживает рестарт).

- **Запуск:** в `lifespan` (`app/main.py`), рядом с нотификатором. Монитор **стартует ВСЕГДА** (не гейтится Telegram) — обновление `check_status` для UI работает независимо от бота.
- **Остановка:** отмена задачи при shutdown (`task.cancel()` + ожидание, обработка `CancelledError`).
- **Цикл:** бесконечный `while True`: одна итерация проверки всех ключей → `asyncio.sleep(AI_KEY_CHECK_INTERVAL_SEC)` (default 900 с). Необработанное исключение внутри итерации логируется и **не валит задачу**.
- **Итерация:** открыть короткоживущую сессию БД (`get_sessionmaker()`), получить все ключи (снимок `id, name, provider, key_encrypted, prev_status=check_status, key_last4`), закрыть сессию. Для каждого ключа: расшифровать, вызвать проверку провайдера, вычислить исход; **при конклюзивном исходе** (`working`/`error`) — обновить БД (`check_status`, `error_message`, `last_checked_at`, `updated_at`) отдельным атомарным `UPDATE`; **при `unknown` — строку не трогать вообще**; вычислить переход относительно `prev_status`, при необходимости отправить алерт (если `notifier_enabled`).

**Немедленная проверка при создании (`POST /api/ai-keys`):** та же логика проверки одного ключа запускается фоново сразу после `INSERT`. Первый переход считается от `prev_status='pending'`.

### Переходы статуса и алерты (нормативно)

`prev` — предыдущий `check_status` из БД, `cur` — исход текущей проверки:

| `prev` | `cur` | Действие |
|--------|-------|----------|
| `pending` / `working` | `error` | **🔴 «Ключ не работает»** (в т.ч. первая проверка сломанного ключа) |
| `error` | `working` | **🟢 «Ключ снова работает»** (recovery/отбой) |
| `working` | `working` | молча |
| `pending` | `working` | молча (первая успешная проверка — не recovery) |
| `error` | `error` | молча (уже сломан; при этом `error_message` обновляется на актуальную причину) |
| любой | `unknown` | молча, `check_status` НЕ меняется |

- Telegram-отправка выполняется **только если** `settings.notifier_enabled` (`TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` заданы). Иначе переход только фиксируется в БД (статус для UI), лог `ai_key_alert_suppressed_no_telegram` (info) — не ошибка.
- `check_status` в БД обновляется **всегда**, независимо от `notifier_enabled` и от результата отправки Telegram.
- Персистентность `check_status` гарантирует: после рестарта backend сломанный ключ не переоткрывается (нет дубль-🔴), а recovery отрабатывает корректно между рестартами.

### Формат сообщений Telegram (точно)

Формат — **точно** (plain-текст, имя ключа в двойных кавычках, `<last4>` = `key_last4`; для короткого ключа, где `key_last4 = NULL`, подставляется пустая строка → `****`).

> **Этот текст ПОБУКВЕННО дублируется** в [modules/notifier §Сообщения AI-ключей](../notifier/README.md#сообщения-ai-ключей) и [modules/ai-keys §Формат сообщений Telegram](../ai-keys/README.md#формат-сообщений-telegram-точно). Оба вхождения обязаны совпадать **посимвольно**; при правке — менять оба. Формат строки бэка — источник истины [modules/backends](../backends/README.md#формат-сообщений-telegram-точно-нормативно--источник-истины).

**🔴 Ключ не работает** (переход `pending|working → error`) — **дополняется перечнем бэков, использующих этот ключ** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1):

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Ключ "<name>" ****<last4>
Ключ не работает: "<reason>"

Бэки:
Бэк "<name1>" [<code1>] <domain1>
Бэк "<name2>" [<code2>] <domain2>
```

`<reason>` = актуальный `error_message` («Ключ недействителен» / «Доступ запрещён» / «Недостаточно средств» / «Ошибка провайдера»).

**Блок «Бэки:» (нормативно, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1):**
- **Источник:** `backends WHERE ai_key_id = :ai_key_id` (связь [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)), порядок — **`position ASC, code ASC`**.
- **⚠️ Порядок перечня в АЛЕРТЕ ≠ порядок API reverse-lookup — намеренно (нормативно, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1).** Репозиторные методы `BackendRepository.list_by_server` / `list_by_ai_key`, обслуживающие эндпоинты `GET /api/servers|ai-keys/{id}/backends` ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)), сортируют **`position ASC, created_at DESC, id ASC`** — **этот контракт НЕ меняется**. Для текста алерта нормативен **`position ASC, code ASC`**: `code` UNIQUE ⇒ порядок **тотальный и детерминированный** (тай-брейк однозначен даже при равных `position`/`created_at`), что необходимо для побайтово воспроизводимого формата сообщения и его тестов. Переупорядочение выполняется **in-memory поверх результата репозитория** (отдельный хелпер), а **не** сменой `ORDER BY` — чтобы не трогать публичный контракт reverse-lookup. Два порядка сосуществуют осознанно.
- **Строка бэка — переиспользуется `_backend_block(code, name, domain)`** побуквенно: `Бэк "<name>" [<code>] <domain>`.
- **⚠️ Механика сортировки (нормативно): in-memory по КОРТЕЖУ `(position, code)` — перенос тай-брейка в SQL `ORDER BY` ЗАПРЕЩЁН.** Запись «`position ASC, code ASC`» описывает **требуемый порядок**, а не способ его получения. Реализация — Python-сортировка по кортежу `(position, code)`, т.е. по **кодпойнтам** строки `code`. Это **не то же самое**, что `ORDER BY position, code` в PostgreSQL: там порядок задаёт **коллация БД** (напр. `en_US.UTF-8` игнорирует регистр и знаки препинания), поэтому для кодов со **смешанным регистром** SQL-порядок и порядок кодпойнтов **расходятся**. Побайтовая воспроизводимость текста алерта (qa сверяет посимвольно) требует именно детерминизма кодпойнтов и независимости от настроек локали БД. **Не «унифицировать» это в `ORDER BY` при будущем рефакторинге** — порядок молча изменится.
- **Пустой перечень → блок не добавляется вовсе** (ни строки `Бэки:`, ни пустой строки перед ней) — сообщение побайтово равно прежнему.
- **Лимит `MAX_ALERT_BACKENDS = 10`:** при `N > 10` печатаются первые 10, последней строкой блока идёт `… и ещё <N-10>` (символ `…` = U+2026). Долг — [TD-053](../../100-known-tech-debt.md).
- Сигнатура: `build_key_error(name, last4, reason, backends=())`; `BackendRef = tuple[str, str, str]` = `(code, name, domain)`.

**🟢 Ключ восстановлен** (переход `error → working`). **Перечнем бэков НЕ расширяется** (`build_key_recovery(name, last4)` — без изменений):

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Ключ "<name>" ****<last4>
Ключ снова работает
```

Доставка — через тот же `TelegramClient.send_message` (best-effort, at-least-once, ограниченные ретраи; секреты не логируются) — см. [modules/notifier](../notifier/README.md#доставка-в-telegram).

### Backend — ориентиры реализации (структура — на усмотрение)

1. **Настройки** (`config.py`): `ai_key_check_interval_sec: int = 900`, `ai_provider_timeout_sec: int = 10`, `openai_api_base: str`, `anthropic_api_base: str`, `anthropic_api_version: str = "2023-06-01"`. `notifier_enabled` переиспользуется.
2. **Провайдер-адаптеры** (`infra/` или `domain/`): функция проверки на провайдер (`check_openai(key)`, `check_anthropic(key)`) → чистый результат `CheckOutcome{status, error_message}` (`working`/`error`/`unknown`). Маппинг статусов тестируется без сети (моки httpx).
3. **Билдеры сообщений** (`domain/`, рядом с `app/domain/notifications.py`): чистые функции `build_ai_key_error_message(name, last4, reason)` / `build_ai_key_recovery_message(name, last4)` → строка. qa проверяет побайтовое совпадение формата.
4. **AiKeyMonitorService** (`services/`): цикл + **чистая функция перехода** `evaluate(prev_status, outcome) -> (new_status, alert | None)` для тестируемости матрицы переходов без сети/БД.
5. **Роутер/сервис/репозиторий** (`api/`, `services/`, `repositories/`, `models/`, `schemas/`): CRUD по образцу серверов.
6. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` монитора при старте (всегда), отмена при shutdown.

## Frontend — ТЗ

Зеркалит модуль `servers`; детальный UI-гайд — [08-design-system.md](../../08-design-system.md#страница-ии---ключи). Реализация строк — русский словарь ([08-design-system.md](../../08-design-system.md#локализация-страницы-ии---ключи)).

### Навигация

- Ввести общий **`AppLayout`** с верхними вкладками (`NavLink`): **«Серверы»** (`/servers`) | **«ИИ - ключи»** (`/ai-keys`). Активная вкладка подсвечивается. Заголовок, ранее зашитый в `ServersPage.tsx`, переносится в layout.
- Роутинг в `App.tsx` (react-router): защищённые маршруты `/servers` и `/ai-keys` внутри `AppLayout`.

### Страница `AiKeysPage`

- **Секции по провайдерам:** страница делится на секцию **OpenAI** и секцию **Anthropic** (заголовки секций), внутри каждой — своя адаптивная сетка карточек `AiKeyCard` + `AddAiKeyCard`. Пустые секции (нет ключей провайдера) — **скрывать** (не рендерить заголовок без карточек); `AddAiKeyCard` присутствует в каждой видимой секции. UI-детали — [08-design-system.md](../../08-design-system.md#группировка-ии-ключей-по-провайдерам).
- `AiKeyCard`: имя, provider (OpenAI/Anthropic), маска ключа (`key_masked`, моношрифт), статус-бейдж (**Работает** / **Не работает** / **Проверка…**), причина ошибки при `error`, кнопка **Удалить**.
- **Клик по карточке = read-only detail-модалка** `AiKeyDetailModal` ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)): Название/Провайдер/Ключ (`key_masked`) + глаз-reveal полного ключа (под `ai-keys:edit`). Карандаш вверху справа → `AddAiKeyModal mode='edit'`. **Зажатие ~200 мс + движение = перетаскивание** (@dnd-kit, [08-design-system.md](../../08-design-system.md#перестановка-карточек-drag-and-drop)). Кнопка **Удалить** — `stopPropagation`. Паттерн — [08-design-system.md](../../08-design-system.md#detail-view-карточных-страниц-read-only--карандаш--edit-adr-035).
- Кнопка **«Добавить»** в правой зоне заголовка страницы ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2б; `AddAiKeyCard` **упразднена**) → `AddAiKeyModal` (Radix Dialog) в режиме **add**: поля **Название**, **Провайдер** (Select), **Ключ** (type=password, toggle видимости). Кнопки **Отмена** / **Добавить**.
- **Режим edit `AddAiKeyModal`:** префил `name` и `provider`; поле **Ключ пустое** с подсказкой «Оставьте пустым, чтобы не менять ключ»; иконка-глаз показывает вводимое значение. Кнопка действия — **Сохранить**. `PATCH /api/ai-keys/{id}` отправляет только изменённые поля; пустой `key` не отправляется (или отправляется `""`). После смены `provider`/`key` карточка возвращается в **Проверка…** и polling статуса возобновляется.
- **Перестановка:** внутри секции своего провайдера через `SortableContext`; на `onDragEnd` — оптимистичное обновление + `PATCH /api/ai-keys/order {provider, ids}`; при ошибке — откат и инвалидация `GET /api/ai-keys`. Между секциями перетаскивание запрещено.
- Данные и polling — через feature-слой `features/ai-keys` (`api.ts`, `hooks.ts`) на TanStack Query, по образцу `features/servers`. Типы — в `types/api.ts`. Статус `pending` → показывать «Проверка…», лёгкий polling `GET /api/ai-keys/{id}/status` до выхода из `pending`.

### Новый UI-примитив `Select`

- **Решение:** нативный `<select>`, стилизованный Tailwind (тёмная поверхность, кастомная стрелка) — **без новой зависимости** ([08-design-system.md](../../08-design-system.md#компонент-select), причина — простота NFR-1: два значения, доступность даёт нативный контрол). В `docs/02-tech-stack.md` новая библиотека не добавляется.
- Значения: `OpenAI` (`provider=openai`) / `Anthropic` (`provider=anthropic`).

### Состояния UI

Loading (skeleton), empty (только `AddAiKeyCard` + подсказка), pending («Проверка…», спиннер), error (акцентная граница + причина + «Удалить»), toast «Ключ добавлен» / «Ключ удалён», обработка `422`/сетевых ошибок — по образцу серверов ([08-design-system.md](../../08-design-system.md#состояния-ui-обязательны)).

## DoD

- [ ] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md#ai-keys); полный ключ отсутствует в ответах/логах.
- [ ] **([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), spec-ready):** `AiKeyListItem += backend_count`; `GET /api/ai-keys/{id}/backends` под `ai-keys:view` → `BackendRef[]`; секция «Бэки» в detail-view ключа (свёрнуто «Бэков: N», раскрытие — список Код/Название/Домен).
- [ ] Ключ зашифрован Fernet (`FERNET_KEY`); `key_masked` собирается из `key_prefix`/`key_last4`; правило маски (в т.ч. `<8` символов) соблюдено.
- [ ] Проверка провайдера использует `GET /v1/models` (токены не тратятся); маппинг статусов и правило `unknown` соблюдены (транзиентные ошибки не флипают статус и не алертят).
- [ ] Матрица переходов и алерты соответствуют таблице; первая проверка сломанного ключа алертит (🔴), recovery `error→working` шлёт 🟢.
- [ ] Формат обоих сообщений Telegram побайтово соответствует спецификации.
- [ ] Монитор стартует всегда; Telegram-отправка гейтится `notifier_enabled`; `check_status` в БД обновляется независимо от бота; переходы переживают рестарт (состояние из БД).
- [ ] Alembic-миграция `ai_keys` с рабочим `downgrade()`; колонка `position` добавлена миграцией `0003_add_position` (backfill по провайдер-группам, рабочий `downgrade()`).
- [ ] `PATCH /api/ai-keys/{id}`: пустой `key` = не менять; непустой → re-encrypt + пересчёт маски; смена `provider`/`key` → `check_status='pending'` + немедленный re-check (первая неудача → 🔴).
- [ ] `PATCH /api/ai-keys/order`: перестановка только внутри провайдер-группы; полная перестановка группы валидируется (иначе `422`); чужой провайдер → `422`.
- [ ] Frontend: `AppLayout` со вкладками, `AiKeysPage` с **секциями по провайдерам**, `AiKeyCard`/`AddAiKeyCard`/`AddAiKeyModal` (add+edit режимы), **`AiKeyDetailModal`** (read-only + глаз-reveal полного ключа, [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)), примитив `Select`, drag-and-drop внутри секции (@dnd-kit, клик=detail→карандаш=edit / зажатие=drag), все состояния UI, русские строки из словаря.
- [ ] Coverage ≥90 % для функций проверки/перехода/билдеров сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [ ] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-11: **Перечень бэков в алерте «Ключ не работает»** (architect, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1; контракт/БД не затронуты). `build_key_error(name, last4, reason, backends=())` дополняется блоком `Бэки:` (`backends WHERE ai_key_id = :id`, порядок `position ASC, code ASC`, лимит 10 + `… и ещё N`, пустой перечень → блока нет). Формат строки бэка — реюз `_backend_block` ([modules/backends](../backends/README.md#формат-сообщений-telegram-точно-нормативно--источник-истины)). **Recovery (`build_key_recovery`) НЕ расширяется.** Раздел «Формат сообщений Telegram» обязан **побуквенно совпадать** с [modules/notifier](../notifier/README.md#сообщения-ai-ключей). **UI:** detail-модалка ключа показывает сразу только **Название**/**Провайдер**; **Ключ** и секция «Бэки» — в свёрнутом блоке **«Информация»** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2в); пустые поля не рендерятся (§3); `AddAiKeyCard` упразднена — «Добавить» переехала в правую зону заголовка (§2б).

- 2026-07-10: **reverse-lookup бэков ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), spec-ready):** `AiKeyListItem += backend_count` (число бэков, использующих ключ, `COUNT` по `backends.ai_key_id`); `GET /api/ai-keys/{id}/backends` (гейт `ai-keys:view`) → `{backends: BackendRef[]}`; в detail-view ключа (`AiKeyDetailModal`) — сворачиваемая секция **«Бэки»** (свёрнуто «Бэков: N», раскрытие → список Код/Название/Домен). Контракт reveal-ключа не меняется.
- 2026-07-09: **detail-view + reveal полного ключа** ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md), spec-ready): клик по карточке → read-only `AiKeyDetailModal` (`key_masked` + глаз), карандаш → edit; `GET /api/ai-keys/{id}/key` (гейт `ai-keys:edit`, `decrypt_secret`, `no-store`, аудит `secret_revealed`). Контракт `AiKeyListItem` не меняется (полный ключ только через reveal).
- 2026-07-01: спецификация создана (architect). Решение об in-backend-мониторе AI-ключей и Fernet-шифровании — [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md); ограничения — [TD-020](../../100-known-tech-debt.md), [TD-021](../../100-known-tech-debt.md).
- 2026-07-01: добавлены `PATCH /api/ai-keys/{id}` (edit `name`/`provider`/`key`, секрет пустой=не менять, re-check при смене provider/key), `PATCH /api/ai-keys/order` (reorder внутри провайдер-группы), UI-группировка по провайдерам, клик=edit / зажатие=drag; колонка `position` + миграция `0003`. Редактирование/ротация ключа переведены из out-of-scope в scope ([ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md)); [TD-021](../../100-known-tech-debt.md) сокращён.
