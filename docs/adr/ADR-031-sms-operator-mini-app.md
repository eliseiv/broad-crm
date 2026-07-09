# ADR-031 · Операторская Telegram Mini App модуля «СМС»

Статус: `accepted` · Дата: 2026-07-09 · Амендмент/связки: [ADR-030](ADR-030-sms-module-full-merge.md) (модуль «СМС», Mini App-привязка), [ADR-002](ADR-002-dvuhshagovyy-auth.md) (JWT-вход), [ADR-021](ADR-021-rbac-users-roles.md) (RBAC), [ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md) (вход по telegram-нику, беспарольные пользователи), [ADR-028](ADR-028-user-status-first-login.md) (`first_login_at`)

> **Ревизия 2026-07-09 (решение пользователя).** Первичная редакция этого ADR предлагала JWT-онбординг (оператор входит кредами CRM внутри Mini App). По решению пользователя модель изменена на **беспарольный Telegram-SSO**: оператор определяется по Telegram-идентичности (сопоставление с `users.telegram`), без пароля. Ниже — действующая (беспарольная) редакция; JWT-вариант перенесён в «Альтернативы».

> **UI-амендмент [ADR-037](ADR-037-mini-app-tabs.md) (2026-07-09):** после SSO Mini App показывает **две вкладки — «Сообщения» (дефолт) / «Номера»** (ранее — только номера), и **убран статус-блок привязки** («Telegram привязан — …» + бейдж «Привязан»). Контракт не меняется (эндпоинты `messages`/`numbers` уже есть); линк технически сохраняется.

## Контекст

[ADR-030](ADR-030-sms-module-full-merge.md) перенёс SMS-агрегатор в CRM: приём Twilio → fan-out операторам в Telegram по команде через живые `sms_telegram_links`. Доставка **работает только для операторов с привязкой** `sms_telegram_links(telegram_user_id → user_id)`. **Блокер:** в CRM-SPA **нет** страницы Mini App, которую оператор открывает внутри Telegram (по кнопке бота), чтобы привязаться и видеть свои номера/сообщения. `SMS_TELEGRAM_WEBAPP_URL=https://broadappsdev.shop/sms` — плейсхолдер на **админскую** страницу `/sms` (page-guard `sms:view`, redirect, админский nav-shell), для Mini App неверный.

**Фундамент в коде (проектируем под него):**
- Модель `users` несёт **`telegram: str | None`** — нормализованный ник (без `@`, lower-case; частичный уникальный индекс `uq_users_telegram WHERE telegram IS NOT NULL`, миграция 0011; нормализация — `app/domain/telegram.py::normalize_telegram`). `password_hash` — **nullable** (беспарольные поддержаны).
- Вход по telegram-нику уже есть ([ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md)): идентификатор входа = логин ИЛИ `telegram`; тот же `telegram`-field — механизм сопоставления.
- `verify_init_data` (`app/domain/sms.py`) уже извлекает из initData `telegram_user_id` **и** `username` (`ValidatedInitData.username`).
- `issue_access_token(sub, role, superadmin, uid)` (`app/infra/jwt.py`) — выпуск CRM access-JWT; `UserRepository.get_by_telegram(normalized)` — поиск по нормализованному нику.

Ключевая развилка — **модель онбординга/аутентификации оператора**. Донор был беспарольным (Telegram = identity, Redis-pending). Пользователь подтвердил **беспарольную** модель и для CRM: определяем оператора по Telegram-идентичности и **выдаём CRM-JWT** без пароля.

## Решение

