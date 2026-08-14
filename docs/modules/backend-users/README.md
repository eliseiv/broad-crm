# Модуль «Пользователи бэков» (backend-users)

Страница управления пользователями внешних бэков из CRM: объединённый список
(поиск, фильтр по приложению/периоду/платности, сводка «всего/платных/сумма
оплат/CR%»), карточка пользователя (баланс, тариф, экономика, генерации,
истории оплат/запросов) и admin-операции «Начислить токены» / «Установить
план». Решение — [ADR-069](../../adr/ADR-069-backend-users-page-admin-contract.md),
API — [04-api.md#backend-users](../../04-api.md#backend-users).

## Состояние (замер на замороженном дереве 2026-08-07)

> **Единственный датированный дом фактов о реализации этого модуля** (та же схема, что
> у [backend-economics](../backend-economics/README.md#состояние-замер-на-замороженном-дереве-2026-08-07)).
> Всё остальное в документе — **предписание**, а не утверждение о дереве; факты о
> состоянии кода, тестов и **чужих репозиториев** держим только здесь, с датой.
>
> ⚠️ **Каждый путь этого блока при КАЖДОМ замере прогоняется через `find`/`ls` — весь
> список целиком, а не «на глаз».** Ценность датированного дома в машинопроверяемости,
> поэтому неверный путь бьёт по нему сильнее, чем такая же опечатка в прозе: он выглядит
> проверяемым фактом и потому читателем не перепроверяется. Правдоподобный, но
> несуществующий путь (`components/__tests__` вместо `pages/__tests__` — реальный случай
> в этом блоке) глазами не отличим, только командой. Готовый свип:
> `grep -ohE "(backend|frontend)/[A-Za-z0-9_./-]+\.(py|tsx|ts)" <файл> | sort -u | while read p; do [ -e "$p" ] || echo "FAIL $p"; done`

| Что | Состояние |
| --- | --- |
| Backend / frontend модуля | реализованы (`backend/app/api/backend_users.py`, `services/backend_user_service.py`, `infra/backend_admin_client.py`; `frontend/src/pages/BackendUsersPage.tsx`, `BackendUserDetailPage.tsx`) |
| Ключ каталога прав `backend-users` (`view`/`edit`) | в `CATALOG` (`backend/app/domain/permissions.py:23`) |
| Тесты | **есть** (заведены волной [ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md); прежняя запись «модуль покрыт нулём тестов» устарела): `backend/tests/unit/test_backend_admin_client.py`, `backend/tests/integration/test_backend_economics_api.py` (общий клиент и резолвер источника), `frontend/src/pages/__tests__/BackendUserDetailRequests.test.tsx`; плюс чужие — `backend/tests/integration/test_users_roles_api.py:59-83`, `frontend/src/routes/__tests__/DefaultRoute.test.tsx:58-67` , **`frontend/src/components/__tests__/GrantPlanModalArchived.test.tsx`** (пометка архивной опции в форме «Установить план», [ADR-073](../../adr/ADR-073-products-archive-and-price-columns.md) §5) |
| **232-claude-backend: contract v1** | **реализован** — роутер `src/app/api_gateway/routers/billing_admin_crm.py:81` (`APIRouter(prefix="/api/billing/admin")`), 8 путей: 6 `GET` + 2 `POST`; DTO — `src/app/schemas/admin_crm.py` |
| **232-claude-backend: расширения v1.1 / v1.2** | **на дату замера не реализованы** ⚠️ **устареет первым:** часть A плана и архив ADR-073 выполняются в том репозитории; актуальное состояние чужого репо проверять в нём, а не здесь |

## Принцип

CRM — **прокси без собственного хранилища**: все данные читаются на лету из
бэков по универсальному **CRM Admin API contract v1**. Условия подключения бэка:

1. Бэк добавлен в реестр «Бэки» и отвечает на health-check.
2. В карточке бэка задан **Admin API Key** (`admin_api_key_encrypted`, Fernet,
   [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)).
   Ключ расшифровывается в памяти обработчика и уходит в бэк заголовком
   `X-Admin-Key`; во frontend/логи не попадает.
3. Бэк реализует контракт v1 под ОДНИМ из префиксов: `/api/billing/admin` или
   `/v1/admin` (эталонный текст контракта хранится у владельца:
   `BA/crm-admin-api-contract.txt`).

## CRM Admin API contract v1 (сводка)

Все эндпоинты — под `X-Admin-Key` (constant-time сравнение; пустой ключ в env
бэка → fail-closed 401). Даты — ISO 8601 UTC; ошибки — `{ "detail": ... }`;
списки — только с пагинацией `limit` (≤100) / `offset`.

| Эндпоинт (относительно префикса) | Назначение |
| --- | --- |
| `GET /users` | список: `total` + `items[]` (`id`, `external_id`, `is_paid`, `payments_count`, `renewals_count`, `tokens`, `subscription_active`, `subscription_expires_at`, `plan_id`, `registered_at`), сортировка `registered_at DESC` (нормативно — на ней построен merge) |
| `GET /users/{id}` | карточка: `balance`, `subscription`, `revenue\|null`, `media_stats\|null` (опциональные блоки — `null`, а не 404/500) |
| `GET /users/{id}/payments` | история оплат, `occurred_at DESC` |
| `GET /users/{id}/requests` | история запросов, `sent_at DESC`; не хранит — `{total:0, items:[]}`. **v1.1:** элемент += `tokens_spent` (number\|null), `provider_cost_usd` (number\|null), `refunded` (**bool в контракте бэка**) — **все три опциональны для читателя**: бэк уровня v1 их не отдаёт, CRM нормализует отсутствие в `null` ⇒ **в схеме ОТВЕТА CRM `refunded` — `bool \| null`** ([04-api.md](../../04-api.md#backend-users); `null` ≠ `false`), [ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md) §1.1 |
| `GET /stats` | `users_total`, `paid_users`, `payments_sum_usd` (CR% считает CRM) |
| `GET /products` | тарифы для «Установить план»: `product_id`, `name`, `price` (str\|null), `period` (str\|null). **v1.1:** элемент += `tokens` (int), `avatar_tokens` (int\|null), `grantable` (bool), `updated_at` (ISO\|null); параметр `scope=grantable\|all` (**по умолчанию `grantable`** — сегодняшнее поведение формы «Установить план» сохраняется; `scope` эта страница **не шлёт**). Все поля v1.1 — **опциональные** ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md)). **v1.2:** элемент += **`archived`** (bool, опц. — [ADR-073](../../adr/ADR-073-products-archive-and-price-columns.md)); ⚠️ **`scope=grantable` МОЖЕТ вернуть архивные** (сервер по `archived` **не отбирает никогда** — это поле, а не фильтр; оси ортогональны: `scope` — право выдачи, `archived` — товарный вид) |
| `POST /users/{id}/tokens` | `{amount}`; **НЕ идемпотентен**; отрицательное — списание; минус-баланс → 400 |
| `POST /users/{id}/subscription` | `{product_id, expires_in_days, grant_id}`; **идемпотентен** по `grant_id`; продление активной подписки добавляет дни |

> **Расширения v1.1 «экономика»** ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md)) —
> `PATCH /products/{id}`, `GET`/`PATCH /pricing`, `GET /capabilities` плюс новые поля
> двух эндпоинтов выше — **и v1.2 «архив»** ([ADR-073](../../adr/ADR-073-products-archive-and-price-columns.md)):
> поле `archived` в элементе `GET /products` и в теле его `PATCH`. Дельта и инварианты —
> [modules/backend-economics](../backend-economics/README.md#дельта-контракта-v11-экономика-и-v12-архив-только-новое-относительно-v1);
> здесь не дублируются. Расширения **аддитивны**: бэк уровня v1 работает без изменений,
> эта страница не деградирует (детекция префикса идёт по v1-пути `GET /products`).

### Архивные продукты в форме «Установить план» (нормативно, [ADR-073](../../adr/ADR-073-products-archive-and-price-columns.md) §5)

Правило живёт здесь, потому что **форма принадлежит этой странице**, а её UI-строка
физически лежит в словаре секции «Продукты и тарифы» дизайн-системы (собственной секции
у страницы «Юзеры бэков» в ДС нет). Исполнитель формы обязан найти правило в ТЗ **своего**
модуля, а не в словаре чужой страницы.

- **Форма архивные продукты НЕ фильтрует.** `archived` и `grantable` **ортогональны**:
  архив — товарный вид витрины, `grantable` — право выдачи (env-список бэка). Выдать
  архивный план — **законная операция**, и скрыть опцию значило бы молча лишить оператора
  её. Начисление по архивным продуктам работает как обычно.
- **Но архивная опция ПОМЕЧАЕТСЯ.** Подпись получает суффикс — **нормативная строка одна,
  и она в словаре**: [08-design-system.md § Локализация страницы «Продукты и тарифы»](../../08-design-system.md#локализация-страницы-продукты-и-тарифы-нормативный-словарь),
  строка «Пометка архивной опции в форме «Установить план»». Дословно здесь **не
  дублируется** (одна строка — один дом).
- **Почему обязательно и то и другое:** `scope=grantable` **может вернуть архивные**
  (сервер по `archived` не отбирает никогда). Без пометки оператор не отличит снятую с
  витрины позицию от активной — операция доступна, а её характер скрыт; это «честность
  наполовину», против которой §5 и написан.
- **Бэк без поддержки архива** поля `archived` не отдаёт ⇒ помечать нечего, форма
  работает как прежде.

## Реализация в CRM

- `infra/backend_admin_client.py` — httpx-клиент: автоопределение префикса
  (404 на кандидате → следующий; рабочий кэшируется в памяти по id бэка),
  таймауты `BACKEND_CHECK_TIMEOUT_SEC` на все фазы, маппинг ошибок бэка в
  `AppError` (401/403 → `backend_admin_rejected`; оба префикса 404 →
  `backend_admin_not_supported`; **400 И 422 → `backend_admin_bad_request`**; **409 →
  `backend_admin_conflict`**; сеть/5xx → `backend_admin_unavailable`).
  **Клиент общий с [backend-economics](../backend-economics/README.md)**
  ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md) §4): детекция префикса
  идёт **всегда по v1-пути `GET /products`** (расширенные пути её не выполняют), а
  семантика 404 задаётся вызывающим **явно, без значения по умолчанию** —
  `CONTRACT` → `backend_admin_not_supported`, `USER` → `backend_user_not_found`
  (**только пути `/users/*`**), `EXTENSION` → `backend_admin_extension_not_supported`.

> **Маппинг `422` действует и на путях v1 — это изменение поведения ЭТОЙ страницы**
> ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md) §7.3; норма написана про
> «бэк» без ограничения путями, а клиент — общий). Раньше `422` от бэка уходил в ветку
> «прочий не-2xx» и давал **`502` с голым «Ошибка бэка (HTTP 422)»**; теперь это
> **`400 backend_admin_bad_request`** с текстом причины. Практически заметно на
> `POST /users/{id}/tokens` и `POST /users/{id}/subscription`: отказ валидации тела у
> бэка (например, слишком большая сумма) виден оператору как «поправьте введённое
> значение», а не как «бэк сломан». **Текст причины:** `detail`-строка — транзитом;
> `detail`-**список** (формат `422` FastAPI) — `msg` первого элемента (+ поле из `loc`);
> не удалось извлечь — фолбэк **«Бэк отверг значение: не прошло проверку на стороне
> бэка»**. Соседние статусы **не** поглощены: `405`/`429`/`5xx`/`3xx` по-прежнему
> `backend_admin_unavailable`, `409` и `401`/`403` — свои коды.

