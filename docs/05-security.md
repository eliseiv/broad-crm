# 05 · Безопасность

## Аутентификация (логин и выпуск JWT)

С Спринта 3 система **многопользовательская** с ролями и RBAC ([ADR-021](adr/ADR-021-rbac-users-roles.md)); `.env`-учётка становится несменяемым **супер-админом (bootstrap)** ([ADR-008](adr/ADR-008-admin-iz-env.md) с амендментом). Порядок проверки при `POST /api/auth/login`:

1. **Сначала супер-админ (`.env`).** Логин/пароль сравниваются constant-time с `ADMIN_USER`/`ADMIN_PASSWORD`. **Учётка в БД НЕ хранится — вход не делает ни одного запроса в БД** (в `users` есть лишь его невидимая системная строка-якорь, [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md); залогиниться под ней невозможно — §[«Системный якорь»](#системный-якорь-супер-админа-нормативно-adr-051)); **всегда парольный** (беспарольным не бывает). Успех → JWT: `sub=ADMIN_USER`, `role="admin"`, `superadmin=true` (без `uid`).
2. **Иначе БД-пользователь.** Идентификатор входа (`username` в запросе) сопоставляется с `users.username` **точно**, иначе с нормализованным `users.telegram` — **вход по Логину ИЛИ Телеграму** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). При `is_active=true`:
   - `password_hash IS NOT NULL` (парольный) → `verify_password` (bcrypt). Успех → JWT: `sub=username`, `uid=users.id`, `role=role.name`, `superadmin=false`. При успехе сервер идемпотентно проставляет `users.first_login_at = now()`, если `NULL` (метка **первого входа** для тристатуса — [ADR-028](adr/ADR-028-user-status-first-login.md)).
   - `password_hash IS NULL` (**беспарольный**) → вход не выполняется; возвращается `password_setup_required: true` + limited-scope setup-token (см. [«Модель открытого первого входа»](#модель-открытого-первого-входа-нормативно)).
3. Неудача парольной ветки (не найден / `is_active=false` / неверный пароль) → единое `401 invalid_credentials`.

- Двухшаговый UI-вход; backend проверяет креды единым запросом `POST /api/auth/login` ([ADR-002](adr/ADR-002-dvuhshagovyy-auth.md)).
- Сравнение кредов **супер-админа** — **constant-time** (`secrets.compare_digest`) для логина и пароля, чтобы исключить timing-атаки. Пароли **БД-пользователей** проверяются bcrypt (`verify_password`, [«Хэширование паролей»](#хэширование-паролей-bcrypt)).
- Сообщение об ошибке входа одинаково для неверного логина и неверного пароля и для несуществующего/деактивированного пользователя (`invalid_credentials`) — не раскрывает существование пользователя.
- Защита от перебора: rate-limit на `/api/auth/login` (по IP, по умолчанию 10 попыток / 5 мин, далее `429`). Реализация — in-memory счётчик на Этапе 1 (один воркер), вынос в Redis — будущий этап ([TD-005](100-known-tech-debt.md)).
- **Определение реального IP клиента за reverse-proxy** (нормативно): backend берёт IP в порядке `X-Real-IP` → первый адрес из `X-Forwarded-For` → `request.client.host`. Поэтому nginx ОБЯЗАН проставлять эти заголовки для `location /api` (см. [07-deployment.md](07-deployment.md#reverse-proxy-nginx--требования)). Без корректного проброса rate-limit считал бы все запросы с одного IP (адрес прокси) и блокировал всех. Доверять `X-Forwarded-For`/`X-Real-IP` допустимо, только когда backend доступен исключительно через доверенный прокси (как в нашей топологии — backend не публикуется наружу).

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

**Claim'ы RBAC (побуквенно, [ADR-021](adr/ADR-021-rbac-users-roles.md)):** `sub` — username; `role` — имя роли (`"admin"` у супер-админа); `superadmin` — `true` у `.env`-супер-админа, `false` у БД-пользователя; `uid` — `users.id` (присутствует **только** у БД-пользователя, **отсутствует у супер-админа — и с [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) тоже**: его `Principal.user_id` берётся из **константы** `SUPERADMIN_USER_ID`, а не из токена — уже выпущенные токены остаются валидными). Токен без `superadmin=true` и без `uid` (легаси до Спринта 3) → `401` (повторный вход). Права в токен **не кладутся** — грузятся из БД на каждый запрос (см. [«RBAC»](#rbac--роли-права-и-enforcement)).

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
| `documents` | `view`, `create`, `edit`, `delete`, `share` ([ADR-059](adr/ADR-059-documents-module.md)) |

Порядок ключей каталога (= порядок строк матрицы в UI): `dashboard, servers, ai-keys, proxies, backends, mail, sms, roles, teams, documents`.

- **Страница `mail`** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) — CRM хранит письма/теги/ящики; агрегатор = IMAP/SMTP-connector). Действия: `view` (лента/ящики/теги + reply на письмо — reply НЕ расширяется, остаётся под `view`; сюда же `GET`/`PATCH /api/mail/me/settings`; **сюда же отметка ЛИЧНОЙ прочитанности письма — `POST` и `DELETE /api/mail/messages/{id}/read`** ([ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2.3: личный артефакт **чтения**, а не мутация домена — нового действия в каталоге **не вводится**; вне `MailScope` → `404 mail_message_not_found`. **Супер-админ из `.env` — НЕ исключение: `204`, как у всех** ([ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) §2 — норма [ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2.5 «`403` супер-админу» **отменена**; сохранённый для него `403` касается **только** `GET`/`PATCH /api/mail/me/settings` — [§Системный якорь](#системный-якорь-супер-админа-нормативно-adr-051))); `create` (`POST /mail/mailboxes`, `POST /mail/mailboxes/test`, `POST /mail/mailboxes/oauth/authorize`); `edit` (`PATCH /mail/mailboxes/{id}`); `delete` (`DELETE /mail/mailboxes/{id}`); `sync` (`POST /mail/mailboxes/{id}/sync` — дорогой форс-синк, отделён как у `sms:sync`); `tags` (управление глобальным каталогом тегов — админская функция на все команды сразу, поэтому отделена от per-mailbox `edit`: `POST/PATCH/DELETE /mail/tags`, правила, apply).
  - **Владение ящиком — команда, напрямую: `mail_accounts.team_id`** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §2). Групп-индирекции нет; **`MailScope(sees_all_teams, team_ids, includes_unassigned)`** — поля `group_ids` нет, поле `includes_unassigned` добавлено [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md); `team_ids` = **`user_teams` ∪ доп-команды канала `mail`** (см. [Per-channel scope](#per-channel-scope-команд-нормативно-adr-055)). `teams.mail_group_id` — мёртвый легаси-остаток, в авторизации **не участвует** ([TD-051](100-known-tech-debt.md)).
  - **Авторизация create** (`POST /mail/mailboxes`, `POST /mail/mailboxes/oauth/authorize`): не-admin — только для команды **своего scope** (`team_id ∈ MailScope.team_ids` — базовые ∪ доп-команды), иначе `403 forbidden`. **`team_id = null` (ящик без команды) — только admin-уровень**; не-admin с `null` → `403` **даже при `includes_unassigned=true`** ([ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §3).
  - **Перенос ящика между командами (`PATCH /mail/mailboxes/{id}` со сменой `team_id`) — ТОЛЬКО admin-уровень** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §4; требование владельца «переносить может только админ»). Гейтится предикатом `MailScope.sees_all_teams` (`is_superadmin OR permissions_subset(full_catalog, permissions)` — тот же admin-level, что «видеть все почты»), **НЕ** членством: участник даже нескольких команд переносить не вправе → `403 forbidden`. **Отдельного действия `mail:transfer` НЕ вводится:** admin-предикат нельзя выдать не-админу, что и требует владелец; симметрично «view all» ([ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)/[ADR-036](adr/ADR-036-sms-team-filter-admin-only.md)). Право `mail:edit` — необходимое, но недостаточное условие переноса.
  - **Просмотр всех почт — только admin-уровень:** вне scope чтение отдаёт пустой результат (анти-энумерация); reply на чужое письмо → `404` (неотличимо от несуществующего). Мутация существующего ящика по `id` (креды/`is_active`/`delete`/`sync`) — ящик обязан пройти **предикат scope** ([Per-channel scope](#per-channel-scope-команд-нормативно-adr-055): `team_id ∈ team_ids` **OR** (`includes_unassigned` **AND** `team_id IS NULL`)), иначе `403`. **Теги глобальны** — scope команд к ним не применяется. Детали — [04-api.md](04-api.md#mail).
- **Страница `sms`** ([ADR-030](adr/ADR-030-sms-module-full-merge.md)) не имеет `create`: **единственный источник строк `sms_phone_numbers` — `POST /api/sms/numbers/sync`** (Twilio, `bulk_upsert_unassigned` — `backend/app/repositories/sms_number_repository.py:138-163`); вручную номер не создаётся. ⚠️ **Входящее SMS номер НЕ заводит** (`backend/app/services/sms_ingest_service.py:120-130`: `find_by_phone` → при отсутствии строки пишется только `sms_inbound` c `team_id=None`) — прежняя формулировка «номера появляются из входящих SMS» была неверна и завышала объём флага `sms_includes_unassigned` ([ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §3.1). Действия: `view` (лента/номера), `edit` (`login`/`app_name`/`note`), `transfer` (команда номера), `sync` (Twilio), `delete` (удаление номера). Привязка Telegram (`POST /api/sms/telegram/link`) — **вне матрицы** `sms` (только аутентификация): доставка операторам — функция членства в команде, а не права на страницу.
- **Страница `documents`** ([ADR-059](adr/ADR-059-documents-module.md), [modules/documents](modules/documents/README.md)). Действия: `view` (страница/API `/api/documents/*` + дерево/узлы под per-node фильтром видимости), `create` (`POST /folders`/`/documents`/`/upload`/`/nodes/{id}/copy`), `edit` (`PATCH /nodes/{id}` — rename/content — и `PATCH /order`), `delete` (`DELETE /nodes/{id}` — soft-delete), **`share`** (смена видимости `PATCH /nodes/{id}/visibility` — отдельное чувствительное действие управления доступом, по образцу `mail:tags`/`sync` сверх CRUD; сюда же share-gated read-эндпоинты `GET /api/documents/role-refs` и `GET /api/documents/nodes/{id}/visibility`). См. [Видимость документов по ролям](#видимость-документов-по-ролям-нормативно-adr-059).

### Видимость документов по ролям (нормативно, [ADR-059](adr/ADR-059-documents-module.md))

Два **независимых** уровня доступа (оба обязаны выполниться):

1. **`documents:view`** — гейт страницы/API (`require("documents","view")`).
2. **Видимость по ролям (per-node)** — фильтр **внутри** модуля. У пользователя ровно одна роль (`users.role_id`). Узел виден ⇔ он **публичен внутри модуля** ИЛИ его **эффективный набор ролей** содержит `role_id` пользователя.

- **Эффективный набор ролей узла — вычисляемый** (рекурсивный CTE вверх по `parent_id` до **ближайшего `restricted`-предка**, включая сам узел; строки `document_node_roles` этого предка). Нет `restricted`-предка до корня → узел **публичен** ([03-data-model.md](03-data-model.md#резолюция-эффективной-видимости-рекурсивный-cte-adr-059-4)).
- **Admin-уровень видит всё:** `sees_all_documents = is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)` — тот же admin-предикат, что «видит все SMS/почты» ([ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)); нового права не вводится.
- **Enforcement — permission-based, НЕ owner-based:** `owner_id` — только автор для отображения, **не** гейт (право читать/править/удалять узел = `documents:<action>` + видимость по роли, а не совпадение `owner_id == user_id`). Согласованность с RBAC-каноном репо.
- **Анти-энумерация:** невидимую ноду нельзя ни читать по `id`, ни править/удалять → **`404 document_node_not_found`** (не `403`); списки/дерево — фильтруются (узел отсутствует). Симметрично «пустому scope» mail/sms.
- **Список ролей для модалки видимости** не-админу с `documents:share` — **`GET /api/documents/role-refs`** под `documents:share`; **НЕ** admin-gated `GET /api/roles` (иначе не-админ получил бы пустой список — дефект класса [TD-050](100-known-tech-debt.md)).

- Страница **«Пользователи» (`users`) в каталог не входит** — управление **пользователями** (создание/удаление, сброс паролей, назначение ролей) гейтится `require_admin` (`is_superadmin || role=="admin"`). Управление **ролями** (`/api/roles`) и **командами** (`/api/teams`) со Спринта A — под матрицей `roles:*`/`teams:*` ([ADR-022](adr/ADR-022-teams-nav-categories.md)). Оговорка: **создание/редактирование CRM-команд де-факто admin-only** — форма выбирает лидера/участников из `GET /api/users` (под `require_admin`), поэтому `teams:create`/`teams:edit` даёт полный контроль состава только вместе с admin-доступом; `teams:view` — полноценный просмотр. Осознанное следствие замыкания эскалации ([ADR-022](adr/ADR-022-teams-nav-categories.md#3-гейтинг-api-нормативно)), контракт `teams:*` не меняется.
- Формат прав роли (`roles.permissions`, jsonb): `{ "<page>": ["<action>", ...] }`. Валиден ⇔ каждый ключ — известная страница (кроме `users`; допустимы `roles`/`teams`), каждое действие ∈ `CATALOG[page]`, без дублей → иначе `422 unprocessable`.
- Каталог отдаётся UI через `GET /api/permissions/catalog` — гейт со Спринта A **`require("roles","view")`** (было `require_admin`): каталог нужен редактору роли.

### Вложения (изображения) документов (нормативно, [ADR-068](adr/ADR-068-documents-image-attachments.md))

Изображения документов хранятся **файлами на volume** + метаданными в `document_attachments` ([03-data-model.md](03-data-model.md#таблица-document_attachments-adr-068)); контракт — [04-api.md](04-api.md#вложения-изображения-документов-adr-068).

**Главный инвариант: доступ к картинке = доступ к её узлу.** `GET /api/documents/attachments/{id}` гейтится `require("documents","view")` **и тем же рекурсивным CTE видимости** узла-владельца, что и сам узел. Все негативные исходы (вложения нет / узел невидим по роли / узел soft-deleted) дают **единый** код `404 document_attachment_not_found` — различие кодов сообщало бы о существовании невидимого узла (та же анти-энумерация, что у `document_node_not_found`).

- **⛔ Анонимная раздача файлов ЗАПРЕЩЕНА** — включая «неугадываемый UUID» как единственную защиту. UUID попадает в `content_md`, историю браузера, копии документа и в выдачу внешнего RAG ⇒ obscurity здесь не защита, а неотзываемый доступ. Отсюда же — запрет подписанных токенов **в query** (`?t=…`): секрет ушёл бы в access-логи nginx, что прямо противоречит [«секрет в теле, не в URL»](#reveal-секретов-по-требованию-adr-035).
- **Следствие для клиента:** картинка грузится **авторизованным `fetch`** и подставляется как `blob:`-URL (JWT в `localStorage` не прикрепляется браузером к `<img src>`); отсюда `blob:` в [`img-src`](#csp). Обязателен `URL.revokeObjectURL` при размонтировании.
- **Path traversal исключён конструктивно, а не санитайзингом.** Путь на диске строится **только** из UUID вложения и расширения, выведенного из `mime` по константному whitelist; пользовательский `filename` в пути **не участвует вовсе** (хранится лишь как метаданные для `Content-Disposition`/alt). Санитайзинг имён — исторический источник обходов (`..%2f`, NUL, юникод-нормализация), поэтому имя из пути изъято целиком. Дополнительно — defensive-проверка: `realpath` результата обязан лежать внутри `realpath` корня, иначе `404`.
- **Тип определяется по содержимому (magic bytes), а не по `Content-Type` клиента** — заявленный тип подделывается тривиально, а сохранённый `mime` управляет заголовком отдачи. Whitelist: `image/png`, `image/jpeg`, `image/webp`, `image/gif`.
- **SVG исключён нормативно и навсегда** (в рамках [ADR-068](adr/ADR-068-documents-image-attachments.md)): SVG — активный документ (скрипты, `<foreignObject>`, внешние ссылки), отдаваемый **с нашего origin** ⇒ XSS-вектор на собственном домене, который не закрывают ни `nosniff`, ни CSP страницы (файл открывается напрямую по своему URL, вне контекста SPA).
- **`Cache-Control: private, max-age=300, must-revalidate` + `ETag`.** Значение **`public` запрещено**: ответ зависит от прав запрашивающего ⇒ shared-кэш прокси отдал бы картинку постороннему.
- **`data:`-изображения в `content_md` запрещены** (`@tiptap/extension-image` с `allowBase64: false`): иначе Ctrl+V раздувал бы `content_md` base64-мусором, съедая `DOCUMENTS_MAX_MD_BYTES` и обходя хранилище/лимиты вложений. Любая картинка проходит через upload.
- **Права на диске — `0600`/`0700`** (строже `0644`/`0755` у file_sd: там второй читатель под другим uid, здесь читатель один — backend). Volume монтируется **только** в `backend`. **Владелец — `app` с ЗАПИНЕННЫМ `uid/gid 999`** (`backend/Dockerfile`): при правах `0600`/`0700` сдвиг uid при пересборке образа сделал бы уже лежащие вложения нечитаемыми, а в бэкап-архиве (`tar -p`) и на volume хранится **число**, а не имя ⇒ uid — часть формата бэкапа ([07-deployment.md §Пин uid/gid](07-deployment.md#пин-uidgid-пользователя-app-нормативно)).
- **Наружу (RAG, `X-API-Key`) байты не отдаются** — внешний контур остаётся текстовым; ссылки приходят в `content_md` как есть ([ADR-068](adr/ADR-068-documents-image-attachments.md) §6, [TD-074](100-known-tech-debt.md)).

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
- Пароль **супер-админа** (`.env`) bcrypt НЕ хэшируется — сравнение plaintext constant-time ([ADR-008](adr/ADR-008-admin-iz-env.md) амендмент); опция `ADMIN_PASSWORD_HASH` ([Q-SEC-1](99-open-questions.md)) для супер-админа не вводится. **`password_hash` его системной строки-якоря — это НЕ его пароль:** это bcrypt-хэш случайного **отброшенного** секрета («locked»), делающий вход по БД-ветке невозможным ([ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) §1.1); `NULL` там **запрещён** (иначе сработала бы ветка «открытого первого входа»).

## Защита SSH-кредов целевых серверов

Со [ADR-067](adr/ADR-067-server-ssh-key-auth.md) у сервера **два взаимоисключающих способа входа** (`servers.auth_method ∈ {password, key}`), а значит **три** класса материала at-rest: SSH-пароль, приватный SSH-ключ, парольная фраза ключа.

- Все три шифруются **Fernet** (`cryptography`) сразу при `POST /api/servers`; в БД — только `ssh_password_encrypted` / `ssh_private_key_encrypted` / `ssh_key_passphrase_encrypted` (`bytea`). **Один примитив и один ключ на все три** ([ADR-007](adr/ADR-007-shifrovanie-fernet.md)) — отдельного ключа для SSH-ключей не вводится.
- Ключ `FERNET_KEY` (base64, 32 байта) — из `.env`, никогда в коде/репозитории/логах/ответах API.
- **«Ровно один способ» гарантирует БД, а не только сервис:** CHECK `ck_servers_auth_material` ([03-data-model.md](03-data-model.md#таблица-servers)) исключает строки «пароль + ключ», «key без ключа», «passphrase без ключа» даже при ошибке кода или ручном `UPDATE`.
- **Валидация ключа при вводе — реальный разбор `cryptography`, не regex, и по 4-шаговой процедуре** ([04-api.md](04-api.md#post-apiservers), [ADR-067](adr/ADR-067-server-ssh-key-auth.md) §3 п.4): структурное определение `is_encrypted` → кросс-проверка с наличием фразы → загрузка (ветка отказа выбирается по `is_encrypted`, **не** по типу/тексту исключения) → проверка типа ключа (RSA/ECDSA/Ed25519; **DSA отвергается**). Побочные security-требования: **текст исключения разбора не пробрасывается ни в ответ, ни в лог** (он может нести фрагменты материала), сообщения фиксированы контрактом; внутреннее чтение исключения не запрещено, но процедурой не требуется.
- Расшифровка — только в памяти провижининг-сервиса непосредственно перед запуском Ansible, **либо** (для пароля) в обработчике reveal-эндпоинта; расшифрованное значение не логируется и не покидает процесс.
- **Парольная фраза НИКОГДА не покидает процесс backend** ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §5): она снимается с ключа **в памяти** (пере-сериализация в незашифрованный OpenSSH-PEM), в Ansible/inventory/env/логи не передаётся.
- Материал (в любом виде) НЕ возвращается в обычных list/detail-ответах API — там присутствует лишь **`auth_method`** (способ входа, не секрет).
  - **Пароль** — исключение [ADR-035](adr/ADR-035-detail-view-secret-reveal.md): `GET /api/servers/{id}/ssh-password` под `servers:edit` отдаёт plaintext по требованию.
  - **Приватный ключ и парольная фраза — write-only** ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §4): reveal-эндпоинтов для них **нет и вводить их запрещено**. У key-сервера `GET /api/servers/{id}/ssh-password` → `404 secret_not_set`.
- Ротация `FERNET_KEY` — `MultiFernet` (новый + старый ключ) — будущий этап ([TD-006](100-known-tech-debt.md)). Для key-серверов цена промаха выше: ключ невосстановим из CRM, битая расшифровка даёт `status=error` `"SSH key unusable"` ([09-provisioning.md](09-provisioning.md#обработка-ошибок)).

### Почему приватный ключ не раскрывается (нормативно, [ADR-067](adr/ADR-067-server-ssh-key-auth.md) §4)

Это **осознанное сужение** контура [ADR-035](adr/ADR-035-detail-view-secret-reveal.md), а не пропущенный эндпоинт:

| Довод | Суть |
|-------|------|
| **Радиус поражения** | Пароль открывает **один** сервер; приватный ключ — **переиспользуемый** креденшл, обычно открывающий весь парк хостов, включая машины вне CRM. Утечка несопоставима по последствиям ⇒ уравнивать гейтом нельзя |
| **Нет симметрии с `PATCH`** | Обоснование [ADR-035](adr/ADR-035-detail-view-secret-reveal.md) — «держатель `edit` и так может перезаписать секрет». У прокси/ИИ-ключа/бэка это верно; у **сервера** `PATCH` правит **только `name`** — симметрии нет. Слабость гейта `servers:edit` уже отмечена [Q-SEC-5](99-open-questions.md) |
| **Нет рабочего сценария** | Пароль оператор набирает руками и может забыть → reveal закрывает реальную нужду. Ключ он **не набирает** — у него есть локальный файл; «достать ключ из CRM» — это экспорт/эксфильтрация |
| **Half-reveal бессмыслен** | Раскрывать только passphrase — либо бесполезно (без ключа), либо опасно (в паре с утёкшим ключом снимает последний барьер) |

**Следствие, принятое явно:** CRM — **не хранилище ключей**. Потеря локальной копии ключа = пересоздание сервера ([TD-073](100-known-tech-debt.md)). В UI у key-сервера строка секрета показывается маской **без кнопки-глаза** — первый в проекте «не раскрываемый» секрет: глаз не рендерится **ни при каком праве**, а не «скрыт из-за отсутствия `edit`».

## Защита AI-ключей

Ключи AI-провайдеров (OpenAI/Anthropic) — секреты того же класса, что и SSH-пароли ([modules/ai-keys](modules/ai-keys/README.md#безопасность-ключа-нормативно), [ADR-010](adr/ADR-010-ai-key-monitor-vnutri-backend.md)).

- Полный ключ шифруется **Fernet** тем же `FERNET_KEY` сразу при `POST /api/ai-keys`; в БД — только `key_encrypted bytea` ([03-data-model.md](03-data-model.md#таблица-ai_keys)).
- Расшифровка — только в памяти монитора/проверки непосредственно перед HTTP-запросом к провайдеру (`GET /v1/models`); расшифрованное значение не логируется и не покидает процесс.
- **Полный ключ (в любом виде) НЕ возвращается в обычных list/detail-ответах API.** В них — только маска `key_masked` (первые 4 … последние 4 символа; для ключа короче 8 символов — полная маска `********`). **Исключение ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)):** reveal-эндпоинт `GET /api/ai-keys/{id}/key` под правом `ai-keys:edit` отдаёт полный ключ по требованию — см. [«Reveal секретов по требованию»](#reveal-секретов-по-требованию-adr-035).
- `key_prefix`/`key_last4` (по 4 plaintext-символа) хранятся ради маски и текста Telegram-алерта — осознанное раскрытие 8 символов, секрет из них не восстанавливается.
- Ключ провайдера **не передаётся** в query-строке/URL и не пишется в structlog (фильтр секретов); заголовки `Authorization: Bearer`/`x-api-key` не логируются.

## Защита ключа почты

Ключ внешнего почтового сервиса (`postapp.store`) — системный секрет того же класса, что AI-ключи и `TELEGRAM_*`. Модуль «Почты» — **CRM хранит письма/теги/ящики; агрегатор — IMAP/SMTP-connector** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md), [modules/mail](modules/mail/README.md)).

- `MAIL_API_KEY` — **только из env**, задаётся администратором развёртывания (НЕ через UI). В БД не хранится. Используется **только** для управляющих вызовов CRM → агрегатор (жизненный цикл ящика + SMTP-send + OAuth-authorize).
- Ключ подставляется backend'ом **только** в заголовок `X-API-Key` исходящего запроса к `postapp.store`. **Никогда** не возвращается в ответах CRM API, не логируется (structlog-фильтр секретов), не передаётся в SPA и не попадает в query-строку/URL.
- **Фронт наружу не ходит** — SPA обращается только к `/api/mail/*` (тот же origin, CSP `connect-src 'self'`); прямой вызов `postapp.store` из браузера исключён.
- HTML-тело письма — недоверенный контент третьих лиц — рендерится **только** в sandbox-iframe (`srcDoc` + `sandbox=""` без `allow-scripts`/`allow-same-origin`): скрипты письма не исполняются, доступа к origin/куки/JWT CRM нет ([ADR-012](adr/ADR-012-mail-read-through-proxy.md) — **этот инвариант пережил супессию ADR-012 и действует**, [modules/mail](modules/mail/README.md#изоляция-html-тела-нормативно--инвариант-adr-012-не-ослаблен)). Согласуется с CSP SPA (`frame-ancestors 'none'`, `script-src 'self'`). Удалённые (remote https) изображения тела письма отрисовываются — в `img-src` добавлен источник `https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md); действующее полное значение директивы — [CSP](#csp)); sandbox и `script-src 'self'` при этом не изменены — грузятся только пассивные `<img>`. **Смена фона/цвета тела письма под тему CRM** ([ADR-047](adr/ADR-047-mail-fix-pack.md) §6) — чисто стилевая инъекция в `srcDoc`; изоляцию **не ослабляет**.

### Транзит IMAP/SMTP-кредов (mail, нормативно)

IMAP/SMTP-пароли ящиков **в CRM не хранятся и не шифруются CRM** (Fernet CRM к почте НЕ применяется — `FERNET_KEY` служит SSH/proxy/AI-паролям, [ADR-007](adr/ADR-007-shifrovanie-fernet.md)). Инвариант введён [ADR-038](adr/ADR-038-mail-headless-integration.md) §5 и **сохранён** при переходе на [ADR-044](adr/ADR-044-mail-full-merge-into-crm.md):

- Пароли (`password`, опц. `smtp_password`) приходят с фронта в `POST/PATCH /api/mail/mailboxes*` и `POST /api/mail/mailboxes/test`, проходят **транзитом** в агрегатор по HTTPS и шифруются **там** (AES-256-GCM). CRM — источник истины писем/тегов/команд/прав/каталога ящиков; агрегатор — источник истины **кредов**.
- Пароль **никогда** не логируется (structlog-фильтр на `password`/`smtp_password`), **не** возвращается в теле ответов CRM (схемы `MailMailbox`/`TeamMailboxItem` полей пароля не содержат), **не** пробрасывается обратно в SPA.
- Эндпоинты записи (`POST /api/mail/mailboxes`, `POST /api/mail/mailboxes/test`, `PATCH /api/mail/mailboxes/{id}`, `POST /api/mail/mailboxes/oauth/authorize`) отвечают заголовком **`Cache-Control: no-store`**.
- **Исходящий payload в агрегатор строится БЕЛЫМ СПИСКОМ** (креды + `email` + производный `display_name` + `is_active`) — а не «`model_dump()` минус пара полей» ([ADR-047](adr/ADR-047-mail-fix-pack.md) §3.4). Это предотвращает молчаливую утечку наружу любого нового поля схемы CRM (`team_id`, `number`, `app_name` и т.д.).
- SSRF-guard хостов IMAP/SMTP выполняет **агрегатор**; CRM креды по сети сам не валидирует — делегирует `POST /api/mail/mailboxes/test`.
- Ретраи вызовов к агрегатору — **только** `ConnectError`/`ConnectTimeout` (запрос заведомо не ушёл), анти-двойная-запись/анти-двойная-отправка — [04-api.md#mail](04-api.md#mail). **Правило сохранено и при удлинённом таймауте** ([ADR-053](adr/ADR-053-mail-timeouts-error-passthrough.md)): read-timeout/исчерпание overall-deadline на write **не** ретраится — на mail-server-путях он отдаёт `504 mail_timeout` (а не `502 mail_unavailable`), автоповтора нет.
- **Таймаут вызова зависит от пути** ([ADR-053](adr/ADR-053-mail-timeouts-error-passthrough.md) §1): mail-server-пути (`POST /mailboxes/test`, `POST /mailboxes`, `PATCH /mailboxes/{id}`, reply) — read `MAIL_API_MAILSERVER_TIMEOUT_SEC` (75 с) при overall-deadline `MAIL_API_MAILSERVER_DEADLINE_SEC` (85 с); быстрые — `MAIL_API_TIMEOUT_SEC` (10 с) / `MAIL_API_DEADLINE_SEC` (30 с). Удлинённый бюджет **не ослабляет** транзитную модель кредов: пароли по-прежнему не хранятся, не логируются и не возвращаются; долгий запрос лишь **дольше держит** соединение до агрегатора (по HTTPS) — новых мест хранения секрета не появляется.
- **Overall-deadline — часть anti-DoS-контура:** без него per-phase таймауты + ретрай позволяли бы одному запросу удерживать соединение и воркер до ~225 с (3 попытки × `connect+write+read`); `asyncio.wait_for` (85/30 с) даёт **жёсткую** верхнюю границу удержания ресурса на один входящий запрос ([ADR-053](adr/ADR-053-mail-timeouts-error-passthrough.md) §1.2). Фаза `connect` — 5 с (агрегатор — известный хост), не 75 с.

### Push-контракт агрегатор → CRM (HMAC, нормативно)

Машинные приёмники **`POST /api/mail/ingest`**, **`POST /api/mail/mailbox-status`**, **`POST /api/mail/oauth/ingest`** — **без JWT, CSRF-exempt** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §3, [ADR-045](adr/ADR-045-mail-outlook-oauth-headless-reonboarding.md) §3). Аутентификация — **HMAC-SHA256, тело-связанная** (симметрично security-модели Twilio-webhook), НЕ статический bearer.

- Заголовки: `X-Mail-Signature: sha256=<hex>`, `X-Mail-Timestamp: <unix_seconds>`.
- **Каноническая форма подписи (побайтно, обе стороны обязаны строить её этим выражением):**
  ```python
  mac_input = str(timestamp).encode("ascii") + b"." + raw_body_bytes
  signature = hmac.new(secret.encode("utf-8"), mac_input, hashlib.sha256).hexdigest()
  ```
  где `raw_body_bytes` — **сырое** тело запроса **до** JSON-парсинга (не ре-сериализованное), разделитель — один байт `b"."`.
- Секрет — **`MAIL_PUSH_SECRET`** (класс секретов: только env, не в БД/логах/ответах/URL). Тем же секретом подписывается stateless `crm_state` OAuth-потока.
- Проверки и их **порядок** (нормативно): пустой `MAIL_PUSH_SECRET` → **`503 mail_ingest_not_configured`** (приёмник выключен) → `abs(now - ts) <= MAIL_PUSH_MAX_SKEW_SEC` (300) и `secrets.compare_digest` → иначе **`401 not_authenticated`** → битое тело/батч вне лимита → **`400 validation_error`**.
- **О timestamp-окне — честно:** это ограничение **окна валидности**, а не полноценный анти-replay (без nonce перехваченный валидный запрос можно воспроизвести в пределах 300 с). Практически безвреден за счёт **идемпотентности приёмника** (`ON CONFLICT (mail_account_id, uidvalidity, uid) DO NOTHING` — повтор не создаёт дубля письма; `mailbox-status`/`oauth/ingest` — upsert). Отдельный nonce-стор **не вводится** (NFR-1).

### Telegram-эндпоинты почты (нормативно)

- **`POST /api/mail/telegram/webhook/{secret}`** — `{secret}` сравнивается **constant-time** с `MAIL_BOT_WEBHOOK_SECRET`; дополнительно проверяется заголовок `X-Telegram-Bot-Api-Secret-Token`, если прислан. Mismatch → **`404`** (анти-энумерация, не `401`/`403`).
- **`POST /api/mail/telegram/push-webhook/{bot_name}`** — per-bot `MAIL_BOT_<NAME>_WEBHOOK_SECRET`, **header-only fail-closed**: отсутствие/несовпадение заголовка → `404`.
- **`POST /api/mail/telegram/auth` (Mini App `/tg/mail` SSO) — граница безопасности — проверка подписи `initData`.** Валидируется **HMAC-подпись** Telegram `initData` ключом бота + TTL по `auth_date` (`MAIL_TG_INITDATA_TTL_SEC`, 300 с). **Факту «страница открыта из Telegram» доверять запрещено** — это не доказательство личности. Неверная подпись → `401 invalid_init_data`; протухший → `401 init_data_expired`; пустой/битый → `400 validation_error`; Telegram-пользователь не сопоставлен с CRM-пользователем → `403 mail_operator_not_provisioned` (симметрично SMS Mini App, [ADR-031](adr/ADR-031-sms-operator-mini-app.md)).
- Резолв личности: приоритет — **иммутабельный `telegram_user_id`** (существующий линк); иначе bootstrap по username → `lower(users.telegram)` (регистронезависимо, ведущий `@` снимается). Остаточный риск подмены telegram-ника **до** первого линка — тот же, что у SMS Mini App ([ADR-031](adr/ADR-031-sms-operator-mini-app.md)).
- **Telegram WebApp SDK — self-hosted** (`script-src 'self'` не ослабляется), как у `/tg/sms`.
- Токены 5 ботов (`MAIL_BOT_TOKEN`, `MAIL_BOT_<NAME>_TOKEN`) и секреты вебхуков — класс секретов: только env, не логируются, в ответы/SPA не попадают.

## Расширение `Principal` полем `user_id` (нормативно)

Модуль «СМС» требует видимость сообщений по командам ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §6). Для этого `Principal` расширен полем `user_id`. **С [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) поле — НЕ-опциональное: `user_id: uuid.UUID`** (принципала без идентичности больше не существует):

- БД-пользователь → `user_id` из claim `uid` (UUID); стоимость нулевая — `users`-ряд уже загружается в `get_current_principal`.
- **Супер-админ из `.env`** → `user_id = SUPERADMIN_USER_ID` — **константа** системной строки-якоря ([ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md)), подставляется **без обращения к БД** (fallback-инвариант [ADR-008](adr/ADR-008-admin-iz-env.md) сохранён). **JWT не меняется** — `uid` супер-админу в токен не кладётся (см. claim'ы выше).
- **Зачем не-опциональность:** прежний `None` у супер-админа был **ловушкой**, о которую спотыкалась каждая персональная фича (личная прочитанность писем, настройки уведомлений, привязка Telegram). Сняв `| None` **из типа**, мы делаем `mypy` гарантом: новый персональный эндпоинт не может «случайно» получить принципала без идентичности.
- **Видимость SMS по роли (нормативно, [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)).** «Видит все команды» ⇔ **`is_superadmin` ИЛИ роль владеет полным каталогом прав**: `sees_all_teams = principal.is_superadmin or permissions_subset(full_catalog_permissions(), principal.permissions)`. Такой актор (консольный супер-админ; seed-роль `admin`; кастомная admin-роль, напр. «Админ», при полном каталоге) видит **все** SMS/номера (scope не сужается). Признак устойчив к переименованию роли (не завязан на редактируемое имя) и не требует нового права/миграции.
- **UI-гейт фильтра «Все команды» ([ADR-036](adr/ADR-036-sms-team-filter-admin-only.md) в редакции [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §6).** Предикат admin-уровня по-прежнему отдаётся фронту через `GET /api/auth/me` как производное булево **`sees_all_sms_teams`** (backend — единственный источник, фронт не дублирует `permissions_subset`). **⚠️ Прежнее правило «фильтр рендерится ТОЛЬКО при `sees_all_sms_teams === true`» ОТМЕНЕНО:** фильтр рендерится при **(число команд канала + «Без команды») ≥ 2** — едино для `/sms`, обеих вкладок `/mail` и **обеих Mini App** (пять экранов; отдельной ветки «`sees_all` → рендерить всегда» **нет**). Опции — из **`/api/auth/me`** (`mail_teams`/`sms_teams`) у **любого** актора, включая admin-уровень (**не** из `GET /api/teams`; гейт `teams:view` не требуется). Это UX; граница безопасности — серверный scope (ниже).
- **Источник команд канала на клиенте — шире фильтра ([ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §6.3).** **Любой** контрол со списком команд канала и у **любого** актора, включая admin-уровень (фильтр «Команда»; **селектор «Команда» в форме ящика**; резолв имени команды в таблице «Почты»; дропдаун переноса ящика; `Select` переноса номера под `sms:transfer`), наполняется из `GET /api/auth/me`, **не** из `GET /api/teams` (он под `require("teams","view")` — `backend/app/api/teams.py:27-31` — и у mail/sms-оператора вернёт `403`/пусто; в Mini App не берётся вовсе). Ветвление «admin — `GET /api/teams`, прочие — `/me`» **запрещено**: в Mini App оно оставило бы актора admin-уровня с **пустым** фильтром. `GET /api/teams` на клиенте остаётся только на страницах «Пользователи»/«Команды». **Принцип: вариант, который пользователь не вправе выбрать, не ПРЕДЛАГАЕТСЯ к выбору** (отображение фактического состояния в `disabled`-контроле — не предложение; исключение «зеркало текущего состояния», [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §6.3.1) — иначе контрол производит заведомый `403` (прод-баг 2026-07-14: единственной опцией у не-админа оставалась admin-only «Без команды» ⇒ ящик не создавался вовсе; [TD-050](100-known-tech-debt.md) закрыт).
- **Прочие роли** (неполный каталог: PM, «Пользователь» и т.п.) → видимость **по scope канала** (см. [Per-channel scope команд](#per-channel-scope-команд-нормативно-adr-055)): `SmsScope` (фабрика `get_sms_scope` в `deps.py`) → фильтр SMS/номеров по **текущей** принадлежности номера команде. Запрос вне scope → **пустой результат** (анти-энумерация, не `403`/`404`); мутация вне scope → `403`. Правило симметрично для сообщений и номеров (`GET /api/sms/messages` и `GET /api/sms/numbers`).
- **`POST /api/sms/telegram/link` — супер-админу `403 forbidden`** (гейт — `principal.is_superadmin`; прежнее основание «нет `uid`» устарело). Причина — security, а не техника: Telegram-привязка + Mini App-SSO дали бы для Telegram-аккаунта CRM-JWT с `role="admin"` — беспарольный и **неотзываемый из UI** второй путь к admin-уровню в обход `ADMIN_PASSWORD`. Bootstrap-учётка остаётся **console-only** ([ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) §1.6).

Поле не влияет на прочие эндпоинты (существующая логика RBAC не читает `user_id`).

## Per-channel scope команд (нормативно, [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md))

Видимость **и действия** в модулях «Почты» и «СМС» считаются по **scope канала** (`channel ∈ {mail, sms}`), а не по одному лишь членству в командах:

```
team_ids(channel)            = user_teams(user)  ∪  user_channel_teams(user, channel)   # union
includes_unassigned(channel) = users.<channel>_includes_unassigned                       # «Без команды»
```

- **Базовое членство (`user_teams`) входит в scope ОБОИХ каналов всегда** — блок канала в форме пользователя задаёт **добавку**, а не замену. Снятие команды в основном блоке **безусловно** снимает доступ в обоих каналах (инвариант нормализации — в `user_channel_teams` базовые команды не хранятся, [03-data-model.md](03-data-model.md#таблица-user_channel_teams-per-channel-добавки-adr-055)).
- **Доп-команда = полноценная команда канала** (решение владельца): пользователь в ней **работает наравне** со своей — read-only-режима нет. «Что можно» задаёт роль (`mail:*`/`sms:*`), «в какой команде можно» — scope. **Новых прав в каталоге не вводится.**

**Инвариант нормализации — на ВСЕХ путях записи в `user_teams` (нормативно, [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §2.3).** Это **security-инвариант**, а не гигиена данных: его нарушение даёт **«висящий» доступ** — пользователь, исключённый из команды, продолжает видеть и обрабатывать её почту/СМС. Путей записи в `user_teams` **два**, и оба обязаны нормализовать добавки в **той же транзакции**:

| Путь | Обязанность сервиса |
|------|---------------------|
| **Users CRUD** — `POST`/`PATCH /api/users` (`team_ids`; `UserRepository.set_membership`) | **вычесть** базовый набор из присланных добавок: `extra := <channel>_extra_team_ids − team_ids` (оба канала). «Лишняя» базовая команда в добавке — **не ошибка** (`422` не поднимается), просто не сохраняется |
| **Teams CRUD** — `POST /api/teams`, `PATCH /api/teams/{id}` (`member_ids`; `TeamRepository.create`/`replace_members`) | после приведения состава команды `T` — **удалить** строки `user_channel_teams (user_id ∈ member_ids, channel ∈ {mail,sms}, team_id = T)`. Участник команды не хранит её же как добавку |

> **Почему одного users-пути мало (сценарий эксплуатации).** (а) Админ даёт `X` доп-команду `B` в блоке «Почты»; (б) добавляет `X` в команду `B` участником **на странice «Команды»** — сервис users не вызывался, добавка осталась; (в) исключает `X` из `B` там же — строка `user_teams` удалена, **добавка осталась** ⇒ `X` **сохраняет** доступ к почте команды `B`. Гарантия обязана быть **path-independent**: она не может зависеть от того, на какой странице админ правил членство.

- **Удаление команды/пользователя** нормализации не требует: `user_teams` **и** `user_channel_teams` каскадят (`ON DELETE CASCADE`).

**Единый предикат scope (нормативно — применять и на чтении, и на мутации):**

```
obj.team_id IN team_ids  OR  (includes_unassigned AND obj.team_id IS NULL)
```

где `obj` — `mail_accounts` (почта) / `sms_phone_numbers` (СМС). **Прямое `obj.team_id in scope.team_ids` без ветки `includes_unassigned` в новом коде = дефект.**

| Путь | Правило |
|------|---------|
| Чтение (лента/каталог) | Предикат выше; вне scope → **пустой результат** (анти-энумерация, не `403`). Пустой `team_ids` **и** `includes_unassigned=false` → пусто **без выборки** |
| Мутация/удаление/синк по `id` | Предикат выше, иначе **`403 forbidden`**. **Изменение против прежней нормы:** объект **без команды** доступен не-админу **только** при `includes_unassigned=true` (прежде — `403` всегда: `backend/app/services/mail_service.py:906`, `backend/app/services/sms_number_service.py:124-127`) |
| Reply / отметка прочитанности письма | Предикат выше; вне scope → **`404 mail_message_not_found`** (анти-энумерация) |
| **Создание ящика с `team_id = null`** | **ТОЛЬКО admin-уровень** — **НЕ разворачивается** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §4): `includes_unassigned` даёт работу с **существующими** бесхозными ящиками, но не право создавать новые вне командной модели |
| **Перенос ящика между командами** | **ТОЛЬКО admin-уровень** — **НЕ разворачивается** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §4): иначе доп-команды стали бы обходным путём выноса ящика из чужой команды |
| **Перенос номера** (`POST /api/sms/numbers/{id}/transfer`, гейт `sms:transfer`) | **Три проверки ([ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §3.2, порядок = прецеденция):** (1) **номер** обязан пройти предикат scope → иначе `403 forbidden`; (2) **`team_id = null`** (снять команду) не-админу — **только при `includes_unassigned=true`**, иначе `403` (иначе актор безвозвратно выбрасывает номер из своего scope); (3) **`team_id = <uuid>`**: не-админ — целевая команда обязана ∈ `team_ids` (базовые ∪ доп) → иначе **`403 forbidden`**, причём проверка scope **первая** ⇒ несуществующая команда не-админу тоже даёт `403` (анти-энумерация: «нет команды» неотличимо от «чужая команда»); admin-уровень — существование → `404 sms_team_not_found`. **Прежде** проверялось **только существование** целевой команды (`sms_number_service.py:94-97`) ⇒ носитель `sms:transfer` мог вынести номер в **любую** команду, включая ту, где не состоит |
| Теги почты | Глобальны — scope не применяется (управление под `mail:tags`) |

### Риск флага «Без команды» в СМС (нормативно, [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §3.1)

Флаг **симметричен по форме, но НЕ по объёму**: каналы по-разному производят объекты с `team_id IS NULL`.

| Канал | Источник бесхозных объектов | Что реально открывает флаг |
|-------|-----------------------------|-----------------------------|
| **Почты** | только **рукотворно и только админом**: создание ящика с `team_id=null` — admin-уровень (`backend/app/services/mail_service.py:878-881`), перенос ящика — admin-уровень. Автоматического источника **нет** | небольшой предсказуемый набор ящиков, каждый заведён админом вручную |
| **СМС** | **массово и автоматически**: `POST /api/sms/numbers/sync` вставляет **все** входящие номера Twilio-аккаунта как unassigned (`team_id=NULL`) — `SmsNumberRepository.bulk_upsert_unassigned` (`backend/app/repositories/sms_number_repository.py:138-163`; вызов — `sms_sync_service.py:57`); команда назначается позже, вручную (`transfer`) | **ВЕСЬ ещё не распределённый поток номеров** и все SMS на них (видимость SMS — по **текущей** команде номера, `sms_message_service.py:95-100`) |

> **⚠️ `sms_includes_unassigned = true` = пользователь видит и ПОЛНОСТЬЮ управляет всем ещё не распределённым потоком номеров:** правка полей, **удаление** номера (`sms:delete`), **перенос** его в свою команду (`sms:transfer`). Каждый новый номер из синхронизации Twilio попадает к нему **автоматически**, пока команда не назначена.

- Последствие **принято владельцем осознанно** (решение 2026-07-14): read-only-режим для бесхозных объектов **отвергнут** — флаг даёт **просмотр И действия** наравне со своей командой.
- **Митигация — прозрачность, а не урезание прав:** чекбокс «Без команды» в блоке **«СМС»** формы пользователя несёт обязательную подсказку объёма ([08-design-system.md](08-design-system.md#блоки-смс-и-почты-в-форме-пользователя-нормативно-adr-055)); выдача флага — операция уровня «дать доступ ко всему входящему потоку», а не «показать пару осиротевших объектов».
- **Не развёрнуто (остаётся admin-only):** создание ящика с `team_id=null` и перенос **ящика** между командами ⇒ флаг не даёт **производить** бесхозные ящики.

**Границы решения (нормативно).** Telegram-**fan-out** уведомлений (`MailDispatcherService`, `mail_telegram_links`/`sms_telegram_links`) **по-прежнему определяется базовым членством** (`user_teams`): доп-команды **не расширяют** рассылку ([ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md) §7 — осознанная асимметрия; расширение — отдельное решение).

**Утечки имён команд нет.** `GET /api/auth/me` отдаёт `mail_teams`/`sms_teams` — команды, **чьи объекты пользователь и так видит**; это не расширение видимости, а способ наполнить фильтр без права `teams:view` ([TD-058](100-known-tech-debt.md) закрыт). Граница безопасности остаётся серверной.

## Системный якорь супер-админа (нормативно, ADR-051)

**Проблема, которую он решает.** Личное состояние с FK на `users` (личная прочитанность писем `mail_message_reads`, [ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md)) невозможно для принципала без строки в `users`. Владелец работает на проде под консольным супер-админом ⇒ [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) заводит ему **системную строку-якорь** в `users` (`is_system = true`, константный `SUPERADMIN_USER_ID`; поля — [03-data-model.md](03-data-model.md#системная-строка-якорь-супер-админа-adr-051)).

**Инвариант (нормативно): якорь — ИДЕНТИЧНОСТЬ, и только она.** Он **не** учётка, **не** источник прав, **не** способ входа, **не** канал доставки:

| Свойство | Как обеспечено |
|----------|----------------|
| **Вход по БД-ветке невозможен** | (1) `password_hash` — **locked** bcrypt-хэш случайного секрета (plaintext отброшен) ⇒ парольная ветка не совпадёт **никогда**, а `NULL` (беспарольный «открытый первый вход», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)) **запрещён** — иначе setup-token достался бы любому, кто назовёт `username` якоря; (2) резолверы `UserRepository` (`get_by_username`/`get_by_telegram`) якорь **не находят**. Две независимые преграды |
| **`username` якоря не подделать и не занять** | `superadmin@system` — символ `@` отвергается правилом `username` ([03-data-model.md](03-data-model.md#правило-username-кириллица-допускающее-нормативно)) ⇒ создать/переименоваться в такое имя через API **невозможно** (`422`) |
| **Права берутся НЕ из БД-роли якоря** | Роль якоря — заглушка под `NOT NULL` FK. Права супер-админа — `full_catalog_permissions()` по claim `superadmin=true`; правка роли `admin` в UI на его полномочия **не влияет** |
| **Невидим для админского API** | `GET /api/users` его не показывает; `PATCH`/`DELETE /api/users/{id}` по нему → **`404`**; лидером/участником команды — **`422`**; в `user_count` роли не входит (но держит FK ⇒ роль `admin` не удаляется: `409 role_in_use`) |
| **Telegram-SSO к нему не ведёт** | Telegram-привязка супер-админу **запрещена** (`403` на `POST /api/sms/telegram/link` и на `GET`/`PATCH /api/mail/me/settings`): иначе владение Telegram-аккаунтом стало бы беспарольным, **неотзываемым из UI** путём к admin-уровню в обход `ADMIN_PASSWORD`. Bootstrap-учётка — **console-only** |
| **Fallback-инвариант [ADR-008](adr/ADR-008-admin-iz-env.md) сохранён** | Логин и построение принципала супер-админа **не делают ни одного запроса в БД** (`user_id` — константа) ⇒ систему по-прежнему нельзя залочить через данные |
| **Смена `ADMIN_USER`/`ADMIN_PASSWORD` — no-op** | Идентичность привязана к константе `SUPERADMIN_USER_ID`, а не к логину: личное состояние (отметки прочитанности) переживает ротацию `.env` |

**Прочитанность — не граница безопасности.** Она не расширяет и не сужает видимость писем (`MailScope` не меняется): супер-админ отмечает прочитанным ровно то, что и так вправе прочитать. Поэтому она в запрет из строки «Telegram-SSO» **не входит**.

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
- **Telegram WebApp SDK — self-hosted (CSP `script-src 'self'` не ослабляется).** Официальный `telegram-web-app.js` с `telegram.org` блокируется CSP; SDK **вендорится** как статика своего origin (`/telegram-web-app.js`) и подключается как `script-src 'self'`. Внешних CDN/скриптов по-прежнему нет — [CSP](#csp) не изменяется.
- **Поверхность — нативные Telegram-webview.** iOS/Android/Desktop открывают Mini App в webview верхнего уровня — `frame-ancestors 'none'`/`X-Frame-Options: DENY` к ним не применяются. **Браузерный Telegram Web** (`web.telegram.org`, iframe) блокируется `frame-ancestors 'none'` — **подтверждённое пользователем ограничение** (native-only); глобальная CSP **не ослабляется** ([Q-SMS-1](99-open-questions.md); поддержка потребовала бы выделенного nginx-`location /tg/` c `frame-ancestors https://web.telegram.org` + снятия `X-Frame-Options: DENY`).
- **`init_data` не логируется** (содержит подпись/PII); супер-админ (`.env`) через SSO **не резолвится** (`403 sms_operator_not_provisioned`) и получателем доставок не является ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §7). С [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) это обеспечивается **не отсутствием строки**, а невидимостью системного якоря в резолверах `UserRepository` + запретом Telegram-привязки (`403`) — якорь не имеет и не может иметь `telegram`/линка.

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

**Frontend (память).** Раскрытое значение хранится **только** в локальном стейте компонента, который его показал (`SecretRevealField`); **не** кладётся в TanStack Query-кэш / Zustand; скрывается по повторному клику и сбрасывается при размонтировании (закрытие модалки / сворачивание блока). Кнопка-глаз рендерится только при `<page>:edit` (для прокси — дополнительно только при `has_password=true`; для бэка — только при `has_api_key`/`has_admin_api_key`).

**Что reveal НЕ ослабляет.** Обычные list/detail-ответы по-прежнему без секретов (сервер/прокси/ключ). `FERNET_KEY` из ответов не выводится. `MAIL_API_KEY`, `TELEGRAM_*`, `ADMIN_PASSWORD`, `JWT_SECRET` и прочие env-секреты reveal-эндпоинтами **не** раскрываются (это секреты окружения, не at-rest-секреты сущностей).

**Что раскрытию НЕ подлежит вовсе (нормативно, [ADR-067](adr/ADR-067-server-ssh-key-auth.md) §4).** Контур reveal покрывает **не каждый** at-rest-секрет сущности. **Приватный SSH-ключ сервера и его парольная фраза — write-only:** эндпоинтов `GET /api/servers/{id}/ssh-key` / `/ssh-key-passphrase` **не существует, и вводить их запрещено** (в т.ч. под admin-only гейтом — это создало бы канал эксфильтрации ради несуществующей потребности). Обоснование — [«Почему приватный ключ не раскрывается»](#почему-приватный-ключ-не-раскрывается-нормативно-adr-067-4). Правило «кнопка-глаз рендерится при `<page>:edit`» дополняется: **для не-раскрываемого секрета глаз не рендерится ни при каком праве**.

### Секреты в card-first UI (нормативно, [ADR-049](adr/ADR-049-servers-backends-card-first-detail.md) §4)

[ADR-049](adr/ADR-049-servers-backends-card-first-detail.md) переносит строки секретов **из свёрнутых блоков в видимую зону**: у **сервера** — «Пользователь»/«Пароль» в главный блок `ServerDetailModal`; у **бэка** — весь блок «Информация» (включая **API KEY**/**ADMIN API KEY**) **на карточку `BackendCard` в сетке страницы**, а `BackendDetailModal` упраздняется. **Ни одна гарантия выше при этом НЕ ослабляется** — меняется только **место строки-МАСКИ**, а не момент и условия раскрытия ЗНАЧЕНИЯ:

- **Преднагрузки нет.** `GET /api/servers` секретов не отдаёт вовсе; `GET /api/backends` отдаёт **только флаги** `has_api_key`/`has_admin_api_key`. Раскрытие блока «Информация» на карточке **не делает ни одного сетевого запроса** — рендерится маска `••••••••` по флагу.
- **Значение — только по клику на глаз**, по одному ресурсу за раз, через тот же per-resource reveal-эндпоинт под тем же гейтом `<page>:edit`, с тем же `Cache-Control: no-store` и той же аудит-записью `secret_revealed`.
- **⚠️ ЗАПРЕЩЕНО (нормативно): массовая преднагрузка секретов при раскрытии карточек.** Не допускаются: батч-эндпоинт раскрытия, `useQueries`/цикл reveal-запросов по карточкам сетки, авто-reveal при раскрытии блока, кнопка «раскрыть все». Раскрытие «Информации» у 20 бэков подряд обязано давать **ноль** обращений к reveal-эндпоинтам. Нарушение = дефект класса «утечка секретов», не UX-мелочь.

## Видимость номеров в `GET /api/teams/{id}/numbers` ([ADR-034](adr/ADR-034-teams-number-login-app.md))

Эндпоинт `GET /api/teams/{id}/numbers` (гейт `teams:view`) отдаёт схему `TeamNumberItem`. По [ADR-034](adr/ADR-034-teams-number-login-app.md) она включает `login` и `app_name` номеров команды (частичный разворот сужения [ADR-030](adr/ADR-030-sms-module-full-merge.md) §8).

- **Раскрывается под `teams:view`:** `phone_number`, `team`, `login`, `app_name`. Держатель `teams:view` видит эти поля номеров **любой** команды (эндпоинт под `teams:view`, не под SMS-scope).
- **НЕ раскрывается:** `note` (свободная заметка — может содержать чувствительный текст) и `label` (системный Twilio `friendly_name`) — только на странице «СМС» под матрицей `sms:*` и SMS-scope.
- **Трейдофф (осознанный):** `login`/`app_name` — слабо-чувствительный идентифицирующий контекст (какой аккаунт/приложение привязаны к номеру), **не** секрет: сами по себе доступа к аккаунту не дают (нет пароля/токена). Управление командами де-факто admin-ориентировано ([ADR-022](adr/ADR-022-teams-nav-categories.md) §3), `teams:view` — доверенная роль. У номера секрета нет (пароли/ключи к номерам не относятся).

## Видимость ящиков в `GET /api/teams/{id}/mailboxes` ([ADR-048](adr/ADR-048-teams-mailbox-count-mail-row.md))

Эндпоинт `GET /api/teams/{id}/mailboxes` (гейт `teams:view`, [ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §4) отдаёт схему `TeamMailboxItem`. По [ADR-048](adr/ADR-048-teams-mailbox-count-mail-row.md) §2 она расширена полями `number`/`app_name` («Номер»/«Приложение» ящика). Симметрично действует агрегат `TeamListItem.mailbox_count` в `GET /api/teams` (тот же гейт).

- **Раскрывается под `teams:view`:** `email`, `number`, `app_name`, `display_name`, `is_active`, а также число ящиков команды (`mailbox_count`). Держатель `teams:view` видит это по **любой** команде — `MailScope` на эндпоинтах `/api/teams/*` **не применяется** (scope команд у модуля `teams` нет: `teams:view` и так отдаёт все команды, сужать нечего).
- **НЕ раскрывается:** IMAP/SMTP-хосты/порты/логины, пароли/OAuth-токены (в схемах модуля «Почты» их нет вовсе — [Транзит IMAP/SMTP-кредов](#транзит-imapsmtp-кредов-mail-нормативно)), а также **операционный статус синка** (`last_synced_at`, `last_sync_error`, `consecutive_failures`) — он доступен только на странице «Почты» под матрицей `mail:*` и `MailScope`.
- **Трейдофф (осознанный).** Счётчик `mailbox_count` — производный агрегат **того же множества**, которое держатель `teams:view` уже видит списком (`/mailboxes` под `teams:view` — действующая норма [ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §4); нового раскрытия он не создаёт. `number`/`app_name` — слабо-чувствительный идентифицирующий контекст (какой номер/приложение привязаны к ящику), **не** секрет: доступа к ящику сами по себе не дают. Это ровно тот класс полей, который [ADR-034](adr/ADR-034-teams-number-login-app.md) уже разрешил под `teams:view` для номеров ([выше](#видимость-номеров-в-get-apiteamsidnumbers-adr-034)); `teams:view` — доверенная роль ([ADR-022](adr/ADR-022-teams-nav-categories.md) §3).

## Личная прочитанность писем ([ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md), [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md))

Прочитанность письма — **ЛИЧНАЯ у каждого пользователя** (таблица связи `mail_message_reads (user_id, message_id)`, [03-data-model.md](03-data-model.md#таблица-mail_message_reads-миграция-0025-adr-050)). Контракт: `MailMessage.is_unread` (персональное производное), `GET /api/mail/messages?unread=`, `POST`/`DELETE /api/mail/messages/{id}/read` → `204`.

- **Гейт — `mail:view`** (нового права/действия в `CATALOG["mail"]` **не вводится**). Обоснование: отметка — личный артефакт **чтения**, а не мутация домена: она не меняет ни письмо, ни ящик, ни то, что видят другие. Прецедент в том же модуле: **`reply` — тоже под `view`**, хотя отправляет письмо наружу; требовать `mail:edit` для отметки собственного прочтения было бы строже, чем для ответа адресату.
- **Граница — серверный `MailScope`** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §7), тот же, что у ленты и reply: письмо обязано принадлежать ящику с `team_id ∈ MailScope.team_ids` (для не-admin), иначе — **`404 mail_message_not_found`**. **Анти-энумерация сохраняется:** чужое письмо **неотличимо от несуществующего** (тот же код, что у reply) — по ответу `POST …/read` нельзя узнать, существует ли письмо с данным `id`. Отметить чужое письмо нельзя.
- **Межпользовательская утечка исключена конструктивно.** `user_id` для записи/чтения берётся **только** из `Principal` (claim `uid`), **никогда** из тела/query запроса — параметра «за кого отметить» в контракте нет. Пользователь не может ни прочитать, ни изменить состояние прочитанности другого: `is_unread` вычисляется исключительно для принципала запроса, а `read_at` наружу **не отдаётся вовсе** (иначе раскрывалось бы «когда коллега прочитал письмо» — лишний, никем не запрошенный сигнал).
- **Супер-админ из `.env`** ([ADR-008](adr/ADR-008-admin-iz-env.md)) — **личная прочитанность работает и под ним** ([ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md); норма [ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2.5 отменена): его `Principal.user_id` = константный `SUPERADMIN_USER_ID` системной строки-якоря ⇒ FK `mail_message_reads.user_id` выполним, `POST`/`DELETE …/read` → **`204`**, `is_unread` — реальное личное значение, `unread=true` — обычный фильтр, UI-контролы **рендерятся**. **`GET`/`PATCH /api/mail/me/settings` — по-прежнему `403`**, но по security-основанию (запрет Telegram-привязки, [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md) §1.6), а не из-за отсутствия строки.
- **Telegram Mini App `/tg/mail`** ходит в **те же** эндпоинты под **обычным CRM access-JWT** с `uid` реального пользователя (SSO по HMAC `initData` → `issue_access_token(uid=…)`, [Telegram-эндпоинты почты](#telegram-эндпоинты-почты-нормативно)). Никакого отдельного «SSO-принципала» с ослабленными проверками нет: `require("mail","view")` + `MailScope` применяются **идентично** вебу. Отдельный эндпоинт отметки для Mini App **не вводится**.
- **Прочитанность — не граница безопасности, а личное состояние.** Она не расширяет и не сужает видимость писем: пользователь может отметить прочитанным ровно то, что он и так вправе прочитать.

## Ansible и секреты

- **Пароль** передаётся в Ansible через inventory/`extravars` **в памяти** ansible-runner, не через файлы на диске.
- **Приватный ключ** ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §5) физически требует файла (`ansible_ssh_private_key_file`) — поэтому нормируется его жизненный цикл:
  - парольная фраза **снимается в памяти** (ключ пере-сериализуется в незашифрованный OpenSSH-PEM) ⇒ ни `ssh-agent`, ни `SSH_ASKPASS`, ни интерактивный ввод не нужны. Штатный `ansible_runner.run(ssh_key=…)` **не используется**: на зашифрованном ключе он виснет на приглашении `ssh-add` до `job_timeout`, и добавление сервера превращается в ложный `provisioning timeout`;
  - файл создаётся **атомарно с правами `0600`** (`os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)` — не `open()` + `chmod`, иначе есть окно с umask-правами) внутри временного `private_data_dir` (`mkdtemp` → `0700`);
  - удаление — явный `os.remove` файла ключа **до** `shutil.rmtree` каталога, оба в `finally`;
  - **`private_data_dir` создаётся в выделенном `ANSIBLE_PRIVATE_DATA_ROOT`** (`tempfile.mkdtemp(dir=…)`, default `/var/run/crm/ansible`), который в проде смонтирован **`tmpfs`** ⇒ расшифрованный ключ не касается постоянного диска, не попадает в volume'ы и бэкапы. **⛔ `mode` этого tmpfs — `0o1777`, а не `1700`, не голое `1777` и не значение в кавычках:** backend работает под non-root (`USER app`), Docker монтирует tmpfs `root:root`, и если в others нет `w`, `mkdtemp` падает с `PermissionError`, **ломая весь провижининг, включая парольную ветку**. Поле `tmpfs.mode` — числовое (`uint32`): значение **в кавычках** отвергается на разборе (`docker compose config`/`up` падают ⇒ стек не поднимается вовсе), а голое `mode: 1777` разбирается **десятично** ⇒ режим `0o3361` — тот же отказ прав; комментарий «восьмерично» парсер не читает. Ключ защищают права вложенного каталога (`0700`) и файла (`0600`). **⛔ Монтировать tmpfs на `/tmp` не следует** — там Starlette спуливает загружаемые файлы (изображения документов до 5 МБ), и общий раздел кончился бы. Нормы и сниппеты — [07-deployment.md](07-deployment.md#tmpfs-для-приватных-данных-ansible-нормативно-adr-067-5);
  - в inventory уходит **только путь** к файлу — печатать его безопасно, материал в inventory/extravars/логи не попадает;
  - остаточный риск (ключ существует файлом на время прогона) принят — [TD-073](100-known-tech-debt.md).
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
- **nginx (`add_header ... always`)** — на ответы SPA (**ВСЕ `location`, раздающие SPA-статику: `/` И `/assets/`**; статику отдаёт nginx, backend не участвует): те же `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` + **`Content-Security-Policy`** (см. ниже). HSTS на проде также может ставиться на уровне `proxy`/TLS-терминатора единожды.
- **Без дублей МЕЖДУ ЗОНАМИ:** `/api/*` отдаёт backend (nginx в `location /api` security-заголовки НЕ добавляет), SPA-статику отдаёт nginx. Зоны не пересекаются — двойных заголовков нет.
- **⚠️ ВНУТРИ nginx-зоны дублирование ОБЯЗАТЕЛЬНО (нормативно, [ADR-046](adr/ADR-046-ui-infra-fix-pack.md) §4.4).** SPA раздаётся **двумя** блоками — `location /` (HTML + нехешированные корневые ассеты, `Cache-Control: no-cache`) и `location /assets/` (хешированные ассеты сборки, `Cache-Control: public, max-age=31536000, immutable`). В nginx **`add_header` НЕ наследуется** в блок, который объявляет собственные `add_header` (наследование не аддитивное, а «замещающее на уровне блока»), поэтому **`location /assets/` обязан продублировать ВСЕ 4 security-заголовка + HSTS + CSP** — **побайтово** те же значения, что и `location /`, все с модификатором `always`. Пропуск дубля **молча снимет** CSP/HSTS/`X-Frame-Options`/`Referrer-Policy` с ответов на бандлы SPA. Точный сниппет — [07-deployment.md §Reverse-proxy](07-deployment.md#reverse-proxy-nginx--требования).
  - **Исключение — только `Cache-Control` в `/assets/`: он задаётся БЕЗ `always`** (immutable-кэш обязан покрывать лишь успешную отдачу существующего ассета, но не `404` битого выката). Security-заголовки и CSP там — **с `always`** (обязаны покрывать и не-2xx). Обоснование — [ADR-046](adr/ADR-046-ui-infra-fix-pack.md) §4.4.
- CORS: разрешён только origin фронтенда (`CORS_ALLOW_ORIGINS` из `.env`); на проде SPA и API за одним origin — CORS можно не открывать.

<a id="csp"></a>

### Content-Security-Policy (SPA-статика: `location /` И `location /assets/`)

Точное нормативное значение (должно **побайтово** совпадать с конфигом nginx **в КАЖДОМ** SPA-`location` — и в `/`, и в `/assets/`; см. правило дублирования выше):

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Обоснование директив:

| Директива | Значение | Причина |
|-----------|----------|---------|
| `default-src` | `'self'` | База: всё только со своего origin (внешних доменов нет) |
| `script-src` | `'self'` | Скрипты только из собранной статики SPA; **inline-скриптов нет** — включая no-FOUC-скрипт темы ([ADR-046](adr/ADR-046-ui-infra-fix-pack.md) §4.1: он **вынесен из inline в отдельный статический файл** своего origin, подключается как обычный `<script src>`). **`'unsafe-inline'` в `script-src` НЕ вводится ни при каких обстоятельствах**, nonce/hash тоже (nginx отдаёт статику — nonce негде генерировать; hash инвалидируется любой правкой скрипта). Тот же приём — self-hosted `telegram-web-app.js`. Инвариант держит и XSS-изоляцию HTML-тела письма ([ADR-012](adr/ADR-012-mail-read-through-proxy.md)/[ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) |
| `style-src` | `'self' 'unsafe-inline'` | Tailwind/Radix используют inline-стили (`style=...`) — без `'unsafe-inline'` UI ломается. `'unsafe-inline'` для стилей — осознанный компромисс ([TD-012](100-known-tech-debt.md)) |
| `img-src` | `'self' data: https: blob:` | Иконки/инлайн-SVG, data:-изображения, **удалённые (remote https) изображения писем** и **`blob:`-URL вложений документов** ([ADR-068](adr/ADR-068-documents-image-attachments.md) §3: картинка документа грузится авторизованным `fetch` и подставляется как `blob:` — `<img src="/api/…">` ушёл бы **без** `Authorization`, а анонимная раздача означала бы утечку в обход per-node видимости). `blob:` — **пассивный same-origin** источник, созданный нашим же JS из уже полученных под JWT данных; внешним источником не является, `script-src 'self'` не затрагивает. `https:` добавлен для отрисовки картинок в HTML-теле писем (sandbox-iframe наследует CSP страницы и не может ослабить её через meta) — [ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md). Только `https:` (не `http:`/`*`); грузятся **пассивные** `<img>`, скрипты остаются заблокированы (sandbox без `allow-scripts`, `script-src 'self'` не тронут). Компромисс — допускаются трекинг-пиксели (отправитель видит факт открытия письма); referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe). `cid:`-инлайн-картинки не резолвятся ([TD-026](100-known-tech-debt.md)) |
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
| SSH-пароли серверов | БД (`servers.ssh_password_encrypted`, **nullable** с [ADR-067](adr/ADR-067-server-ssh-key-auth.md)) | заполнен ⇔ `auth_method='password'`; вводится при создании, шифруется `FERNET_KEY`; не в env/логах/обычных ответах; plaintext — только провижининг in-memory **или** on-demand reveal под `servers:edit` ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)); `ssh_user`/`auth_method` — не секреты |
| **Приватные SSH-ключи серверов** | БД (`servers.ssh_private_key_encrypted`) | заполнен ⇔ `auth_method='key'` ([ADR-067](adr/ADR-067-server-ssh-key-auth.md)); шифруется тем же `FERNET_KEY`; **write-only — reveal-эндпоинта НЕТ**; plaintext существует только (а) в памяти провижининга и (б) файлом `0600` во временном `private_data_dir` на `tmpfs`, смонтированном на `ANSIBLE_PRIVATE_DATA_ROOT` (⛔ не `/tmp`), на время прогона плейбука, удаляется в `finally` ([TD-073](100-known-tech-debt.md)) |
| **Парольные фразы SSH-ключей** | БД (`servers.ssh_key_passphrase_encrypted`) | опциональна, только вместе с ключом; **write-only — reveal-эндпоинта НЕТ**; **не покидает процесс backend вовсе** — снимается с ключа в памяти, в Ansible/inventory/env/логи не передаётся ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §5) |
| API-ключи бэка (`api_key`/`admin_api_key`) | БД (`backends.api_key_encrypted`/`admin_api_key_encrypted`) | опциональны; вводятся через API, шифруются `FERNET_KEY`; не в env/логах/обычных ответах (в API — только `has_api_key`/`has_admin_api_key`); plaintext — только on-demand reveal под `backends:edit` ([ADR-040](adr/ADR-040-backend-relations-secrets-reverse-lookup.md), [ADR-035](adr/ADR-035-detail-view-secret-reveal.md)); `git`/`note`/`server_id`/`ai_key_id` — не секреты |
| `MAIL_API_KEY` | `.env` | секрет внешнего почтового API; только в заголовке `X-API-Key` backend→`postapp.store`; не в БД/логах/ответах/SPA/URL ([modules/mail](modules/mail/README.md)) |
| `MAIL_PUSH_SECRET` | `.env` | ключ **HMAC** push-контракта агрегатор→CRM (`/api/mail/ingest`, `/mailbox-status`, `/oauth/ingest`) и подписи stateless `crm_state` OAuth-потока ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §3, [ADR-045](adr/ADR-045-mail-outlook-oauth-headless-reonboarding.md)); общий с агрегатором; пустой ⇒ приёмники выключены (`503 mail_ingest_not_configured`) |
| `DOCUMENTS_API_KEY` | `.env` | статический ключ внешнего **read-only** API документов (RAG); входящий `X-API-Key` сверяется constant-time (`hmac.compare_digest`); не в БД/логах/ответах/SPA/URL; ротация через деплой; пустой ⇒ внешний контур выключен (`503 documents_external_not_configured`) ([ADR-060](adr/ADR-060-documents-external-readonly-api-key.md), [modules/documents](modules/documents/README.md)) |
| Токены и webhook-секреты 5 Telegram-ботов почты (`MAIL_BOT_TOKEN`/`MAIL_BOT_WEBHOOK_SECRET`, `MAIL_BOT_<NAME>_TOKEN`/`_WEBHOOK_SECRET`) | `.env` | основной бот + 4 push-бота команд ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §9); не в БД/логах/ответах/SPA; несовпадение секрета вебхука → `404` (анти-энумерация) |
| IMAP/SMTP-пароли ящиков | **НЕ в CRM** | **транзитом** в агрегатор (шифрование AES-256-GCM там); Fernet CRM к почте не применяется; не логируются, не возвращаются ([Транзит IMAP/SMTP-кредов](#транзит-imapsmtp-кредов-mail-нормативно)) |

- `.env` — в `.gitignore`; в репозитории только `.env.example` без значений.
- Логи проходят через structlog с фильтром секретов (пароли, токены, ключи маскируются).

## Модель угроз (Этап 1)

| Угроза | Митигация |
|--------|-----------|
| Перебор пароля админа | rate-limit + constant-time сравнение |
| Кража JWT через XSS | токен в `localStorage` (персист-сессия/мульти-вкладка, [ADR-041](adr/ADR-041-login-theme-session-ux.md)) — осознанно принятая расширенная поверхность; митигации: строгая CSP (`default-src 'self'`), нет сторонних скриптов, экранирование React, 24-часовой TTL JWT, полная очистка `crm.auth.*` на `401`/logout |
| Утечка SSH-паролей из БД | Fernet at-rest, ключ вне БД |
| Утечка **приватного SSH-ключа** сервера (радиус — весь парк хостов) | Fernet at-rest (`ssh_private_key_encrypted`), ключ вне БД; **reveal-эндпоинта НЕТ** — ключ и парольная фраза write-only ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §4); passphrase не покидает процесс backend; в ответах API — только `auth_method` |
| Расшифрованный SSH-ключ на диске во время провижининга | Файл `0600` (создан `O_CREAT\|O_EXCL`) в каталоге `mkdtemp` `0700` внутри **`ANSIBLE_PRIVATE_DATA_ROOT` на `tmpfs`** (не постоянный диск, не volume, не бэкап; `mode: 0o1777` у корня — поле числовое: значение в кавычках отвергается на разборе и роняет весь стек, а голое `1777` читается десятичным (`0o3361`); иначе non-root `app` не создаст каталог и провижининг умрёт целиком), удаление в `finally`; окно ограничено `ANSIBLE_TIMEOUT_SEC`. Остаточный риск принят — [TD-073](100-known-tech-debt.md) |
| Утечка материала ключа через текст ошибки разбора | Исключение `cryptography` **не пробрасывается** ни в ответ, ни в лог; сообщения `422` фиксированы контрактом ([04-api.md](04-api.md#post-apiservers)) |
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
| Энумерация чужих писем/ящиков через `/mail` | серверный **`MailScope(sees_all_teams, team_ids, includes_unassigned)`** ([ADR-044](adr/ADR-044-mail-full-merge-into-crm.md) §7 в редакции [ADR-055](adr/ADR-055-per-channel-teams-mail-sms.md); **поля `group_ids` нет**): `team_ids` — **`user_teams` ∪ доп-команды канала `mail`**; `includes_unassigned` — доступ к ящикам без команды; владение ящиком — `mail_accounts.team_id`. **Чтение** вне scope → **пустой результат без выборки писем** (чужой/несуществующий `mail_account_id`/`team_id` не попадает в пересечение → пустая страница; чужой ящик **неотличим от несуществующего**). **Reply** на чужое письмо → `404` (не `403`). **Отметка прочитанности** (`POST`/`DELETE /api/mail/messages/{id}/read`, [ADR-050](adr/ADR-050-mail-search-team-filter-personal-read-state.md)) на чужое/несуществующее письмо → `404 mail_message_not_found` (та же анти-энумерация, что у reply); `user_id` берётся **только** из `Principal` — параметра «за кого отметить» в контракте нет ⇒ прочитать/изменить чужое состояние прочитанности невозможно, `read_at` наружу не отдаётся. **Создание** ящика — только для своей команды (`team_id ∈ team_ids`), `team_id = null` — только admin; иначе `403`. **Перенос** ящика между командами — только admin-уровень (`sees_all_teams`), не членство, иначе `403`. Мутация/удаление/синк по `id` вне scope → `403`. Теги глобальны (scope не применяется, управление — под `mail:tags`). Фронт-гейтинг только UX; граница — backend |
| Подделка push'а писем/статуса ящика (`/api/mail/ingest`, `/mailbox-status`, `/oauth/ingest`) | **HMAC-SHA256 над сырым телом** + timestamp-окно (`MAIL_PUSH_SECRET`, `MAIL_PUSH_MAX_SKEW_SEC=300`), constant-time сравнение; пустой секрет → приёмник выключен (`503`). Окно — не полноценный анти-replay; повтор гасится **идемпотентностью** приёмника (`ON CONFLICT DO NOTHING`/upsert) — [Push-контракт](#push-контракт-агрегатор--crm-hmac-нормативно) |
| Подмена личности в Mini App `/tg/mail` | **Проверка HMAC-подписи Telegram `initData`** ключом бота + TTL (`MAIL_TG_INITDATA_TTL_SEC`); факту «открыто из Telegram» не доверяем. Резолв по иммутабельному `telegram_user_id`, иначе bootstrap по `users.telegram` (ci). Не сопоставлен → `403 mail_operator_not_provisioned`. Остаточный риск подмены ника **до** первого линка — как у `/tg/sms` ([ADR-031](adr/ADR-031-sms-operator-mini-app.md)) |
| XSS/кража JWT через HTML-тело письма | рендер только в sandbox-iframe (без `allow-scripts`/`allow-same-origin`), CSP SPA; скрипты письма не исполняются. `img-src ... https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) разрешает только пассивные `<img>` — XSS-инвариант не ослаблен ([ADR-012](adr/ADR-012-mail-read-through-proxy.md)) |
| Трекинг-пиксели в письме (отправитель узнаёт факт открытия) | принятый компромисс remote-картинок ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) — стандартно для почтовых клиентов; referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe), только `https:` (не `http:`); анти-трекинг-прокси отложён |
| Энумерация чужих документов через `/api/documents` | серверный per-node фильтр видимости по роли ([ADR-059](adr/ADR-059-documents-module.md)): невидимый узел → **`404 document_node_not_found`** (не `403`, неотличим от несуществующего), списки/дерево — фильтруются (пустой результат); enforcement permission-based (`owner_id` — не гейт); admin-уровень (`is_superadmin OR permissions_subset(full_catalog, permissions)`) видит всё. Фронт-гейтинг — только UX |
| Утечка изображения документа в обход видимости узла | `GET /api/documents/attachments/{id}` — гейт `documents:view` + **тот же CTE видимости узла-владельца**; единый `404 document_attachment_not_found` на «нет/невидим/удалён» (анти-энумерация); **анонимная раздача и токен в query запрещены** ⇒ клиент грузит картинку авторизованным `fetch` + `blob:`; `Cache-Control: private` (не `public` — иначе shared-кэш отдал бы её постороннему) ([ADR-068](adr/ADR-068-documents-image-attachments.md)) |
| Path traversal / перезапись файлов через загрузку вложения | Путь строится **только** из UUID + расширения из `mime` (whitelist); пользовательский `filename` в пути не участвует **вовсе**; defensive `realpath`-containment; запись атомарная (temp + `os.replace`) в каталог `0700`, файлы `0600` |
| XSS через загруженный «образ» (SVG/HTML под видом картинки) | Тип определяется по **magic bytes**, а не по `Content-Type` клиента; whitelist ровно `png/jpeg/webp/gif`; **SVG исключён нормативно**; `Content-Type` отдачи — из БД; `nosniff` на всём `/api` ([ADR-068](adr/ADR-068-documents-image-attachments.md)) |
| Утечка `DOCUMENTS_API_KEY` (чтение всего корпуса документов) | ключ обходит per-role фильтр (машина видит всё) ⇒ read-only; хранение только `.env` (не в БД/логах/ответах/SPA/URL, structlog-фильтр), HTTPS, ротация деплоем; сравнение constant-time; пустой ключ → внешний контур выключен (`503`). Внешний роутер регистрирует **только GET** (нет write-эндпоинтов) ([ADR-060](adr/ADR-060-documents-external-readonly-api-key.md)) |

## Вне scope безопасности

- Многофакторная аутентификация, OAuth/SSO.
- ~~RBAC (одна роль — админ)~~ — **реализован** в Спринте 3 ([ADR-021](adr/ADR-021-rbac-users-roles.md)): роли + права на все страницы, серверный enforcement, bcrypt-хэш паролей БД-пользователей, `.env`-супер-админ как bootstrap.
- Аудит-лог действий пользователей ([TD-001](100-known-tech-debt.md)).
- UI-смена пароля супер-админа (`.env`) — by design только через `.env`/деплой; UI-управление паролями есть для БД-пользователей ([TD-009](100-known-tech-debt.md)).
- Refresh-токены, отзыв конкретного токена, история сессий.