### 1. Выделенный публичный SPA-маршрут `/tg/sms` (Mini App), вне админского shell
Операторская Mini App — **отдельный публичный маршрут SPA `/tg/sms`**, вне `AppLayout` и вне page-guard'ов RBAC:
- **Без** redirect на `/login`, **без** заглушек «Недостаточно прав», **без** админского nav-shell (категории/топбар).
- Отдаётся тем же `index.html` (`try_files $uri /index.html`); React Router резолвит `/tg/sms` в компонент `SmsMiniAppPage` **вне** ветки `AppLayout` (сосед публичного `/login`). Пункта в меню нет (вход — только по кнопке Telegram-бота); в `DefaultRoute` не участвует.
- `SMS_TELEGRAM_WEBAPP_URL` меняется c плейсхолдера `…/sms` на **`https://broadappsdev.shop/tg/sms`** ([07-deployment.md](../07-deployment.md#переменные-окружения)). Namespace `/tg/*` зарезервирован под Telegram-mini-app-входы.

### 2. Беспарольный Telegram-SSO (ревизия `POST /api/sms/telegram/auth`) — initData как аутентификатор, выдача CRM-JWT
Эндпоинт `POST /api/sms/telegram/auth` **пересматривается** из «статус-bootstrap» в **беспарольный SSO** (публичный, HMAC-гейт): валидирует initData → резолвит CRM-оператора по Telegram-идентичности → **выдаёт CRM access-JWT** (как `/api/auth/login`) → авто-привязывает линк. Полный контракт — [04-api.md#post-apismstelegramauth](../04-api.md#post-apismstelegramauth).

**Алгоритм резолва (нормативно):**
1. Валидировать `init_data` (HMAC-SHA256 `WebAppData` из `SMS_TELEGRAM_BOT_TOKEN` + TTL `auth_date`). Извлечь `telegram_user_id` и `username` (может быть `None`).
2. **Первично — по иммутабельному `telegram_user_id`:** найти линк `sms_telegram_links WHERE telegram_user_id = X` (независимо от `dead_at`). Есть → `user_id` линка; если `dead_at IS NOT NULL` — **revive** (`dead_at = NULL`).
3. **Bootstrap — по username (только если линка нет):** `username` присутствует → `normalize_telegram(username)` → `users WHERE telegram = norm AND is_active`. Найден → **upsert** линк (`telegram_user_id → user_id`, `dead_at = NULL`).
4. Резолв: `user_id` получен и пользователь `is_active` → идемпотентно `first_login_at = now()` если `NULL` ([ADR-028](ADR-028-user-status-first-login.md)) → `issue_access_token(sub=user.username, role=user.role.name, superadmin=False, uid=str(user.id))` → вернуть JWT + `linked:true`. **`sub` — ВСЕГДА `users.username` резолвнутого CRM-пользователя** (не Telegram-`username` из `init_data`): в id-first-пути `username` из initData может быть `None`, а логин-флоу ([05-security.md](../05-security.md#аутентификация-логин-и-выпуск-jwt)) кладёт в `sub` именно CRM-логин — единый непустой источник, стабильный к смене Telegram-ника.
5. Иначе (нет линка И нет активного username-совпадения, либо в initData нет `username`, либо пользователь неактивен) → **`403 sms_operator_not_provisioned`**.

`init_data` (подпись/PII) **и извлечённый `username`** (PII) не логируются ([05-security.md](../05-security.md#mini-app-initdata-post-apismstelegramauth-sso--link)).

**Приоритет сопоставления (обоснование).** Первичен **`telegram_user_id` через линк** — он иммутабелен и переживает смену Telegram-ника: после первого успешного сопоставления резолв идёт по id, устаревший `users.telegram` не мешает. **Username** — только **bootstrap-ключ первого контакта** (когда линка ещё нет). Если оператор сменил ник **после** привязки — вход продолжает работать (линк по id). Если ник сменился **до** первой привязки и в `users.telegram` записан старый — совпадения нет до обновления админом (осознанно).

### 3. Просмотр номеров/сообщений — существующие JWT-эндпоинты под `sms:view`
После SSO Mini App держит CRM-JWT и показывает свои номера/сообщения через **существующие** `GET /api/sms/numbers` и `GET /api/sms/messages` — уже суженные SMS-scope до команд оператора ([ADR-030](ADR-030-sms-module-full-merge.md) §6). **Отдельный initData-scoped read-эндпоинт НЕ вводится** (JWT покрывает). **Требование к провижинингу:** роль оператора **обязана включать `sms:view`**, иначе лента/номера пусты (сервер вернёт `403`/пустой scope). Это фиксируется как требование к заведению операторов ([05-security.md](../05-security.md#операторская-mini-app-tgsms-adr-031), [modules/sms](../modules/sms/README.md#операторская-telegram-mini-app-нормативно)).

### 4. Self-hosted Telegram WebApp SDK (CSP `script-src 'self'` не ослабляется)
Официальный `telegram-web-app.js` с `telegram.org` блокируется CSP `script-src 'self'`. Решение — **вендорить SDK как статику своего origin** (`/telegram-web-app.js`, `frontend/public/`); Mini App подключает его как `script-src 'self'`. **CSP не меняется**; обновление SDK — ручной bump вендоренного файла (deploy-шаг).

### 5. Поверхность — только нативные Telegram-webview (Telegram Web не поддержан)
CSP `frame-ancestors 'none'` + `X-Frame-Options: DENY` глобальны. Нативные клиенты (iOS/Android/Desktop) открывают Mini App в webview верхнего уровня — `frame-ancestors` к ним не применяется, работает. **Браузерный Telegram Web** (`web.telegram.org`, iframe) блокируется — **подтверждённое пользователем ограничение**: глобальная CSP **не ослабляется** ([Q-SMS-1](../99-open-questions.md), resolved native-only).

### 6. `POST /api/sms/telegram/link` — вне критического пути Mini App
Привязка в беспарольной модели происходит **автоматически** внутри SSO (шаги 2–3). Эндпоинт `POST /api/sms/telegram/link` (JWT, self-link) остаётся в контракте как валидный путь для аутентифицированного CRM-пользователя (напр. из админ-SPA), но **Mini App его не использует** ([ADR-030](ADR-030-sms-module-full-merge.md) §7 сохраняется; для Mini App он суперседится SSO-авто-линком).

## Последствия

**Плюсы:**
- Беспарольный вход оператора: открыл Mini App → сразу привязан и видит свои SMS (нативный UX донора, без пароля).
- Единый identity: оператор = CRM-пользователь (`users`) под JWT+RBAC; нет второго реестра идентичностей и Redis-pending.
- Просмотр — на существующих `GET /api/sms/numbers`/`messages` (SMS-scope); отдельного read-эндпоинта не нужно.
- CSP не ослабляется: SDK self-hosted, Mini App в нативном webview.

**Минусы / риски:**
- **Риск подмены telegram-ника (username reuse/takeover).** Сопоставление по `username` доверяет тому, что заявитель реально владеет ником на момент auth (initData подписан Telegram) — это верно **в моменте**, но Telegram-ники **рециклятся**: если `users.telegram` оператора освобождён и захвачен злоумышленником **до** первой привязки, тот получит SSO как оператор. Окно риска — **только до первого линка** (после — резолв по иммутабельному `telegram_user_id`). Митигации: провижинить операторов и требовать первого входа своевременно; будущее усиление — запись `telegram_user_id` админом напрямую (без username-bootstrap). Остаётся как [Q-SMS-3](../99-open-questions.md).
- **Роль оператора обязана включать `sms:view`** — иначе Mini App покажет пустой просмотр (лента/номера под scope). Требование к провижинингу ролей.
- **Оператор обязан иметь CRM-аккаунт с заполненным `telegram`** (нормализованный ник совпадает с ником Telegram) — заводит админ. Не сопоставленный Telegram → `403 sms_operator_not_provisioned` (Mini App показывает «обратитесь к администратору»). Политика авто-провижининга (создавать ли оператора автоматически) — [Q-SMS-3](../99-open-questions.md), дефолт: **нет** (ручное заведение админом).
- **Telegram Web (браузер) не поддержан** (native-only, [Q-SMS-1](../99-open-questions.md)).
- Оператор без Telegram-username (аккаунт без ника) не сопоставим по username-bootstrap — необходим предварительно созданный линк или задание ника. Практически: операторы должны иметь публичный @username.

## Альтернативы
- **JWT-онбординг (вход кредами CRM в Mini App)** — первичная редакция этого ADR; **отклонён** решением пользователя в пользу беспарольного SSO (нативный UX, без пароля у оператора).
- **Passwordless-SSO с pending-cookie (донор, Redis)** — отклонён: Redis упразднён ([ADR-030](ADR-030-sms-module-full-merge.md) §3); в CRM SSO реализуется stateless (initData → JWT), без промежуточного pending-токена.
- **Отдельный initData-scoped read-эндпоинт «мои номера/сообщения»** — отклонён: JWT после SSO покрывает существующими эндпоинтами под `sms:view`.
- **Сопоставление только по `telegram_user_id` (без username-bootstrap)** — отклонён на текущем шаге: требует, чтобы админ вводил числовой `telegram_user_id` при заведении (недоступен до первого контакта); username-bootstrap проще для провижининга. Усиление по `telegram_user_id` — направление [Q-SMS-3](../99-open-questions.md).
- **Ослабление CSP под внешний Telegram SDK / Telegram Web** — отклонён: self-hosting SDK + native-only сохраняют строгую CSP.
