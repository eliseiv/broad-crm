# 04 · API-контракты

Базовый префикс: `/api`. Формат — JSON (UTF-8). Аутентификация — `Authorization: Bearer <JWT>` (кроме `/auth/*` и `/health`). Все временные метки — ISO 8601 UTC.

## Общие правила

### Заголовки
- Запрос с телом: `Content-Type: application/json`.
- Защищённые эндпоинты: `Authorization: Bearer <token>`.

### Единый формат ошибки

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Неверный логин или пароль",
    "details": null
  }
}
```

| HTTP | `code` | Когда |
|------|--------|-------|
| 400 | `validation_error` | Невалидное тело/параметры (детали в `details[]`) |
| 401 | `invalid_credentials` | Неверные логин/пароль на `/auth/login` |
| 401 | `unauthorized` | Отсутствует/просрочен/невалиден JWT |
| 404 | `server_not_found` | Сервера с таким `id` нет |
| 404 | `ai_key_not_found` | AI-ключа с таким `id` нет |
| 409 | `server_conflict` | Сервер с таким `ip` уже существует |
| 422 | `unprocessable` | Семантически некорректные данные (например, невалидный IP) |
| 429 | `rate_limited` | Превышен лимит попыток входа |
| 500 | `internal_error` | Непредвиденная ошибка |
| 502 | `prometheus_unavailable` | Prometheus недоступен/таймаут при запросе метрик |
| 503 | `provisioning_unavailable` | Невозможно запустить фоновую задачу провижининга |

`validation_error.details` — массив `{ "field": "ip", "message": "..." }`.

---

## Auth

Двухшаговый вход — это UX (два экрана). Бэкенд проверяет креды единым запросом на шаге 2. Обоснование — [ADR-002](adr/ADR-002-dvuhshagovyy-auth.md). Эндпоинт шага 1 на сервере НЕ требуется (шаг 1 — клиентский переход), что исключает user-enumeration.

### POST `/api/auth/login`
Проверяет логин+пароль против `ADMIN_USER`/`ADMIN_PASSWORD`, возвращает JWT.

**Request**
```json
{ "username": "admin", "password": "secret" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `username` | string | required, 1–128 |
| `password` | string | required, 1–256 |

**Response 200**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Ошибки:** `401 invalid_credentials` (логин и/или пароль неверны — без указания, что именно), `400 validation_error`, `429 rate_limited`.

> Защита от перебора: rate-limit по IP (например, 10 попыток / 5 мин). Конкретный механизм — [modules/auth](modules/auth/README.md). Сообщение об ошибке одинаково для неверного логина и неверного пароля (не раскрывать существование пользователя).

### GET `/api/auth/me`
Проверка валидности токена / получение профиля сессии. Требует JWT.

**Response 200**
```json
{ "username": "admin" }
```
**Ошибки:** `401 unauthorized`.

---

## Servers

### GET `/api/servers`
Список серверов с последними метриками и статусом. Требует JWT.

**Сортировка:** `position ASC, created_at DESC, id` — порядок задаётся пользователем через drag-and-drop (`PATCH /api/servers/order`, [«Перестановка»](#перестановка-порядок-карточек)); при равных `position` новые выше; `id` — финальный тай-брейк ([03-data-model.md](03-data-model.md#колонка-position-порядок-карточек)). Пагинации на Этапе 1 нет (до ~50 серверов, NFR-5).

**Кэш и устойчивость:** ответ может отдаваться из короткого TTL-кэша (`METRICS_CACHE_TTL_SEC`, default 5 с) с single-flight, и устойчив к транзиентным ошибкам Prometheus (ретраи на `429`/`5xx`/таймаут, ограничение конкурентности). Деградация (`metrics=null`/`online=false`) наступает только при **устойчивой** недоступности Prometheus (после исчерпания ретраев); кэш не маскирует недоступность дольше своего TTL. Детали — [modules/monitoring](modules/monitoring/README.md#устойчивость-read-path-нормативно).

**Query (опц.):** `status` ∈ {`pending`,`installing`,`online`,`error`} — фильтр.

**Response 200**
```json
{
  "items": [
    {
      "id": "8f1d...e2",
      "name": "Server 01",
      "ip": "10.0.0.12",
      "exporter_port": 9100,
      "provision_status": "online",
      "position": 0,
      "online": true,
      "uptime_seconds": 1323120,
      "last_updated": "2026-06-28T18:55:00Z",
      "metrics": {
        "cpu":  { "usage_percent": 65.0, "zone": "green",  "detail": { "value": null, "total": 8, "unit": "cores" } },
        "ram":  { "usage_percent": 72.0, "zone": "green",  "detail": { "value": 11.5, "total": 16.0, "unit": "GB" } },
        "ssd":  { "usage_percent": 48.0, "zone": "green",  "detail": { "value": 238.0, "total": 500.0, "unit": "GB" } }
      }
    }
  ]
}
```
- `online` — из Prometheus `up`. Если `provision_status != online` или `up == 0` → `online=false`, `metrics` могут быть `null`.
- `position` — `integer`, порядок карточки (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/servers/order`.
- `zone` ∈ {`green`,`yellow`,`red`} вычисляется backend по порогам (см. ниже) для единообразия с UI.
- Для серверов в `pending`/`installing`/`error` поля `metrics`, `uptime_seconds` = `null`.

#### Схема объекта метрики и `detail`

Каждая из `cpu`/`ram`/`ssd` — объект `{ usage_percent: number, zone: "green"|"yellow"|"red", detail: Detail }`, где `Detail = { value: number|null, total: number|null, unit: string }`.

`detail` различается по метрике (закрытие [Q-MON-1](99-open-questions.md)):

| Метрика | `unit` | `value` | `total` | Условие |
|---------|--------|---------|---------|---------|
| RAM | `"GB"` | used GB | total GB | всегда (метрики памяти доступны) |
| SSD | `"GB"` | used GB | total GB | всегда (метрики ФС `/` доступны) |
| CPU | `"cores"` | `null` | число логических ядер | всегда (стандартизировано) |

> **CPU `detail` ВСЕГДА в ядрах** (переоткрытие/обновление [Q-MON-1](99-open-questions.md)): `unit:"cores"`, `value:null`, `total:<число логических ядер>`. Вариант с частотой (`GHz`) убран из scope ради единообразия отображения между серверами (на VM частота часто недоступна → был разнобой). UI показывает только `total` (например, `8 ядер` — локализацию единицы и формы мн.ч. см. [08-design-system.md](08-design-system.md#локализация-ui-русский-словарь-строк)). Если число ядер недоступно — `total:null` (UI скрывает строку абсолютных значений CPU). Поддержка частоты (GHz) — отложена ([TD-013](100-known-tech-debt.md)). `usage_percent` для CPU не зависит от `detail` и считается всегда по `node_cpu_seconds_total`.
>
> `unit` в API остаётся техническим строковым идентификатором `"cores"` (`"GB"` для RAM/SSD); локализованное отображение единиц (`ядра`/`ГБ`) — на стороне frontend.

#### Доступность метрик: `up==0` / отсутствие данных vs Prometheus down

Различаются два независимых случая (нормативно для backend и frontend):

| Ситуация | Поведение | HTTP |
|----------|-----------|------|
| **Prometheus доступен**, но `up==0` (сервер offline) ИЛИ конкретная метрика отсутствует в ответе | `online=false`; объекты `cpu`/`ram`/`ssd` присутствуют, но их `detail.value`/`detail.total = null` и/или `metrics=null` — ложные/нулевые значения НЕ подставляются. `usage_percent` отдаётся только если метрика реально получена | `200` |
| **Prometheus недоступен** (соединение/таймаут к самому Prometheus) | в `GET /api/servers` — graceful degradation (`metrics=null`, `online=false`, список `200`); в `GET /api/servers/{id}/metrics` — явная ошибка | список: `200`; одиночные метрики: `502 prometheus_unavailable` |

Ключевое: `502` — **только** при недоступности самого Prometheus. Случай «Prometheus ответил, но сервер `up==0` или метрики нет» НЕ является `502` — это валидный `200` с `online=false` и `null`-полями, чтобы не показывать пользователю выдуманные значения.

**Ошибки:** `401 unauthorized`. Недоступность Prometheus НЕ роняет список: для таких серверов `metrics=null`, `online=false` (graceful degradation), список возвращается `200`.

### POST `/api/servers`
Создаёт сервер и запускает асинхронный провижининг. Требует JWT.

**Request**
```json
{ "name": "Server 02", "ip": "10.0.0.13", "ssh_user": "root", "ssh_password": "p@ss" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |
| `ip` | string | required, валидный IPv4/IPv6 |
| `ssh_user` | string | required, 1–64 |
| `ssh_password` | string | required, 1–256 |

**Response 202 Accepted**
```json
{ "id": "a1b2...", "name": "Server 02", "ip": "10.0.0.13", "exporter_port": 9100, "provision_status": "pending", "position": 0 }
```
> `202`, т.к. установка не мгновенна; статус отслеживается через `GET /api/servers/{id}/status`. Пароль в ответе не возвращается. `position` берёт `DEFAULT 0` — новая карточка появляется вверху списка (тай-брейк `created_at DESC`).

**Ошибки:** `400 validation_error`, `409 server_conflict` (дубликат `ip`), `422 unprocessable` (невалидный IP), `503 provisioning_unavailable`.

### PATCH `/api/servers/{id}`
Редактирование сервера. На Этапе 1 меняется **только `name`**. Требует JWT. `ip`/`ssh_user`/`ssh_password`/`exporter_port`/провижининг НЕ затрагиваются (переустановка агента вне scope).

**Request**
```json
{ "name": "Server 01 (renamed)" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |

**Response 200** — обновлённый summary-объект сервера (без метрик):
```json
{ "id": "8f1d...e2", "name": "Server 01 (renamed)", "ip": "10.0.0.12", "exporter_port": 9100, "provision_status": "online", "position": 0, "created_at": "2026-06-28T18:00:00Z", "updated_at": "2026-07-01T12:00:00Z" }
```
> Смена `name` обновляет `updated_at` и, при `provision_status=online`, отражается в file_sd-таргете (label `name`) при следующей записи. Немедленная перезапись таргета для переименования не требуется (label `name` информативный, скрейп идёт по `instance`).

**Ошибки:** `401 unauthorized`, `404 server_not_found`, `400 validation_error` (пустое/слишком длинное `name`).

### GET `/api/servers/{id}/metrics`
Текущие метрики одного сервера (для polling карточки). Требует JWT.

**Response 200**
```json
{
  "id": "8f1d...e2",
  "online": true,
  "uptime_seconds": 1323120,
  "last_updated": "2026-06-28T18:55:00Z",
  "cpu":  { "usage_percent": 65.0, "zone": "green",  "detail": { "value": null, "total": 8,    "unit": "cores" } },
  "ram":  { "usage_percent": 72.0, "zone": "green",  "detail": { "value": 11.5, "total": 16.0, "unit": "GB" } },
  "ssd":  { "usage_percent": 48.0, "zone": "green",  "detail": { "value": 238.0,"total": 500.0,"unit": "GB" } }
}
```
При `up==0` или отсутствии метрики (Prometheus доступен) → `200` с `online=false` и `null`-полями `detail`/значений (см. [«Доступность метрик»](#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down)), а не `502`.

**Ошибки:** `401`, `404 server_not_found`, `502 prometheus_unavailable` — **только** при недоступности самого Prometheus (соединение/таймаут), т.к. это endpoint про метрики.

### GET `/api/servers/{id}/status`
Лёгкий endpoint статуса провижининга (для прогресс-индикатора). Требует JWT.

**Response 200**
```json
{ "id": "a1b2...", "provision_status": "installing", "error_message": null, "updated_at": "2026-06-28T18:50:11Z" }
```
**Ошибки:** `401`, `404 server_not_found`.

### DELETE `/api/servers/{id}`
Удаляет сервер из мониторинга (снимает file_sd-таргет, удаляет запись). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 server_not_found`.
> node_exporter на целевом сервере не удаляется ([TD-002](100-known-tech-debt.md)).

---

## AI Keys

Реестр API-ключей AI-провайдеров с автоматической проверкой валидности. Модуль — [modules/ai-keys](modules/ai-keys/README.md), модель — [03-data-model.md](03-data-model.md#таблица-ai_keys), решение — [ADR-010](adr/ADR-010-ai-key-monitor-vnutri-backend.md). Все эндпоинты требуют JWT. **Полный ключ никогда не возвращается** — только маска `key_masked`.

### Схема `AiKeyListItem`

```json
{
  "id": "3f2a...c1",
  "name": "OpenAI Prod",
  "provider": "openai",
  "key_masked": "sk-p…bA3T",
  "check_status": "working",
  "error_message": null,
  "position": 0,
  "last_checked_at": "2026-07-01T10:15:00Z",
  "created_at": "2026-07-01T09:00:00Z",
  "updated_at": "2026-07-01T10:15:00Z"
}
```

- `provider` ∈ {`openai`,`anthropic`}.
- `position` — `integer`, порядок карточки **внутри провайдер-группы** (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/ai-keys/order`.
- `key_masked` — производное: `"<первые4>…<последние4>"` (разделитель `…` U+2026), например `sk-p…bA3T`. Для ключа короче 8 символов — полная маска `"********"`. Правило — [modules/ai-keys](modules/ai-keys/README.md#правило-маски-key_masked). Backend НИКОГДА не отдаёт полный ключ или его расшифровку.
- `check_status` ∈ {`pending`,`working`,`error`}. `error_message` — рус. причина при `error` (иначе `null`).

### GET `/api/ai-keys`
Список AI-ключей. Требует JWT. Сортировка `position ASC, created_at DESC, id`. Пагинации нет.

Backend отдаёт **единый плоский список** (без секций); frontend группирует по `provider` в секции OpenAI/Anthropic, сохраняя относительный порядок внутри каждой (см. [08-design-system.md](08-design-system.md#группировка-ии-ключей-по-провайдерам)). Так как перестановка идёт внутри провайдер-группы, `position` непрерывен `0..M-1` в пределах группы; между провайдерами значения могут совпадать — это ожидаемо (frontend сначала группирует).

**Response 200**
```json
{ "items": [ { "id": "3f2a...c1", "name": "OpenAI Prod", "provider": "openai", "key_masked": "sk-p…bA3T", "check_status": "working", "error_message": null, "position": 0, "last_checked_at": "2026-07-01T10:15:00Z", "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T10:15:00Z" } ] }
```
**Ошибки:** `401 unauthorized`.

### POST `/api/ai-keys`
Создаёт ключ и запускает **немедленную фоновую проверку** валидности. Требует JWT.

**Request**
```json
{ "name": "OpenAI Prod", "provider": "openai", "key": "sk-proj-...bA3T" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |
| `provider` | string | required, ∈ {`openai`,`anthropic`} |
| `key` | string | required, 1–512 |

**Response 202 Accepted** — созданный `AiKeyListItem` с `check_status:"pending"`:
```json
{ "id": "3f2a...c1", "name": "OpenAI Prod", "provider": "openai", "key_masked": "sk-p…bA3T", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": null, "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T09:00:00Z" }
```
> `202`, т.к. проверка провайдера асинхронна; статус отслеживается через `GET /api/ai-keys/{id}/status`. Ключ (plaintext) в ответе не возвращается. `position` берёт `DEFAULT 0` — новая карточка вверху своей провайдер-секции.

**Ошибки:** `400 validation_error`, `422 unprocessable` (невалидный `provider`).

### PATCH `/api/ai-keys/{id}`
Редактирование ключа. Требует JWT. Изменяемые поля — `name`, `provider`, `key`. **Все поля опциональны**; переданы только изменяемые.

**Request**
```json
{ "name": "OpenAI Prod (rotated)", "provider": "openai", "key": "sk-proj-...NEW" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string? | опц., 1–64 |
| `provider` | string? | опц., ∈ {`openai`,`anthropic`} |
| `key` | string? | опц., 1–512. **Пустая строка `""` или отсутствие поля = «не менять ключ»** |

**Семантика секрета (нормативно):**
- **`key` отсутствует или `""`** → текущий ключ, `key_encrypted`, `key_prefix`/`key_last4` НЕ меняются. Форма редактирования секрет не префилит (backend не хранит и не отдаёт plaintext) — поэтому пустое поле = «оставить как есть».
- **`key` непустой** → новый секрет: **re-encrypt** (Fernet, тем же `FERNET_KEY`), пересчёт маски `key_prefix`/`key_last4`, `key_masked` в ответе — по новому ключу.

**Триггер повторной проверки (нормативно):** если изменился `provider` **ИЛИ** передан непустой `key` → `check_status='pending'`, `error_message=NULL`, и запускается **немедленная фоновая проверка** у провайдера (`asyncio.create_task`, тот же путь, что при `POST`). Первый переход считается от `prev_status='pending'` — то есть неуспешная проверка после edit шлёт 🔴, как для нового ключа ([modules/ai-keys](modules/ai-keys/README.md#переходы-статуса-и-алерты-нормативно)). Если изменился только `name` — проверка НЕ перезапускается, `check_status` сохраняется.
- Смена `provider` без нового `key`: `key_encrypted`/маска остаются прежними (тот же секрет), но проверка идёт против нового провайдера → `check_status='pending'` + re-check.

**Response 200** — обновлённый `AiKeyListItem` (полный ключ не возвращается никогда):
```json
{ "id": "3f2a...c1", "name": "OpenAI Prod (rotated)", "provider": "openai", "key_masked": "sk-p…9QzK", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": "2026-07-01T10:15:00Z", "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T12:00:00Z" }
```
> При перезапуске проверки `check_status` в ответе = `pending`; frontend опрашивает `GET /api/ai-keys/{id}/status` до выхода из `pending` (как после создания). `last_checked_at` не сбрасывается (остаётся временем последней конклюзивной проверки до завершения новой).

**Ошибки:** `401 unauthorized`, `404 ai_key_not_found`, `422 unprocessable` (невалидный `provider`), `400 validation_error` (длина `name`/`key`).

### GET `/api/ai-keys/{id}/status`
Лёгкий endpoint статуса проверки (для polling после добавления). Требует JWT.

**Response 200**
```json
{ "id": "3f2a...c1", "check_status": "error", "error_message": "Недостаточно средств", "last_checked_at": "2026-07-01T10:15:00Z" }
```
**Ошибки:** `401`, `404 ai_key_not_found`.

### DELETE `/api/ai-keys/{id}`
Удаляет ключ из реестра (hard delete). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 ai_key_not_found`.

---

## Перестановка (порядок карточек)

Сохранение пользовательского порядка карточек (drag-and-drop), решение — [ADR-011](adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md), модель — [03-data-model.md](03-data-model.md#колонка-position-порядок-карточек). Оба endpoint'а требуют JWT и принимают **полный упорядоченный список `id`**; backend в одной транзакции присваивает `position = индекс` (`0..N-1`). Идемпотентны (повторная отправка того же порядка не меняет результат).

#### Прецеденция ошибок валидации (нормативно, едино для обоих order-эндпоинтов)

Проверки выполняются в фиксированном порядке; возвращается код **первого** сработавшего шага (последующие не проверяются). Это снимает неоднозначность, когда один и тот же `id` одновременно «неизвестен» (→404) и «лишний» относительно ожидаемого множества (→422).

1. **Форма тела** — тело не соответствует схеме (не массив UUID; для ai-keys отсутствует/невалиден `provider`) → `400 validation_error`.
2. **Существование всех переданных `id`** — если **любой** `id` из `ids` не существует в БД → `404` (`server_not_found` / `ai_key_not_found`). Проверяется раньше полноты множества.
3. **Полнота перестановки** — только если все `id` существуют. Проверяется, что `ids` — строго полная перестановка ожидаемого множества (та же длина, без дублей, без пропусков и лишних). Для ai-keys ожидаемое множество — ровно ключи переданного `provider` (любой существующий `id`, принадлежащий другому провайдеру, здесь трактуется как «лишний/чужой») → `422 unprocessable`.

Итог: несуществующий `id` всегда даёт `404` (даже если он же нарушает полноту); `422` возможен только когда все `id` существуют, но множество собрано неверно (пропуск/дубль/лишний-своего-провайдера/чужой-провайдер).

Для ai-keys `provider` присутствует по схеме, но вне enum {`openai`,`anthropic`} → `422 unprocessable` (семантически некорректный, по аналогии с невалидным IP); эта проверка `provider` идёт до шагов 2–3 (нельзя определить ожидаемое множество группы без валидного `provider`). Отсутствие поля `provider` в теле — `400 validation_error` (шаг 1).

### PATCH `/api/servers/order`
Перестановка серверов (единый список, свободный порядок).

**Request**
```json
{ "ids": ["8f1d...e2", "a1b2...", "c3d4..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `ids` | string[] | required. **Полная перестановка** текущего множества серверов: та же длина, без дублей, все `id` существуют |

**Response 204** (без тела). Frontend после `204` инвалидирует запрос `GET /api/servers` (канонический порядок + свежие метрики).

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-обоих-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID). Проверяется первым.
- `404 server_not_found` — какой-либо `id` не существует. Проверяется **до** полноты множества: несуществующий `id` даёт `404`, даже если он же нарушает перестановку.
- `422 unprocessable` — **только если все `id` существуют**, но `ids` не является полной перестановкой (пропущены/дублируются/лишние элементы относительно текущего множества серверов).

### PATCH `/api/ai-keys/order`
Перестановка AI-ключей **внутри одной провайдер-группы**. Провайдер у ключа при перестановке фиксирован — между секциями OpenAI/Anthropic карточки не перемещаются (сменить провайдера можно только через `PATCH /api/ai-keys/{id}`).

**Request**
```json
{ "provider": "openai", "ids": ["3f2a...c1", "7b8c...", "d9e0..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `provider` | string | required, ∈ {`openai`,`anthropic`} |
| `ids` | string[] | required. **Полная перестановка** множества ключей указанного `provider`: та же длина, без дублей, все `id` существуют И принадлежат этому провайдеру |

Backend присваивает `position = 0..M-1` только ключам этой группы (`WHERE provider = :provider`).

**Response 204** (без тела). Frontend инвалидирует `GET /api/ai-keys`.

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-обоих-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID / отсутствует `provider`). Проверяется первым.
- `422 unprocessable` (`provider` вне enum) — если `provider` присутствует, но не ∈ {`openai`,`anthropic`}. Проверяется до шагов существования/полноты (без валидного `provider` не определить ожидаемую группу).
- `404 ai_key_not_found` — какой-либо `id` не существует. Проверяется **до** полноты группы: несуществующий `id` даёт `404`, даже если он же лишний/чужой.
- `422 unprocessable` (неполная группа) — **только если все `id` существуют**: `ids` не является полной перестановкой ключей этого `provider` (пропуски/дубли/лишние) ЛИБО какой-либо существующий `id` принадлежит другому провайдеру.

---

## Health

### GET `/api/health`
Без JWT. Liveness/readiness.

**Response 200**
```json
{ "status": "ok", "db": "up", "prometheus": "up" }
```
- `db`/`prometheus` ∈ {`up`,`down`}. Если зависимость down → статус `200` с `status:"degraded"` (для мониторинга самого backend), решение не ронять health на 503 при деградации Prometheus — он не критичен для liveness.

---

## Пороги зон (используются backend и frontend одинаково)

Цвет дуги спидометра СТРОГО по нагрузке:

| Зона | Условие (`usage_percent`) | Цвет |
|------|---------------------------|------|
| `green` | `< 80` | зелёный |
| `yellow` | `>= 80` и `<= 90` | жёлтый |
| `red` | `> 90` | красный |

Граничные значения: 80 → yellow, 90 → yellow, 90.01 → red. Backend проставляет `zone` в ответах; frontend дублирует ту же логику для оптимистичной отрисовки (значения порогов — единый конфиг, см. [08-design-system.md](08-design-system.md#зоны-нагрузки)).

## Версионирование и OpenAPI

- На Этапе 1 без версии в пути (`/api/...`). При несовместимых изменениях — `/api/v2`.
- FastAPI публикует OpenAPI в `/api/openapi.json` и Swagger UI в `/api/docs` только в dev; в production они отключены (`404`) — см. [05-security.md](05-security.md#документация-api-apidocs-apiopenapijson).
- Этот документ — нормативный источник; OpenAPI должен ему соответствовать.