> **Следствия probe для ЭТОЙ страницы** ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md) §4а.1).
> Probe идёт при **любом** холодном вызове, включая пути `/users*`, поэтому:
> **(1)** первый вызов процесса к бэку делает **один дополнительный upstream-запрос**
> (`GET {P}/products` перед запрошенным путём) — цена, уже принятая
> [ADR-069](../../adr/ADR-069-backend-users-page-admin-contract.md) §2;
> **(2)** бэк с **неработающим `/products` при рабочем `/users`** теперь **блокирует
> страницу** (`502 backend_admin_not_supported`), тогда как прежде она бы работала —
> принято осознанно: `/products` обязателен в v1 и является нормативным probe-путём,
> так что такой бэк нарушает контракт, и скрывать это не следует;
> **(3) побочно закрыт латентный дефект:** прежде при холодном кэше `404` от
> `GET /users/{id}` **несуществующего** пользователя давал `backend_admin_not_supported`
> вместо `backend_user_not_found` (в ветке детекции семантика 404 не учитывалась —
> ветка детекции префикса в `_request`). Теперь префикс определяется
> probe'ом заранее, и `404` попадает в ветку известного префикса.
- `services/backend_user_service.py` — режим «Все приложения»: конкурентный
  fan-out (семафор 5) по бэкам с admin-ключом, merge по `registered_at DESC`,
  суммирование stats; упавший источник → `errors[]` ответа (partial data), при
  единственном источнике ошибка пробрасывается. Ответы бэка валидируются
  Pydantic-схемами (`schemas/backend_user.py`): не по контракту → 502.
  > **Бэк реестра БЕЗ admin-ключа тоже попадает в `errors[]`**
  > (`Admin API Key не задан в CRM — бэк НЕ опрошен`). Прод-инцидент `selquro`:
  > инстанс был в реестре и под мониторингом, но без ключа, поэтому
  > `list_with_admin_key()` убирал его из fan-out **без следа** — поиск
  > существующего пользователя (и по `user_id`, и по `apphud_id`) давал
  > «Ничего не найдено», неотличимое от «такого пользователя нет», хотя сам бэк
  > находил его за 0.15 с. Разделение реестра — `BackendAdminSourceResolver.list_split()`;
  > регресс-гейт — `tests/integration/test_backend_users_unqueried_sources.py`.
