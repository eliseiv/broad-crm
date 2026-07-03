# Модуль `mail` — Почты (read-through-прокси к внешнему сервису)

Статус: `spec-ready` · Исполнитель: backend, frontend

## Scope

Страница **«Почты»** в CRM: просмотр писем, приходящих во внешний почтовый сервис `postapp.store`, и **ответ (reply)** на письмо. CRM работает как **read-through-прокси без хранения** (без БД, без миграций): backend синхронно проксирует запросы во внешний external-API, подставляя системный ключ `MAIL_API_KEY`, и возвращает данные фронту. Решение — [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); контракт CRM — [04-api.md#mail](../../04-api.md#mail).

Функции Этапа 1:
- Лента писем: список с адресами, темой, датой, тегами, телом (text/HTML), почтовым аккаунтом-получателем.
- Пагинация «Загрузить ещё» (keyset вперёд по `since_id`).
- Ответ на письмо (reply) через прокси к внешнему reply-эндпоинту.

## Out of scope (Этап 1)

- **Хранение писем в БД CRM** (синхронизация/индексация) — отклонено в [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); server-side поиск/фильтры/строгий глобальный «новые сверху» недоступны как следствие read-through + keyset-only внешнего API ([TD-024](../../100-known-tech-debt.md)).
- Поиск и фильтрация писем на сервере (внешний API их не предоставляет).
- Составление нового письма «с нуля» (compose) — только reply на существующее.
- Управление почтовыми аккаунтами / тегами из CRM (только чтение того, что отдаёт внешний сервис).
- Вложения (attachments) — внешний DTO их на Этапе 1 не отдаёт; вне scope.
- Пометка прочитано/непрочитано, папки, архив.
- Подгрузка внешних ресурсов HTML-письма (картинки/шрифты по URL) — блокируется CSP + sandbox ([изоляция HTML](#изоляция-html-тела-нормативно)).

## Архитектура (read-through-прокси)

- **Без БД.** Модуль не заводит таблиц, моделей SQLAlchemy и Alembic-миграций. Данные не персистятся в CRM.
- **Слои backend:** `api/mail.py` (роутер, JWT) → `services/mail_service.py` (сборка запроса, маппинг ошибок) → `infra/mail_client.py` (httpx-клиент к `postapp.store`, паттерн `app/infra/ai_provider.py`/`prometheus.py`). Pydantic-схемы в `schemas/mail.py` = контракт ([04-api.md#mail](../../04-api.md#mail)).
- **HTTP-клиент:** `httpx` с таймаутом `MAIL_API_TIMEOUT_SEC` (default 10 с) и ограниченными ретраями на транзиентные ошибки — как у клиента Prometheus/AI. Заголовок `X-API-Key: {MAIL_API_KEY}` ставится в исходящем запросе; в CRM-ответ/логи не попадает.
- **Идемпотентность ретраев (нормативно, отражает `mail_client.py`):**
  - **`list_messages` (GET, идемпотентно)** — ретраит **все** транзиентные ошибки: ошибки соединения (`ConnectError`/`ConnectTimeout`), read-timeout, `5xx`.
  - **`reply` (POST, мутирующая отправка письма — НЕ идемпотентно)** — ретраит **ТОЛЬКО** ошибки установки соединения (`ConnectError`/`ConnectTimeout`: запрос заведомо не был отправлен). На read-timeout и `5xx` **ретрая нет** — сервер мог принять письмо, повтор привёл бы к двойной отправке; такая ошибка сразу маппится в `502 mail_unavailable`.
- **Egress:** backend → `https://postapp.store` (HTTPS), см. [07-deployment.md](../../07-deployment.md#переменные-окружения). Учитывать внешний rate-limit 120 запросов/мин на IP — не опрашивать чаще необходимого (лента обновляется по запросу пользователя/`env.pollIntervalMs`, не агрессивно).

## Endpoints (все под JWT, префикс `/api`)

Точные схемы, поля и коды — [04-api.md#mail](../../04-api.md#mail).

- `GET /api/mail/messages?since_id=&limit=` → `MailListResponse{messages:[MailMessage], next_since_id, has_more}`. Прокси к внешнему `GET /api/external/messages`. `limit` 1..200 (default 50); `since_id` опционален (keyset вперёд).
- `POST /api/mail/messages/{id}/reply` тело `MailReplyRequest{to?, cc?, subject?, body}` → `MailReplyResponse{sent_id, smtp_message_id}`. Прокси к внешнему `POST /api/external/messages/{id}/reply`.

### Коды ошибок (нормативно)

| HTTP | `code` | Когда |
|------|--------|-------|
| `401` | `unauthorized` | Нет/просрочен/невалиден JWT CRM |
| `400` | `validation_error` | Невалидные параметры запроса CRM (`limit` вне 1..200, битое тело reply) |
| `422` | `unprocessable` | Семантически некорректное тело reply (например, пустой `body`) |
| `404` | `mail_message_not_found` | Письмо не найдено (проброс `404` от внешнего сервиса при reply) |
| `502` | `mail_unavailable` | Внешний сервис `postapp.store` недоступен/таймаут/вернул `5xx` |
| `503` | `mail_not_configured` | Почта не настроена (`MAIL_API_KEY` пуст, `mail_enabled=false`) |

- Фабрика ошибки `mail_unavailable` добавляется в `app/errors.py` (для backend-исполнителя), рядом с `prometheus_unavailable`. Также добавляются `mail_message_not_found` (`404`) и `mail_not_configured` (`503`).
- Маппинг ответов внешнего сервиса → коды CRM: внешний `2xx` → `200`; внешний `404` (reply на несуществующее письмо) → `404 mail_message_not_found`; внешний `4xx` валидации reply → `422 unprocessable`; внешний `5xx`/таймаут/сетевая ошибка/исчерпание ретраев → `502 mail_unavailable`. Тело/детали ошибки внешнего сервиса в ответ CRM дословно не пробрасываются (только нормативный `code`+рус. `message`).

## Безопасность ключа (нормативно)

- `MAIL_API_KEY` — **системный секрет**, только из env; задаётся администратором развёртывания (НЕ через UI, в отличие от AI-ключей). В БД не хранится (БД у модуля нет).
- Ключ подставляется **только** в заголовок `X-API-Key` исходящего запроса backend → `postapp.store`. **Никогда** не возвращается в ответах CRM API, не логируется (structlog-фильтр секретов), не передаётся в SPA, не попадает в query-строку/URL.
- Инвариант тот же, что для AI-ключей и `TELEGRAM_*` — [05-security.md](../../05-security.md#защита-ключа-почты). Образцы исходящего клиента с секретом в заголовке — `app/infra/ai_provider.py`, `app/infra/prometheus.py`.

## Изоляция HTML-тела (нормативно)

- `body_html` письма — **недоверенный** контент третьих лиц. Рендерится **только** внутри `<iframe srcDoc={body_html} sandbox>` с максимально строгим `sandbox` (без `allow-scripts` и без `allow-same-origin`): скрипты письма не исполняются, доступа к origin/куки/DOM/JWT CRM у письма нет. Решение — [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); **без новой зависимости** (DOMPurify не добавляется).
- Если `body_html` = `null`/пустой → показывается `body_text` (моношрифт, сохранение переносов), iframe не создаётся.
- `body_truncated=true` → показать пометку, что тело обрезано внешним сервисом; `body_present=false` → «Тело письма недоступно».
- Внешние ресурсы письма (картинки/шрифты по URL) на Этапе 1 не подгружаются (блокируются CSP SPA + sandbox) — осознанное упрощение, служебная лента (см. [00-vision.md](../../00-vision.md), [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md)).

## Пагинация (нормативно)

- Внешний API — **keyset вперёд по `id ASC` от `since_id`** (возвращает письма с `id > since_id`), `limit` 1..200 (default 50), `next_since_id` = максимальный `id` в батче (`integer | null`: **`null` для пустого батча** — нет новых писем вперёд; backend отдаёт `int | None`, см. [04-api.md#mail](../../04-api.md#mail)), `has_more` — есть ли ещё. **Server-side фильтров/поиска нет.**
- Первый запрос — без `since_id` (или `since_id=0`): фронт получает первый батч (до `limit` писем по возрастанию `id`) и отображает **новыми сверху в пределах батча** (сортировка по `id` DESC на клиенте).
- «Загрузить ещё» — фронт шлёт `since_id = next_since_id` предыдущего ответа; показывать кнопку, пока `has_more=true`.
- **Ограничение:** строгий глобальный порядок «новые сверху» по всей ленте и поиск/фильтры недоступны (keyset-only) — [TD-024](../../100-known-tech-debt.md). Ретенция внешнего сервиса ~30 дней — письма старше выпадают из ленты (у CRM хранилища нет).

## Поведение при `mail_enabled=false`

- Почта активна, только если задан `MAIL_API_KEY` (`settings.mail_enabled = bool(MAIL_API_KEY)`).
- При `mail_enabled=false` **оба** эндпоинта отвечают `503 mail_not_configured` (единый код). Фронт при `503 mail_not_configured` показывает состояние **«Сервис почт не настроен»** (без ленты/полей reply, без ошибочного toast-спама).

## Backend — ТЗ

Слои и стек — как у существующих модулей ([modules/ai-keys](../ai-keys/README.md), [modules/servers](../servers/README.md)), но **без репозитория/модели/миграции** (хранилища нет). Образцы исходящего HTTP-клиента с секретом — `app/infra/ai_provider.py`, `app/infra/prometheus.py`; конфиг — `app/config.py`; ошибки — `app/errors.py`; зависимость `CurrentUser` (JWT) — как в существующих роутерах; регистрация роутера — `app/api/router.py`.

1. **Настройки** (`config.py`): `mail_api_base: str = "https://postapp.store"`, `mail_api_key: str = ""` (секрет), `mail_api_timeout_sec: int = 10`. Свойство `mail_enabled: bool` = `bool(mail_api_key)`.
2. **Клиент** (`infra/mail_client.py`): `httpx` с таймаутом `mail_api_timeout_sec`, заголовок `X-API-Key`, ограниченные ретраи на транзиентные ошибки с **разной идемпотентностью для GET и POST** (см. [Архитектура — идемпотентность ретраев](#архитектура-read-through-прокси)): `list_messages` ретраит все транзиентные (connect/read-timeout/`5xx`); `reply` ретраит **только** ошибки соединения (`ConnectError`/`ConnectTimeout`), **не** read-timeout/`5xx` — во избежание двойной отправки письма. Методы: `list_messages(since_id, limit)`, `reply(message_id, payload)`. Секрет не логируется.
3. **Сервис** (`services/mail_service.py`): гейт `mail_enabled` (иначе `mail_not_configured`), валидация `limit` (1..200) и тела reply, вызов клиента, маппинг статусов внешнего сервиса → коды CRM ([коды ошибок](#коды-ошибок-нормативно)), нормализация в Pydantic-схемы.
4. **Роутер** (`api/mail.py`, под JWT): `GET /api/mail/messages`, `POST /api/mail/messages/{id}/reply`. Регистрация в `app/api/router.py`.
5. **Схемы** (`schemas/mail.py`): `MailMessage`, `MailAccount`, `MailTag`, `MailListResponse`, `MailReplyRequest`, `MailReplyResponse` — строго по [04-api.md#mail](../../04-api.md#mail).
6. **Ошибки** (`errors.py`): фабрики `mail_unavailable` (`502`), `mail_message_not_found` (`404`), `mail_not_configured` (`503`).

> Внешний reply-контракт (`POST /api/external/messages/{id}/reply`) фиксирует architect mail-агрегатора; поля тела/ответа CRM берёт из [04-api.md#mail](../../04-api.md#mail). При расхождении внешнего контракта — синхронизировать через architect (эскалация docs↔контракт).

## Frontend — ТЗ

Зеркалит feature-слой существующих страниц; UI-детали и словарь — [08-design-system.md](../../08-design-system.md#страница-почты). Наружу фронт **не ходит** — только `/api/mail/*` через `lib/api.apiRequest`.

### Навигация

- Добавить в общий `AppLayout` третью вкладку **«Почты»** (`NavLink`, маршрут `/mail`) рядом с «Серверы» / «ИИ - ключи». Активная вкладка подсвечивается ([08-design-system.md](../../08-design-system.md#навигация-верхние-вкладки-applayout)).
- Маршрут `/mail` — защищённый (внутри `AppLayout` под auth-guard). Роутинг — `App.tsx`, [02-tech-stack.md](../../02-tech-stack.md#frontend).

### Страница `MailPage`

- **Лента писем:** список элементов `MailListItem` (или `MailCard`) в порядке `id` DESC в пределах загруженных батчей. Каждый элемент: `from_name`/`from_addr`, `subject` (или «(без темы)» при `null`), `internal_date` (относительное/абсолютное время), теги (`Badge` с `color`), почтовый аккаунт-получатель (`mail_account.email`/`display_name`).
- **Просмотр тела:** по клику — раскрытие/панель с телом. `body_html` → **sandbox-iframe** (`srcDoc` + `sandbox`, без `allow-scripts`/`allow-same-origin`, [изоляция HTML](#изоляция-html-тела-нормативно)); иначе `body_text` (моношрифт, перенос строк). Пометки `body_truncated`/`body_present` — по [изоляции HTML](#изоляция-html-тела-нормативно).
- **Reply:** форма ответа (примитив **Textarea** для `body`; опционально поля `to`/`cc`/`subject` с префилом из письма). Отправка → `POST /api/mail/messages/{id}/reply`. Успех → toast «Ответ отправлен». Ошибки — по кодам ниже.
- **Пагинация:** кнопка **«Загрузить ещё»** видна, пока `has_more=true`; шлёт `since_id = next_since_id`; новые батчи мержатся, порядок отображения — новые сверху ([пагинация](#пагинация-нормативно)).
- **Данные и polling:** feature-слой `features/mail` (`api.ts`, `hooks.ts`) на TanStack Query, по образцу `features/servers`/`features/ai-keys`; интервал обновления — `env.pollIntervalMs` (не агрессивно, учитывать внешний rate-limit). Типы — в `types/api.ts`.

### Новый UI-примитив `Textarea`

- В `components/ui` примитива Textarea **нет** — добавить (стилизованный Tailwind `<textarea>`, тёмная поверхность `--surface-2`/`--surface-3`, граница `--border-subtle`, скругление 8–10px, видимый focus-ring `--accent`, согласован с `Input`). **Без новой зависимости** (нативный `<textarea>`). Props: `value`, `onChange`, `rows`, `placeholder`, `disabled`, `id`/`name`. Детали — [08-design-system.md](../../08-design-system.md#компонент-textarea).

### Состояния UI

- **loading** (skeleton списка), **empty** (нет писем — подсказка), **error** (`502 mail_unavailable` → «Почтовый сервис временно недоступен» + повтор), **не настроено** (`503 mail_not_configured` → «Сервис почт не настроен», без ленты/reply), **reply отправляется** (loading на кнопке), **reply ошибка** (`404` → «Письмо не найдено»; `422`/`400` → подсветка/сообщение; общая → toast). Строки — словарь [08-design-system.md](../../08-design-system.md#локализация-страницы-почты).

## DoD

- [ ] Backend: `GET /api/mail/messages` и `POST /api/mail/messages/{id}/reply` под JWT; схемы и коды строго по [04-api.md#mail](../../04-api.md#mail); `MailMessage` содержит все поля внешнего DTO.
- [ ] Read-through без хранения: нет таблиц/моделей/миграций под почту.
- [ ] `MAIL_API_KEY` только в заголовке `X-API-Key` исходящего запроса; отсутствует в ответах CRM/логах/SPA/URL; `mail_enabled` гейтит оба эндпоинта (`503 mail_not_configured`).
- [ ] Маппинг ошибок внешнего сервиса → коды CRM (`502 mail_unavailable` / `404 mail_message_not_found` / `422` / `400`); фабрики добавлены в `app/errors.py`.
- [ ] Границы `limit` 1..200 (default 50); пагинация keyset вперёд по `since_id`, `next_since_id`/`has_more` пробрасываются.
- [ ] Frontend: вкладка «Почты» (`/mail`), `MailPage` с лентой, reply (примитив `Textarea`), «Загрузить ещё», все состояния UI, русские строки из словаря.
- [ ] `body_html` рендерится **только** в sandbox-iframe (без `allow-scripts`/`allow-same-origin`); `body_text`/`body_truncated`/`body_present` обрабатываются; DOMPurify не добавлен.
- [ ] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-03: уточнения по ревью реализации (architect, docs↔код): (1) `next_since_id` зафиксирован как `integer | null` (`null` для пустого батча) — синхронизация с [04-api.md#mail](../../04-api.md#mail) и `int | None` в `schemas/mail.py`; (2) идемпотентность ретраев `mail_client.py` — `reply` (POST) ретраит только `ConnectError`/`ConnectTimeout`, не read-timeout/`5xx` (защита от двойной отправки); `list_messages` (GET) ретраит все транзиентные; (3) лейбл ленты «Получено на:» vs «Кому» (только reply `to`) — [08-design-system.md](../../08-design-system.md#локализация-страницы-почты).
- 2026-07-03: спецификация создана (architect). Read-through-прокси без хранения, ключ почты только на backend, HTML-изоляция sandbox-iframe — [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); ограничение (нет server-side поиска/фильтров/newest-first) — [TD-024](../../100-known-tech-debt.md).
