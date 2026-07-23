# Модуль «Пользователи бэков» (backend-users)

Страница управления пользователями внешних бэков из CRM: объединённый список
(поиск, фильтр по приложению/периоду/платности, сводка «всего/платных/сумма
оплат/CR%»), карточка пользователя (баланс, тариф, экономика, генерации,
истории оплат/запросов) и admin-операции «Начислить токены» / «Установить
план». Решение — [ADR-069](../../adr/ADR-069-backend-users-page-admin-contract.md),
API — [04-api.md#backend-users](../../04-api.md#backend-users).

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
| `GET /users/{id}/requests` | история запросов, `sent_at DESC`; не хранит — `{total:0, items:[]}` |
| `GET /stats` | `users_total`, `paid_users`, `payments_sum_usd` (CR% считает CRM) |
| `GET /products` | тарифы для «Установить план» |
| `POST /users/{id}/tokens` | `{amount}`; **НЕ идемпотентен**; отрицательное — списание; минус-баланс → 400 |
| `POST /users/{id}/subscription` | `{product_id, expires_in_days, grant_id}`; **идемпотентен** по `grant_id`; продление активной подписки добавляет дни |

## Реализация в CRM

- `infra/backend_admin_client.py` — httpx-клиент: автоопределение префикса
  (404 на кандидате → следующий; рабочий кэшируется в памяти по id бэка),
  таймауты `BACKEND_CHECK_TIMEOUT_SEC` на все фазы, маппинг ошибок бэка в
  `AppError` (401/403 → `backend_admin_rejected`; оба префикса 404 →
  `backend_admin_not_supported`; сеть/5xx → `backend_admin_unavailable`).
- `services/backend_user_service.py` — режим «Все приложения»: конкурентный
  fan-out (семафор 5) по бэкам с admin-ключом, merge по `registered_at DESC`,
  суммирование stats; упавший источник → `errors[]` ответа (partial data), при
  единственном источнике ошибка пробрасывается. Ответы бэка валидируются
  Pydantic-схемами (`schemas/backend_user.py`): не по контракту → 502.
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
- 232-claude-backend контракт v1 пока НЕ реализует (только `add-tokens` /
  `grant-subscription` в собственном формате) — приведение к контракту
  запланировано отдельной задачей в его репозитории.