- `api/backend_users.py` — RBAC `backend-users:view` / `backend-users:edit`
  (каталог прав, [ADR-021](../../adr/ADR-021-rbac-users-roles.md)); admin-операции
  пишут аудит-событие `backend_admin_action` (`infra/audit.py`, без секретов).
- Frontend: `features/backend-users/`, `pages/BackendUsersPage.tsx` (список,
  `/backend-users`), `pages/BackendUserDetailPage.tsx`
  (`/backend-users/:backendId/:userId`), модалки
  `components/BackendUserActionModals.tsx`. Двойной сабмит токенов блокируется
  (`loading` + недismissible-модалка); `grant_id` генерируется при открытии
  формы плана (идемпотентный ретрай). Фильтр «приложение» строится из
  `GET /api/backends` и скрывается при 403 (страница остаётся рабочей в режиме
  «Все приложения»).

## Ограничения

- Merge-пагинация «Все приложения» ограничена окном 1000 строк (глубже UI не
  листает); глубокие страницы дочитываются у источников страницами по 100.
- БД-миграций нет — модуль не добавляет таблиц.
- **Каждый бэк реализует контракт отдельной задачей в СВОЁМ репозитории** — v1, а затем
  расширение v1.1 ([ADR-072](../../adr/ADR-072-crm-admin-api-v11-economics.md)). Норма
  CRM от этого не зависит: страница обязана работать и с бэком уровня v1 (поля v1.1
  опциональны), и с бэком без контракта вовсе (`502 backend_admin_not_supported`).
  **Кто из бэков что реализовал — факт о ЧУЖОМ дереве и здесь не фиксируется**:
  датированный срез — в [§Состояние](#состояние-замер-на-замороженном-дереве-2026-08-07),
  актуальное состояние — в репозитории соответствующего бэка.
