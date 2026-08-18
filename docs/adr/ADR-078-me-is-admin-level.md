# ADR-078 — `is_admin_level` в `GET /api/auth/me`: клиент не пересчитывает покрытие каталога

- **Статус:** implemented (сверка состава 2026-08-17: `backend/app/schemas/auth.py`, `backend/app/api/auth.py`, `frontend/src/store/auth.ts`, `frontend/src/features/auth/hooks.ts`, `frontend/src/routes/AdminRoute.tsx`, `frontend/src/routes/DefaultRoute.tsx`, `frontend/src/components/AppLayout.tsx`, `frontend/src/pages/DocumentsPage.tsx`; `adminLevel.ts` удалён; deploy frontend `f29c6e1`; in-process CI run 32059875139 — фильтр `role.name == «Админ»`, вывод в §Verification; ветки `role==admin` и `is_superadmin` run не покрывал; UX e2e Users nav + «Сменить видимость» — не проверялась)
- **Дата:** 2026-08-17
- **Контекст-модули:** [auth](../modules/auth/README.md), [documents](../modules/documents/README.md)
- **Связано:** [ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4–5, [ADR-036](ADR-036-sms-team-filter-admin-only.md), [ADR-021](ADR-021-rbac-users-roles.md), [ADR-032](ADR-032-sms-visibility-admin-full-catalog.md), [ADR-059](ADR-059-documents-module.md)

## Context

[ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4 перевёл серверный `require_admin` на `is_admin_level` (`is_superadmin OR role=="admin" OR permissions_subset(full_catalog, permissions)`). Кириллическая роль «Админ» с полным jsonb проходит гейт Users API.

§5 того же ADR предписал **клиенту** считать тот же предикат сам: `GET /api/permissions/catalog` + `coversFullCatalog` (`frontend/src/features/auth/adminLevel.ts`, сверка 2026-08-17). Каталог грузится только при `roles:view`; ошибка каталога → `isAdmin=false` (deny); пока каталог в полёте — `catalogPending` (Spinner вместо пункта «Пользователи»).

На проде jsonb роли «Админ» уже покрывает полный `CATALOG` (включая `documents.share` и `broadcast`), backend `is_admin_level` — `True`. Пользователь всё равно не видит «Пользователи» и не может сменить видимость документов. Причина — хрупкий клиентский пересчёт (гонка `/me` ↔ catalog, ошибка catalog → deny), а не серверный предикат.

Паттерн «backend — единственный источник производного admin-признака» уже зафиксирован для `sees_all_sms_teams` / `sees_all_mail_teams` ([ADR-036](ADR-036-sms-team-filter-admin-only.md)): фронт **не** дублирует `permissions_subset` и **не** тянет каталог ради UX-гейта.

`useCan('documents','share')` уже читает `permissions` из `/me` (`frontend/src/features/auth/hooks.ts`). Если в `/me.permissions.documents` есть `share` (факт прода для «Админ»), пункт «Сменить видимость» обязан рендериться — без второго запроса к каталогу.

## Decision

### 1. `MeResponse += is_admin_level: bool`

`GET /api/auth/me` отдаёт производный флаг **`is_admin_level`** = тот же предикат, что `app/domain/permissions.py::is_admin_level` / `require_admin` ([ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4):

```
is_superadmin OR role == "admin" OR permissions_subset(full_catalog_permissions(), principal.permissions)
```

Вычисляется в handler `/me` из уже загруженного `Principal` (нулевая стоимость). Поле обязательное, `bool`. Миграции нет. Каталог прав и Users API не меняются.

`sees_all_sms_teams` / `sees_all_mail_teams` остаются отдельными полями (scope каналов). Их не заменять `is_admin_level` и не выводить на клиенте одно из другого.

### 2. Клиент читает флаг из `/me`, не пересчитывает каталог

`AdminRoute`, пункт навигации «Пользователи», `DefaultRoute` (лист `users`), `useIsAdmin` — **только** `me.is_admin_level` (стор, наполняется `setPrincipal` из `/me`, персист в `localStorage` по образцу `sees_all_*`: ключ `crm.auth.isAdminLevel`).

**Запрещено** для гейта `/users`:

- `GET /api/permissions/catalog` ради `is_admin_level`;
- `coversFullCatalog` / `needsPermissionsCatalog` / `catalogPending`;
- Spinner на месте пункта «Пользователи» или в `AdminRoute` «пока грузится каталог»;
- deny при ошибке каталога.

`GET /api/permissions/catalog` остаётся **только** для матрицы `/roles` (столбцы extra-действий, [ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4, закрытие TD-068). `AppLayout` каталог для admin-уровня **не** запрашивает.

Пока `/me` ещё не пришёл и персиста нет — пункт «Пользователи» скрыт (как прочие пункты при `permissions == null`). После `/me` с `is_admin_level=true` — ссылка, без промежуточного Spinner каталога. Ошибка `/me` с `401` — штатный сброс сессии; иная ошибка `/me` — оставить персист, не считать «не admin» из каталога.

### 3. `documents:share` — UX из `/me.permissions`, минимальный фолбэк

Пункт kebab **«Сменить видимость»** рендерится ⇔

```
useCan('documents', 'share')   // share ∈ me.permissions.documents  ИЛИ  is_superadmin
OR me.is_admin_level
```

- Первичное правило: если `/me.permissions.documents` содержит `share` (факт прода для полного каталога) — пункт **обязан** быть. Источник — `permissions` из `/me`, не каталог.
- Фолбэк `me.is_admin_level` — минимальный: admin-уровень подразумевает полный каталог, включая `share`; закрывает рассинхрон персиста `permissions` без `share` при уже истинном флаге. Третьего пути (запрос каталога ради share) нет.
- Граница безопасности — серверный `403` на `GET`/`PATCH …/visibility` и `GET /role-refs` (`require("documents","share")`). UX-гейт не ослабляет enforcement.

`useCan` по-прежнему читает только стор из `/me` (супер-админ → `true`; иначе `action ∈ permissions[page]`). Не гейтить share через `coversFullCatalog`.

## Consequences

- (+) Роль «Админ» с полным каталогом видит «Пользователи» и «Сменить видимость» без второго запроса и без `roles:view`.
- (+) Один паттерн с `sees_all_*`: производный признак считает backend.
- (−) Старые клиенты без поля игнорируют его (JSON extra); новый SPA без поля на старом API не соберёт тип — выкатывать backend и frontend вместе.
- Клиентские `coversFullCatalog` / `catalogPending` для `/users` отменяются; тесты на Spinner каталога в `AdminRoute`/`AppLayout` — переписать.

## Verification (prod, 2026-08-17)

One-off workflow (уже удалён). **Метод:** in-process проверка предиката `is_admin_level` и `permissions.documents.share` на `Principal` из строк БД прода; `is_superadmin=False` всегда. Не HTTP `GET /api/auth/me`. Артефакт — CI log run `32059875139` (2026-08-17).

**Фильтр run:** только `role.name == «Админ»`. Ветки `role==admin` и `is_superadmin` этот run **не** покрывал. Строка `superadmin@system` попала потому что у якоря в БД `role.name == «Админ»` ([ADR-051](ADR-051-superadmin-db-anchor-personal-state.md)), не как проверка ветки супер-админа.

Дословный вывод run 32059875139:

```
user='superadmin@system' is_admin_level=True documents_share=True me_field_present=True
user='Елисей' is_admin_level=True documents_share=True me_field_present=True
user='Иван' is_admin_level=True documents_share=True me_field_present=True
user='rusbear28' is_admin_level=True documents_share=True me_field_present=True
user='Андрей' is_admin_level=True documents_share=True me_field_present=True
user='Анна' is_admin_level=True documents_share=True me_field_present=True
user='Никита' is_admin_level=True documents_share=True me_field_present=True
checked_admin_users 7
BACKEND_OK
```

UX e2e (Users nav + «Сменить видимость») не проверялась. Deploy frontend `f29c6e1`.

## Alternatives

- **Оставить клиентский `coversFullCatalog`** — отвергнуто: прод уже даёт полный jsonb и `is_admin_level=True` на сервере; баг живёт в гонке/ошибке каталога.
- **Синоним имени «Админ» ≡ `admin`** — отвергнуто повторно ([ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) Alternatives).
- **Ключ `users` в каталоге** — отвергнуто повторно (замыкание эскалации [ADR-022](ADR-022-teams-nav-categories.md) §4в).
- **Не отдавать флаг, чинить только share** — отвергнуто: гейт «Пользователи» останется хрупким.
