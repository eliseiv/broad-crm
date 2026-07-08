# Модуль `auth` — Аутентификация

Статус: `implemented` (Спринт 3, [ADR-021](../../adr/ADR-021-rbac-users-roles.md)) · Исполнители: backend, frontend

## Scope
Двухшаговый вход, защита API через JWT **и RBAC** (роли, права на все страницы, управление пользователями/ролями). Супер-админ — из `.env` ([ADR-008](../../adr/ADR-008-admin-iz-env.md) + амендмент [ADR-021](../../adr/ADR-021-rbac-users-roles.md)); дополнительные пользователи/роли — в БД. Поток входа — [ADR-002](../../adr/ADR-002-dvuhshagovyy-auth.md); RBAC — [ADR-021](../../adr/ADR-021-rbac-users-roles.md).

## Out of scope
Refresh-токены, отзыв конкретного токена, история сессий, OAuth/SSO, MFA, UI-смена пароля супер-админа (`.env`), аудит-лог действий ([TD-001](../../100-known-tech-debt.md)).

## RBAC — ТЗ (Спринт 3, [ADR-021](../../adr/ADR-021-rbac-users-roles.md))

### Модель и каталог прав
- Каталог прав — серверная константа `app/domain/permissions.py::CATALOG`: `dashboard:[view]`; `servers`/`ai-keys`/`proxies`/`backends:[view,create,edit,delete]`; `mail:[view]`; **`roles`/`teams:[view,create,edit,delete]`** ([ADR-022](../../adr/ADR-022-teams-nav-categories.md)). Порядок ключей = порядок строк матрицы: `dashboard, servers, ai-keys, proxies, backends, mail, roles, teams`. Страница «Пользователи» (`users`) — **вне каталога** (admin-only). Каталог отдаётся `GET /api/permissions/catalog` — гейт **`require("roles","view")`** (со Спринта A; было `require_admin`).
- Модель БД — `roles`/`users` ([03-data-model.md](../../03-data-model.md#таблицы-roles-и-users-rbac)), миграция `0008_create_users_roles` (`down_revision=0007_create_backends`, сид роли `admin`). Со Спринта A ([ADR-022](../../adr/ADR-022-teams-nav-categories.md)): `users += email` (миграция `0010_add_user_email`, `down_revision=0009_create_teams`), CRM-команды `teams`/`user_teams` (миграция `0009_create_teams`, модуль [teams](../teams/README.md)). Миграция `0010` также **обновляет seed-роль `admin`** до полного нового каталога (добавляет `roles`/`teams`).

### Auth-поток и JWT (claim'ы — побуквенно)
1. Логин: **сперва** супер-админ (`.env`, constant-time) → JWT `sub=ADMIN_USER, role="admin", superadmin=true` (без `uid`); **иначе** БД-пользователь (`verify_password` bcrypt, `is_active=true`) → JWT `sub=username, uid=users.id, role=role.name, superadmin=false`.
2. Расширить `app/infra/jwt.py`: payload несёт `sub`, `uid?`, `role`, `superadmin`, плюс прежние `iat`, `exp`, `type:"access"`. `decode_access_token` возвращает полный payload (не только `sub`).
3. Пароли БД-пользователей — `app/infra/passwords.py` (`hash_password`/`verify_password`, bcrypt), [05-security.md](../../05-security.md#хэширование-паролей-bcrypt).

### Enforcement
- `get_current_principal` (в `app/api/deps.py`) декодит JWT и **грузит актуальные права из БД** каждый запрос: супер-админ → полный каталог; БД-пользователь → `role.permissions` по `uid` (если нет пользователя/`is_active=false` → `401`). Возвращает `Principal(username, role, permissions, is_superadmin)`.
- `require(page, action)` → `Principal` или `403 forbidden()` (новый в `app/errors.py`). Применить ко **всем** ресурсным роутерам вместо `_user: CurrentUser`, а также к `/api/roles`, `/api/teams` и `GET /api/permissions/catalog` ([ADR-022](../../adr/ADR-022-teams-nav-categories.md); маппинг метод→действие — [04-api.md](../../04-api.md#rbac-и-enforcement-прав)).
- `require_admin` (`is_superadmin || role=="admin"`) → со Спринта A гейтит **только Users API** (`/api/roles`, `/api/teams`, каталог переведены на матрицу `roles:*`/`teams:*`).
- `GET /api/auth/me` — `{username, role, is_superadmin, permissions}` (permissions теперь может содержать ключи `roles`/`teams`).

### Users / Roles / Teams API
- `GET/POST/PATCH/DELETE /api/users` (гейт `require_admin`) — схемы/поля/коды строго по [04-api.md](../../04-api.md#users). Со Спринта A: `UserCreateRequest`/`UserUpdateRequest` += `email?` (валидный формат → `422`, дубль → `409 email_taken`) и `team_ids?` (существующие CRM-команды → иначе `422`); `UserListItem` += `email`, `teams` (CRM-команды). `DELETE` пользователя-лидера команды → `409 user_is_team_leader`. Пароль — только на вход; уникальность `username` → `409`.
- `GET/POST/PATCH/DELETE /api/roles` — гейт **`require("roles", <action>)`** (было `require_admin`); `RoleListItem` += `user_count` (`COUNT(users) GROUP BY role_id`). Схемы/коды — [04-api.md](../../04-api.md#roles).
- `GET/POST/PATCH/DELETE /api/teams` — гейт `require("teams", <action>)`; модуль [teams](../teams/README.md), контракт — [04-api.md](../../04-api.md#teams).

### Security-инвариант эскалации ролей (нормативно, [ADR-022](../../adr/ADR-022-teams-nav-categories.md))
Раз `/api/roles` под матрицей `roles:*`, backend ОБЯЗАН запрещать эскалацию (проверка в handler после гейта; `403 forbidden` — единственная граница):
- **(а) subset:** не-супер-админ/не-`admin` не может создать/изменить роль с `permissions ⊄ permissions актора` (по каждой `page` набор `actions` — подмножество). Нарушение → `403`.
- **(б) защита `admin`:** роль `name=="admin"` меняет/удаляет только `is_superadmin || role=="admin"` → иначе `403`.
- **(в)** назначение ролей пользователям и управление учётками остаётся под `require_admin` (Users API вне матрицы) — замыкает эскалацию.

Прецеденция `POST`/`PATCH /api/roles`: каталожная валидация (`422`) → эскалация/защита `admin` (`403`) → уникальность имени (`409`). Детали — [05-security.md](../../05-security.md#security-инвариант-эскалации-привилегий-нормативно-adr-022), [04-api.md](../../04-api.md#roles).

## Backend — ТЗ

### Endpoints (контракт — [04-api.md](../../04-api.md#auth))
- `POST /api/auth/login {username,password}` → `200 {access_token,token_type,expires_in}` | `401 invalid_credentials` | `400 validation_error` | `429 rate_limited`. Две ветки проверки (сперва `.env`-супер-админ, затем БД-пользователь bcrypt+`is_active`) — [05-security.md](../../05-security.md#аутентификация-логин-и-выпуск-jwt).
- `GET /api/auth/me` (JWT) → `200 MeResponse {username, role, is_superadmin, permissions}` | `401 unauthorized` ([04-api.md](../../04-api.md#get-apiauthme)).

### Требования
1. Креды из настроек (`ADMIN_USER`, `ADMIN_PASSWORD`) через pydantic-settings.
2. Сравнение логина и пароля — `secrets.compare_digest` (constant-time), оба сравнения выполняются всегда (без раннего возврата), чтобы не было timing-разницы.
3. JWT: HS256, `JWT_SECRET`, claims `sub`, `iat`, `exp`, `type:"access"`, TTL `JWT_EXPIRES_MIN` (1440 мин / 24 ч, [05-security.md](../../05-security.md#jwt)).
4. FastAPI-dependency `get_current_principal` (базовый JWT-decode) валидирует токен для всех роутеров, кроме `/api/auth/login` и `/api/health`; невалидный/просроченный/легаси-токен → `401 unauthorized`. **Защита ресурсных роутеров — через фабрику `require(page, action)`** (нет права → `403 forbidden`), Users/Roles/Permissions — через `require_admin`; прежний «любой аутентифицированный» (`get_current_user`/`_user: CurrentUser`) заменяется на `require(...)`. Полная модель и маппинг метод→действие — [RBAC — ТЗ](#rbac--тз-спринт-3-adr-021), [04-api.md](../../04-api.md#rbac-и-enforcement-прав).
5. Rate-limit на `/api/auth/login`: по IP, 10 попыток / 5 мин (in-memory на Этапе 1), превышение → `429 rate_limited`.
6. Единое сообщение об ошибке для неверного логина и/или пароля.
7. Логи аутентификации — без паролей/токенов (structlog маскирование).

## Frontend — ТЗ
1. Роуты: `/login` (двухшаговый), `/servers` (защищён).
2. Шаг 1 — поле «Логин» + «Далее» (клиентский переход, без запроса). Шаг 2 — показ логина + «назад», поле «Пароль» + «Войти» → `POST /api/auth/login`.
3. Хранение access-токена — в памяти (Zustand); допустимо `sessionStorage` для переживания перезагрузки. НЕ `localStorage` ([05-security.md](../../05-security.md)).
4. Все запросы к `/api/*` шлют `Authorization: Bearer`. На `401` — сброс сессии и редирект на `/login`.
5. Ошибка входа → единое сообщение «Неверный логин или пароль», без раскрытия деталей; shake-анимация (учитывать `prefers-reduced-motion`).
6. UI экрана входа — [08-design-system.md](../../08-design-system.md#экран-входа-двухшаговый).

## DoD
- [x] Endpoints соответствуют [04-api.md](../../04-api.md) (auth + Users/Roles/Permissions API).
- [x] Тесты auth+RBAC (unit+интеграция) из [06-testing-strategy.md](../../06-testing-strategy.md) зелёные (backend 665/665, cov 89.97 % — на пороге coverage-гейта).
- [x] Нет секретов в логах; пароли БД-пользователей — bcrypt, plaintext не хранится/не логируется/не возвращается.
- [x] Двухшаговый UI работает, защита роутов и обработка 401; серверный RBAC-enforcement (`403 forbidden`) на всех ресурсных роутерах, UI-гейтинг вкладок/кнопок по правам.

## Changelog
- 2026-07-08: **Спринт A** ([ADR-022](../../adr/ADR-022-teams-nav-categories.md)): каталог прав += `roles`/`teams`; `/api/roles` и `GET /api/permissions/catalog` переведены с `require_admin` на матрицу `roles:*`; `require_admin` оставлен только на Users API; security-инвариант эскалации ролей (subset + защита `admin`); `users += email`; `UserCreateRequest`/`UserListItem` += `email`/`team_ids`/`teams`; `RoleListItem += user_count`; миграции `0009_create_teams`/`0010_add_user_email` (обновление seed-роли `admin`). Модуль CRM-команд — [teams](../teams/README.md).
- 2026-06-28: спецификация создана (architect, bootstrap).
- 2026-07-07: TTL JWT увеличен с 60 до **1440 мин (24 ч)** по запросу пользователя (`JWT_EXPIRES_MIN`); обоснование и trade-off — [05-security.md](../../05-security.md#jwt).
- 2026-07-07: **Спринт 3 — RBAC** ([ADR-021](../../adr/ADR-021-rbac-users-roles.md)): пользователи+роли+права на все страницы, `.env`-админ → супер-админ вне БД, JWT `role`/`uid`/`superadmin`, enforcement `require(page,action)`/`require_admin`/`403 forbidden`, bcrypt-хэш паролей, Users/Roles/Permissions API. Из out-of-scope сняты «многопользовательский режим, RBAC».
- 2026-07-08: статус модуля → **`implemented`** (Спринт 3, [ADR-021](../../adr/ADR-021-rbac-users-roles.md)). backend 665/665 (cov 89.97 %), frontend 224/224, reviewer — approve/production_ready. DoD-чеклист закрыт. Остаточный долг: UI-смена пароля супер-админа (`.env`) — [TD-009](../../100-known-tech-debt.md) (by design, bootstrap); аудит-лог админских действий — [TD-001](../../100-known-tech-debt.md); per-request-кэш прав роли — будущая оптимизация ([ADR-021](../../adr/ADR-021-rbac-users-roles.md#последствия)).
