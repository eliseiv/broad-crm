# 05 · Безопасность

## Аутентификация (логин и выпуск JWT)

С Спринта 3 система **многопользовательская** с ролями и RBAC ([ADR-021](adr/ADR-021-rbac-users-roles.md)); `.env`-учётка становится несменяемым **супер-админом (bootstrap)** ([ADR-008](adr/ADR-008-admin-iz-env.md) с амендментом). Порядок проверки при `POST /api/auth/login`:

1. **Сначала супер-админ (`.env`).** Логин/пароль сравниваются constant-time с `ADMIN_USER`/`ADMIN_PASSWORD`. В БД супер-админ НЕ хранится; **всегда парольный** (беспарольным не бывает). Успех → JWT: `sub=ADMIN_USER`, `role="admin"`, `superadmin=true` (без `uid`).
2. **Иначе БД-пользователь.** Идентификатор входа (`username` в запросе) сопоставляется с `users.username` **точно**, иначе с нормализованным `users.telegram` — **вход по Логину ИЛИ Телеграму** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). При `is_active=true`:
   - `password_hash IS NOT NULL` (парольный) → `verify_password` (bcrypt). Успех → JWT: `sub=username`, `uid=users.id`, `role=role.name`, `superadmin=false`. При успехе сервер идемпотентно проставляет `users.first_login_at = now()`, если `NULL` (метка **первого входа** для тристатуса — [ADR-028](adr/ADR-028-user-status-first-login.md)).
   - `password_hash IS NULL` (**беспарольный**) → вход не выполняется; возвращается `password_setup_required: true` + limited-scope setup-token (см. [«Модель открытого первого входа»](#модель-открытого-первого-входа-нормативно)).
3. Неудача парольной ветки (не найден / `is_active=false` / неверный пароль) → единое `401 invalid_credentials`.

- Двухшаговый UI-вход; backend проверяет креды единым запросом `POST /api/auth/login` ([ADR-002](adr/ADR-002-dvuhshagovyy-auth.md)).
- Сравнение кредов **супер-админа** — **constant-time** (`secrets.compare_digest`) для логина и пароля, чтобы исключить timing-атаки. Пароли **БД-пользователей** проверяются bcrypt (`verify_password`, [«Хэширование паролей»](#хэширование-паролей-bcrypt)).
- Сообщение об ошибке входа одинаково для неверного логина и неверного пароля и для несуществующего/деактивированного пользователя (`invalid_credentials`) — не раскрывает существование пользователя.
- Защита от перебора: rate-limit на `/api/auth/login` (по IP, по умолчанию 10 попыток / 5 мин, далее `429`). Реализация — in-memory счётчик на Этапе 1 (один воркер), вынос в Redis — будущий этап ([TD-005](100-known-tech-debt.md)).
- **Определение реального IP клиента за reverse-proxy** (нормативно): backend берёт IP в порядке `X-Real-IP` → первый адрес из `X-Forwarded-For` → `request.client.host`. Поэтому nginx ОБЯЗАН проставлять эти заголовки для `location /api` (см. [07-deployment.md](07-deployment.md#reverse-proxy-nginx-требования)). Без корректного проброса rate-limit считал бы все запросы с одного IP (адрес прокси) и блокировал всех. Доверять `X-Forwarded-For`/`X-Real-IP` допустимо, только когда backend доступен исключительно через доверенный прокси (как в нашей топологии — backend не публикуется наружу).

### Хранение `ADMIN_PASSWORD`
- На Этапе 1 допускается plaintext в `.env` (это секрет окружения, не в репозитории). Рекомендация: bcrypt-хэш `ADMIN_PASSWORD_HASH` как опция — зафиксировано как [Q-SEC-1](99-open-questions.md). По умолчанию — plaintext-сравнение constant-time.

## Модель открытого первого входа (нормативно)

**Осознанное решение пользователя** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)), а не пробел безопасности. Пароль при создании пользователя **опционален** (`users.password_hash` nullable); беспарольный пользователь задаёт пароль **сам** при первом входе.

**Поток:**
1. Админ создаёт пользователя без пароля (`password_hash = NULL`).
2. Пользователь вводит свой Логин/Телеграм (без пароля) → `POST /api/auth/login` возвращает `password_setup_required: true` + **setup-token** (limited-scope JWT, `type:"pwd_setup"`, `uid`, TTL `PWD_SETUP_TOKEN_EXPIRES_MIN`=10 мин, **без** `role`/прав).
3. Пользователь **придумывает** пароль (≥ 8, свой — без генератора/случайного) на экране **«Придумайте пароль»** ([ADR-029](adr/ADR-029-ui-login-password-nav-team-form.md)) → `POST /api/auth/set-password` (Bearer setup-token) → `password_hash` устанавливается (bcrypt), выдаётся обычный access-token (сразу залогинен). Так как это первый вход — сервер идемпотентно проставляет `users.first_login_at = now()`, если `NULL` ([ADR-028](adr/ADR-028-user-status-first-login.md)).
4. После установки вход только по паролю; setup-ветка больше не срабатывает.

**Границы беспарольного принципала (что может / не может):**
- **Не может** ничего, кроме `set-password`: setup-token (`type:"pwd_setup"`) проходит **только** этот эндпоинт. `get_current_principal` **отвергает** любой токен с `type != "access"` → `401` на ресурсных/Users/Roles/Teams-эндпоинтах (setup-token не даёт RBAC-прав). Access-token до установки пароля **не выдаётся** — доступа к данным нет.
- **Может** только задать себе пароль (захватив тем самым учётку — см. риск ниже).

**Осознанный риск (окно уязвимости):** с момента создания беспарольного пользователя и до установки им пароля **любой**, кто знает его Логин/Телеграм, может первым задать пароль и захватить учётку. Принято осознанно ради простого онбординга. Митигация: оперативно сообщать идентификатор адресату, не держать беспарольные учётки долго.

**Взаимодействие с супер-админом и RBAC:** супер-админ (`.env`) всегда парольный — модель его не касается. Роль беспарольного пользователя существует, но прав не даёт, пока не выдан access-token (пока пароль не задан). Деактивированный (`is_active=false`) беспарольный пользователь пароль задать не может (`401`).

**Энумерация:** для **парольных** пользователей вход сохраняет единое `401` (без раскрытия). Для **беспарольных** ответ `password_setup_required` раскрывает существование и беспарольность идентификатора — осознанный побочный эффект модели.

## JWT

| Параметр | Значение |
|----------|----------|
| Алгоритм | `HS256` (симметричный, `JWT_SECRET` из `.env`) |
| TTL | `JWT_EXPIRES_MIN`, по умолчанию **1440 мин (24 часа)** |
| Claims | `sub` (=username), `role`, `superadmin` (bool), `uid` (uuid — только у БД-пользователя), `iat`, `exp`, `type:"access"` ([ADR-021](adr/ADR-021-rbac-users-roles.md#4-auth-поток-см-modulesauth-05-securitymd)) |
| Передача | заголовок `Authorization: Bearer <token>` |

**Claim'ы RBAC (побуквенно, [ADR-021](adr/ADR-021-rbac-users-roles.md)):** `sub` — username; `role` — имя роли (`"admin"` у супер-админа); `superadmin` — `true` у `.env`-супер-админа, `false` у БД-пользователя; `uid` — `users.id` (присутствует **только** у БД-пользователя, отсутствует у супер-админа). Токен без `superadmin=true` и без `uid` (легаси до Спринта 3) → `401` (повторный вход). Права в токен **не кладутся** — грузятся из БД на каждый запрос (см. [«RBAC»](#rbac--роли-права-и-enforcement)).

- **Setup-token первого входа** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)): отдельный тип JWT `type:"pwd_setup"` с `uid`, **без** `role`/`superadmin`/прав, TTL `PWD_SETUP_TOKEN_EXPIRES_MIN` (default 10 мин). Выдаётся `POST /api/auth/login` беспарольному пользователю; принимается **только** `POST /api/auth/set-password`. `get_current_principal` (защищающий все прочие эндпоинты) **отвергает** токены с `type != "access"` → `401` (иначе setup-token дал бы доступ к ресурсам — критичный инвариант).
- Выбор HS256 (а не RS256) — один сервис, симметричный ключ проще; обоснование в [ADR-002](adr/ADR-002-dvuhshagovyy-auth.md). При появлении нескольких сервисов-валидаторов — пересмотр на RS256.
- **TTL access-токена — 1440 мин (24 часа).** По запросу пользователя срок жизни увеличен с 60 мин до 24 ч, чтобы снизить частоту релогина (админ-панель, одна учётка). Осознанный trade-off: более длинное окно валидности украденного токена. Компенсируется строгой CSP/no-referrer (снижают риск XSS-кражи) и отсутствием refresh-токенов (по истечении 24 ч — повторный вход). Значение — env-параметр `JWT_EXPIRES_MIN`, при необходимости ужесточается без изменения кода.
- Refresh-токенов на Этапе 1 нет: по истечении TTL — повторный вход. **Хранение access-токена на фронте — `localStorage`** (ключ `crm.auth.token`, сопутствующие `crm.auth.*`), амендмент [ADR-041](adr/ADR-041-login-theme-session-ux.md) — **изменено с прежнего `sessionStorage`/in-memory**. Обоснование ([ADR-041](adr/ADR-041-login-theme-session-ux.md)):
  - **Персистентная сессия (фикс 12):** `localStorage` переживает закрытие/повторное открытие браузера → в пределах 24 ч (TTL JWT) повторный вход не требуется; автовыход остаётся по истечении JWT.
  - **Сессия шарится между вкладками/окнами того же origin (фикс 16):** открытие ссылки в новой вкладке (ПКМ → «Открыть в новой вкладке») или прямой ввод URL страницы читает токен из `localStorage` → пользователь остаётся авторизован, `ProtectedRoute` не редиректит на `/login`. Требование к фронту: **auth-стор инициализируется из `localStorage` синхронно при загрузке SPA (регидрация) — ДО резолва маршрута/guard**, чтобы `ProtectedRoute` видел восстановленную сессию (см. [modules/auth](modules/auth/README.md), [modules/ui](modules/ui/README.md)).
  - **Security-трейдофф (осознанно принят):** `localStorage` доступен JS всего origin → шире XSS-поверхность кражи токена, чем in-memory/`sessionStorage`. Принято для внутренней админ-панели: строгая CSP (`default-src 'self'`), нет сторонних скриптов, экранирование React, 24-часовой TTL. `401`/logout полностью очищают `crm.auth.*`. Ужесточение (httpOnly-cookie + CSRF, refresh-ротация) — при недоверенном окружении.
- Все эндпоинты, кроме `/api/auth/login` и `/api/health`, требуют валидный JWT → иначе `401 unauthorized`.

## RBAC — роли, права и enforcement

Многопользовательский режим с правами на все страницы — [ADR-021](adr/ADR-021-rbac-users-roles.md); модель — [03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac); API — [04-api.md](04-api.md#rbac-и-enforcement-прав). **RBAC обеспечивается на сервере (`403 forbidden`); UI-гейтинг — только UX.**

### Каталог прав (канон на сервере)

Единственный источник — константа `app/domain/permissions.py::CATALOG`. Страница → допустимые действия:

| Страница | Действия |
|----------|----------|
| `dashboard` | `view` |
| `servers` / `ai-keys` / `proxies` / `backends` | `view`, `create`, `edit`, `delete` |
| `mail` | `view`, `create`, `edit`, `delete`, `sync`, `tags` ([ADR-038](adr/ADR-038-mail-headless-integration.md)) |
| `sms` | `view`, `edit`, `transfer`, `sync`, `delete` ([ADR-030](adr/ADR-030-sms-module-full-merge.md)) |
| `roles` / `teams` | `view`, `create`, `edit`, `delete` ([ADR-022](adr/ADR-022-teams-nav-categories.md)) |

Порядок ключей каталога (= порядок строк матрицы в UI): `dashboard, servers, ai-keys, proxies, backends, mail, sms, roles, teams`.

- **Страница `mail`** ([ADR-038](adr/ADR-038-mail-headless-integration.md), привязка/перенос — [ADR-043](adr/ADR-043-lazy-mail-group-provisioning.md)) — headless-прокси к mail-агрегатору. Действия: `view` (лента/ящики/теги/команды-почты + reply на письмо — reply НЕ расширяется, остаётся под `view` по [ADR-012](adr/ADR-012-mail-read-through-proxy.md)); `create` (`POST /mail/mailboxes`, `POST /mail/mailboxes/test`); `edit` (`PATCH /mail/mailboxes/{id}` — креды/`is_active`); `delete` (`DELETE /mail/mailboxes/{id}`); `sync` (`POST /mail/mailboxes/{id}/sync` — дорогой форс-синк, отделён как у `sms:sync`); `tags` (управление глобальным каталогом тегов — админская функция на все команды сразу, поэтому отделена от per-mailbox `edit`: `POST/PATCH/DELETE /mail/tags`, правила, apply).
  - **Привязка ящика к команде — по `team_id` (UUID CRM), не `group_id`** ([ADR-043](adr/ADR-043-lazy-mail-group-provisioning.md) §2; группа агрегатора создаётся/резолвится сервером лениво). **Авторизация create** (`POST /mail/mailboxes` с `team_id`): не-admin — только для **своей** команды (`team_id ∈ MailScope.team_ids`, членство в `user_teams`), иначе `403 forbidden` (`errors.forbidden()`, `code="forbidden"`).
  - **Перенос ящика между командами (`PATCH /mail/mailboxes/{id}` со сменой `team_id`) — ТОЛЬКО admin-уровень** ([ADR-043](adr/ADR-043-lazy-mail-group-provisioning.md) §5; требование владельца «переносить может только админ»). Гейтится предикатом `MailScope.sees_all_teams` (`is_superadmin OR permissions_subset(full_catalog, permissions)` — тот же admin-level, что «видеть все почты»), **НЕ** членством: участник даже нескольких команд переносить не вправе → `403 forbidden`. **Отдельного действия `mail:transfer` НЕ вводится** (обоснование — [ADR-043](adr/ADR-043-lazy-mail-group-provisioning.md) §5): admin-предикат нельзя выдать не-админу, что и требует владелец; симметрично «view all» ([ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)/[ADR-036](adr/ADR-036-sms-team-filter-admin-only.md)). Право `mail:edit` — необходимое, но недостаточное условие переноса.
  - **Просмотр всех почт — только admin-уровень:** вне scope чтение отдаёт пустую страницу (анти-энумерация, ниже); полный набор всех команд видит лишь `sees_all_teams`. Мутация существующего ящика по `id` (креды/`is_active`/`delete`/`sync`) — ящик ∈ `MailScope.group_ids` (не-admin), иначе `403`. Детали — [04-api.md](04-api.md#mail). Расширение аддитивно: роли с `mail:["view"]` не затронуты.
- **Страница `sms`** ([ADR-030](adr/ADR-030-sms-module-full-merge.md)) не имеет `create` (номера появляются автоматически из входящих SMS/`sync`). Действия: `view` (лента/номера), `edit` (`login`/`app_name`/`note`), `transfer` (команда номера), `sync` (Twilio), `delete` (удаление номера). Привязка Telegram (`POST /api/sms/telegram/link`) — **вне матрицы** `sms` (только аутентификация): доставка операторам — функция членства в команде, а не права на страницу.

- Страница **«Пользователи» (`users`) в каталог не входит** — управление **пользователями** (создание/удаление, сброс паролей, назначение ролей) гейтится `require_admin` (`is_superadmin || role=="admin"`). Управление **ролями** (`/api/roles`) и **командами** (`/api/teams`) со Спринта A — под матрицей `roles:*`/`teams:*` ([ADR-022](adr/ADR-022-teams-nav-categories.md)). Оговорка: **создание/редактирование CRM-команд де-факто admin-only** — форма выбирает лидера/участников из `GET /api/users` (под `require_admin`), поэтому `teams:create`/`teams:edit` даёт полный контроль состава только вместе с admin-доступом; `teams:view` — полноценный просмотр. Осознанное следствие замыкания эскалации ([ADR-022](adr/ADR-022-teams-nav-categories.md#3-гейтинг-api-нормативно)), контракт `teams:*` не меняется.
- Формат прав роли (`roles.permissions`, jsonb): `{ "<page>": ["<action>", ...] }`. Валиден ⇔ каждый ключ — известная страница (кроме `users`; допустимы `roles`/`teams`), каждое действие ∈ `CATALOG[page]`, без дублей → иначе `422 unprocessable`.
- Каталог отдаётся UI через `GET /api/permissions/catalog` — гейт со Спринта A **`require("roles","view")`** (было `require_admin`): каталог нужен редактору роли.

### Enforcement (свежая загрузка прав из БД)

`get_current_principal` декодирует JWT и **на каждый запрос** формирует `Principal(username, role, permissions, is_superadmin, user_id)` (поле `user_id` добавлено [ADR-030](adr/ADR-030-sms-module-full-merge.md) — см. [Расширение Principal](#расширение-principal-полем-user_id-нормативно)):

- `superadmin=true` → полный доступ (`permissions` = полный каталог, все `require(...)` и `require_admin` проходят).
- иначе по `uid` грузятся `users`+`roles`; если пользователь не найден **или** `is_active=false` → `401 unauthorized` (действующий JWT аннулируется **без пере-логина**). Иначе `permissions = roles.permissions`.

Свежая загрузка → **правки прав роли применяются мгновенно** (без отзыва токена; refresh-токенов нет). Стоимость — один SELECT на защищённый запрос БД-пользователя (приемлемо при NFR-1).

- Фабрика `require(page, action)` → `403 forbidden`, если не супер-админ и `action ∉ permissions[page]`. Применена ко **всем** ресурсным эндпоинтам, а также к `/api/roles`, `/api/teams` и `GET /api/permissions/catalog` ([ADR-022](adr/ADR-022-teams-nav-categories.md); маппинг метод→действие — [04-api.md](04-api.md#rbac-и-enforcement-прав)).
- Фабрика `require_admin` → `403 forbidden`, если не (`is_superadmin || role=="admin"`). Со Спринта A гейтит **только Users API** (`/api/roles`, `/api/teams`, каталог переведены на матрицу).
- `forbidden()` — фабрика в `app/errors.py` (403, `code="forbidden"`, message «Недостаточно прав»).

### Security-инвариант эскалации привилегий (нормативно, [ADR-022](adr/ADR-022-teams-nav-categories.md))

Перевод редактирования ролей под матрицу (`roles:create/edit`) несёт риск эскалации: носитель `roles:edit`, не будучи админом, мог бы выдать роли (в т.ч. своей) права сверх собственных. Backend ОБЯЗАН защищать (проверка в handler `/api/roles` после прохождения гейта); сервер (`403`) — **единственная граница**:

- **(а) subset:** для актора, который **не** супер-админ и **не** роль `admin`, при `POST`/`PATCH /api/roles`: `permissions` роли ⊆ `permissions` актора (по каждой `page` набор `actions` — подмножество). Нарушение → `403 forbidden`. Супер-админ и роль `admin` (полный каталог) проходят всегда.
- **(б) защита `admin`:** роль `name == "admin"` меняет/удаляет **только** `is_superadmin || role == "admin"`. Иначе → `403 forbidden`.
- **(в)** назначение ролей пользователям и управление учётками остаётся под `require_admin` (Users API вне матрицы) — замыкает эскалацию: не-админ не может назначить усиленную роль пользователю.

Прецеденция кодов `POST`/`PATCH /api/roles`: каталожная валидация (`422`) → эскалация/защита `admin` (`403`) → уникальность имени (`409`) — [04-api.md](04-api.md#roles).

## Хэширование паролей (bcrypt)

Пароли **БД-пользователей** ([03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac), [ADR-021](adr/ADR-021-rbac-users-roles.md)):

- Библиотека — **`bcrypt`** напрямую (без `passlib`), новая зависимость ([02-tech-stack.md](02-tech-stack.md#backend)). Модуль `app/infra/passwords.py`: `hash_password(plain) -> str`, `verify_password(plain, hashed) -> bool`. Cost — дефолт bcrypt (12 раундов, `bcrypt.gensalt()`).
- В БД хранится **только** `users.password_hash` (**nullable** — `NULL` у беспарольного пользователя до «открытого первого входа», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). **Plaintext-пароль никогда** не хранится, не логируется (structlog-фильтр секретов), не возвращается ни в одном ответе API (в схемах `User*` поля `password` нет на выход; есть лишь производный `has_password`).
- Политика пароля: 8–128 символов (валидация Pydantic) — как при создании (если задан), так и при `set-password` первого входа и сбросе через `PATCH`. **Пароль опционален при создании** (беспарольный пользователь), но при установке подчиняется тем же 8–128. **Известное поведение bcrypt:** значимы только первые 72 **байта** (для кириллицы в UTF-8 — ~36 символов); это документированное ограничение bcrypt, принято осознанно (не дефект).
- Пароль **супер-админа** (`.env`) bcrypt НЕ хэшируется — сравнение plaintext constant-time ([ADR-008](adr/ADR-008-admin-iz-env.md) амендмент); опция `ADMIN_PASSWORD_HASH` ([Q-SEC-1](99-open-questions.md)) для супер-админа не вводится.

## Защита SSH-кредов целевых серверов

- SSH-пароль шифруется **Fernet** (`cryptography`) сразу при `POST /api/servers`; в БД — только `ssh_password_encrypted` (`bytea`).
- Ключ `FERNET_KEY` (base64, 32 байта) — из `.env`, никогда в коде/репозитории/логах/ответах API.
- Расшифровка — только в памяти провижининг-сервиса непосредственно перед запуском Ansible, **либо** в обработчике reveal-эндпоинта (см. ниже); расшифрованное значение не логируется и не покидает процесс.
- Пароль (в любом виде) НЕ возвращается в обычных list/detail-ответах API. **Исключение ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)):** выделенный reveal-эндпоинт `GET /api/servers/{id}/ssh-password` под правом `servers:edit` отдаёт plaintext по требованию — см. [«Reveal секретов по требованию»](#reveal-секретов-по-требованию-adr-035).
- Ротация `FERNET_KEY` — `MultiFernet` (новый + старый ключ) — будущий этап ([TD-006](100-known-tech-debt.md)).

## Защита AI-ключей

Ключи AI-провайдеров (OpenAI/Anthropic) — секреты того же класса, что и SSH-пароли ([modules/ai-keys](modules/ai-keys/README.md#безопасность-ключа-нормативно), [ADR-010](adr/ADR-010-ai-key-monitor-vnutri-backend.md)).

- Полный ключ шифруется **Fernet** тем же `FERNET_KEY` сразу при `POST /api/ai-keys`; в БД — только `key_encrypted bytea` ([03-data-model.md](03-data-model.md#таблица-ai_keys)).
- Расшифровка — только в памяти монитора/проверки непосредственно перед HTTP-запросом к провайдеру (`GET /v1/models`); расшифрованное значение не логируется и не покидает процесс.
- **Полный ключ (в любом виде) НЕ возвращается в обычных list/detail-ответах API.** В них — только маска `key_masked` (первые 4 … последние 4 символа; для ключа короче 8 символов — полная маска `********`). **Исключение ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)):** reveal-эндпоинт `GET /api/ai-keys/{id}/key` под правом `ai-keys:edit` отдаёт полный ключ по требованию — см. [«Reveal секретов по требованию»](#reveal-секретов-по-требованию-adr-035).
- `key_prefix`/`key_last4` (по 4 plaintext-символа) хранятся ради маски и текста Telegram-алерта — осознанное раскрытие 8 символов, секрет из них не восстанавливается.
- Ключ провайдера **не передаётся** в query-строке/URL и не пишется в structlog (фильтр секретов); заголовки `Authorization: Bearer`/`x-api-key` не логируются.

## Защита ключа почты

Ключ внешнего почтового сервиса (`postapp.store`) — системный секрет того же класса, что AI-ключи и `TELEGRAM_*`. Модуль «Почты» — headless-прокси ([ADR-012](adr/ADR-012-mail-read-through-proxy.md), [ADR-038](adr/ADR-038-mail-headless-integration.md), [modules/mail](modules/mail/README.md)).

- `MAIL_API_KEY` — **только из env**, задаётся администратором развёртывания (НЕ через UI). В БД не хранится (у модуля почты хранилища нет). Единственный системный секрет модуля (инвариант ADR-012 неизменен при переходе к write-прокси).
- Ключ подставляется backend'ом **только** в заголовок `X-API-Key` исходящего запроса к `postapp.store`. **Никогда** не возвращается в ответах CRM API, не логируется (structlog-фильтр секретов), не передаётся в SPA и не попадает в query-строку/URL. Внешний write API агрегатора гейтится дополнительным флагом на его стороне (`EXTERNAL_WRITE_ENABLED`, mail-агрегатор ADR-0039).
- **Фронт наружу не ходит** — SPA обращается только к `/api/mail/*` (тот же origin, CSP `connect-src 'self'`); прямой вызов `postapp.store` из браузера исключён.
- HTML-тело письма — недоверенный контент третьих лиц — рендерится **только** в sandbox-iframe (`srcDoc` + `sandbox` без `allow-scripts`/`allow-same-origin`): скрипты письма не исполняются, доступа к origin/куки/JWT CRM нет ([ADR-012](adr/ADR-012-mail-read-through-proxy.md), [modules/mail](modules/mail/README.md#изоляция-html-тела-нормативно)). Согласуется с CSP SPA (`frame-ancestors 'none'`, `script-src 'self'`). Удалённые (remote https) изображения тела письма отрисовываются — `img-src` расширен до `'self' data: https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)); при этом sandbox без `allow-scripts`/`allow-same-origin` и `script-src 'self'` не изменены — грузятся только пассивные `<img>`.

### Транзит IMAP/SMTP-кредов (mail, нормативно)

Модуль «Почты» — headless-прокси write ([ADR-038](adr/ADR-038-mail-headless-integration.md) §5). IMAP/SMTP-пароли ящиков **в CRM не хранятся и не шифруются CRM** (Fernet CRM к почте НЕ применяется — `FERNET_KEY` служит SSH/proxy/AI-паролям, [ADR-007](adr/ADR-007-shifrovanie-fernet.md)):

- Пароли (`password`, опц. `smtp_password`) приходят с фронта в `POST/PATCH /api/mail/mailboxes*` и `POST /api/mail/mailboxes/test`, проходят **транзитом** в агрегатор по HTTPS и шифруются **там** (AES-256-GCM с AAD по id строки — mail-агрегатор ADR-0005). CRM — источник истины команд/прав, агрегатор — источник истины ящиков/кредов.
- Пароль **никогда** не логируется (structlog-фильтр на `password`/`smtp_password`), **не** возвращается в теле ответов CRM (схемы `MailMailbox`/`TeamMailboxItem` полей пароля не содержат), **не** пробрасывается обратно в SPA.
- Эндпоинты записи (`POST/PATCH /api/mail/mailboxes*`, `test`) отвечают заголовком **`Cache-Control: no-store`** (тело запроса несёт транзитные креды; ответ не кэшируется браузером/прокси).
- SSRF-guard хостов IMAP/SMTP выполняет **агрегатор** (`assert_public_host`); CRM креды по сети сам не валидирует — делегирует `POST /api/mail/mailboxes/test`.
- Ретраи write-методов — **только** `ConnectError`/`ConnectTimeout` (запрос заведомо не ушёл), анти-двойная-запись — [04-api.md#mail](04-api.md#mail).

## Расширение `Principal` полем `user_id` (нормативно)

Модуль «СМС» требует видимость сообщений по командам ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §6). Для этого `Principal` расширяется полем **`user_id: uuid.UUID | None`**:

- БД-пользователь → `user_id` из claim `uid` (UUID); стоимость нулевая — `users`-ряд уже загружается в `get_current_principal`.
- **Видимость SMS по роли (нормативно, [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)).** «Видит все команды» ⇔ **`is_superadmin` ИЛИ роль владеет полным каталогом прав**: `sees_all_teams = principal.is_superadmin or permissions_subset(full_catalog_permissions(), principal.permissions)`. Такой актор (консольный супер-админ; seed-роль `admin`; кастомная admin-роль, напр. «Админ», при полном каталоге) видит **все** SMS/номера (scope не сужается). Признак устойчив к переименованию роли (не завязан на редактируемое имя) и не требует нового права/миграции.
- **UI-гейт фильтра «Все команды» ([ADR-036](adr/ADR-036-sms-team-filter-admin-only.md)).** Тот же предикат отдаётся фронту через `GET /api/auth/me` как производное булево **`sees_all_sms_teams`** (backend — единственный источник, фронт не дублирует `permissions_subset`). Фильтр «Все команды» на вкладке «Сообщения» `/sms` рендерится **только** при `sees_all_sms_teams === true`; для прочих ролей не рендерится (для них team-фильтр бесполезен — scope и так сужает до своих команд). Это UX; граница безопасности — серверный SMS-scope (ниже).
- **Прочие роли** (неполный каталог: PM, «Пользователь» и т.п.) → видимость **только по своим командам**: `SmsScope` (фабрика `get_sms_scope` в `deps.py`) берёт `team_ids` из `user_teams` пользователя → фильтр SMS/номеров по **текущей** принадлежности номера команде (`sms_phone_numbers.team_id ∈ team_ids`). Запрос вне scope → **пустой результат** (анти-энумерация, не `403`/`404`). Правило симметрично для сообщений и номеров (`GET /api/sms/messages` и `GET /api/sms/numbers`).
- Побочный эффект: `POST /api/sms/telegram/link` требует `user_id` (супер-админ без `uid` не может привязать линк к строке `users` → `403 forbidden`).

Поле не влияет на прочие эндпоинты (существующая логика RBAC не читает `user_id`).

## Защита модуля СМС (Twilio / Telegram)

Модуль «СМС» — приём входящих SMS от Twilio и доставка операторам через отдельный Telegram-бот ([ADR-030](adr/ADR-030-sms-module-full-merge.md), [modules/sms](modules/sms/README.md)). Три публичных эндпоинта без JWT гейтятся криптографически.

### Секреты (только env)
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` — креды Twilio (подпись webhook + Numbers API). **Секрет** (`AUTH_TOKEN`) — только env, не в БД/логах/ответах/SPA/URL.
- `SMS_TELEGRAM_BOT_TOKEN` — токен **отдельного** SMS-delivery-бота (НЕ notifier-бот, ADR-009). Секрет, только env. `sms_bot_enabled = bool(SMS_TELEGRAM_BOT_TOKEN)`.
- `SMS_TELEGRAM_WEBHOOK_SECRET` — секрет-токен Telegram-webhook. Секрет, только env.
- Все секреты — фильтр structlog; `raw` тело Twilio-webhook и Telegram-`init_data`/`Update` не логируются.

### Подпись Twilio (`POST /api/sms/webhooks/twilio/sms`)
- При `VERIFY_TWILIO_SIGNATURE=true` (default) — валидация `X-Twilio-Signature` через `twilio.request_validator.RequestValidator(TWILIO_AUTH_TOKEN)`. Неверная/отсутствующая подпись → `401 invalid_twilio_signature` (до обработки тела). `VERIFY_TWILIO_SIGNATURE=true` без `TWILIO_AUTH_TOKEN` → `503 twilio_not_configured`.
- **Реконструкция URL для подписи (критично, единственный источник истины).** Twilio считает подпись по **полному внешнему URL** (`https://<host>/api/sms/webhooks/twilio/sms`) + отсортированным form-полям. За nginx backend видит внутренний `http`/host, поэтому URL для проверки подписи **реконструируется ТОЛЬКО из `SMS_PUBLIC_BASE_URL`** (нормативный источник истины) + путь запроса — детерминированно и независимо от заголовков. `SMS_PUBLIC_BASE_URL` **обязан** совпадать с внешним HTTPS-адресом, на который Twilio шлёт webhook, иначе подпись не сойдётся ([07-deployment.md](07-deployment.md#reverse-proxy-nginx--требования)). Заголовки `X-Forwarded-Proto`/`X-Forwarded-Host` для валидации подписи **не требуются** (проброс `X-Forwarded-*` полезен для логов/HSTS, но источником URL подписи не является).

### Секрет Telegram-webhook (`POST /api/sms/telegram/webhook`)
- Заголовок `X-Telegram-Bot-Api-Secret-Token` обязан совпадать с `SMS_TELEGRAM_WEBHOOK_SECRET` — **constant-time** (`secrets.compare_digest`), **до** разбора тела. Несовпадение/отсутствие → `403 invalid_webhook_secret`. Бот обрабатывает только `/start`; ошибка `sendMessage` не роняет обработчик (`200`).

### Mini App initData (`POST /api/sms/telegram/auth` SSO / `link`)
- Валидация `init_data` — HMAC-SHA256 (`WebAppData`-ключ из `SMS_TELEGRAM_BOT_TOKEN`) + TTL `auth_date` (порт `telegram/init_data.py` донора, чистая функция без I/O). Плохой HMAC → `401 invalid_init_data`; протухший `auth_date` → `401 init_data_expired`. `init_data` (содержит подпись/PII) не логируется; **извлечённый `username` также не логируется** (PII, единообразно с [ADR-031](adr/ADR-031-sms-operator-mini-app.md) §2 и [modules/sms](modules/sms/README.md#backend-доработка-telegram-sso-нормативно-adr-031)).
- `auth` — **беспарольный Telegram-SSO** ([ADR-031](adr/ADR-031-sms-operator-mini-app.md)): `init_data` выступает **аутентификатором** — доказывает владение `telegram_user_id` и текущим Telegram-`username`; сервер резолвит CRM-оператора и **выдаёт CRM access-JWT** (детали — раздел «Операторская Mini App» ниже). `link` дополнительно требует **валидный JWT** — привязывает `telegram_user_id` только к `principal.user_id` (self-link из админ-SPA; Mini App его не использует).

### Доставка (fan-out)
- Fan-out идёт получателям **команды приёма** (снимок `sms_inbound.team_id`) через живые `sms_telegram_links` — независимо от RBAC-права на страницу `sms` (право `sms:view` управляет просмотром ленты, а не получением Telegram-доставок). `403`/forbidden от Bot API → линк помечается `dead_at`, доставка `dead` (оператор перепривязывает через Mini App).

### Операторская Mini App (`/tg/sms`, ADR-031)
Страница, открываемая оператором **внутри Telegram** (по кнопке SMS-бота) для беспарольного входа и просмотра своих SMS ([ADR-031](adr/ADR-031-sms-operator-mini-app.md), [modules/sms](modules/sms/README.md#операторская-telegram-mini-app-нормативно)).

- **Публичный маршрут вне админского SPA-shell.** `/tg/sms` — публичный route SPA (сосед `/login`), **вне** `AppLayout` и **вне** page-guard'ов RBAC: **нет** redirect на `/login`, **нет** заглушек «Недостаточно прав», **нет** админского nav-shell. Изоляция от админского SPA: маршрут не рендерит защищённые страницы и не даёт доступа к данным без соответствующего JWT/права.
- **`init_data` — аутентификатор беспарольного SSO.** `POST /api/sms/telegram/auth` валидирует `init_data` (HMAC-SHA256 `WebAppData` из `SMS_TELEGRAM_BOT_TOKEN` + TTL `auth_date`) и **выдаёт CRM access-JWT** оператору: резолв по иммутабельному `telegram_user_id` (через линк), иначе bootstrap по `users.telegram = normalize(username)` из `init_data`. Не сопоставлен → `403 sms_operator_not_provisioned`. JWT — обычный access-токен (`uid`/`role`/`superadmin:false`), хранится в памяти auth-store (не в `localStorage`; `sessionStorage` для переживания перезагрузки webview — как в [JWT](#jwt)). Пароль не участвует. Просмотр номеров/сообщений — существующими `GET /api/sms/numbers`/`messages` под этим JWT и `sms:view` (роль оператора обязана включать `sms:view`).
- **Риск подмены telegram-ника (username reuse/takeover, нормативно).** Bootstrap-сопоставление по `username` доверяет владению ником **в моменте auth** (initData подписан Telegram) — это верно, но Telegram-ники **рециклятся**: если записанный в `users.telegram` ник оператора освобождён и захвачен другим лицом **до первой привязки**, тот пройдёт SSO как оператор. Окно риска — **только до первого линка** (после — резолв по иммутабельному `telegram_user_id`, ник не участвует). Митигации: своевременный провижининг операторов и первый вход; будущее усиление — привязка по `telegram_user_id`, вводимому админом (без username-bootstrap). Открытый вопрос — [Q-SMS-3](99-open-questions.md).
- **Telegram WebApp SDK — self-hosted (CSP `script-src 'self'` не ослабляется).** Официальный `telegram-web-app.js` с `telegram.org` блокируется CSP; SDK **вендорится** как статика своего origin (`/telegram-web-app.js`) и подключается как `script-src 'self'`. Внешних CDN/скриптов по-прежнему нет — [CSP](#content-security-policy-spa-location-) не изменяется.
- **Поверхность — нативные Telegram-webview.** iOS/Android/Desktop открывают Mini App в webview верхнего уровня — `frame-ancestors 'none'`/`X-Frame-Options: DENY` к ним не применяются. **Браузерный Telegram Web** (`web.telegram.org`, iframe) блокируется `frame-ancestors 'none'` — **подтверждённое пользователем ограничение** (native-only); глобальная CSP **не ослабляется** ([Q-SMS-1](99-open-questions.md); поддержка потребовала бы выделенного nginx-`location /tg/` c `frame-ancestors https://web.telegram.org` + снятия `X-Frame-Options: DENY`).
- **`init_data` не логируется** (содержит подпись/PII); супер-админ (`.env`, без `uid`/`telegram`) через SSO не резолвится (`403 sms_operator_not_provisioned`) и получателем доставок не является ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §7).

## Защита паролей прокси

Пароль прокси — секрет того же класса, что SSH-пароли и AI-ключи ([modules/proxies](modules/proxies/README.md#безопасность-пароля-нормативно), [ADR-019](adr/ADR-019-proxies-availability-monitor.md)).

- Пароль прокси (опциональный) шифруется **Fernet** тем же `FERNET_KEY` сразу при `POST /api/proxies`; в БД — только `password_encrypted bytea` (`NULL`, если пароль не задан) ([03-data-model.md](03-data-model.md#таблица-proxies)). Переиспользуются `encrypt_secret`/`decrypt_secret`.
- Расшифровка — только в памяти монитора непосредственно перед сборкой URL (`scheme://user:pass@host:port`) и HTTP-запросом через прокси; расшифрованное значение и собранный URL не логируются и не покидают процесс.
- **Пароль (в любом виде) НЕ возвращается в обычных list/detail-ответах API.** Вместо него — производный флаг `has_password: bool`; фрагменты пароля не хранятся и не раскрываются (маски по фрагментам, как у AI-ключей, здесь нет). **Исключение ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)):** reveal-эндпоинт `GET /api/proxies/{id}/password` под правом `proxies:edit` отдаёт пароль по требованию (при `has_password=true`; иначе `404 secret_not_set`) — см. [«Reveal секретов по требованию»](#reveal-секретов-по-требованию-adr-035).
- **`username` (логин прокси) — не секрет:** хранится plaintext, возвращается в API как есть. Осознанно (нужен для отображения и сборки URL; сам по себе доступа не даёт без пароля/хоста).
- Пароль/URL прокси **не передаются** в query-строке и не пишутся в structlog (фильтр секретов).

## Защита API-ключей бэка ([ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md))

У бэка **два опциональных секрета** — `api_key` и `admin_api_key` (ключи доступа к API самого бэка), класса того же, что SSH-пароли/пароли прокси/AI-ключи.

- Каждый ключ (опциональный) шифруется **Fernet** тем же `FERNET_KEY` при `POST`/`PATCH /api/backends` (только если ключ задан); в БД — только `api_key_encrypted`/`admin_api_key_encrypted bytea` (`NULL`, если не задан) ([03-data-model.md](03-data-model.md#таблица-backends)). Переиспользуются `encrypt_secret`/`decrypt_secret`.
- **Секреты (в любом виде) НЕ возвращаются в обычных list/detail-ответах.** Вместо них — производные флаги `has_api_key`/`has_admin_api_key`. **Раскрытие ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)):** reveal-эндпоинты `GET /api/backends/{id}/api-key` и `GET /api/backends/{id}/admin-api-key` под правом **`backends:edit`** отдают plaintext по требованию (если ключ задан; иначе `404 secret_not_set`) — см. [«Reveal секретов»](#reveal-секретов-по-требованию-adr-035). Обоснование гейта симметрично: держатель `backends:edit` может перезаписать ключ через `PATCH`.
- **`git`/`note` бэка — НЕ секреты:** plaintext, возвращаются в API как есть (ссылка на репозиторий и свободные примечания; сами по себе доступа не дают). Связи `server_id`/`ai_key_id` — тоже не секреты.
- Расшифровка — только в памяти обработчика reveal-эндпоинта; plaintext не логируется (фильтр секретов).

## Reveal секретов по требованию ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md))

Секреты сущностей (`ssh_password` сервера, `password` прокси, полный `key` ИИ-ключа, `api_key`/`admin_api_key` бэка — [ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md)) хранятся зашифрованными (Fernet, `FERNET_KEY`) и **не преднагружаются** в обычных ответах API. Раскрытие — только по явному действию оператора через выделенный per-resource эндпоинт (контракт — [04-api.md](04-api.md#reveal-секретов-по-требованию-adr-035)). Это осознанный контролируемый разворот принципа «секрет не возвращается никогда».

**Эндпоинты и расшифровка.** `GET /api/servers/{id}/ssh-password`, `GET /api/proxies/{id}/password`, `GET /api/ai-keys/{id}/key`, `GET /api/backends/{id}/api-key`, `GET /api/backends/{id}/admin-api-key` → `SecretRevealResponse {value}`. Расшифровка — `app/infra/crypto.decrypt_secret` в памяти обработчика непосредственно перед формированием ответа; plaintext не логируется и не сохраняется.

**Гейтинг (нормативно): право `<page>:edit`.** Reveal гейтится `require("<page>","edit")` соответствующей страницы (`servers:edit`/`proxies:edit`/`ai-keys:edit`/`backends:edit`); супер-админ и роль `admin` (полный каталог) — всегда. Обоснование: держатель `edit` для прокси/ИИ-ключей уже может **перезаписать** секрет (`PATCH`, re-encrypt) → раскрытие ему симметрично; для серверов `edit` — доверенное управление серверами (введённый SSH-пароль нужен оператору). Новое право/действие в каталоге **НЕ вводится** — переиспользуется `edit` (NFR-1); каталог `app/domain/permissions.py::CATALOG` не меняется. Строгий вариант (admin-only/`delete`) отклонён ради простоты — вынесен на подтверждение ([Q-SEC-5](99-open-questions.md)).

**Транспорт.** Метод **GET** (секрет — в теле ответа, не в URL: в URL только `id` → в access-логах секрета нет). Ответ обязан нести заголовок **`Cache-Control: no-store`** — секрет не кэшируется прокси/браузером.

**Аудит (нормативно).** Каждый **успешный** reveal порождает структурированную запись лога `secret_revealed` со `structlog`-полями `actor` (`username`/`user_id` принципала), `resource_type` (`server`/`proxy`/`ai_key`/`backend`), `resource_id`, `at` (timestamp). **Само значение секрета в лог НЕ пишется** (фильтр секретов). (У бэка два секрета `api_key`/`admin_api_key` на одном ресурсе — `resource_type="backend"` для обоих; конкретное поле в аудите не различается.) Это лёгкий аудит через логи; персистентная аудит-таблица действий пользователей остаётся [TD-001](100-known-tech-debt.md).

**Frontend (память).** Раскрытое значение хранится **только** в локальном стейте компонента detail-модалки; **не** кладётся в TanStack Query-кэш / Zustand; скрывается по повторному клику и сбрасывается при закрытии модалки. Кнопка-глаз рендерится только при `<page>:edit` (для прокси — дополнительно только при `has_password=true`).

**Что reveal НЕ ослабляет.** Обычные list/detail-ответы по-прежнему без секретов (сервер/прокси/ключ). `FERNET_KEY` из ответов не выводится. `MAIL_API_KEY`, `TELEGRAM_*`, `ADMIN_PASSWORD`, `JWT_SECRET` и прочие env-секреты reveal-эндпоинтами **не** раскрываются (это секреты окружения, не at-rest-секреты сущностей).

## Видимость номеров в `GET /api/teams/{id}/numbers` ([ADR-034](adr/ADR-034-teams-number-login-app.md))

Эндпоинт `GET /api/teams/{id}/numbers` (гейт `teams:view`) отдаёт схему `TeamNumberItem`. По [ADR-034](adr/ADR-034-teams-number-login-app.md) она включает `login` и `app_name` номеров команды (частичный разворот сужения [ADR-030](adr/ADR-030-sms-module-full-merge.md) §8).

- **Раскрывается под `teams:view`:** `phone_number`, `team`, `login`, `app_name`. Держатель `teams:view` видит эти поля номеров **любой** команды (эндпоинт под `teams:view`, не под SMS-scope).
- **НЕ раскрывается:** `note` (свободная заметка — может содержать чувствительный текст) и `label` (системный Twilio `friendly_name`) — только на странице «СМС» под матрицей `sms:*` и SMS-scope.
- **Трейдофф (осознанный):** `login`/`app_name` — слабо-чувствительный идентифицирующий контекст (какой аккаунт/приложение привязаны к номеру), **не** секрет: сами по себе доступа к аккаунту не дают (нет пароля/токена). Управление командами де-факто admin-ориентировано ([ADR-022](adr/ADR-022-teams-nav-categories.md) §3), `teams:view` — доверенная роль. У номера секрета нет (пароли/ключи к номерам не относятся).

## Ansible и секреты

- Креды передаются в Ansible через переменные среды/`extravars` в памяти ansible-runner, не через файлы на диске (или через временные файлы с `0600`, удаляемые в `finally`).
- `no_log: true` на тасках, использующих пароль (см. [09-provisioning.md](09-provisioning.md)).
- SSH host key checking: на Этапе 1 `ANSIBLE_HOST_KEY_CHECKING=false` (новые серверы без known_hosts) — задокументированный риск MITM при первом подключении ([TD-007](100-known-tech-debt.md), [Q-SEC-2](99-open-questions.md)).
- **Привилегии (`become`):** Этап 1 предполагает целевого SSH-пользователя `root` ИЛИ sudoer с passwordless `sudo` (`NOPASSWD`) — `ansible_become_password` не передаётся. Sudoer с паролем не поддерживается ([Q-SEC-3](99-open-questions.md)). Детали — [09-provisioning.md](09-provisioning.md#привилегии-become).

## Сетевая безопасность инфраструктуры

- **Prometheus и Grafana не публикуются наружу** (NFR-9). В docker-compose их порты не маппятся на хост-интерфейс `0.0.0.0`; доступ — только внутри docker-сети или через защищённый reverse-proxy с auth.
- Grafana: сменить дефолтный admin-пароль (`GF_SECURITY_ADMIN_PASSWORD` из `.env`), `GF_AUTH_ANONYMOUS_ENABLED=false`.
- Grafana защищена собственным логином; доступ — напрямую через proxy `/grafana` (drill-down ссылки из карточки в UI нет — [ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)).
- **Telegram-нотификатор:** `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` — секреты, только из env; в код/логи/ответы API не попадают ([modules/notifier](modules/notifier/README.md)). Исходящий трафик — HTTPS к `api.telegram.org`.
- Reverse-proxy (nginx) терминирует TLS, проксирует `/api`→backend, `/`→SPA.

## TLS-сертификаты

- Продакшен-домен — **`broadappsdev.shop`** (DNS A → `37.27.192.211`).
- **Production (основной путь):** реальный сертификат **Let's Encrypt** (certbot standalone, HTTP-01), выпуск/продление скриптами `infra/scripts/issue-cert.sh` / `renew-cert.sh`; `fullchain.pem`+`privkey.pem` кладутся в volume `proxy-certs` (`TLS_CERT_DIR`), nginx отдаёт им приоритет над self-signed.
- **Self-signed (fallback):** автогенерируется entrypoint'ом `proxy` в `proxy-certs` (CN/SAN = `PUBLIC_HOSTNAME`), когда реального серта нет (окружение без домена / до первого выпуска LE).
- Приватные ключи — только в volume `proxy-certs`, НЕ в репозитории и НЕ в образе.
- Выпуск LE через standalone требует кратковременной остановки `proxy` (порт :80) — допустимо; zero-downtime продление (webroot/ACME-companion) — улучшение ([TD-011](100-known-tech-debt.md)).
- Конфигурация и процедура — [07-deployment.md](07-deployment.md#tls-сертификаты).

## Документация API (`/api/docs`, `/api/openapi.json`)

- В **production** интерактивная документация и спецификация **отключены**: FastAPI инициализируется с `docs_url=None`, `redoc_url=None`, `openapi_url=None`, когда `APP_ENV=production`. Тогда `/api/docs` и `/api/openapi.json` отдают `404`.
- В **development** (`APP_ENV=development`, по умолчанию для локальной разработки) они доступны по `/api/docs` и `/api/openapi.json` без дополнительной auth (среда разработки изолирована).
- Управляющая переменная — `APP_ENV` (`development` | `production`), фиксируется в [07-deployment.md](07-deployment.md#переменные-окружения). На проде SPA, API и (закрытые) docs за одним reverse-proxy; OpenAPI наружу не публикуется.
- Требование к backend: значение `docs_url`/`redoc_url`/`openapi_url` вычисляется из `APP_ENV` в фабрике приложения.

## HTTP-заголовки безопасности (нормативно)

Заголовки выставляются **по зоне ответственности, без дублирования** (закрытие [Q-SEC-4](99-open-questions.md)):

- **Backend (FastAPI middleware, `setdefault`)** — на ответы API (`/api/*`, отдаёт backend): `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. `setdefault` не перетирает уже заданный заголовок. **CSP backend для JSON-API не выставляет** (CSP применяется к HTML-документу SPA).
- **nginx (`add_header ... always`)** — на ответы SPA (`location /`, статику отдаёт nginx, backend не участвует): те же `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` + **`Content-Security-Policy`** (см. ниже). HSTS на проде также может ставиться на уровне `proxy`/TLS-терминатора единожды.
- **Без дублей:** `/api/*` отдаёт backend (nginx в `location /api` security-заголовки НЕ добавляет), `/` отдаёт nginx. Зоны не пересекаются — двойных заголовков нет.
- CORS: разрешён только origin фронтенда (`CORS_ALLOW_ORIGINS` из `.env`); на проде SPA и API за одним origin — CORS можно не открывать.

### Content-Security-Policy (SPA, `location /`)

Точное нормативное значение (должно **побайтово** совпадать с конфигом nginx):

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Обоснование директив:

| Директива | Значение | Причина |
|-----------|----------|---------|
| `default-src` | `'self'` | База: всё только со своего origin (внешних доменов нет) |
| `script-src` | `'self'` | Скрипты только из собранной статики SPA; inline-скриптов нет |
| `style-src` | `'self' 'unsafe-inline'` | Tailwind/Radix используют inline-стили (`style=...`) — без `'unsafe-inline'` UI ломается. `'unsafe-inline'` для стилей — осознанный компромисс ([TD-012](100-known-tech-debt.md)) |
| `img-src` | `'self' data: https:` | Иконки/инлайн-SVG, data:-изображения **и удалённые (remote https) изображения писем**. `https:` добавлен для отрисовки картинок в HTML-теле писем (sandbox-iframe наследует CSP страницы и не может ослабить её через meta) — [ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md). Только `https:` (не `http:`/`*`); грузятся **пассивные** `<img>`, скрипты остаются заблокированы (sandbox без `allow-scripts`, `script-src 'self'` не тронут). Компромисс — допускаются трекинг-пиксели (отправитель видит факт открытия письма); referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe). `cid:`-инлайн-картинки не резолвятся ([TD-026](100-known-tech-debt.md)) |
| `font-src` | `'self' data:` | Шрифты self-hosted (`@fontsource`, Inter/JetBrains Mono); `data:` — на случай инлайна шрифтов сборщиком |
| `connect-src` | `'self'` | XHR/fetch только на свой origin (`/api`) — backend за тем же origin |
| `frame-ancestors` | `'none'` | CRM нельзя встраивать во фрейм (анти-clickjacking; усиливает `X-Frame-Options: DENY`) |
| `base-uri` | `'self'` | Запрет подмены `<base>` |
| `form-action` | `'self'` | Формы только на свой origin |

- **Grafana** доступна **напрямую** по `/grafana` (тот же origin); **ссылки/drill-down из карточки в UI нет** ([ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки), см. также строку про сетевую безопасность выше). На главной — кастомные SVG-гейджи, Grafana **не встраивается во фрейм**. Поэтому `frame-src` не требуется (`frame-ancestors 'none'` достаточно), а `connect-src`/расширений под Grafana не нужно. Grafana под `/grafana` имеет собственную CSP, выставляемую самим Grafana.
- Поскольку внешних CDN/доменов нет (шрифты и ассеты self-hosted), ослаблять директивы внешними источниками не требуется.

## Управление секретами

| Секрет | Источник | Примечание |
|--------|----------|-----------|
| `ADMIN_USER` / `ADMIN_PASSWORD` | `.env` | Супер-админ (bootstrap); не в БД, не в репо ([ADR-008](adr/ADR-008-admin-iz-env.md)+[ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Пароли БД-пользователей | БД (`users.password_hash`, **nullable**) | bcrypt-хэш at-rest; `NULL` = беспарольный (до «открытого первого входа», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)); plaintext не в env/логах/ответах ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| `JWT_SECRET` | `.env` | ≥ 32 байта случайных |
| `FERNET_KEY` | `.env` | base64 32 байта |
| `POSTGRES_PASSWORD` | `.env` | — |
| `GF_SECURITY_ADMIN_PASSWORD` | `.env` | Grafana admin |
| `TELEGRAM_BOT_TOKEN` | `.env` | секрет, маскируется в логах; нотификатор ([modules/notifier](modules/notifier/README.md)) |
| `TELEGRAM_CHAT_ID` | `.env` | не секрет в строгом смысле, но не в репо; вместе с токеном активирует нотификатор и Telegram-алерты AI-ключей |
| AI-ключи (OpenAI/Anthropic) | БД (`ai_keys.key_encrypted`) | вводятся через API, шифруются `FERNET_KEY`; не в env/логах/обычных ответах (маска `key_masked`); полный ключ — только on-demand reveal под `ai-keys:edit` ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md), [modules/ai-keys](modules/ai-keys/README.md)) |
| Пароли прокси | БД (`proxies.password_encrypted`) | опциональны; вводятся через API, шифруются `FERNET_KEY`; не в env/логах/обычных ответах (в API — только `has_password`); plaintext — только on-demand reveal под `proxies:edit` ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)); `username` — не секрет ([modules/proxies](modules/proxies/README.md)) |
| SSH-пароли серверов | БД (`servers.ssh_password_encrypted`) | вводятся при создании, шифруются `FERNET_KEY`; не в env/логах/обычных ответах; plaintext — только провижининг in-memory **или** on-demand reveal под `servers:edit` ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)); `ssh_user` — не секрет |
| API-ключи бэка (`api_key`/`admin_api_key`) | БД (`backends.api_key_encrypted`/`admin_api_key_encrypted`) | опциональны; вводятся через API, шифруются `FERNET_KEY`; не в env/логах/обычных ответах (в API — только `has_api_key`/`has_admin_api_key`); plaintext — только on-demand reveal под `backends:edit` ([ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md), [ADR-035](adr/ADR-035-detail-view-secret-reveal.md)); `git`/`note`/`server_id`/`ai_key_id` — не секреты |
| `MAIL_API_KEY` | `.env` | секрет внешнего почтового API; только в заголовке `X-API-Key` backend→`postapp.store`; не в БД/логах/ответах/SPA/URL ([modules/mail](modules/mail/README.md)) |

- `.env` — в `.gitignore`; в репозитории только `.env.example` без значений.
- Логи проходят через structlog с фильтром секретов (пароли, токены, ключи маскируются).

## Модель угроз (Этап 1)

| Угроза | Митигация |
|--------|-----------|
| Перебор пароля админа | rate-limit + constant-time сравнение |
| Кража JWT через XSS | токен в `localStorage` (персист-сессия/мульти-вкладка, [ADR-041](adr/ADR-041-login-theme-session-ux.md)) — осознанно принятая расширенная поверхность; митигации: строгая CSP (`default-src 'self'`), нет сторонних скриптов, экранирование React, 24-часовой TTL JWT, полная очистка `crm.auth.*` на `401`/logout |
| Утечка SSH-паролей из БД | Fernet at-rest, ключ вне БД |
| Утечка AI-ключей из БД / логов / API | Fernet at-rest (`key_encrypted`), полный ключ не в ответах/логах, в UI/API только маска ([modules/ai-keys](modules/ai-keys/README.md)) |
| Утечка паролей прокси из БД / логов / API | Fernet at-rest (`password_encrypted`), пароль/URL не в ответах/логах, в API только `has_password` ([modules/proxies](modules/proxies/README.md)) |
| Утечка API-ключей бэка из БД / логов / API | Fernet at-rest (`api_key_encrypted`/`admin_api_key_encrypted`), не в ответах/логах, в API только `has_api_key`/`has_admin_api_key`; plaintext — только on-demand reveal под `backends:edit` ([ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md)) |
| Утечка секретов в логи | маскирование, `no_log` в Ansible |
| Доступ к Prometheus/Grafana извне | не публикуются наружу |
| User enumeration на входе | единое сообщение об ошибке, шаг 1 без запроса; та же ошибка для несуществующего/деактивированного БД-пользователя. **Исключение:** беспарольные идентификаторы раскрываются ответом `password_setup_required` — осознанный побочный эффект «открытого первого входа» ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)) |
| Захват учётки в окне «открытого первого входа» (беспарольный пользователь) | **Осознанный принятый риск** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)): setup-token limited-scope (только `set-password`, access-token не выдаётся до установки пароля); митигация — оперативная выдача идентификатора, короткое окно беспарольности |
| Обход UI-гейтинга прямым запросом к API | RBAC на сервере (`403 forbidden`); UI-скрытие — только UX ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Несанкционированный reveal секрета (сервер/прокси/ключ/бэк) | Гейт `require("<page>","edit")` на сервере (`403`; для бэка — `backends:edit`, [ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md)); секрет не преднагружается (только on-demand); `Cache-Control: no-store`; аудит-лог `secret_revealed` без значения; на фронте — только локальный стейт модалки, не в кэше/сторе ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)) |
| Утечка секрета в кэш/логи при reveal | секрет в теле ответа (не в URL) → нет в access-логах; `no-store` исключает кэш; plaintext не логируется (фильтр секретов) ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)) |
| Эскалация привилегий через устаревший токен после смены роли | права грузятся из БД на каждый запрос; деактивация/смена роли применяется без пере-логина ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Утечка паролей пользователей из БД/логов/API | bcrypt-хэш at-rest (`users.password_hash`); plaintext не хранится/не логируется/не в ответах ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Lockout (потеря доступа через данные) | супер-админ вне БД — вход работает даже при пустой/битой таблице ролей ([ADR-008](adr/ADR-008-admin-iz-env.md) амендмент) |
| MITM при первом SSH | принятый риск Этапа 1 ([TD-007](100-known-tech-debt.md)) |
| SSRF/инъекции в IP-поле | строгая валидация `inet`, без выполнения произвольных команд по вводу |
| Утечка `MAIL_API_KEY` в SPA/логи/URL | ключ только на backend, в заголовке `X-API-Key`; не в ответах/логах/SPA; фронт наружу не ходит ([modules/mail](modules/mail/README.md)) |
| Утечка IMAP/SMTP-паролей ящиков из CRM | CRM их **не хранит** — транзит в агрегатор по HTTPS, шифрование AES-256-GCM ТАМ (Fernet CRM не задействован); пароль не в логах/ответах/SPA; эндпоинты записи `Cache-Control: no-store` ([ADR-038](adr/ADR-038-mail-headless-integration.md) §5) |
| Энумерация чужих писем/ящиков через `/mail` | серверный `MailScope(sees_all_teams, group_ids, team_ids)` ([ADR-038](adr/ADR-038-mail-headless-integration.md) §3, [ADR-043](adr/ADR-043-lazy-mail-group-provisioning.md) §5): `group_ids`/`team_ids` из `user_teams` (`group_ids` — непустые `mail_group_id`, `team_ids` — все команды). **Чтение** вне scope → **пустая страница БЕЗ вызова внешнего API** (пустой `group_ids` → внешний запрос не делается; чужой `mail_account_id` AND-пересекается со scope-`group_id` → пустое пересечение — чужой ящик **неотличим от несуществующего**). **Создание** ящика — только для своей команды (`team_id ∈ team_ids`), иначе `403`. **Перенос** ящика между командами — только admin-уровень (`sees_all_teams`), не членство, иначе `403`. **Просмотр всех почт** — только admin-уровень. Мутация по `id` вне `group_ids` → `403`. Фронт-гейтинг только UX; граница — backend |
| XSS/кража JWT через HTML-тело письма | рендер только в sandbox-iframe (без `allow-scripts`/`allow-same-origin`), CSP SPA; скрипты письма не исполняются. `img-src ... https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) разрешает только пассивные `<img>` — XSS-инвариант не ослаблен ([ADR-012](adr/ADR-012-mail-read-through-proxy.md)) |
| Трекинг-пиксели в письме (отправитель узнаёт факт открытия) | принятый компромисс remote-картинок ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) — стандартно для почтовых клиентов; referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe), только `https:` (не `http:`); анти-трекинг-прокси отложён |

## Вне scope безопасности

- Многофакторная аутентификация, OAuth/SSO.
- ~~RBAC (одна роль — админ)~~ — **реализован** в Спринте 3 ([ADR-021](adr/ADR-021-rbac-users-roles.md)): роли + права на все страницы, серверный enforcement, bcrypt-хэш паролей БД-пользователей, `.env`-супер-админ как bootstrap.
- Аудит-лог действий пользователей ([TD-001](100-known-tech-debt.md)).
- UI-смена пароля супер-админа (`.env`) — by design только через `.env`/деплой; UI-управление паролями есть для БД-пользователей ([TD-009](100-known-tech-debt.md)).
- Refresh-токены, отзыв конкретного токена, история сессий.
