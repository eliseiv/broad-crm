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

**Сортировка:** всегда `created_at DESC` (новые сверху), вторичный ключ — `id` для детерминизма. Пагинации на Этапе 1 нет (до ~50 серверов, NFR-5).

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
{ "id": "a1b2...", "name": "Server 02", "ip": "10.0.0.13", "exporter_port": 9100, "provision_status": "pending" }
```
> `202`, т.к. установка не мгновенна; статус отслеживается через `GET /api/servers/{id}/status`. Пароль в ответе не возвращается.

**Ошибки:** `400 validation_error`, `409 server_conflict` (дубликат `ip`), `422 unprocessable` (невалидный IP), `503 provisioning_unavailable`.

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
