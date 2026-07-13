# ADR-051 — Системный якорь супер-админа в `users`: личное состояние (прочитанность писем) работает и под консольным `admin`

- **Статус:** accepted
- **Дата:** 2026-07-13
- **Контекст-модули:** [auth](../modules/auth/README.md), [mail](../modules/mail/README.md), [teams](../modules/teams/README.md), [ui](../modules/ui/README.md)
- **ОТМЕНЯЕТ (supersede части):** [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) **§2.5 целиком** — норма «супер-админ из `.env` личного состояния НЕ имеет» (`403` на отметке, `is_unread` всегда `false`, `unread=true` → пустая страница, UI-контролы скрыты) **отменена**. Что остаётся из §2.5 — §2 ниже.
- **Амендмент:** [ADR-008](ADR-008-admin-iz-env.md) (супер-админ получает **строку-якорь** в `users`; при этом **вход, права и fallback-инвариант НЕ меняются** — он по-прежнему не БД-пользователь), [ADR-021](ADR-021-rbac-users-roles.md) (формулировка «в таблицу `users` не попадает» уточняется: попадает **системная строка-якорь**, невидимая для Users/Roles/Teams API)
- **Переиспользуется без изменений (НЕ амендируется):** [ADR-030](ADR-030-sms-module-full-merge.md) §7 (`POST /api/sms/telegram/link` для супер-админа — по-прежнему `403`, но по другому основанию — §1.6), [ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md) (беспарольный «открытый первый вход» — к якорю **неприменим** by construction, §1.1), [ADR-044](ADR-044-mail-full-merge-into-crm.md) §7 (`MailScope`), [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §1/§2.1–§2.4/§2.6–§2.8 (кроме отменённого §2.5)

- **Миграция:** **`0026_users_is_system`** (`revision = "0026_users_is_system"` — **20 символов**, укладывается в `alembic_version.version_num VARCHAR(32)`, норматив [ADR-047](ADR-047-mail-fix-pack.md) §3.5; `down_revision = "0025_mail_message_reads"` — текущая голова цепочки, `backend/alembic/versions/0025_mail_message_reads.py:37`). Миграция — **только схема** (колонка + индекс). **Строку-якорь миграция НЕ вставляет** — она создаётся идемпотентным bootstrap'ом приложения (§1.3).

> **Ссылки на код — по ИМЕНАМ СИМВОЛОВ** (файл + символ); номера строк — там, где нужна факт-цитата (`claims-from-code`) на момент принятия ADR.

## Контекст

**Требование владельца (2026-07-13, после деплоя [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md)):** личная прочитанность писем обязана работать и для **консольного супер-админа** (`ADMIN_USER`/`ADMIN_PASSWORD` из `.env`) — владелец работает на проде именно под ним. Прочитанность **остаётся личной** (не общей на команду) — это подтверждено отдельно.

Норма [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.5, действовавшая до этого ADR, прямо запрещала это: `403` на `POST`/`DELETE /api/mail/messages/{id}/read`, `is_unread` всегда `false`, `unread=true` → пустая страница. Это **разворот утверждённой нормы** ⇒ оформляется супессией, а не «мелким фиксом».

**Что в коде (сверено, `claims-from-code`):**

- **Корень проблемы — `Principal.user_id is None` у супер-админа:** «`user_id` из claim `uid` (UUID) — ТОЛЬКО у БД-пользователя; супер-админ → None (он не строка в `users`)» (`backend/app/api/deps.py:83-85`); ветка `if claims.superadmin: return Principal(..., user_id=None)` (`backend/app/api/deps.py:108-115`).
- **Отсюда — «ловушка `user_id is None`», уже размноженная по коду:** `app/api/mail_me.py:32-33` и `:47-49` (`403` на `/api/mail/me/settings`), `app/services/mail_service.py:208` (`if unread and user_id is None` → пустая страница), `:276`, `:336` (`is_unread=false`), `app/services/sms_telegram_link_service.py:74` (`403` на привязке Telegram), `app/api/deps.py:240` и `:270` (пустой SMS/Mail-scope), `app/infra/audit.py:32` (`user_id=None` в аудите). Это **не единичный дефект mail'а, а класс дефектов**: любая новая персональная фича (прочитанность, настройки, избранное, черновики) будет об него спотыкаться заново.
- **Логин супер-админа БД не касается:** `AuthService.login` сравнивает креды constant-time с `settings.admin_user`/`settings.admin_password` и выпускает токен `issue_access_token(sub=..., role="admin", superadmin=True)` **без `uid`** (`backend/app/services/auth_service.py:70-87`). Env-переменные — `ADMIN_USER`/`ADMIN_PASSWORD` (`backend/app/config.py:55-56`; **не** `ADMIN_USERNAME` — так в [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.5 было написано ошибочно).
- **Права супер-админа берутся НЕ из БД:** `permissions=full_catalog_permissions()` в той же ветке (`backend/app/api/deps.py:112`). Гейты: `require(page, action)` → `if principal.is_superadmin or action in ...`; `require_admin` → `if principal.is_superadmin or principal.role == "admin"` (`backend/app/api/deps.py:167`, `:176`).
- **FK прочитанности:** `mail_message_reads.user_id` → `users(id)` `ON DELETE CASCADE` (`backend/alembic/versions/0025_mail_message_reads.py`, `pk_mail_message_reads`) — для принципала без строки в `users` физически невыполним.
- **Беспарольная ветка входа опасна для «пустой» строки:** пользователь с `password_hash IS NULL` получает **setup-token** и может **сам задать пароль** (`backend/app/services/auth_service.py:112-121`, `set_password`). ⇒ строка-якорь с `password_hash = NULL` была бы **дырой эскалации** (кто угодно, зная её `username`, логинится и назначает себе пароль под ролью `admin`). Это определяет §1.1.
- **Резолв БД-пользователя — через `UserRepository`:** `get_by_username` / `get_by_telegram` (логин, `AuthService._resolve_db_user`), `get_by_id` / `get_by_telegram` (Telegram-SSO Mini App — `MailTelegramService._resolve_user`, `SmsTelegramLinkService.auth`), `list_all` (`/api/users`), `get_existing_ids` (валидация `leader_id`/`member_ids` команд — `TeamService._require_user_exists` / `_validate_members`). ⇒ **единая точка перехвата РЕЗОЛВА пользователя как субъекта существует**. **Но `users` читают напрямую и мимо `UserRepository`** — `RoleRepository` (`list_all_with_counts`/`count_users`/`is_in_use`), `MailTelegramLinkRepository.team_recipients`/`sees_all_candidates`, `SmsTelegramLinkRepository.recipients_for_team`; их разбор — §1.4(б).
- **Роли считают носителей:** `RoleRepository.list_all_with_counts` (`COUNT(users)`, `backend/app/repositories/role_repository.py:39-46`), `count_users` (`:48-52`) — **отображение**; `is_in_use` (`:70-74`) — **гард удаления** (зеркало FK `ON DELETE RESTRICT`, `backend/app/models/user.py:63-67`).
- **`hash_password` усекает пароль до 72 байт** (`backend/app/infra/passwords.py:17-28`) ⇒ хэшировать длинный случайный секрет **безопасно и допустимо** (не бросит `ValueError`).
- **`validate_identity_name` — регэксп `^(?=.*[^\W\d_])[\w.\- ]{1,64}$`** (`backend/app/domain/identity.py:15`): символ **`@` не допускается** ⇒ имя с `@` **невозможно создать через API** (422). DB-CHECK `ck_users_username` при этом `@` разрешает (`char_length 1..64`, `btrim`, без control-символов — `backend/app/models/user.py:41-47`). Это даёт **неколлизионное зарезервированное имя** (§1.1).
- **Прод-факт:** `GET /api/auth/me` под `admin` отдаёт `is_superadmin: true`; в `users` 9 строк, `admin` среди них нет.

## Решение

### §1. Супер-админ получает СИСТЕМНУЮ СТРОКУ-ЯКОРЬ в `users`

**Идея (нормативно, одной фразой): якорь — это ИДЕНТИЧНОСТЬ для личного состояния, и ТОЛЬКО она.** Он **не** источник прав, **не** способ входа, **не** канал доставки, **не** участник команд и **не** элемент реестра пользователей. Всё, что супер-админ мог до этого ADR, он может ровно так же; всё, чего не мог (личное состояние с FK на `users`) — начинает работать.

#### §1.1. Модель: `users += is_system` + одна системная строка

Миграция **`0026_users_is_system`** (только схема):

| Изменение | Значение |
|-----------|----------|
| Колонка `users.is_system` | `boolean NOT NULL DEFAULT false`. `true` — системная строка-якорь; **не отдаётся наружу ни в одном контракте** |
| Частичный уникальный индекс `uq_users_system_singleton` | `ON users (is_system) WHERE is_system` — якорь **ровно один** (страховка от второй системной строки). **⚠️ Объявляется в МОДЕЛИ — `User.__table_args__`** (`Index("uq_users_system_singleton", "is_system", unique=True, postgresql_where=text("is_system"))`; там уже есть `CheckConstraint(name="ck_users_username")`, `backend/app/models/user.py:40-47`), **а миграция `0026` его ЗЕРКАЛИТ**. Иначе схема тестов (`Base.metadata.create_all` — `backend/tests/integration/mail_helpers.py:56`, `mail_s34_helpers.py:57`, `sms_helpers.py:59`) разошлась бы с прод-схемой: регрессия, создающая **вторую** системную строку, прошла бы зелёные тесты и упала бы только на проде |

Сама строка (создаётся bootstrap'ом, §1.3) — **нормативные значения**:

| Поле | Значение | Почему именно так |
|------|----------|-------------------|
| `id` | **константа `SUPERADMIN_USER_ID = 00000000-0000-0000-0000-000000000001`** (новый модуль `app/domain/superadmin.py`) | **Ключевое решение.** Идентичность якоря **фиксирована и не зависит ни от `ADMIN_USER`, ни от БД** ⇒ (а) `get_current_principal` подставляет `user_id` супер-админу **без единого запроса в БД** (hot-path не дорожает, fallback-инвариант [ADR-008](ADR-008-admin-iz-env.md) не нарушается); (б) смена env-логина/пароля **не теряет** личное состояние (§1.7) |
| `username` | **константа `SUPERADMIN_USERNAME = "superadmin@system"`** (17 символов) | **Зарезервировано by construction:** символ `@` отвергается `validate_identity_name` (`backend/app/domain/identity.py:15`) ⇒ такое имя **невозможно** создать/переименовать через API (422) ⇒ коллизия с реальным пользователем исключена. DB-CHECK `ck_users_username` его пропускает (`@` — не control-символ, длина 17 ≤ 64). Имя **внутреннее**: наружу не отдаётся, `Principal.username` супер-админа по-прежнему `claims.sub` = `ADMIN_USER` |
| `password_hash` | **bcrypt-хэш случайного секрета**, сгенерированного при создании строки (`hash_password(secrets.token_urlsafe(64))`), plaintext **отбрасывается** (нигде не хранится и не логируется) | «Locked account». **`NULL` ЗАПРЕЩЁН:** `NULL` = беспарольный ⇒ [ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md)-ветка «открытый первый вход» выдала бы setup-token любому, кто назовёт этот `username` (`backend/app/services/auth_service.py:112-121`) — прямая эскалация до роли `admin`. Непустой недостижимый хэш делает **обе** ветки входа (парольную и беспарольную) неприменимыми к якорю **независимо** от §1.4. `hash_password` усекает до 72 байт (`backend/app/infra/passwords.py:17-28`) — 86-символьный `token_urlsafe(64)` хэшируется корректно |
| `role_id` | **Цепочка резолва (нормативно, выполняется bootstrap'ом, §1.3): (1)** роль с `name = "admin"`; **(2)** если её нет — самая ранняя роль (`ORDER BY created_at ASC, id ASC LIMIT 1`); **(3)** если ролей нет ВООБЩЕ — bootstrap **САМ создаёт роль `admin`** с `full_catalog_permissions()` (ровно тот же сид, что в data-миграции `0008`, `backend/alembic/versions/0008_create_users_roles.py::_ADMIN_PERMISSIONS`). Ветки «строка не создаётся» **НЕТ** | `role_id` — `NOT NULL` FK (`backend/app/models/user.py:63-67`), заглушка обязательна. **Шаг (3) делает bootstrap САМОДОСТАТОЧНЫМ** и снимает зависимость от порядка «сначала data-миграции, потом bootstrap» (§1.3): без него в тестах, поднимающих схему через `metadata.create_all` **без** data-миграций, ролей на момент bootstrap нет ⇒ якорь не создался бы ⇒ каждый тест «супер-админ отмечает письмо» падал бы на нарушении FK (`500` вместо `204`). Шаг (3) **не воскрешает удалённые данные** (антипаттерн [ADR-047](ADR-047-mail-fix-pack.md) §1). Точная формулировка: **пустая таблица ролей — вырожденное состояние** (свежая БД без data-миграций **либо** удалены ВСЕ роли; последнее достижимо привилегированным актором в окне между миграцией `0026` и первым bootstrap'ом — `DELETE /api/roles` даёт `403` лишь непривилегированному и `409` лишь при `is_in_use`). В таком состоянии БД **нерабочая** (ни один БД-пользователь не существует и не создаётся без роли), поэтому создание одной роли-заглушки — **восстановление работоспособности**, а не воскрешение удалённых данных: воскрешённая роль имеет **0 носителей** (кроме якоря) и никому прав не возвращает. **После первого успешного bootstrap ветка (3) недостижима:** якорь пиннит свою роль через FK `ON DELETE RESTRICT` (§1.5) ⇒ удалить её нельзя (`409 role_in_use`). **Роль якоря НЕ является источником его прав:** права супер-админа — `full_catalog_permissions()` из ветки `claims.superadmin` (`backend/app/api/deps.py:112`), БД-роль в них не участвует. Правка/переименование этой роли на права супер-админа **не влияет** |
| `is_active` | `true` | Супер-админ — действующий принципал. `false` означало бы «деактивирован» и провоцировало бы будущий код считать его личные артефакты невалидными. Безопасность обеспечивают locked-хэш + §1.4, а не флаг активности |
| `telegram` | `NULL` (и **никогда** не задаётся) | Якорь не резолвится Telegram-SSO ни по линку, ни по username (§1.6) |
| `first_login_at` | `NULL` | Метка первого входа ([ADR-028](ADR-028-user-status-first-login.md)) к якорю неприменима — он не входит через БД-ветку |

#### §1.2. `Principal.user_id` становится НЕ-опциональным

**Нормативно:** `Principal.user_id: uuid.UUID` (**без `| None`, без default**) — `backend/app/api/deps.py::Principal`.

- Супер-админ → `user_id = SUPERADMIN_USER_ID` (константа, **без обращения к БД**).
- БД-пользователь → `user_id = user.id` (как сейчас).
- Легаси-токен без `uid` у БД-пользователя → `401` (как сейчас, `backend/app/api/deps.py:117-118`).

**JWT НЕ меняется:** `uid` в токен супер-админа **не добавляется** (`issue_access_token(..., superadmin=True)` — как есть). Причина: (а) уже выпущенные токены супер-админа продолжают работать без пере-логина; (б) `uid` в токене был бы вторым источником истины для константы.

**Почему тип, а не «ещё одна проверка»:** `user_id is None` — это и есть ловушка, о которую спотыкается каждая новая персональная фича (перечень мест — «Контекст»). Убрав `None` **из типа**, мы получаем компилятор (`mypy`) в роли гаранта: ни один будущий персональный эндпоинт не сможет «случайно» получить принципала без идентичности. Все ветки `if ... user_id is None` (deps.py, mail_me.py, mail_service.py, sms_telegram_link_service.py, audit.py) — **снимаются или переписываются на явный `principal.is_superadmin`** (§1.6, §4).

#### §1.3. Bootstrap якоря — идемпотентный, в приложении (НЕ в миграции)

**Единственный писатель строки — `UserRepository.ensure_superadmin_anchor()`.** Алгоритм (нормативно, строго в этом порядке):

1. **Резолв роли** — цепочка из §1.1 (`admin` → самая ранняя → **создать `admin` с полным каталогом**). Ветки «ролей нет ⇒ якоря нет» **не существует**: bootstrap самодостаточен.
2. **Вставка:** `INSERT INTO users (id, username, password_hash, role_id, is_active, is_system) VALUES (SUPERADMIN_USER_ID, …) ON CONFLICT DO NOTHING` (**без указания target** — накрывает конфликт и по `id` (`pk_users`), и по `username` (`uq_users_username`), и по `uq_users_system_singleton`). Повторный старт / несколько воркеров / рестарт — но-оп. Существующая строка **не перезаписывается** (пароль-заглушка не ротируется, `read_at`-отметки не трогаются).
3. **Верификация:** `SELECT id FROM users WHERE id = SUPERADMIN_USER_ID`. Строки нет (например, `username` занят древней записью, созданной в обход валидации) → **ERROR-лог `superadmin_anchor_missing`**; приложение **всё равно поднимается** (fallback-инвариант [ADR-008](ADR-008-admin-iz-env.md): супер-админ обязан входить даже при проблемах с БД). Наблюдаемое следствие отсутствия якоря: `POST …/read` под супер-админом даст `500` (нарушение FK `user_id` — [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.2 намеренно **не** маскирует его в `404`).
4. **Транзакция (нормативно): bootstrap владеет СВОЕЙ транзакцией и коммитит её САМ.** Шаги 1–3 (создание роли-заглушки при необходимости + `INSERT` якоря + верификация) — **один коммит**. Причина: у `ensure_superadmin_anchor` **нет сервиса-обёртки**, управляющей транзакцией (в отличие от остальных методов `UserRepository`, где коммитит сервис), а вызывается он из `lifespan` **и** напрямую из тестовой фикстуры. Норма: метод **принимает/использует переданную сессию и выполняет `await session.commit()` по завершении**; вызывающий обязан передать **отдельную** сессию (в `lifespan` — из `get_sessionmaker()`), а **не** переиспользовать сессию с чужой незакоммиченной работой. Без явной нормы исполнитель либо забудет `commit()` (роль и якорь откатятся ⇒ FK-`500` на первой отметке), либо закоммитит чужую фикстурную транзакцию.
5. **Ошибка bootstrap'а (БД недоступна) — не валит старт:** логируется `superadmin_anchor_bootstrap_failed` (ERROR), `lifespan` продолжается (как уже сделано для `startup_recovery`, `backend/app/main.py:44-49`).

**ПОРЯДОК ВЫЗОВА (нормативно — иначе спека неисполнима):**

| Среда | Точка вызова | Гарантия порядка |
|-------|--------------|------------------|
| **Прод / staging** | `backend/app/main.py::lifespan` (после блока `startup_recovery`) | Миграции применяются **в entrypoint backend-контейнера ДО старта приложения**: `alembic upgrade head && uvicorn app.main:app …` (`claims-from-code`: [07-deployment.md §Порядок запуска](../07-deployment.md#порядок-запуска), п. 4: «Миграции применяются в entrypoint backend-контейнера»). ⇒ к моменту `lifespan` роль `admin` из data-миграции `0008` уже есть, и цепочка резолва останавливается на шаге (1). **Посев ролей в `lifespan` не вводится** (в `main.py` его нет и не должно быть — [ADR-047](ADR-047-mail-fix-pack.md) §1) |
| **Тесты (integration)** | **Фикстура БД обязана вызвать `ensure_superadmin_anchor(session)` СРАЗУ ПОСЛЕ `Base.metadata.create_all`** (`backend/tests/integration/mail_helpers.py:56`, `mail_s34_helpers.py:57`, `sms_helpers.py:59`) — **до** любых тестов, использующих принципала супер-админа. `lifespan` в этих фикстурах не выполняется, поэтому вызвать обязана именно фикстура. Роль якоря при этом создаётся **самим bootstrap'ом** (шаг (3) цепочки): тестовые фикстуры сеют роли со случайными именами (`role-<hex>`, `mail_helpers.py:66`), роли `admin` там нет — и это **штатно** | Зона `qa` (§4) |

**Почему НЕ в миграции.** (а) [Требование к миграциям: они НЕ импортируют код приложения](../03-data-model.md#3-миграции-не-импортируют-код-приложения) — а строке нужны `hash_password` и константы якоря; дублировать их в SQL значит завести **второй** источник истины. (б) Тесты поднимают схему через `metadata.create_all`, а не Alembic — bootstrap-функция даёт **одну** точку и для прода, и для тестовых фикстур. (в) Самолечение: удалённая вручную строка восстанавливается на рестарте — миграция не восстановила бы (она уже применена).

> **⚠️ Цена ручного удаления якоря (нормативно, назвать явно).** `mail_message_reads.user_id → users(id)` — **`ON DELETE CASCADE`** ([ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.1). Значит `DELETE FROM users WHERE is_system` (только прямым SQL — через API это невозможно, §1.4) **безвозвратно стирает ВСЮ прочитанность супер-админа**, а рестарт восстановит **ПУСТОЙ** якорь (тот же `id`, ноль отметок). «Самолечение» относится к **существованию строки**, а не к её содержимому: личное состояние не восстанавливается ничем.

#### §1.4. Невидимость якоря: `UserRepository` — choke point РЕЗОЛВА субъекта (+ инвариант пустоты связей)

> **⚠️ Точная формулировка (важно, не «единственный choke point вообще»).** `UserRepository` — единственная точка, где пользователь **резолвится как СУБЪЕКТ** (реестр, логин, SSO, валидация ссылок). Но таблицу `users` читают **напрямую, в обход него**, ещё три репозитория — их перечень и защита ниже. Считать `UserRepository` единственным читателем `users` **нельзя** (это же опровергает §1.5, который правит `RoleRepository`).

**(а) `UserRepository` — правило по смыслу метода:**

| Класс методов | Якорь | Методы |
|---------------|-------|--------|
| **Возвращают пользователя как объект/субъект** (реестр, резолв логина, резолв SSO, валидация ссылок) — **исключают** `is_system` (`WHERE NOT users.is_system`) | **невидим** | `list_all`, `get_by_id`, `get_with_teams`, `get_by_username`, `get_by_telegram`, `get_existing_ids`, `delete_by_id` |
| **Проверяют уникальность** — зеркало DB-констрейнтов, **видят все строки** (иначе `409` подменился бы `500`-IntegrityError) | виден | `exists_by_username`, `exists_by_telegram` |
| **Обслуживают сам якорь** | — | `ensure_superadmin_anchor` (новый, §1.3) |

> **⚠️ `get_by_id` ОБЯЗАН быть переписан на `select(...)`** (нормативно): сегодня это `return await self._session.get(User, user_id)` (`backend/app/repositories/user_repository.py:67-69`) — PK-lookup через identity-map, в который предикат `WHERE NOT is_system` **физически невыразим**. Без переписывания на `select(User).where(User.id == user_id, User.is_system.is_(False))` правило §1.4 **не выполняется** именно там, где это критично: `get_by_id` — hot-path построения принципала (`get_current_principal`) **и** путь Telegram-SSO (`MailTelegramService._resolve_user` резолвит по `link.user_id`).

**(б) Прямые читатели `users` ВНЕ `UserRepository` (repo-wide grep по `select(User`/`join(User`) — полный перечень и защита:**

| Читатель | Запрос | Почему якорь НЕ попадает в выборку |
|----------|--------|-------------------------------------|
| `RoleRepository.list_all_with_counts`, `count_users` (`backend/app/repositories/role_repository.py:39-52`) | `COUNT(users) … OUTER JOIN` | **НЕ защищён автоматически** — якорь попал бы в счётчик ⇒ §1.5 **явно добавляет** им фильтр `WHERE NOT users.is_system` |
| `RoleRepository.is_in_use` (`:70-74`) | `SELECT users.id WHERE role_id = …` | Якорь **обязан** попадать (зеркало FK `ON DELETE RESTRICT`) — §1.5, это не течь, а требование |
| `MailTelegramLinkRepository.team_recipients` (`:149-158`) | `select(User.id, …).join(user_teams).join(MailTelegramLink)` — **INNER JOIN ×2** | У якоря **нет строк** ни в `user_teams`, ни в `mail_telegram_links` ⇒ INNER JOIN его отсекает |
| **`MailTelegramLinkRepository.sees_all_candidates`** (`:171-178`) | `select(User.id, …).join(MailTelegramLink).join(Role)` — **INNER JOIN на линк** + отбор **ПО ПРАВАМ РОЛИ** | **Самый опасный:** сервис фильтрует кандидатов по «полному каталогу прав», а роль якоря — `admin` (полный каталог) ⇒ **единственное**, что его отсекает, — **INNER JOIN на `mail_telegram_links`**, строки в котором у якоря нет и быть не может (§1.6: Telegram-привязка супер-админу запрещена, `403`) |
| `SmsTelegramLinkRepository.recipients_for_team` (`:100-106`) | `select(User.id, …).join(user_teams).join(SmsTelegramLink)` — **INNER JOIN ×2** | То же: нет строк ни в `user_teams`, ни в `sms_telegram_links` |

**(в) ИНВАРИАНТ ПУСТОТЫ СВЯЗЕЙ (нормативно — на нём держится (б), поэтому он фиксируется ЯВНО, а не подразумевается):**

> **Строка-якорь НЕ ИМЕЕТ и НЕ МОЖЕТ ИМЕТЬ строк в `user_teams`, `mail_telegram_links`, `sms_telegram_links`, `mail_user_settings`.** Обеспечено: членство в командах → `422` (валидация через `get_existing_ids`, (а)); Telegram-привязка → `403` (§1.6); `telegram IS NULL` у якоря ⇒ ленивое связывание orphan-линков по username (`bind_orphans_for_user`) его не находит.
>
> **⚠️ ОБЯЗАТЕЛЬНО (нормативно, не рекомендация): явный `WHERE NOT users.is_system` добавляется в ВСЕ ТРИ fan-out-выборки** — `MailTelegramLinkRepository.team_recipients`, **`MailTelegramLinkRepository.sees_all_candidates`**, `SmsTelegramLinkRepository.recipients_for_team`. Обоснование: сегодня якорь отсекает **только** `INNER JOIN` по линку — то есть **неявное** условие, нарушение которого (замена на `LEFT`/`OUTER JOIN`, новая выборка получателей без линка) **не ловится ни одним машинным гейтом** (типы и тесты останутся зелёными), а ловится лишь ревью. Цена явного фильтра — три клаузы; выигрыш — инвариант (в) становится **defense-in-depth**, а не единственной преградой. Особенно критично для `sees_all_candidates`: он отбирает получателей **по правам роли**, а роль якоря — `admin` с полным каталогом ⇒ единственная утечка сразу дала бы супер-админу-якорю статус admin-получателя всех писем. **Замена INNER→LEFT/OUTER в этих выборках без сохранения фильтра — по-прежнему ЗАПРЕЩЕНА** (фильтр не отменяет запрет, а страхует его).

**(г) Прямые следствия (без единой строки спец-кода в вышележащих слоях):**

- `GET /api/users` — якоря **нет** в списке (`list_all`). `PATCH`/`DELETE /api/users/{id}` по его `id` → **`404 user_not_found`** (`UserService.update_user`/`delete_user` резолвят через `get_by_id`). Супер-админ по-прежнему «из UI не редактируется и не удаляется» ([ADR-021](ADR-021-rbac-users-roles.md), US-16).
- **Команды:** якорь **невозможно** назначить лидером или участником — `TeamService._require_user_exists` / `_validate_members` резолвят через `get_existing_ids` → **`422`** («Пользователь не существует»).
- **Вход по БД-ветке невозможен:** `AuthService._resolve_db_user` → `get_by_username`/`get_by_telegram` якоря **не находят** (плюс locked-хэш §1.1 — вторая, независимая преграда).
- **Telegram-SSO не резолвит якорь:** `MailTelegramService._resolve_user` (`get_by_id` по линку / `get_by_telegram`) и `SmsTelegramLinkService.auth` якоря **не вернут**; линка у него нет и быть не может (§1.6, инвариант (в)), `telegram` — `NULL`.

#### §1.5. Роли: `user_count` не врёт, FK не ломается

- **Отображение** (`RoleRepository.list_all_with_counts`, `count_users`) — **исключает** якорь (`WHERE NOT users.is_system`): в UI роль `admin` не получает фантомного «+1 пользователь».
- **Гард удаления** (`RoleRepository.is_in_use`) — **включает** якорь (**без** фильтра): он обязан быть зеркалом FK `users.role_id → roles.id ON DELETE RESTRICT`, иначе `DELETE /api/roles/{id}` вместо `409 role_in_use` упал бы `IntegrityError` → `500`.
- **Осознанное следствие (нормативно):** роль, которую держит якорь (по умолчанию встроенная `admin`), **не удаляется** — `409 role_in_use`, даже если её `user_count = 0`. Для встроенной `admin` это усиление уже существующей защиты (`RoleService._ADMIN_ROLE_NAME`), а не новый запрет с сюрпризом.

#### §1.6. Чего якорь НЕ даёт (граница безопасности, нормативно)

Якорь — идентичность для **личного состояния**, но **НЕ**:

| Не даёт | Как обеспечено | Что видит пользователь |
|---------|----------------|------------------------|
| **Альтернативный вход** (пароль в БД, «открытый первый вход») | locked bcrypt-хэш случайного секрета (§1.1) **И** невидимость в `get_by_username`/`get_by_telegram` (§1.4) — две независимые преграды | Логин супер-админа — **только** `ADMIN_USER`/`ADMIN_PASSWORD` constant-time, как и был |
| **Права из БД-роли** | Права супер-админа — `full_catalog_permissions()` в ветке `claims.superadmin` (`backend/app/api/deps.py:108-115`), роль якоря не читается | Правка роли `admin` в UI не меняет полномочий супер-админа (как и раньше) |
| **Привязку Telegram и Telegram-SSO** | **`POST /api/sms/telegram/link` → `403 forbidden` при `principal.is_superadmin`** (условие переписывается с `user_id is None` на `is_superadmin` — поведение **то же, что сегодня**) | Как сегодня: `403` |
| **Персональные Telegram-уведомления почты** | **`GET`/`PATCH /api/mail/me/settings` → `403 forbidden` при `principal.is_superadmin`** (условие тоже переписывается на `is_superadmin`) | Как сегодня: `403`, контрол «Уведомления» в UI скрыт |

**Почему `403` на Telegram-привязке и `/mail/me/settings` СОХРАНЯЕТСЯ, хотя FK теперь выполним** (прямой ответ на вопрос «чинить ли заодно, раз причина общая»):
Причина-«ловушка» (`user_id is None`) устраняется **везде** — но **основание** этих двух `403` меняется с технического на **security**. Разреши мы привязку Telegram к якорю, Mini App-SSO (`MailTelegramService.auth`) выпустил бы для этого Telegram-аккаунта обычный CRM-JWT с `role="admin"` (роль якоря!) — то есть **владение Telegram-аккаунтом стало бы вторым, беспарольным путём к admin-уровню CRM**, в обход `ADMIN_PASSWORD`, причём **неотзываемым из UI** (якоря нет в `/api/users` — его нельзя деактивировать или удалить). Bootstrap-учётка обязана оставаться **console-only** ([ADR-008](ADR-008-admin-iz-env.md): «супер-админ вне БД → систему нельзя залочить через данные»). А раз Telegram-привязки у него нет и быть не может, персональная настройка «получать уведомления в Telegram» для него **бессодержательна** — `403` честнее, чем инертный тумблер.
**Личная прочитанность в этот запрет НЕ попадает:** она не канал доставки и не путь входа — это состояние **чтения того, что он и так вправе прочитать** (граница видимости — `MailScope`, [ADR-044](ADR-044-mail-full-merge-into-crm.md) §7, и она не меняется).

#### §1.7. Смена `ADMIN_USER` / `ADMIN_PASSWORD` в `.env` (нормативно)

**Ничего не происходит — ни с входом, ни с личным состоянием.**

- Вход по-прежнему сверяется constant-time с `settings.admin_user`/`settings.admin_password` (`backend/app/services/auth_service.py:70-87`) — якорь в логине **не участвует**.
- Идентичность якоря привязана к **константе `SUPERADMIN_USER_ID`**, а не к `ADMIN_USER` ⇒ смена логина/пароля **не создаёт вторую строку**, **не переименовывает** якорь и **не теряет** отметки прочитанности (`mail_message_reads` ссылаются на константный `user_id`).
- Строка-якорь **не синхронизируется** с `ADMIN_USER` намеренно: sync-переименование ломалось бы о `uq_users_username` (если новое имя занято реальным пользователем) и валило бы старт — цена нулевая, риск ненулевой. `username` якоря — внутренний технический идентификатор; в API он не показывается.
- Развёртывание с **пустой БД** и другим `ADMIN_USER`: bootstrap создаст якорь с теми же константами — прочитанность работает с первого старта.

#### §1.8. RBAC-инварианты — НЕ меняются (проверено по коду)

| Инвариант | Затронут? | Почему |
|-----------|-----------|--------|
| `require(page, action)` (`backend/app/api/deps.py:167`) | **нет** | Читает `is_superadmin`/`permissions`, не `user_id` |
| `require_admin` (`:176`) | **нет** | `principal.is_superadmin or principal.role == "admin"`; `role` супер-админа — из claim, не из БД-роли якоря |
| **Subset-инвариант эскалации** ([ADR-022](ADR-022-teams-nav-categories.md) §4, `RoleService.update_role`/`create_role`) | **нет** | Считается по `actor_permissions` принципала (полный каталог у супер-админа) — якорь не участвует |
| Защита встроенной роли `admin` | **усилена** | §1.5: роль якоря не удаляется (`409 role_in_use`) |
| **Эскалация через якорь** (может ли кто-то «стать» якорем?) | **нет** | `uid` в JWT выдаётся только резолвом реальной строки (`login` / Telegram-SSO), а якорь этими путями **не резолвится** (§1.4); подделать подписанный JWT нельзя |
| `MailScope` / `SmsScope` (`get_mail_scope`, `get_sms_scope`) | **семантически нет** | У супер-админа `sees_all_teams=True` — ветка `user_id` не достигается. Ветки `if principal.user_id is None → пустой scope` становятся недостижимыми и **снимаются** (§1.2) |
| Fallback-инвариант «нельзя залочить систему через данные» ([ADR-008](ADR-008-admin-iz-env.md)) | **сохранён** | Логин и построение принципала супер-админа **не делают ни одного запроса в БД** (§1.2) |

### §2. Судьба нормы [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.5 (нормативно)

| Пункт §2.5 | Судьба |
|------------|--------|
| `POST`/`DELETE /api/mail/messages/{id}/read` → `403` супер-админу | **ОТМЕНЁН.** Теперь — как у всех: `204` (идемпотентно), вне `MailScope` → `404 mail_message_not_found` |
| `is_unread` всегда `false` у супер-админа | **ОТМЕНЁН.** Теперь — реальное личное значение по `mail_message_reads(user_id=SUPERADMIN_USER_ID, …)` |
| `GET /api/mail/messages?unread=true` → пустая страница у супер-админа | **ОТМЕНЁН.** Теперь — обычный серверный анти-джойн ([ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.4, не меняется) |
| UI скрывает индикатор / фильтр «Непрочитанные» / кнопку «Отметить непрочитанным» при `me.is_superadmin === true` | **ОТМЕНЁН.** Контролы рендерятся **всем** с `mail:view`, безусловно (§3) |
| Прецедент-обоснование «как `/api/mail/me/settings`» | **Больше не является обоснованием прочитанности.** `403` на `/mail/me/settings` **сохраняется**, но по security-основанию (§1.6), а не «нет строки» |
| Принцип «backend-`403` — граница, UI-скрытие — только UX» | **ОСТАЁТСЯ в силе** как общий принцип (граница безопасности всегда на сервере) |
| Общее правило «принципал без `user_id` личного состояния не имеет» | **Становится беспредметным:** после §1.2 принципала без `user_id` **не существует** (`Principal.user_id: uuid.UUID`) — ни у одного аутентифицированного актора |

Остальные разделы [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) (§1, §2.1–§2.4, §2.6–§2.8) — **в силе без изменений**: схема `mail_message_reads`, контракт (`is_unread`, `unread`, два эндпоинта, `404`-анти-энумерация), гейт `mail:view`, производительность (батч-лукап + анти-джойн, без badge-счётчика), пометка при открытии, откат в «непрочитано», серверный фильтр, запрет автополлинга ленты.

### §3. UI после правки (нормативно; точный рендер — [08-design-system.md](../08-design-system.md))

- **Индикатор непрочитанного** (жирная тема + точка `--accent` + sr-only «Непрочитано»), **фильтр-тумблер «Непрочитанные»**, **кнопка «Отметить непрочитанным»** в шапке детали — **рендерятся для ЛЮБОГО пользователя с `mail:view`, включая супер-админа**. Никакого гейта по `me.is_superadmin` в почтовой прочитанности **нет** (условие `readStateEnabled = !isSuperadmin`, `frontend/src/pages/MailPage.tsx:179-180`, **удаляется**).
- **Mini App `/tg/mail`** — без изменений ([ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.8): индикатор есть, фильтра и кнопки отката нет. Супер-админа в Mini App не бывает (SSO поднимает строку `users`, а якорь не резолвится — §1.4).
- **Контрол «Уведомления»** (`MailNotificationsToggle`) — **по-прежнему скрыт** при `me.is_superadmin === true`, но нормативное **основание** меняется: не «нет БД-строки / `403`», а «bootstrap-учётка не привязывается к Telegram, уведомления ей не доставляются» (§1.6). Поведение и код-условие (`if (isSuperadmin) return null`) — те же.

### §4. Судьба потребителей (repo-wide grep, нормативно)

Grep по `user_id is None` / `Principal(` / `is_superadmin` / `UserRepository` / `RoleRepository` (включая зоны **вне** CI-scope: `scripts/`, `tests/`, `alembic/`, конфиги):

| Потребитель | Зона | Судьба | Владелец |
|-------------|------|--------|----------|
| `backend/app/api/deps.py` (`Principal.user_id` → non-optional; ветка `claims.superadmin` → `SUPERADMIN_USER_ID`; `get_sms_scope`/`get_mail_scope` — снять ветки `user_id is None`) | prod | **Изменить** по §1.2 | `backend` |
| `backend/app/domain/superadmin.py` (**новый**: `SUPERADMIN_USER_ID`, `SUPERADMIN_USERNAME`) | prod | **Создать** | `backend` |
| `backend/app/repositories/user_repository.py` — фильтр `is_system` по §1.4(а) + **`get_by_id` переписать с `session.get()` на `select(...)`** (иначе предикат невыразим) + новый `ensure_superadmin_anchor` (§1.3) | prod | **Изменить** | `backend` |
| `backend/app/models/user.py` — колонка `is_system` **И частичный уникальный индекс `uq_users_system_singleton` в `__table_args__`** (миграция его лишь зеркалит — §1.1; иначе схема тестов `create_all` разойдётся с продом) | prod | **Изменить** | `backend` |
| `backend/app/repositories/mail_telegram_link_repository.py` (`team_recipients`, `sees_all_candidates`), `backend/app/repositories/sms_telegram_link_repository.py` (`recipients_for_team`) | prod | **Функционально менять НЕ обязано** (INNER JOIN на линк отсекает якорь — §1.4(б)). **Рекомендуется** добавить явный `WHERE NOT users.is_system`, чтобы инвариант §1.4(в) не был неявным. **ЗАПРЕЩЕНО** менять эти INNER JOIN на LEFT/OUTER без явного фильтра | `backend` |
| `backend/app/repositories/role_repository.py` (§1.5: фильтр в `list_all_with_counts`/`count_users`, **без** фильтра в `is_in_use`) | prod | **Изменить** | `backend` |
| `backend/app/main.py::lifespan` — вызов `ensure_superadmin_anchor()` после блока `startup_recovery` (§1.3). Порядок на проде гарантирован entrypoint'ом `alembic upgrade head && uvicorn …` ([07-deployment.md §Порядок запуска](../07-deployment.md#порядок-запуска)) | prod | **Изменить** | `backend` |
| `backend/app/api/mail_me.py`, `backend/app/api/sms.py` + `backend/app/services/sms_telegram_link_service.py` (условие `403`: `user_id is None` → `principal.is_superadmin`, §1.6) | prod | **Изменить** | `backend` |
| `backend/app/services/mail_service.py` (снять ветки `user_id is None` — `:208`, `:276`, `:336`), `backend/app/infra/audit.py:32` (`user_id` теперь всегда есть) | prod | **Изменить** | `backend` |
| `backend/alembic/versions/0026_users_is_system.py` (**новая**) | prod | **Создать** | `backend` |
| `frontend/src/pages/MailPage.tsx` (`readStateEnabled`/`useIsSuperadmin` в прочитанности — удалить), `MailListItem`/`MailDetail` (пропсы гейта, если есть) | prod | **Изменить** по §3 | `frontend` |
| `frontend/src/components/MailNotificationsToggle.tsx` | prod | **НЕ менять** (поведение то же; правится только комментарий-обоснование) | `frontend` |
| `backend/tests/conftest.py`, `backend/tests/unit/test_principal_enforcement.py`, `backend/tests/integration/mail_s34_helpers.py`, `backend/tests/integration/sms_helpers.py` — **конструируют `Principal(...)` без `user_id`** ⇒ после §1.2 **перестанут компилироваться/проходить**; плюс кейсы `403` супер-админу в `backend/tests/integration/test_mail_message_read_api.py` (ADR-050 §2.5) становятся **orphaned** | **тесты** | **Переписать под новый контракт** (обязательный `user_id`; супер-админ → `SUPERADMIN_USER_ID`; `403`-кейсы прочитанности → заменить на «супер-админ отмечает и видит `is_unread` персонально»; `403` на `/mail/me/settings` и `/sms/telegram/link` — **оставить**). **ОБЯЗАТЕЛЬНО (§1.3): фикстуры БД (`mail_helpers.py:56`, `mail_s34_helpers.py:57`, `sms_helpers.py:59`) вызывают `ensure_superadmin_anchor(session)` СРАЗУ ПОСЛЕ `Base.metadata.create_all`** — иначе якоря в тестовой БД нет и кейсы прочитанности супер-админа падают на FK (`500`). **⚠️ Побочный эффект нового порядка (учесть в тестах):** роли в фикстурах ещё не посеяны ⇒ сработает шаг (3) цепочки и **в КАЖДОЙ интеграционной тестовой БД появится реальная роль `admin` с полным каталогом**. Следствия: `GET /api/roles` отдаёт **на одну роль больше** (ассерты на состав/длину списка поедут — поправить); создание в тесте роли с именем `admin` → **`409 role_name_taken`**; роль `admin` **неудаляема** (`409 role_in_use` — её держит якорь, §1.5) | **`qa`** |
| `frontend/src/pages/__tests__/MailPage.test.tsx`, `frontend/src/pages/__tests__/MailMiniAppPage.test.tsx`, `frontend/src/components/__tests__/MailDetail.test.tsx`, `frontend/src/features/mail/__tests__/hooks.test.tsx` (кейсы «у супер-админа контролы скрыты») | **тесты** | **Переписать** под §3 | **`qa`** |
| `backend/scripts/`, `alembic/`, конфиги, `.env.example` | вне CI-scope | **Живых потребителей нет** (repo-wide grep: в `backend/scripts/` осталась только `__pycache__`-артефакт удалённого ETL — исполняемых скриптов, читающих `users`, нет). **Новых env-переменных ADR НЕ вводит** (`ADMIN_USER`/`ADMIN_PASSWORD` — без изменений) ⇒ `.env.example` и конфиги **не трогаются** | — |

**Гарантия полноты — МАШИННЫЙ ГЕЙТ (нормативно), а не таблица выше:** перед деплоем — зелёные `ruff` + **`mypy`** (именно он ловит каждое место, где `Principal.user_id` считался `Optional` — снятие `| None` из типа делает пропуск невозможным) + `tsc -b` + **полный** CI-scope тестов + `alembic upgrade head` на копии прода. Таблица — guidance/порядок, **не** исчерпывающий перечень.

## Последствия

- **Владелец получает то, что просил:** под консольным `admin` письма помечаются прочитанными при открытии, индикатор гаснет, фильтр «Непрочитанные» работает, откат в «непрочитано» доступен. Прочитанность **осталась личной**: отметки супер-админа не гасят индикатор коллегам (у него собственный `user_id`).
- **Класс дефектов закрыт, а не один случай.** `Principal.user_id` больше не `Optional` ⇒ следующая персональная фича (избранное, черновики, per-user фильтры) заработает для супер-админа **по умолчанию**, без нового ADR и без «ловушки».
- **Одна миграция `0026_users_is_system`** — аддитивная (колонка + частичный уникальный индекс; индекс объявлен в модели, миграция зеркалит). **`downgrade()` (нормативно): `DELETE FROM users WHERE is_system` → `DROP INDEX uq_users_system_singleton` → `DROP COLUMN is_system`.** Удаление строки в `downgrade` **обязательно**: иначе якорь остался бы в `users` как **обычный** пользователь с ролью `admin` — видимый в `/api/users`, редактируемый (можно задать ему пароль!), т.е. откат схемы создал бы учётку-призрак с admin-ролью. Цена (принимается): `ON DELETE CASCADE` унесёт отметки прочитанности супер-админа — при откате схемы это корректно (личное состояние на откатываемой версии всё равно неработоспособно). **Backfill не нужен:** `DEFAULT false` для 9 существующих строк корректен; строка-якорь создаётся bootstrap'ом. На проде — мгновенная.
- **Поверхность безопасности не расширена:** вход супер-админа — только `.env` (locked-хэш + невидимость якоря в резолве логина), Telegram-SSO к якорю не ведёт, права берутся не из БД-роли, якорь недоступен в `/api/users`, `/api/teams`, `/api/roles`.
- **Смена `ADMIN_USER`/`ADMIN_PASSWORD` — no-op** для личного состояния (§1.7): идентичность на константе, а не на логине.
- **Роль `admin` становится неудаляемой** (`409 role_in_use`), пока её держит якорь (§1.5) — осознанное усиление защиты встроенной роли.
- **`GET`/`PATCH /api/mail/me/settings` и `POST /api/sms/telegram/link` для супер-админа — по-прежнему `403`** (§1.6), но по осознанному security-основанию. Внешне для владельца ничего не меняется.
- **Тесты — обязательный хендофф на `qa`** (§4): снятие `| None` из типа `Principal.user_id` ломает компиляцию тестов, конструирующих `Principal` без `user_id`; `403`-тесты прочитанности супер-админа — orphaned по этому ADR. Это **штатный** хендофф (правило `CLAUDE.md` про orphaned qa-тесты), а не дефект backend'а.
- **QA (обязательный минимум):** супер-админ помечает письмо → `204`, `is_unread=false` **только у него**, у другого пользователя того же письма — `true`; `unread=true` под супер-админом отдаёт непрочитанные (не пустую страницу); якорь **отсутствует** в `GET /api/users`; `PATCH`/`DELETE /api/users/{SUPERADMIN_USER_ID}` → `404`; якорь как `leader_id`/`member_ids` → `422`; **логин под `superadmin@system`** (с любым паролем и без пароля) → `401`, **setup-token не выдаётся**; `DELETE` роли `admin` → `409 role_in_use`; `user_count` роли `admin` не включает якорь; повторный старт приложения не создаёт вторую строку (идемпотентность bootstrap'а); `403` сохраняется на `/mail/me/settings` и `/sms/telegram/link` под супер-админом.

## Альтернативы (отклонены)

1. **Оставить как есть (норма [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.5) и предложить владельцу завести себе БД-пользователя.** Отклонено: владелец прямо потребовал работу под консольным `admin` (он ходит на прод под ним); плюс это не устраняет класс дефектов — следующая персональная фича упрётся в ту же стену.
2. **Ключ прочитанности не FK на `users`, а текстовый `principal_key`** (`username` или `"__superadmin__"`), т.е. `mail_message_reads.user_id → TEXT`. **Отклонено:** (а) теряется `ON DELETE CASCADE` — отметки удалённых пользователей превращаются в мусор, который никто не чистит; (б) ключ становится **неустойчивым**: у супер-админа он завязан на `ADMIN_USER`, и смена логина в `.env` **молча теряет** всю его прочитанность (ровно то, что §1.7 гарантирует не терять); (в) один и тот же субъект получает два разных представления (uuid у БД-юзеров, строка у супер-админа) — каждая будущая персональная таблица обязана повторять эту развилку; (г) ломает уже задеплоенную схему `0025` (`user_id uuid`, PK, FK) — потребовалась бы миграция данных ради худшей модели.
3. **Отдельная таблица `mail_message_reads_superadmin(message_id)`** (у супер-админа нет FK на `users`, а он один). **Отклонено:** минимальный blast-radius, но **удваивает каждый путь** прочитанности навсегда (батч-лукап, анти-джойн `unread`, `POST`, `DELETE` — везде ветвление «супер-админ / все остальные»), и **не лечит класс**: `mail_user_settings` (и любая следующая персональная таблица) потребует третьей таблицы и третьей развилки. Мы выбрали дать субъекту **идентичность**, а не размножать хранилища.
4. **Строка-якорь как ОБЫЧНЫЙ пользователь (виден в `/users`, редактируем, синхронизируется с `ADMIN_USER`).** **Отклонено, это дыра:** (а) `password_hash IS NULL` → «открытый первый вход» ([ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md)) отдаёт setup-token любому, кто знает `username` → эскалация до роли `admin`; (б) админ мог бы **деактивировать/удалить** супер-админа из UI → нарушение US-16 и fallback-инварианта [ADR-008](ADR-008-admin-iz-env.md); (в) синхронизация `username` с `ADMIN_USER` роняет старт приложения при коллизии с реальным пользователем (`uq_users_username`); (г) он попадал бы в `user_count` ролей и в кандидаты лидера/участника.
5. **Класть `uid` супер-админа в JWT** (вместо константы в `get_current_principal`). **Отклонено:** второй источник истины для фиксированного значения + **все уже выпущенные токены супер-админа стали бы неполноценными** (`uid` нет → `user_id` нет) — потребовался бы форс-релогин ради нуля выгоды. Константа даёт то же самое без единого запроса в БД и без миграции токенов.
6. **Резолвить якорь из БД в `get_current_principal`** (SELECT по `is_system` на каждый запрос супер-админа). **Отклонено:** лишний запрос в hot-path и, главное, **нарушение fallback-инварианта** [ADR-008](ADR-008-admin-iz-env.md) — принципал супер-админа стал бы зависеть от доступности/целостности `users`.
7. **Разрешить якорю Telegram-привязку** (раз FK теперь выполним — «пусть и уведомления работают»). **Отклонено (security):** Mini App-SSO выпустил бы для этого Telegram-аккаунта CRM-JWT с `role="admin"` — беспарольный, неотзываемый из UI второй путь к admin-уровню в обход `ADMIN_PASSWORD` (§1.6).
8. **Сделать прочитанность общей на команду** (одна колонка `mail_messages.is_read`) — тогда супер-админ «работает» без якоря. **Отклонено:** прямо противоречит подтверждённому требованию владельца («прочитанность остаётся личной»); прочтение одним оператором гасило бы индикатор всей команде.
