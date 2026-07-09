# Модуль `sms` — СМС (Twilio-приём + Telegram-доставка операторам)

Статус: `spec-ready` · Исполнитель: backend, frontend, devops

## Scope

Страница **«СМС»** в CRM (категория «Агрегатор», рядом с «Почты») + доработка `/teams`. Полное слияние движка SMS-агрегатора в CRM ([ADR-030](../../adr/ADR-030-sms-module-full-merge.md)): приём входящих SMS от Twilio по webhook (проверка подписи), хранение номеров/сообщений в БД `crm`, привязка номеров к **CRM-командам** и доставка входящих операторам в **Telegram** (fan-out по команде + retry дохлых доставок + dead-links). Контракт — [04-api.md#sms](../../04-api.md#sms); модель — [03-data-model.md](../../03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links).

Функции Этапа 1:
- **Вкладка «Сообщения»** — лента входящих SMS (newest-first, keyset-курсор). Карточка: `from_number → to_number` + бейдж команды (зелёная пилюля / серая «Команды нет») + дата; пилюли `Логин:`/`Приложение:`/`Примечание:`; текст SMS. Два фильтра (взаимо-**комбинируемые**): «Все номера» + «Все команды».
- **Вкладка «Номера»** — таблица всех видимых номеров с инлайн-правкой `login`/`app_name`/`note`, переносом в команду (`transfer`), удалением (`delete`), синхронизацией из Twilio (`sync`). Клиентский поиск по номеру.
- **Twilio-приём** — публичный webhook `POST /api/sms/webhooks/twilio/sms` (проверка `X-Twilio-Signature`); дедуп по `MessageSid`; резолв команды по номеру-получателю; crash-recoverable fan-out.
- **Telegram-доставка** — новый бот (`SMS_TELEGRAM_BOT_TOKEN`): fan-out входящего SMS всем операторам команды (по `user_teams` + живой линк), retry pending/failed фоновым монитором, dead-link при `403`. Привязка оператора — операторская **Mini App** на маршруте `/tg/sms` (**беспарольный Telegram-SSO**: `POST /api/sms/telegram/auth` резолвит оператора по Telegram-идентичности → CRM-JWT + авто-линк), см. §«Операторская Telegram Mini App» и [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md).
- **Доработка `/teams`** — `number_count` на карточке команды + список номеров команды (`GET /api/teams/{id}/numbers`) в detail-панели.

## Out of scope (Этап 1)

- **Исходящие SMS / ответ на SMS** — только приём входящих (в отличие от `mail`-reply). Отправка через Twilio — вне scope.
- **Импорт исторических данных** из старой SMS-БД — не мигрируем в первом переносе (BIGINT→UUID id-маппинга нет) — [TD](../../100-known-tech-debt.md) «Импорт исторических данных SMS».
- **Ручное создание номера** — номера появляются только автоматически (входящие SMS + `POST /api/sms/numbers/sync`). Действие `create` в RBAC-каталоге `sms` отсутствует ([ADR-030](../../adr/ADR-030-sms-module-full-merge.md)).
- **Редактирование `label`** — системное поле (зеркало Twilio `friendly_name`), правится только `sync`, не через UI.
- **Донорские auth/Redis** (сессии/CSRF/lockout/pending Mini App SSO) — заменены JWT+RBAC CRM, Redis не добавляется.
- **Управление составом команды из страницы «СМС»** — только через `/teams`/`/users`.

### Известные ограничения (осознанно отложены)

- **[TD-030](../../100-known-tech-debt.md)** — исторические SMS-данные не мигрируются; rate-limit webhook/link — in-memory; `label` авто-обновляется только при `sync`.
- **[TD-031](../../100-known-tech-debt.md)** — **гонка двойной доставки (at-least-once):** retry-монитор может параллельно повторить только что зарезервированную (ещё не отправленную) pending-доставку → возможен дубль Telegram-сообщения. Корректность сохранена (UNIQUE доставки), дублируется лишь сам месседж. Fix — claim со статусом `sending`+lease или `SKIP LOCKED`.
- **[TD-032](../../100-known-tech-debt.md)** — **синхронный fan-out в теле webhook:** доставка всем получателям идёт до возврата `200` Twilio; на крупной команде/медленном Telegram возможен таймаут Twilio (~15с) → ретрай. Корректность сохранена (дедуп по SID + идемпотентный `try_reserve` + retry-монитор). Fix — вернуть `200` сразу, fan-out в фоновую задачу.

## Архитектура (backend)

Порт движка донора (`sms-agreagtor`) на модели/сессии CRM. **Слои** (по образцу существующих модулей):

- **Модели** (`app/models/sms_*.py` + экспорт в `models/__init__.py`) — 4 ORM-класса, PK `BigInteger Identity`, внешние FK — `UUID` на CRM `teams`/`users`. DDL — [03-data-model.md](../../03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links). Миграция `0017_create_sms_module` (`down_revision = "0016_backfill_team_leaders"`).
- **Схемы** (`app/schemas/sms.py`) — Pydantic-модели контракта [04-api.md#sms](../../04-api.md#sms): `SmsMessageItem`/`SmsMessagesResponse`, `SmsNumberItem`/`SmsNumbersResponse`, `SmsNumberRef`, `SmsTeamRef`, `SmsNumberUpdateRequest` (presence-семантика `login`/`app_name`/`note`), `SmsNumberTransferRequest`, `SmsSyncResult`, `TelegramLinkRequest`/`TelegramLinkResponse`, `TelegramAuthRequest`/`TelegramAuthResponse`, `TeamNumberItem`/`TeamNumbersResponse` (**минимальная** схема для teams-detail — см. §Доработка `/teams`).
- **Репозитории** (`app/repositories/sms_*.py`) — порт `app/infrastructure/repositories.py` донора: `SmsNumberRepository` (find_by_phone, list_all, list_by_team(s), upsert-sync, set fields, transfer, delete), `SmsInboundRepository` (find_by_sid, create, keyset `list_inbound`), `SmsDeliveryRepository` (try_reserve, mark_sent/failed/dead, pending), `SmsTelegramLinkRepository` (upsert, mark_dead, get_active, `recipients_for_team` = JOIN `user_teams`→`users`→`sms_telegram_links` WHERE `dead_at IS NULL`).
- **Сервисы** (`app/services/`):
  - `sms_ingest_service.py` — порт `application/services.py`: `handle_incoming_sms` (нормализация → дедуп по SID → сохранение → fan-out), `_deliver`, `deliver_sms_to_recipient`, `format_sms_message`. Транзакционная модель — как у донора (crash-recoverable, `try_reserve` идемпотентен по UNIQUE `(inbound_sms_id, telegram_user_id)`).
  - `sms_message_service.py` — порт `messages_service.py` + keyset-курсор: `list_messages(sees_all_teams, team_ids, number_id, team_id, cursor, limit)` → страница + `next_cursor`. Видимость — по **текущей** принадлежности номера (§Видимость; `sees_all_teams` — [ADR-032](../../adr/ADR-032-sms-visibility-admin-full-catalog.md)).
  - `sms_number_service.py` — список/правка полей/перенос/удаление номеров.
  - `sms_sync_service.py` — порт `twilio_sync_service.py`: подтянуть все входящие номера Twilio (пагинация), upsert как unassigned (`ON CONFLICT (phone_number) DO NOTHING`), обновить `label` из `friendly_name`.
  - `sms_delivery_monitor_service.py` — фоновый retry-loop (порт `retry_pending_deliveries`) по образцу `proxy_monitor_service.py`; стартует в `lifespan` при `sms_bot_enabled`; интервал `SMS_DELIVERY_RETRY_INTERVAL_SEC`, потолок попыток `SMS_DELIVERY_MAX_ATTEMPTS`.
  - `sms_telegram_link_service.py` — привязка/статус Mini App (`verify_init_data` + upsert линка текущего CRM-юзера).
- **Домен** (`app/domain/sms.py`) — чистые функции: `normalize_phone`, `encode_cursor`/`decode_cursor` (base64url `(received_at, id)`), `verify_init_data` (порт `telegram/init_data.py`, HMAC-SHA256 + TTL `auth_date`).
- **Инфра** (`app/infra/`):
  - `twilio_security.py` — `validate_twilio_signature` (SDK `twilio.request_validator.RequestValidator`).
  - `twilio_numbers.py` — синхронный Twilio SDK через `asyncio.to_thread` (список входящих номеров, пагинация).
  - `sms_telegram.py` — новый `SmsBotClient` (`sendMessage` c `reply_markup`, `setWebhook`, `setMyCommands`); **отдельный токен** `SMS_TELEGRAM_BOT_TOKEN`. **Notifier-бот `app/infra/telegram.py` НЕ трогается** ([ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §4).
- **Точки регистрации:** `app/api/router.py` (include SMS-роутеров), `app/api/deps.py` (`Principal.user_id`, `SmsScope`, фабрики сервисов), `app/domain/permissions.py` (`CATALOG["sms"]`), `app/main.py` (старт retry-монитора в `lifespan` при `sms_bot_enabled`), `app/config.py` (namespaced-env).

## Endpoints (контракт — [04-api.md#sms](../../04-api.md#sms))

Приватные (JWT), префикс `/api`:
- `GET /api/sms/messages` — лента (фильтры `number_id`/`team_id`, курсор `cursor`/`limit`), `require("sms","view")` + scope.
- `GET /api/sms/numbers` — список номеров, `require("sms","view")` + scope.
- `PATCH /api/sms/numbers/{id}` — правка `login`/`app_name`/`note`, `require("sms","edit")`.
- `POST /api/sms/numbers/{id}/transfer` — назначить/снять команду, `require("sms","transfer")`.
- `DELETE /api/sms/numbers/{id}` — удалить номер, `require("sms","delete")`.
- `POST /api/sms/numbers/sync` — синк из Twilio, `require("sms","sync")`.
- `POST /api/sms/telegram/link` — self-link текущего юзера, **только JWT** (без action `sms`). Mini App его **не использует** (привязка авто-происходит в SSO `telegram/auth`, [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)).
- `GET /api/teams/{id}/numbers` — номера команды для detail-панели `/teams`, `require("teams","view")`. Отдаёт **минимальную** `TeamNumberItem` (`id`/`phone_number`/`team`) — **без** `login`/`app_name`/`note`/`label` (авторизационное сужение, [ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §8).

Публичные (без JWT, гейтятся подписью/секретом/HMAC), наружу через nginx:
- `POST /api/sms/webhooks/twilio/sms` — приём SMS (подпись `X-Twilio-Signature`).
- `POST /api/sms/telegram/webhook` — апдейты SMS-бота (секрет `X-Telegram-Bot-Api-Secret-Token`, constant-time).
- `POST /api/sms/telegram/auth` — **беспарольный Telegram-SSO** Mini App (HMAC `init_data` → резолв оператора → CRM-JWT + авто-линк; не сопоставлен → `403 sms_operator_not_provisioned`, [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)).

Операторская Mini App (`/tg/sms`, [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)) использует **беспарольный SSO** через **пересмотренный** `POST /api/sms/telegram/auth` (initData → резолв оператора по `telegram_user_id`/`users.telegram` → авто-upsert линка + **выдача CRM-JWT**; не сопоставлен → `403 sms_operator_not_provisioned`). Просмотр номеров/сообщений — существующие `GET /api/sms/numbers`/`GET /api/sms/messages` под этим JWT и `sms:view`. Требуется backend-доработка `telegram/auth` (см. §Backend-доработка Telegram-SSO).

Расширение существующего: `GET /api/teams` — новое поле `number_count` в `TeamListItem` ([04-api.md#teams](../../04-api.md#teams)).

## Видимость по командам (нормативно)

`Principal` расширяется полем **`user_id: UUID | None`** (из claim `uid`; супер-админ → `None`). `SmsScope` (фабрика `get_sms_scope` в `deps.py`) несёт флаг **`sees_all_teams`** ([ADR-032](../../adr/ADR-032-sms-visibility-admin-full-catalog.md)):
- **admin-уровень** (`sees_all_teams = is_superadmin ИЛИ роль владеет полным каталогом прав` — `permissions_subset(full_catalog_permissions(), permissions)`; консольный супер-админ, seed-`admin`, кастомная «Админ» с полным каталогом) → видит **все** SMS/номера; опц. фильтры `number_id`/`team_id` применяются как есть. Признак устойчив к переименованию роли, без нового права/миграции.
- **прочие роли** (неполный каталог: PM, «Пользователь» и т.п.) → `team_ids` пользователя из `user_teams`; видимые номера = `sms_phone_numbers.team_id ∈ team_ids` (по **текущей** принадлежности, не по снимку). SMS — только на видимые `to_number`. Запрос `number_id`/`team_id` вне scope → **пустой результат** (анти-энумерация, не `403`/`404`). Правило симметрично для сообщений и номеров.

**Снимок `sms_inbound.team_id`** пишется на момент приёма (определяет получателей fan-out) и обнуляется `ON DELETE SET NULL` при удалении команды; **для отображения** (бейдж команды, пилюли) карточка использует **текущий** номер (`sms_phone_numbers` по `to_number`). SMS на unassigned-номер (`team_id IS NULL`) или на удалённый номер видны **только actor'у admin-уровня** (`sees_all_teams`).

## Приём SMS и fan-out (нормативно)

`handle_incoming_sms` (порт донора, транзакционная модель сохраняется):
1. Нормализация `to_number`/`from_number` (E.164).
2. Дедуп по `twilio_message_sid` (partial-UNIQUE `sms_inbound_sid_uq`): дубликат/webhook-retry **не** делает ранний возврат — идёт в общий fan-out (crash-recovery); гонка на insert (`IntegrityError`) → чтение уже сохранённого SMS.
3. Резолв команды по номеру-получателю (`sms_phone_numbers.team_id` на момент приёма) → снимок в `sms_inbound.team_id`. Неизвестный номер (`team_id IS NULL`) → SMS сохраняется, доставок нет.
4. Резолв получателей команды (`recipients_for_team` = участники `user_teams` с живым `sms_telegram_links`).
5. Fan-out: на каждого получателя `try_reserve` (идемпотентно по UNIQUE `(inbound_sms_id, telegram_user_id)`) → `SmsBotClient.sendMessage`. Успех → `mark_sent`; `403`/forbidden → `mark_dead` + `link.mark_dead`; прочая ошибка Bot API → `mark_failed` (переотправит retry-монитор).

**Текст сообщения (нормативно, порт `format_sms_message`):**
```
📩 Новое SMS

📱 Номер: {to_number}
👤 От: {from_number}
💬 Текст: {body}
🕒 Время: {DD.MM HH:MM local}
```
Длинные сообщения (> 3500 симв.) разбиваются на части (`_split_message`).

**Retry-монитор** (`sms_delivery_monitor_service.py`): периодически (`SMS_DELIVERY_RETRY_INTERVAL_SEC`) добирает `sms_deliveries` со `status ∈ (pending, failed)` и `attempts < SMS_DELIVERY_MAX_ATTEMPTS` (partial-индекс `ix_sms_deliveries_retry`); отсутствует исходное SMS → `mark_failed`; линк мёртв → `mark_dead`; иначе повтор отправки. Стартует в `lifespan` только при `sms_bot_enabled` (задан `SMS_TELEGRAM_BOT_TOKEN`).

## Telegram-привязка оператора (нормативно)

Без Redis/pending ([ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §3). Основной путь Mini App — **беспарольный Telegram-SSO** ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md), см. §«Операторская Telegram Mini App»):
- **Беспарольный SSO** — `POST /api/sms/telegram/auth` (публичный, HMAC `init_data`): резолвит CRM-оператора по `telegram_user_id`/`users.telegram`, **авто-upsert линка** (`sms_telegram_links(telegram_user_id → user_id, dead_at=NULL)`, revive при `dead_at`) и **выдаёт CRM access-JWT**. Не сопоставлен → `403 sms_operator_not_provisioned`. Это основная точка привязки оператора.
- **Self-link** — `POST /api/sms/telegram/link` (**только аутентификация**, любой валидный JWT): проверяет initData → upsert `sms_telegram_links(telegram_user_id, user_id = principal.user_id, dead_at = NULL)` (идемпотентно, `ON CONFLICT (telegram_user_id) DO UPDATE`). Привязывает **свой** Telegram к своему CRM-юзеру (напр. из админ-SPA); **Mini App его не использует** (привязка авто-происходит в SSO). **Гейтится только JWT, не action `sms`** — доставка операторам определяется членством в команде (`user_teams`), а не правом на страницу.
- **Webhook бота** — `POST /api/sms/telegram/webhook`: бот обрабатывает **только `/start`** → `sendMessage` с кнопкой `web_app` (`url = SMS_TELEGRAM_WEBAPP_URL`); прочие апдейты → `200` no-op. Валидация секрет-токена `X-Telegram-Bot-Api-Secret-Token` constant-time (`secrets.compare_digest`) до разбора тела; несовпадение → `403`.

## Операторская Telegram Mini App (нормативно)

Страница, которую **оператор** открывает **внутри Telegram** (по кнопке от SMS-бота), чтобы **беспарольно** войти (по Telegram-идентичности) и видеть свои номера/сообщения. Решение — [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md). Без Mini App после cutover привязок `sms_telegram_links` нет → доставка не работает (это блокер доставки).

### Маршрут и модель (ADR-031)
- **Маршрут — выделенный публичный SPA `/tg/sms`**, вне `AppLayout` и вне page-guard'ов RBAC: **без** redirect на `/login`, **без** заглушек «Недостаточно прав», **без** админского nav-shell. Отдаётся тем же `index.html` (`try_files $uri /index.html`), React Router резолвит `/tg/sms` в компонент `SmsMiniAppPage` **вне** ветки `AppLayout` (сосед публичного `/login`). `SMS_TELEGRAM_WEBAPP_URL = https://broadappsdev.shop/tg/sms` ([07-deployment.md](../../07-deployment.md#переменные-окружения)).
- **Онбординг — беспарольный Telegram-SSO** ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md), решение пользователя). Оператор определяется по Telegram-идентичности (сопоставление с `users.telegram`), **без пароля**; сервер выдаёт CRM-JWT и авто-привязывает линк. `init_data` — **аутентификатор** (HMAC доказывает владение `telegram_user_id` и текущим `username`). Оператор обязан иметь CRM-аккаунт с заполненным `telegram` (заводит админ) и роль с `sms:view` (иначе просмотр пуст).

### Флоу end-to-end (беспарольный SSO)
1. Оператор → боту `/start` → `POST /api/sms/telegram/webhook` → бот отвечает кнопкой `web_app` (`url = …/tg/sms`).
2. Кнопка → Telegram открывает Mini App на `/tg/sms` (передаёт `initData`).
3. Mini App читает `initData` (self-hosted SDK, см. §Безопасность) → `POST /api/sms/telegram/auth` (SSO).
4. Сервер резолвит оператора (§Резолв ниже): **успех** → `{ access_token, token_type, expires_in, telegram_user_id, linked:true }`, линк upserted/revived, `first_login_at` проставлен идемпотентно ([ADR-028](../../adr/ADR-028-user-status-first-login.md)).
5. Mini App сохраняет `access_token` в памяти auth-store → показывает статус «Привязан» + свои номера/сообщения через `GET /api/sms/numbers`/`GET /api/sms/messages` под этим JWT и `sms:view` (SMS-scope до команд оператора, §Видимость).
6. **`403 sms_operator_not_provisioned`** → Mini App показывает «Ваш Telegram не сопоставлен с оператором CRM. Обратитесь к администратору».
7. Привязка → fan-out входящих SMS команды оператора идёт ему в Telegram (§Приём SMS и fan-out).

### Резолв оператора (нормативно)
1. Валидировать `init_data` (HMAC + TTL). Извлечь `telegram_user_id` и `username` (может отсутствовать).
2. **Первично — по иммутабельному `telegram_user_id`:** линк `sms_telegram_links WHERE telegram_user_id = X` (независимо от `dead_at`). Есть → `user_id`; `dead_at` не пуст → revive (`dead_at=NULL`).
3. **Bootstrap — по username (только если линка нет):** `username` есть → `normalize_telegram(username)` → `users WHERE telegram = norm AND is_active` → upsert линк.
4. `user_id` активен → идемпотентно `first_login_at` → выпуск access-JWT (`sub = users.username`, `uid`/`role`/`superadmin:false`) → `200`. **`sub` — ВСЕГДА `users.username` резолвнутого пользователя** (не Telegram-`username` из `init_data`, который в id-first-пути может быть `None`) — консистентно с `POST /api/auth/login`.
5. Иначе → `403 sms_operator_not_provisioned`.

**Приоритет:** `telegram_user_id` (через линк) первичен — иммутабелен, переживает смену ника; `username` — bootstrap первого контакта. Ник сменился **после** привязки → вход работает (линк по id); ник сменился **до** первой привязки и в `users.telegram` старый → нет совпадения до обновления админом. Полный контракт — [04-api.md#post-apismstelegramauth](../../04-api.md#post-apismstelegramauth).

Супер-админ (`.env`, без `uid`/`telegram`) через SSO не резолвится (`403 sms_operator_not_provisioned`) и получателем доставок не является ([ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §7). `POST /api/sms/telegram/link` (JWT self-link) **Mini App не использует** — привязка авто-происходит в SSO.

### Требования к провижинингу операторов
- Оператор = CRM-пользователь (`users`) с **заполненным `telegram`** (нормализованный ник совпадает с его ником в Telegram) — заводит админ.
- Роль оператора **обязана включать `sms:view`** (иначе Mini App покажет пустой просмотр).
- Оператор должен быть в нужной команде (`user_teams`) — иначе fan-out его не достигнет.
- Оператор должен иметь публичный Telegram-`@username` (без ника username-bootstrap невозможен).

### Открытые продуктовые развилки
- **[Q-SMS-1](../../99-open-questions.md)** (resolved, native-only) — браузерный Telegram Web не поддержан (CSP `frame-ancestors 'none'` не ослабляется).
- **[Q-SMS-2](../../99-open-questions.md)** (resolved) — номера/сообщения оператор видит через существующие JWT-эндпоинты под `sms:view`; отдельный initData-scoped read-эндпоинт не нужен.
- **[Q-SMS-3](../../99-open-questions.md)** (open) — риск подмены telegram-ника до первого линка + политика авто-провижининга операторов (дефолт: ручное заведение админом).

## Backend-доработка Telegram-SSO (нормативно, ADR-031)

Беспарольный SSO пересматривает **существующий** `POST /api/sms/telegram/auth` (сейчас возвращает лишь `{ linked, telegram_user_id }`). Требуется:

1. **Схема ответа** `TelegramAuthResponse` (`app/schemas/sms.py`) — заменить на SSO-форму: `access_token: str`, `token_type: str = "bearer"`, `expires_in: int`, `telegram_user_id: int`, `linked: bool` (полный контракт — [04-api.md#post-apismstelegramauth](../../04-api.md#post-apismstelegramauth)).
2. **Сервис** `sms_telegram_link_service.py::auth` — реализовать резолв (§Резолв оператора): валидация initData (уже есть `verify_init_data` → `ValidatedInitData` c `telegram_user_id` и `username`); поиск линка по `telegram_user_id` (независимо от `dead_at`, revive при `dead_at`); иначе bootstrap `UserRepository.get_by_telegram(normalize_telegram(username))` (обе функции уже есть) + upsert линка; идемпотентный `first_login_at` ([ADR-028](../../adr/ADR-028-user-status-first-login.md)); выпуск JWT `issue_access_token(sub=user.username, role=user.role.name, superadmin=False, uid=str(user.id))` (уже есть). **`sub` берётся из `users.username` резолвнутого пользователя, НЕ из Telegram-`username` initData** (тот в id-first-пути может быть `None` → malformed JWT; login-флоу тоже кладёт CRM-логин). Сервису нужны зависимости `UserRepository` + `Settings` (для TTL/JWT).
3. **Репозиторий** `SmsTelegramLinkRepository` — метод получения линка по `telegram_user_id` **независимо от `dead_at`** + revive (`dead_at=NULL`) при upsert (для шага 2).
4. **Ошибка** `sms_operator_not_provisioned` (`app/errors.py`, `403`) — не сопоставлен активный оператор. `init_data`/`username` не логируются.
5. **Роутер** `sms_webhooks.py::telegram_auth` — вернуть новую `TelegramAuthResponse`; остаётся публичным (CSRF/JWT-exempt).
6. **Регистрация** SPA-маршрута `/tg/sms` вне `AppLayout` и вендоринг `telegram-web-app.js` — на стороне frontend/devops (не backend).

## Безопасность (нормативно)

Детали — [05-security.md](../../05-security.md#защита-модуля-смс-twilio--telegram). Кратко:
- **Twilio-подпись**: `POST /api/sms/webhooks/twilio/sms` валидирует `X-Twilio-Signature` по `TWILIO_AUTH_TOKEN` (при `VERIFY_TWILIO_SIGNATURE=true`); URL для подписи реконструируется **только из `SMS_PUBLIC_BASE_URL` + путь** (единственный источник истины; `X-Forwarded-*` для подписи не используется — [05-security.md](../../05-security.md#подпись-twilio-post-apismswebhookstwiliosms)). Неверная подпись → `401 invalid_twilio_signature`.
- **Telegram-webhook-секрет**: `SMS_TELEGRAM_WEBHOOK_SECRET`, constant-time compare; `raw` тело/токены не логируются.
- **Mini App initData**: HMAC-SHA256 (`WebAppData`-ключ из `SMS_TELEGRAM_BOT_TOKEN`) + TTL `auth_date`; `init_data` не логируется.
- **Маршрут Mini App `/tg/sms`** ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md), [05-security.md](../../05-security.md#операторская-mini-app-tgsms-adr-031)): публичный (без JWT-redirect), вне админского SPA-shell; **Telegram WebApp SDK self-hosted** (`/telegram-web-app.js`, свой origin) — CSP `script-src 'self'` **не ослабляется**; поверхность — нативные Telegram-webview (браузерный Telegram Web вне MVP, `frame-ancestors 'none'` не ослабляется, [Q-SMS-1](../../99-open-questions.md)).
- **Секреты** (`TWILIO_AUTH_TOKEN`, `SMS_TELEGRAM_BOT_TOKEN`, `SMS_TELEGRAM_WEBHOOK_SECRET`) — только из env, не в БД/логах/ответах API/SPA/URL.

## Каскады удаления (нормативно)

- **Удаление номера** (`DELETE /api/sms/numbers/{id}`): удаляется строка `sms_phone_numbers`; **`sms_inbound` не затрагивается** (нет FK inbound→number, связь по строке `to_number`) → история SMS сохраняется. Такие SMS (номера больше нет) видны **только actor'у admin-уровня** (`sees_all_teams`, [ADR-032](../../adr/ADR-032-sms-visibility-admin-full-catalog.md)).
- **Удаление пользователя**: `sms_telegram_links` (`user_id`) и `sms_deliveries` (`user_id`) — `ON DELETE CASCADE`; `sms_phone_numbers.added_by_user_id` — `SET NULL`. `sms_inbound` не затрагивается.
- **Удаление команды**: `sms_phone_numbers.team_id` — `SET NULL` (номера → unassigned-пул); `sms_inbound.team_id` (снимок) — `SET NULL`; `sms_deliveries` не затрагиваются.
- **Удаление `sms_inbound`** (не через API): `sms_deliveries` (`inbound_sms_id`) — `ON DELETE CASCADE`.

## Frontend — ТЗ

Стек — как у существующих страниц (React 18 + TS strict + Vite + React Router 6 + Tailwind + TanStack Query). Тёмная тема; словарь и токены — [08-design-system.md#страница-смс](../../08-design-system.md#страница-смс). Наружу фронт не ходит — только `/api/sms/*`.

### Навигация
- Пункт **«СМС»** (`NavLink`, маршрут `/sms`) — в категорию **«Агрегатор»** ([ADR-022](../../adr/ADR-022-teams-nav-categories.md)) рядом с «Почты». Не-full-bleed (обычный поток документа). Page-guard `useCanViewPage('sms')`; в объект `access` добавляется `sms`.
- **Операторская Mini App — маршрут `/tg/sms` вне навигации и вне `AppLayout`** ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)): публичный route (сосед `/login`), **без** page-guard/redirect/nav-shell. Пункта в меню нет (вход — только по кнопке Telegram-бота). См. [08-design-system.md#операторская-telegram-mini-app-смс](../../08-design-system.md#операторская-telegram-mini-app-смс).

### Операторская Mini App `SmsMiniAppPage` (`/tg/sms`, ADR-031)
- Компонент `SmsMiniAppPage` в `features/sms` (отдельный от админской `SmsPage`), рендерится публичным маршрутом `/tg/sms` **вне** `AppLayout`/auth-guard. Наружу не ходит — только `/api/*` (тот же origin).
- **Telegram WebApp SDK — self-hosted** (`frontend/public/telegram-web-app.js`, свой origin; CSP `script-src 'self'`). Подключение — на маршруте `/tg/sms` (в `index.html` либо динамической инъекцией), не глобально в админ-shell. Обновление SDK — ручной bump вендоренного файла.
- **SSO-bootstrap (без формы входа):** прочитать `initData` из `window.Telegram.WebApp` → `POST /api/sms/telegram/auth` → при `200` сохранить `access_token` в память auth-store и войти. Применить `themeParams` Telegram (нативный вид), `ready()`/`expand()`.
- **Состояния:** loading (SSO) · **успех** (JWT получен: статус «Привязан» + номера/сообщения под `sms:view`) · `403 sms_operator_not_provisioned` (экран «Ваш Telegram не сопоставлен с оператором CRM — обратитесь к администратору») · `401 invalid_init_data`/`init_data_expired` (сообщение «Сессия Telegram устарела — откройте заново через бота») · «вне Telegram» (пустой/битый `initData` — подсказка «Откройте по кнопке бота в Telegram») · сетевая ошибка (сообщение + повтор). **Пароль/форма входа отсутствуют** (беспарольный SSO).
- **Просмотр номеров/сообщений (`sms:view`):** реюз хуков `useSmsNumbers`/`useSmsMessages` (те же JWT-эндпоинты, SMS-scope) под полученным `access_token`. Без `sms:view` — просмотр пуст (показывается только статус привязки).
- Словарь строк и токены Mini App — [08-design-system.md#операторская-telegram-mini-app-смс](../../08-design-system.md#операторская-telegram-mini-app-смс).

### Страница `SmsPage`
- Локальные табы-тумблеры (`role="tablist"`, как в `MailPage`), default = **«Сообщения»**.
- **Вкладка «Сообщения»:** два `Select` («Все номера» из `useSmsNumbers`, «Все команды» из `useTeams`) + лента `SmsMessageCard` + `IntersectionObserver`-догрузка (`useInfiniteQuery`, курсор `cursor`). Фильтры комбинируемы (AND). Состояния loading/empty/error.
- **Вкладка «Номера»:** поиск `Input` (клиентский фильтр по номеру) + таблица `SmsNumberRow` с инлайн-полями (`Pencil` → `Input`/`Textarea` + `Check`/`X`), колонка ДЕЙСТВИЯ (`Select` команд + «Перенести» + «Удалить» с confirm). Пагинации нет (номера немногочисленны). Состояния loading/empty/error/«ничего не найдено».
- **Гейтинг действий:** инлайн-правка — `useCan('sms','edit')`; перенос — `useCan('sms','transfer')`; удаление — `useCan('sms','delete')`; синк — `useCan('sms','sync')`. Без права контролы не рендерятся.

### Компоненты
- `SmsMessageCard` — строка 1: `from_number → to_number` + бейдж команды (зелёная пилюля / серая «Команды нет») + дата (абсолютный `ru-RU`); строка 2 — пилюли `Логин:`/`Приложение:`/`Примечание:` (`note ?? '-'`); строка 3 — текст.
- `SmsNumberRow` + `InlineEditField` — строка таблицы «Номера».
- `ui/Pill` — цветная пилюля с заливкой (обобщение mail `TagPill`); маппинг цветов — [08-design-system.md#страница-смс](../../08-design-system.md#страница-смс).
- `TeamDetailPanel` (страница `/teams`) — см. ниже.

### Доработка `/teams`
- **Кол-во номеров на карточке:** новое поле `TeamListItem.number_count`; чип «N номеров» рядом с `membersPlural(...)` (хелпер склонения в `lib/plural.ts`).
- **Detail-панель:** клик по карточке раскрывает/сворачивает `TeamDetailPanel` (аккордеон, `aria-expanded`, `expandedId`) вместо edit-модалки. Панель (просмотр): Название, Лидер, Участники (`team.members` уже в `TeamListItem`), **Список номеров команды** (ленивый `GET /api/teams/{id}/numbers`, свой `useQuery` + loading/empty/error). **Строка номера показывает ТОЛЬКО номер телефона** (`TeamNumberItem` — без пилюль `Логин`/`Приложение`/`Примечание`/`label`; авторизационное сужение [ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §8: чувствительный учётный контекст доступен только на странице «СМС» под `sms:*`). Иконка `Pencil` (`stopPropagation`) → существующий `AddTeamModal mode='edit'`. Гейт `canEdit` сохраняется.

## DoD

- [ ] Backend: миграция `0017_create_sms_module` (4 таблицы, индексы, FK/ON DELETE по [03-data-model.md](../../03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links)); `upgrade`/`downgrade` round-trip на чистой БД.
- [ ] Приватные эндпоинты `GET /api/sms/messages`, `GET /api/sms/numbers`, `PATCH`/`POST .../transfer`/`DELETE /api/sms/numbers/{id}`, `POST /api/sms/numbers/sync`, `POST /api/sms/telegram/link`, `GET /api/teams/{id}/numbers` — схемы/коды строго по [04-api.md#sms](../../04-api.md#sms); RBAC-гейты `sms:view/edit/transfer/sync/delete`. `GET /api/teams/{id}/numbers` отдаёт **минимальную `TeamNumberItem`** (без `login`/`app_name`/`note`/`label`) под `teams:view` — авторизационное сужение [ADR-030](../../adr/ADR-030-sms-module-full-merge.md) §8; полный `SmsNumberItem` — только под `sms:*`.
- [ ] Публичные `POST /api/sms/webhooks/twilio/sms` (подпись), `POST /api/sms/telegram/webhook` (секрет), `POST /api/sms/telegram/auth` (HMAC) — CSRF/JWT-exempt, гейтятся подписью/секретом/HMAC.
- [ ] `Principal.user_id` из claim `uid`; `SmsScope` (current-ownership видимость, анти-энумерация пустым результатом).
- [ ] Fan-out crash-recoverable (дедуп по SID, `try_reserve` идемпотентен), retry-монитор стартует при `sms_bot_enabled`; текст сообщения и формат — по §Приём SMS.
- [ ] Секреты Twilio/SMS-бота только из env; не в БД/логах/ответах/SPA/URL. Notifier-бот (`app/infra/telegram.py`) не изменён.
- [ ] `CATALOG["sms"] = ("view","edit","transfer","sync","delete")`; `GET /api/teams` отдаёт `number_count`.
- [ ] Frontend: страница `/sms` (табы Сообщения/Номера), фильтры, инлайн-правка, перенос/удаление/синк, все состояния UI, словарь из [08-design-system.md](../../08-design-system.md#страница-смс); `/teams` — `number_count` на карточке + detail-панель со списком номеров; тёмная тема, без layout-регрессии `/teams` и `/mail`.
- [ ] Backend: беспарольный Telegram-SSO — пересмотр `POST /api/sms/telegram/auth` ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md), §Backend-доработка Telegram-SSO): резолв по `telegram_user_id`/`users.telegram`, авто-upsert+revive линка, идемпотентный `first_login_at`, выпуск CRM-JWT; новая `TelegramAuthResponse`; `403 sms_operator_not_provisioned`; репозиторный lookup по `telegram_user_id` независимо от `dead_at`. Контракт/коды — строго по [04-api.md#post-apismstelegramauth](../../04-api.md#post-apismstelegramauth).
- [ ] Frontend: операторская Mini App — публичный маршрут `/tg/sms` (`SmsMiniAppPage`) **вне `AppLayout`/RBAC-guard** ([ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)); **self-hosted** Telegram WebApp SDK (`frontend/public/telegram-web-app.js`, CSP `script-src 'self'` не ослабляется); беспарольный SSO-флоу (`auth` → JWT → просмотр под `sms:view`); `themeParams`/`ready`/`expand`; все состояния (успех / `sms_operator_not_provisioned` / `invalid_init_data` / `init_data_expired` / вне-Telegram / сеть); **без формы пароля**; словарь — [08-design-system.md#операторская-telegram-mini-app-смс](../../08-design-system.md#операторская-telegram-mini-app-смс).
- [ ] Config/devops: `SMS_TELEGRAM_WEBAPP_URL = https://broadappsdev.shop/tg/sms` (не плейсхолдер `…/sms`); домен Mini App зарегистрирован в @BotFather; `setWebhook` SMS-бота настроен ([07-deployment.md](../../07-deployment.md#reverse-proxy-nginx--требования)).
- [ ] Провижининг операторов: у оператора заполнен `users.telegram` (нормализованный ник = его ник в Telegram), роль включает `sms:view`, состоит в нужной команде (`user_teams`), имеет публичный `@username`.
- [ ] Twilio SDK в `pyproject.toml` ([02-tech-stack.md](../../02-tech-stack.md#backend)); namespaced-env в `.env.example` (корневой) + [07-deployment.md](../../07-deployment.md#переменные-окружения); nginx открывает наружу два webhook-пути с пробросом `X-Forwarded-Proto/Host`.
- [ ] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-09: операторская Telegram Mini App (architect, [ADR-031](../../adr/ADR-031-sms-operator-mini-app.md)). Выделенный публичный маршрут `/tg/sms` вне `AppLayout`/RBAC-guard; онбординг — **беспарольный Telegram-SSO** (ревизия по решению пользователя): `POST /api/sms/telegram/auth` резолвит оператора по иммутабельному `telegram_user_id`, иначе bootstrap по `users.telegram = normalize(username)` → авто-upsert линка + выдача CRM-JWT; не сопоставлен → `403 sms_operator_not_provisioned`. Просмотр номеров/сообщений — существующие JWT-эндпоинты под `sms:view` (роль оператора обязана включать `sms:view`). Self-hosted Telegram WebApp SDK (CSP `script-src 'self'` не ослабляется); только нативные Telegram-webview ([Q-SMS-1](../../99-open-questions.md) resolved native-only, [Q-SMS-2](../../99-open-questions.md) resolved JWT+`sms:view`, [Q-SMS-3](../../99-open-questions.md) риск подмены ника/провижининг). `SMS_TELEGRAM_WEBAPP_URL → …/tg/sms`. Backend-доработка `telegram/auth` (SSO) требуется.
  - (черновая JWT-редакция того же дня заменена этой беспарольной по решению пользователя.)
- 2026-07-09: спецификация создана (architect, [ADR-030](../../adr/ADR-030-sms-module-full-merge.md)). Полное слияние SMS-агрегатора в CRM: 4 таблицы (PK BIGINT + внешние FK UUID), Twilio-приём + отдельный SMS-delivery Telegram-бот (fan-out по команде/retry/dead-links), отказ от Redis (Mini App-привязка под JWT), новые поля номера `login`/`app_name`/`note` (системный `label`), видимость по текущей принадлежности номера, RBAC-страница `sms:view/edit/transfer/sync/delete`, `Principal.user_id`. Импорт исторических данных — TD (не мигрируем).
