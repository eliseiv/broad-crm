# Модуль `mail` — Почты (read-through-прокси к внешнему сервису)

Статус: `spec-ready` · Исполнитель: backend, frontend

## Scope

Страница **«Почты»** в CRM: просмотр писем, приходящих во внешний почтовый сервис `postapp.store`, и **ответ (reply)** на письмо. CRM работает как **read-through-прокси без хранения** (без БД, без миграций): backend синхронно проксирует запросы во внешний external-API, подставляя системный ключ `MAIL_API_KEY`, и возвращает данные фронту. Решение — [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); контракт CRM — [04-api.md#mail](../../04-api.md#mail).

Функции Этапа 1:
- Лента писем: **master-detail** (список слева ~30% / тело выбранного справа ~70%), с адресами, темой, датой, тегами, телом (text/HTML), почтовым аккаунтом-получателем. По умолчанию выбрано самое свежее письмо.
- **Бесконечная лента newest-first** (`order=desc`): новейшие сверху, догрузка более старых при скролле по `before_id` (без кнопки «Загрузить ещё»). Backward-контракт внешнего API — mail-агрегатор ADR-0036; решение CRM — [ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md).
- **Inline-ответ** (chat-like): `Textarea` + кнопка «Ответить» под телом письма (без модалки), через прокси к внешнему reply-эндпоинту.
- Теги письма (`tags[]`) — цветными пилюлями по `tag.color`.

## Out of scope (Этап 1)

- **Хранение писем в БД CRM** (синхронизация/индексация) — отклонено в [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); server-side **поиск/фильтры** недоступны как следствие read-through + внешнего API без поиска ([TD-024](../../100-known-tech-debt.md)). Строгий глобальный «новые сверху» теперь **доступен** через backward-пагинацию (`order=desc`) — [ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md).
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

- `GET /api/mail/messages?order=&since_id=&before_id=&limit=` → `MailListResponse{messages:[MailMessage], next_since_id, next_before_id, has_more}`. Прокси к внешнему `GET /api/external/messages`. `order` ∈ {`asc`,`desc`} (default `desc`); `limit` 1..200 (default 50, страница шлёт 20); `since_id` — только при `asc`, `before_id` (`ge=1`) — только при `desc`. Взаимоисключение режимов → `400 validation_error`. Точные поля/маппинг курсоров — [04-api.md#mail](../../04-api.md#get-apimailmessages).
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

Внешний API (mail-агрегатор ADR-0036) поддерживает два режима; CRM-прокси проксирует оба, **страница «Почты» использует `desc`** (newest-first). Решение — [ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md), контракт — [04-api.md#mail](../../04-api.md#get-apimailmessages).

- **desc (основной, newest-first):** `order=desc`. Без `before_id` → последние `limit` писем по `id DESC` (самые свежие). С `before_id` → письма `id < before_id` по `id DESC`. Ответ: `next_before_id` = `min(id)` батча (`int | null`: `null`, если старее нет или батч пуст), `has_more`. `next_since_id` в desc-режиме = `null`.
  - Первый запрос страницы — `order=desc&limit=20` (без `before_id`) → новейшие 20.
  - **Догрузка старых (бесконечная лента):** `order=desc&before_id=<next_before_id>&limit=20`, пока `has_more=true`. Триггер — `IntersectionObserver` на sentinel в конце списка (без кнопки «Загрузить ещё»). Дедуп по `id`.
  - Даёт **строгий глобальный порядок «новые сверху»** по всей ленте.
- **asc (совместимость):** `order=asc` (+опц. `since_id`) — keyset вперёд по `id ASC` от `since_id`; ответ `next_since_id` = `max(id)` батча (`int | null`, `null` для пустого батча), `next_before_id` = `null`. Прежнее поведение; страницей не используется.
- **Взаимоисключение:** `before_id` при `order=asc` или `since_id` при `order=desc` → `400 validation_error` (CRM валидирует локально; внешний `400` также маппится в `400`).
- **Опциональный фоновый poll новых (v1, отложен — [TD-025](../../100-known-tech-debt.md)):** периодически (`env.pollIntervalMs`, неагрессивно — внешний rate-limit 120 req/min) `order=desc&limit=20` без `before_id`; письма с `id > текущего max` — prepend вверх с дедупом по `id`, порядок newest-first не нарушается. На 2026-07-04 **не реализован** (свежие письма подтягиваются перезагрузкой ленты); может быть включён как v1-опция.
- **Ограничение (остаток):** server-side **поиск/фильтры** недоступны (внешний API их не предоставляет) — [TD-024](../../100-known-tech-debt.md). Ретенция внешнего сервиса ~30 дней — письма старше выпадают из ленты (у CRM хранилища нет). Newest-first-часть TD-024 снята backward-пагинацией.

## Поведение при `mail_enabled=false`

- Почта активна, только если задан `MAIL_API_KEY` (`settings.mail_enabled = bool(MAIL_API_KEY)`).
- При `mail_enabled=false` **оба** эндпоинта отвечают `503 mail_not_configured` (единый код). Фронт при `503 mail_not_configured` показывает состояние **«Сервис почт не настроен»** (без ленты/полей reply, без ошибочного toast-спама).

## Backend — ТЗ

Слои и стек — как у существующих модулей ([modules/ai-keys](../ai-keys/README.md), [modules/servers](../servers/README.md)), но **без репозитория/модели/миграции** (хранилища нет). Образцы исходящего HTTP-клиента с секретом — `app/infra/ai_provider.py`, `app/infra/prometheus.py`; конфиг — `app/config.py`; ошибки — `app/errors.py`; зависимость `CurrentUser` (JWT) — как в существующих роутерах; регистрация роутера — `app/api/router.py`.

1. **Настройки** (`config.py`): `mail_api_base: str = "https://postapp.store"`, `mail_api_key: str = ""` (секрет), `mail_api_timeout_sec: int = 10`. Свойство `mail_enabled: bool` = `bool(mail_api_key)`.
2. **Клиент** (`infra/mail_client.py`): `httpx` с таймаутом `mail_api_timeout_sec`, заголовок `X-API-Key`, ограниченные ретраи на транзиентные ошибки с **разной идемпотентностью для GET и POST** (см. [Архитектура — идемпотентность ретраев](#архитектура-read-through-прокси)): `list_messages` ретраит все транзиентные (connect/read-timeout/`5xx`); `reply` ретраит **только** ошибки соединения (`ConnectError`/`ConnectTimeout`), **не** read-timeout/`5xx` — во избежание двойной отправки письма. Методы: `list_messages(order, since_id, before_id, limit)` (передаёт `order` во внешний API **всегда явно**; `since_id` только при `asc`, `before_id` только при `desc`), `reply(message_id, payload)`. Секрет не логируется.
3. **Сервис** (`services/mail_service.py`): гейт `mail_enabled` (иначе `mail_not_configured`), валидация `limit` (1..200), **валидация взаимоисключения режимов** (`before_id` только при `order=desc`, `since_id` только при `order=asc` — иначе `400 validation_error`, до вызова внешнего API) и тела reply, вызов клиента, маппинг статусов внешнего сервиса → коды CRM ([коды ошибок](#коды-ошибок-нормативно); внешний `400` взаимоисключения → `400 validation_error`), нормализация в Pydantic-схемы (`next_since_id`/`next_before_id` — оба `int | None`, заполнен курсор запрошенного режима, второй `null`).
4. **Роутер** (`api/mail.py`, под JWT): `GET /api/mail/messages`, `POST /api/mail/messages/{id}/reply`. Регистрация в `app/api/router.py`.
5. **Схемы** (`schemas/mail.py`): `MailMessage`, `MailAccount`, `MailTag`, `MailListResponse`, `MailReplyRequest`, `MailReplyResponse` — строго по [04-api.md#mail](../../04-api.md#mail).
6. **Ошибки** (`errors.py`): фабрики `mail_unavailable` (`502`), `mail_message_not_found` (`404`), `mail_not_configured` (`503`).

> Внешний reply-контракт (`POST /api/external/messages/{id}/reply`) фиксирует architect mail-агрегатора; поля тела/ответа CRM берёт из [04-api.md#mail](../../04-api.md#mail). При расхождении внешнего контракта — синхронизировать через architect (эскалация docs↔контракт).

## Frontend — ТЗ

Зеркалит feature-слой существующих страниц; UI-детали и словарь — [08-design-system.md](../../08-design-system.md#страница-почты). Наружу фронт **не ходит** — только `/api/mail/*` через `lib/api.apiRequest`.

### Навигация

- Вкладка **«Почты»** (`NavLink`, маршрут `/mail`) — **ПЕРВАЯ** в ряду вкладок `AppLayout` (порядок: **Почты → Серверы → ИИ - ключи**). Активная вкладка подсвечивается ([08-design-system.md](../../08-design-system.md#навигация-верхние-вкладки-applayout)).
- **Дефолтный маршрут после логина — `/mail`** (index-роут `/` → `Navigate to="/mail" replace`; редирект после успешного входа — на `/mail`, ранее `/servers`).
- Маршрут `/mail` — защищённый (внутри `AppLayout` под auth-guard). Роутинг — `App.tsx`, [02-tech-stack.md](../../02-tech-stack.md#frontend).

### Страница `MailPage` (master-detail)

Двухпанельный layout: **список слева (~30%)**, **тело выбранного письма справа (~70%)**. UI-детали, адаптив и словарь — [08-design-system.md](../../08-design-system.md#страница-почты).

- **Full-bleed layout:** страница `/mail` занимает **всю ширину** и примыкает к sticky-хэдеру (без `max-w-[1400px]`/`py-8`); реализация — **условный контейнер `<main>` в `AppLayout` по маршруту `/mail`** (страницы «Серверы»/«ИИ-ключи» не затронуты) или эквивалентный per-route layout. Детали — [08-design-system.md](../../08-design-system.md#full-bleed-layout-нормативно).
- **Фильтр «Только с тегами»:** клиентский тумблер над списком (server-side фильтров нет — [TD-024](../../100-known-tech-debt.md)); фильтрует загруженный набор по непустому `tags[]`; догрузка старых по скроллу и авто-выбор не ломаются; пустое состояние — «Нет писем с тегами среди загруженных». Детали — [08-design-system.md](../../08-design-system.md#фильтр-только-с-тегами-тулбар-списка).
- **Список (`MailListItem`):** порядок `id` DESC глобально (backward-лента, `order=desc`). Каждый элемент: `from_name`/`from_addr`, `subject` (или «(без темы)» при `null`), `internal_date` (относительное время), **теги** (`Badge` по `tags[]`, фон/акцент из `tag.color`), аккаунт-получатель (`mail_account.email`/`display_name`). Активный элемент подсвечен.
- **Выбор:** по умолчанию выбрано и показано **самое свежее** письмо (первое в ленте). Клик по элементу → показ его тела в правой панели.
- **Правая панель (деталь):** шапка (отправитель, тема, дата, теги-пилюли, аккаунт-получатель) + тело. **Аккаунт-получатель** в детали — полный формат **«Получено на: {display_name} <{email}>»** (`mail_account = {id, email, display_name}`; при пустом `display_name` — только `{email}`; оба значения видны полностью, длинный адрес переносится, не `truncate`). **Тело — единый серый фон `--surface-2`** для text и html. `body_html` → **sandbox-iframe** (`srcDoc` + `sandbox`, без `allow-scripts`/`allow-same-origin`, [изоляция HTML](#изоляция-html-тела-нормативно)); серый достигается инъекцией `html,body{background:#161A22;…}` в **обёртку `srcDoc`** (sandbox не ослабляется; best-effort для html с собственным фоном). Иначе `body_text` (моношрифт, `white-space: pre-wrap`) на том же сером фоне. Пометки `body_truncated`/`body_present` — по [изоляции HTML](#изоляция-html-тела-нормативно). Тело скроллится внутри своего контейнера; значимый контент не обрезается. UI-детали — [08-design-system.md](../../08-design-system.md#деталь-письма-правая-панель).
- **Inline-reply (chat-like):** под телом — многострочный **`Textarea`** (`body`, обязателен) + кнопка **«Ответить»** рядом. Форма = **только** `Textarea` + «Ответить»; блок **«Расширенно»** и поля `to`/`cc`/`subject` в UI **удалены** — ответ шлётся телом `{body}`, дефолты (`to`=`from_addr`, `subject`=`Re: <subject>`) подставляет внешний сервис (`to`/`cc`/`subject` опциональны в `MailReplyRequest`). Кнопка «Ответить» — **штатной высоты ДС, выровнена по центру высоты Textarea** (`flex items-center`, `Textarea flex-1`, не растягивается). Отправка → `POST /api/mail/messages/{id}/reply`. Успех → toast «Ответ отправлен» + **очистка поля**. Прежняя модалка `ReplyModal` **удалена/заменена**. UI-детали — [08-design-system.md](../../08-design-system.md#inline-ответ-reply-chat-like). Ошибки — по кодам ниже.
- **Бесконечная лента:** первый запрос `order=desc&limit=20` (новейшие 20). Догрузка более старых при скролле вниз — `order=desc&before_id=<next_before_id>&limit=20` через `IntersectionObserver` на sentinel; **без кнопки «Загрузить ещё»**. Индикатор загрузки внизу списка; остановка при `has_more=false`; дедуп по `id` ([пагинация](#пагинация-нормативно)). Опционально (v1, отложено — [TD-025](../../100-known-tech-debt.md)) — фоновый poll новых (prepend `id > max`); на 2026-07-04 не реализован, свежие письма подтягиваются перезагрузкой ленты, см. [пагинация](#пагинация-нормативно).
- **Пустое состояние:** нет писем → в левой панели подсказка «Писем пока нет», правая панель — заглушка/пусто.
- **Адаптив (узкие вьюпорты):** одноколоночный режим — список на всю ширину; выбор письма → full-width деталь с кнопкой «Назад» к списку. Значимый контент (тело, значения) **не скрывается и не обрезается** (CLAUDE.md); тело скроллится.
- **Данные и polling:** feature-слой `features/mail` (`api.ts`, `hooks.ts`) на TanStack Query (`useInfiniteQuery` для ленты), по образцу `features/servers`/`features/ai-keys`; интервал фонового poll — `env.pollIntervalMs` (не агрессивно, внешний rate-limit 120 req/min). Типы — в `types/api.ts`.

### Новый UI-примитив `Textarea`

- В `components/ui` примитива Textarea **нет** — добавить (стилизованный Tailwind `<textarea>`, тёмная поверхность `--surface-2`/`--surface-3`, граница `--border-subtle`, скругление 8–10px, видимый focus-ring `--accent`, согласован с `Input`). **Без новой зависимости** (нативный `<textarea>`). Props: `value`, `onChange`, `rows`, `placeholder`, `disabled`, `id`/`name`. Детали — [08-design-system.md](../../08-design-system.md#компонент-textarea).

### Состояния UI

- **loading** (skeleton списка), **empty** (нет писем — подсказка, правая панель пуста), **error** (`502 mail_unavailable` → «Почтовый сервис временно недоступен» + повтор), **не настроено** (`503 mail_not_configured` → «Сервис почт не настроен», без ленты/reply), **догрузка** (индикатор внизу списка при подгрузке старых), **reply отправляется** (loading на кнопке «Ответить»), **reply ошибка** (`404` → «Письмо не найдено»; `422`/`400` → подсветка/сообщение; общая → toast). Строки — словарь [08-design-system.md](../../08-design-system.md#локализация-страницы-почты).

## DoD

- [ ] Backend: `GET /api/mail/messages` и `POST /api/mail/messages/{id}/reply` под JWT; схемы и коды строго по [04-api.md#mail](../../04-api.md#mail); `MailMessage` содержит все поля внешнего DTO.
- [ ] Read-through без хранения: нет таблиц/моделей/миграций под почту.
- [ ] `MAIL_API_KEY` только в заголовке `X-API-Key` исходящего запроса; отсутствует в ответах CRM/логах/SPA/URL; `mail_enabled` гейтит оба эндпоинта (`503 mail_not_configured`).
- [ ] Маппинг ошибок внешнего сервиса → коды CRM (`502 mail_unavailable` / `404 mail_message_not_found` / `422` / `400`); фабрики добавлены в `app/errors.py`.
- [ ] Границы `limit` 1..200 (default 50); режимы `order=desc`/`asc`, курсоры `before_id`/`next_before_id` (desc) и `since_id`/`next_since_id` (asc) пробрасываются; взаимоисключение режимов → `400`.
- [x] Frontend: вкладка «Почты» **первая** (`/mail`), дефолтный маршрут после логина → `/mail`; `MailPage` master-detail (список ~30% / тело ~70%), авто-выбор самого свежего письма, inline-reply (`Textarea` + «Ответить», без `ReplyModal`), бесконечная лента (`IntersectionObserver`, без «Загрузить ещё»), теги-пилюли, адаптив (стек на узких), все состояния UI, русские строки из словаря. — выполнено 2026-07-04 ([ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md)).
- [x] `body_html` рендерится **только** в sandbox-iframe (без `allow-scripts`/`allow-same-origin`); `body_text`/`body_truncated`/`body_present` обрабатываются; DOMPurify не добавлен. — выполнено 2026-07-04 (`MailDetail.tsx`).
- [ ] Frontend UX-доводка (2026-07-04, [ADR-013 поправка](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md#поправка-2026-07-04--ux-доводка-страницы)): `/mail` **full-bleed** (условный `<main>` в `AppLayout`, др. страницы не затронуты); **единый серый фон тела** `--surface-2` (инъекция фона в srcDoc-обёртку, sandbox не ослаблен); клиентский **фильтр «Только с тегами»**; кнопка **«Ответить»** центрирована по высоте `Textarea` (штатная высота ДС); форма reply **без «Расширенно»**/`to`/`cc`/`subject`; **«Получено на:»** = `{display_name} <{email}>` (при пустом `display_name` — только `{email}`, без обрезки). Инварианты [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md) сохранены.
- [ ] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-04: **UX-доводка страницы «Почты»** (architect, [ADR-013 поправка](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md#поправка-2026-07-04--ux-доводка-страницы); docs, **без изменения контракта API**): (1) `/mail` **full-bleed** — условный контейнер `<main>` в `AppLayout` по маршруту, «Серверы»/«ИИ-ключи» не затронуты; (2) **единый серый фон тела** `--surface-2` для text и html (инъекция `html,body{background:#161A22…}` в srcDoc-обёртку sandbox-iframe, best-effort для html с собственным фоном; sandbox [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md) не ослаблен); (3) клиентский **фильтр «Только с тегами»** (тумблер над списком; server-side фильтров нет — [TD-024](../../100-known-tech-debt.md)); (4) кнопка **«Ответить»** — штатная высота ДС, центрирована по высоте `Textarea`; (5) удалён блок **«Расширенно»** и поля `to`/`cc`/`subject` из формы reply — ответ шлётся телом `{body}`, дефолты подставляет внешний сервис (`to`/`cc`/`subject` опциональны в `MailReplyRequest`); (6) **«Получено на:»** в детали — `{display_name} <{email}>` (при пустом `display_name` — только `{email}`, без обрезки). Контракт `MailReplyRequest`/[04-api.md#mail](../../04-api.md#mail) не менялся.
- 2026-07-04: **frontend-часть завершена** ([ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md); frontend + reviewer approve, qa 94/94 зелёные, CI-гейты pass, production_ready). Реализовано: вкладка «Почты» первая + дефолтный `/mail` (`AppLayout.tsx`, `App.tsx`, `LoginPage.tsx`); `MailPage` master-detail ~32/68 с авто-выбором самого свежего; inline-reply (`MailReplyForm`) вместо удалённой модалки `ReplyModal`; удалён `MailMessageCard`; бесконечная лента `order=desc` (`useInfiniteQuery` + `IntersectionObserver`, без «Загрузить ещё», дедуп по `id`); теги-пилюли (`MailTags`); примитив `Textarea`; типы `types/api.ts` (`MailOrder`, `next_before_id`). Фоновый poll новых — **отложенная v1-опция** ([ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md), [TD-025](../../100-known-tech-debt.md)): свежие письма подтягиваются перезагрузкой ленты (reload), не автоматическим prepend. DoD-пункты frontend и sandbox-iframe закрыты; backend-часть — вне этой поставки. Расхождений docs↔реализация не выявлено.
- 2026-07-04: расширение под backward-пагинацию и переработку UX (architect, [ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md), внешний контракт mail-агрегатор ADR-0036): (A) `GET /api/mail/messages` — параметры `order` (default `desc`)/`before_id`, поле ответа `next_before_id` (`int|null`), взаимоисключение режимов → `400`; клиент `list_messages(order, since_id, before_id, limit)`; (B) UX-переработка: вкладка «Почты» первая + дефолтный `/mail`; master-detail (список ~30% / тело ~70%, авто-выбор свежего); inline-reply вместо `ReplyModal`; бесконечная лента newest-first (`IntersectionObserver`, без «Загрузить ещё»); теги-пилюли. Инварианты [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md) сохранены. Newest-first-часть [TD-024](../../100-known-tech-debt.md) снята.
- 2026-07-03: уточнения по ревью реализации (architect, docs↔код): (1) `next_since_id` зафиксирован как `integer | null` (`null` для пустого батча) — синхронизация с [04-api.md#mail](../../04-api.md#mail) и `int | None` в `schemas/mail.py`; (2) идемпотентность ретраев `mail_client.py` — `reply` (POST) ретраит только `ConnectError`/`ConnectTimeout`, не read-timeout/`5xx` (защита от двойной отправки); `list_messages` (GET) ретраит все транзиентные; (3) лейбл ленты «Получено на:» vs «Кому» (только reply `to`) — [08-design-system.md](../../08-design-system.md#локализация-страницы-почты).
- 2026-07-03: спецификация создана (architect). Read-through-прокси без хранения, ключ почты только на backend, HTML-изоляция sandbox-iframe — [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md); ограничение (нет server-side поиска/фильтров/newest-first) — [TD-024](../../100-known-tech-debt.md).
