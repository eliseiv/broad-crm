# Модуль `mail` — Почты (CRM — система-запись; агрегатор — IMAP/SMTP-connector)

Статус: `in-prod` (cut-over выполнен 2026-07-10) · Исполнитель: backend, frontend, devops

> **Действующая архитектура — [ADR-044](../../adr/ADR-044-mail-full-merge-into-crm.md)** (полный перенос почты в CRM) + [ADR-045](../../adr/ADR-045-mail-outlook-oauth-headless-reonboarding.md) (Outlook OAuth headless) + [ADR-047](../../adr/ADR-047-mail-fix-pack.md) (mail-пакет фиксов 2026-07-11).
>
> **ОТМЕНЕНО, не реализовывать:** read-through-прокси «без хранения» ([ADR-012](../../adr/ADR-012-mail-read-through-proxy.md)/[ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md)/[ADR-017](../../adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)) и headless-прокси с групп-индирекцией ([ADR-038](../../adr/ADR-038-mail-headless-integration.md) `superseded`, [ADR-043](../../adr/ADR-043-lazy-mail-group-provisioning.md) `superseded`). Понятий **`group_id`**, **`MailTeam`**, **`GET /api/mail/teams`**, **`teams.mail_group_id`** (как связи ящик↔команда), проксирования ленты/тегов в external API — в действующей модели **нет**.
>
> **Инварианты ADR-012, которые ПЕРЕЖИЛИ супессию и действуют:** `MAIL_API_KEY` только на backend (в заголовке `X-API-Key` исходящего запроса); HTML-тело письма — только в sandbox-iframe без `allow-scripts`/`allow-same-origin`; JWT на всех пользовательских эндпоинтах.

## Scope

Страница **«Почты»** (`/mail`, три вкладки: «Сообщения» / «Почты» / «Теги») + Telegram-доставка + Telegram Mini App `/tg/mail`.

**CRM — единственный UI и система-запись (system of record):** письма, теги, каталог ящиков, привязка ящика к команде, история Telegram-уведомлений, отправленные reply — **хранятся в БД CRM**.

**Агрегатор `postapp.store` — чистый mail-connector:** подключение ящиков (IMAP/SMTP-креды, шифрование AES-256-GCM **там**), IMAP-синк, **push нового письма в CRM**, push статуса синка ящика, SMTP-отправка. Групп, тегов, пользователей, Telegram и UI у него больше нет.

**Владение ящиком — команда, напрямую:** `mail_accounts.team_id → teams.id` (per-mailbox; `NULL` = ящик без команды). Групп-индирекции нет.

## Out of scope

- **Вложения** — не переносятся и не отображаются by design ([ADR-044](../../adr/ADR-044-mail-full-merge-into-crm.md), [TD-034](../../100-known-tech-debt.md)). `cid:`-инлайн-картинки не резолвятся ([TD-026](../../100-known-tech-debt.md)).
- **Пересылка (forwarding) лидеру** — отложена решением владельца; **не работает** с момента cut-over ([TD-040](../../100-known-tech-debt.md)). Таблиц `mail_forwarding`/`mail_message_forwards` **нет**.
- **Compose «с нуля»** — только reply на существующее письмо.
- **Полнотекстовый поиск ПО ПИСЬМАМ** — не реализован (данные в БД CRM есть, индекса/эндпоинта нет) — остаток [TD-024](../../100-known-tech-debt.md). **⚠️ Не путать с поиском на вкладке «Почты»** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §1): тот ищет **по ЯЩИКАМ** (`number`/`app_name`/`email`, клиентский фильтр над загруженным каталогом), **а не по содержимому писем**.
- **Папки** и **архив**. (**«Пометка прочитано/непрочитано» ИЗ non-goals ВЫВЕДЕНА** — реализована как **личная** прочитанность, [ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md); см. [Прочитанность писем](#прочитанность-писем-личная-нормативно-adr-050).)
- **Счётчик-badge непрочитанных** (число «N» в навигации/на вкладке) — **не вводится** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2.4): потребовал бы `COUNT` по всей видимой ленте на каждый рендер. Индикатор + фильтр закрывают задачу без агрегата.
- Внешние **шрифты** HTML-письма (`font-src` не расширен).

## Архитектура

```mermaid
flowchart LR
  IMAP[(IMAP/SMTP ящики)] <--> AGG[postapp.store<br/>mail-connector]
  AGG -- "push HMAC<br/>POST /api/mail/ingest<br/>POST /api/mail/mailbox-status" --> CRM[CRM backend]
  CRM -- "X-API-Key<br/>create/patch/delete/sync/test/send/oauth" --> AGG
  CRM <--> DB[(БД CRM<br/>mail_accounts / mail_messages<br/>mail_tags / mail_telegram_*)]
  CRM -- "MailDispatcherService<br/>(asyncio, lifespan)" --> TG[Telegram Bot API<br/>1 основной + 4 push-бота]
  SPA[SPA /mail и /tg/mail] -- "JWT, /api/mail/*" --> CRM
```

**Слои backend:**

| Слой | Файлы |
|------|-------|
| Роутеры | `api/mail.py` (пользовательский, JWT+RBAC), `api/mail_ingest.py` (машинный, HMAC), `api/mail_telegram.py` (вебхуки/SSO), `api/mail_me.py` (self-настройки) |
| Сервисы | `services/mail_service.py` (чтение из БД + транзит в агрегатор), `services/mail_ingest_service.py` (приём push), `services/mail_dispatcher_service.py` (фоновая Telegram-доставка), `services/mail_telegram_service.py` (линковка/SSO/настройки) |
| Инфра | `infra/mail_client.py` (httpx → агрегатор, `X-API-Key`), `infra/mail_push_security.py` (HMAC), `infra/mail_oauth_state.py` (`crm_state`) |
| Модели | `models/mail_account.py`, `mail_message.py`, `mail_tag.py` (+`MailTagRule`/`MailMessageTag`), `mail_telegram.py`, `mail_user_settings.py`, `mail_sent_message.py` |
| Схемы | `schemas/mail.py`, `schemas/mail_ingest.py`, `schemas/mail_telegram.py` — контракт по [04-api.md#mail](../../04-api.md#mail) |

**Данные** (миграции `0021`, `0022`, `0023`, `0024`) — [03-data-model.md §Таблицы модуля «Почты»](../../03-data-model.md#таблицы-модуля-почты-mail_accounts-mail_messages-mail_tags-).

## Приём почты (push агрегатор → CRM)

- **`POST /api/mail/ingest`** — машинный, **без JWT**, аутентификация **HMAC-SHA256** над **сырыми байтами** тела (`X-Mail-Signature: sha256=<hex>`, `X-Mail-Timestamp`), секрет `MAIL_PUSH_SECRET`, окно `MAIL_PUSH_MAX_SKEW_SEC` (300 с). Батч до `MAIL_INGEST_MAX_BATCH` (100) писем.
- **Идемпотентность:** `INSERT ... ON CONFLICT (mail_account_id, uidvalidity, uid) DO NOTHING` (`uq_mail_messages_account_uidv_uid`). Повтор доставки дубля не создаёт.
- **На приёме:** вставка письма → при фактической вставке применить теги (§Теги). **Telegram-рассылку хендлер НЕ делает** — оставляет `notified_at IS NULL`, доставку берёт фоновый диспетчер.
- Неизвестный `mail_account_id` → письмо **пропускается** (счётчик `unknown_mailbox`), батч не отклоняется ([TD-041](../../100-known-tech-debt.md)).
- **`POST /api/mail/mailbox-status`** — тот же HMAC; зеркалит статус синка ящика (`is_active`/`last_synced_at`/`last_sync_error`/`consecutive_failures`) в `mail_accounts`; переход `true→false` триггерит mailbox-down-алерт (идемпотентность — `down_alert_sent_at`).
- **Очередь/ретраи push — на стороне агрегатора** (у него Redis); CRM однопроцессная, брокера не заводит.
- **nginx:** `client_max_body_size` на `location /api` ОБЯЗАН вмещать батч (сейчас `50m`) — [07-deployment.md](../../07-deployment.md#reverse-proxy-nginx--требования); нарушение даёт `413` и молчаливую потерю приёма ([TD-045](../../100-known-tech-debt.md)).

## Лента писем (чтение из БД CRM)

- **Порядок — `internal_date DESC, id DESC`** (истинная дата письма, **НЕ** `id`: `id BIGSERIAL` отражает порядок прихода push'а, и recovery-ре-пуш старого письма иначе «всплыл» бы в топ).
- **Компаундный keyset-курсор `(internal_date, id)`** — `internal_date` не уникален (массовая рассылка приходит одной секундой), пагинация по одному полю дала бы пропуски/дубли на границах страниц. Предикат: `WHERE (internal_date, id) < (:cursor_date, :cursor_id)`.
- Клиент получает **непрозрачный** `next_cursor` и возвращает его в параметре **`before`**. Интерпретировать курсор клиенту запрещено. Битый курсор → `400 invalid_cursor`.
- **Фильтры** `mail_account_id` (**повторяемый**), `team_id` и **`unread`** — **AND-комбинируемы**, пересекаются со scope пользователя.
- **Бесконечная лента:** первая страница без `before`, догрузка старых — `before=<next_cursor>`, пока `next_cursor ≠ null`.

## Прочитанность писем (ЛИЧНАЯ, нормативно, [ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md))

**Прочитанность — ЛИЧНАЯ у каждого пользователя, не общая на команду.** Хранится в таблице связи **`mail_message_reads (user_id, message_id, read_at)`**, PK `(user_id, message_id)` + обязательный `ix_mail_message_reads_message_id` (миграция **`0025_mail_message_reads`**). **Существование строки = «прочитано»**; отсутствие = «не прочитано». Схема — [03-data-model.md](../../03-data-model.md#таблица-mail_message_reads-миграция-0025-adr-050).

- **Контракт:** `MailMessage += is_unread: boolean` (персональное производное — приходит **и в ленте, и в детали**: схема одна); `GET /api/mail/messages += unread: boolean?` (серверный фильтр); **`POST` / `DELETE /api/mail/messages/{id}/read` → `204`** (оба **идемпотентны**). Контракт — [04-api.md](../../04-api.md#post-apimailmessagesmessage_idread).
- **Гейт — `mail:view`** (нового права нет): отметка — личный артефакт **чтения**, а не мутация домена. Прецедент: `reply` — тоже под `view`.
- **Scope — тот же `MailScope`:** письмо вне scope → **`404 mail_message_not_found`** (анти-энумерация, как у reply). Отметить чужое письмо нельзя.
- **Пометка ПРИ ОТКРЫТИИ — в обеих поверхностях:** веб `/mail` (смена выбранного письма, **включая авто-выбор самого свежего**) **и Mini App `/tg/mail`**. Mini App использует **тот же эндпоинт** — он несёт **обычный CRM access-JWT с `uid`** (SSO `POST /api/mail/telegram/auth`), поэтому `principal.user_id` там всегда непуст; спец-эндпоинта не требуется.
- **Возврат в «непрочитано» — есть** (`DELETE …/read`; кнопка «Отметить непрочитанным» в шапке детали веб-UI; в Mini App её нет). Нужен потому, что пометка частично непроизвольна (авто-выбор свежего письма).
- **Супер-админ из `.env`** (`Principal.user_id is None` — нет строки в `users`): `POST`/`DELETE …/read` → **`403`**, `is_unread` всегда `false`, `unread=true` → пустая страница. То же ограничение, что у `/api/mail/me/settings`.
- **Производительность (нормативно):** `is_unread` — **батч-лукап по PK** на уже отобранную страницу (`WHERE user_id = :uid AND message_id = ANY(:page_ids)`), **НЕ** JOIN в keyset-запрос и **не** N+1. Фильтр `unread=true` — **наоборот**, анти-джойн **`NOT EXISTS` ВНУТРИ** keyset-запроса (клиентская фильтрация **запрещена** — сломала бы курсорную догрузку).

## Ролевая видимость (`MailScope`, нормативно)

`MailScope(sees_all_teams: bool, team_ids: frozenset[UUID])` — **поля `group_ids` НЕТ**. `sees_all_teams` = тот же admin-предикат, что `sees_all_sms_teams` (`is_superadmin OR permissions_subset(full_catalog, permissions)`); `team_ids` = команды пользователя из `user_teams`. Граница безопасности — **backend**.

| Операция | Правило |
|----------|---------|
| Чтение ленты / ящиков | не-admin: только ящики с `team_id ∈ team_ids`; вне scope → **пусто** (анти-энумерация), не `403`/`404` |
| Reply на письмо | письмо вне scope → `404 mail_message_not_found` (чужое неотличимо от несуществующего) |
| Создание ящика | не-admin: `team_id ∈ team_ids`, иначе `403`. **`team_id = null` (без команды) — только admin-уровень** |
| Перенос ящика (смена `team_id`) | **только admin-уровень** (`sees_all_teams`), даже если пользователь состоит в обеих командах — требование владельца |
| Мутация/удаление/синк ящика по `id` | не-admin: ящик ∈ scope, иначе `403` |
| Теги | глобальны, scope команд **не применяется**: чтение под `mail:view`, управление под `mail:tags` |

RBAC: `CATALOG["mail"] = ("view","create","edit","delete","sync","tags")`; `reply` — под `view`. `GET /api/auth/me` отдаёт `sees_all_mail_teams`. Детали — [05-security.md](../../05-security.md#каталог-прав-канон-на-сервере).

## Ящики: CRUD в CRM, креды транзитом в агрегатор

- **Каталог живёт в CRM** (`mail_accounts`), чтение (`GET /api/mail/mailboxes`, `GET /api/teams/{id}/mailboxes`) — из БД CRM, **без** обращения к агрегатору.
- **Каталог обслуживает и страницу «Команды»** ([ADR-048](../../adr/ADR-048-teams-mailbox-count-mail-row.md)): `TeamListItem.mailbox_count` = `COUNT(mail_accounts WHERE team_id = teams.id)` (батч `MailAccountRepository.count_by_teams` для списка + одиночный `count_by_team` для тел `201`/`200`, индекс `ix_mail_accounts_team_id`), а `TeamMailboxItem` отдаёт `number`/`app_name`. Эти пути гейтятся **`teams:view`** и **`MailScope` НЕ применяют** — это осознанное решение [ADR-048](../../adr/ADR-048-teams-mailbox-count-mail-row.md) §4 (симметрия с [ADR-034](../../adr/ADR-034-teams-number-login-app.md)); креды/хосты/статус синка через них по-прежнему **не** раскрываются. Мутации ящика и лента писем — по-прежнему только под `mail:*` + `MailScope`.
- **IMAP/SMTP-креды в CRM НЕ хранятся** — идут **транзитом** в агрегатор (шифрование AES-256-GCM там; Fernet CRM к почте не применяется). Эндпоинты записи отвечают `Cache-Control: no-store`. Пароли не логируются и не возвращаются в ответах.
- **Создание:** CRM → агрегатор `POST /api/external/mailboxes` (владелец там — служебный `crm-service`) → присвоенный `id` → вставка строки `mail_accounts` с тем же `id`. Провал вставки каталога → best-effort компенсация (удалить ящик в агрегаторе), чтобы не оставить сироту.
- **Правка/удаление/синк/тест** — проброс в агрегатор; **смена `team_id` — локальный `UPDATE`** (агрегатор о командах не знает, сетевой вызов не делается).

### Имя ящика: «Номер» и «Приложение» ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §3, нормативно)

- Поля БД: **`number TEXT NULL`**, **`app_name TEXT NULL`** (миграция `0024`). В форме — два поля: **«Номер»** и **«Приложение»**; поля «Отображаемое имя» в UI **нет**.
- **`display_name` — ПРОИЗВОДНОЕ поле**, вычисляется **сервером** при каждом create/update: `" ".join(part for part in (number, app_name) if part and part.strip())`; обе части пусты → `NULL`. **Клиент `display_name` не передаёт** (его нет в схемах запросов).
- **В агрегатор `number`/`app_name` НЕ уходят никогда** — туда уходит только вычисленный `display_name` (единственная форма имени во внешнем контракте агрегатора).
- **Исходящий payload в агрегатор строится БЕЛЫМ СПИСКОМ** (креды + `email` + `display_name` + `is_active`), а **не** «`model_dump()` минус пара полей» — иначе любое новое поле схемы CRM молча утечёт наружу. Долг производного поля — [TD-052](../../100-known-tech-debt.md).
- **OAuth-ingest ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §3.7, нормативно).** Путь `POST /api/mail/oauth/ingest` заводит Outlook-ящик в обход `POST /api/mail/mailboxes` — агрегатор присылает готовый `display_name`. **На INSERT** он **разбирается** в `number`/`app_name` **тем же правилом**, что и backfill-миграция `0024` (единая чистая функция `parse_display_name` в `app/domain/mail.py`), а `display_name` сохраняется канонически производным (`build_display_name`). Иначе у OAuth-ящиков `number`/`app_name` остались бы `NULL` при непустом `display_name` — инвариант «`display_name` производный» нарушен, а в строке ящика пропали бы «Номер» и «Приложение».
- **На `ON CONFLICT DO UPDATE` (re-consent) поля имени НЕ перезаписываются** (`number`/`app_name`/`display_name`): после создания **CRM — источник истины имени ящика** (админ мог их отредактировать), агрегатор лишь эхо-возвращает то, что CRM ему отдала. Обновляются `email`, `is_active`, `team_id` (из `crm_state`); поля синка ведёт status-канал.

### Outlook OAuth (headless, [ADR-045](../../adr/ADR-045-mail-outlook-oauth-headless-reonboarding.md))

`POST /api/mail/mailboxes/oauth/authorize {team_id}` (гейт `mail:create`, правила команды — как при обычном создании) → CRM минтит HMAC-подписанный stateless `crm_state` → агрегатор отдаёт Microsoft authorize-URL → пользователь открывает ссылку **в нужном профиле OctoBrowser** (не auto-redirect) → после consent агрегатор уведомляет CRM `POST /api/mail/oauth/ingest` (тот же HMAC) → **upsert** `mail_accounts` с `team_id` из `crm_state`.

## Теги (глобальный админский каталог, движок матчинга — в CRM)

- **У тега нет владельца.** Каталог глобальный, применяется ко **всем** письмам всех команд. Чтение — `mail:view`, управление — `mail:tags`.
- **Признака «встроенный» БОЛЬШЕ НЕТ** ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §1): колонка `mail_tags.is_builtin` дропнута (миграция `0023`), поле `is_builtin` убрано из `MailTagFull`, слово «встроенный» из UI убрано. **Удалить можно ЛЮБОЙ тег** — ветки `409` при удалении нет.
- **Сева тегов при старте приложения НЕТ.** Канонические 10 тегов + правила создаются **один раз data-миграцией** `0023` (`ON CONFLICT (name) DO NOTHING`); удалённый пользователем тег **не воскресает** при рестарте. Восстановление — вручную.
- **Типы правил** (CHECK `ck_mail_tag_rules_type`, 4 значения — не меняются): `subject_contains`, `body_contains`, `sender_contains`, `sender_exact`. **UI-лейблы (нормативно, [ADR-047](../../adr/ADR-047-mail-fix-pack.md) §2):**

  | `type` | Лейбл | В списке создания правила |
  |--------|-------|--------------------------|
  | `subject_contains` | **Тема письма** | да |
  | `body_contains` | **Текст письма** | да |
  | `sender_contains` | **Отправитель** | да |
  | `sender_exact` | **Отправитель равен** | **нет** (создание из UI недоступно — [TD-055](../../100-known-tech-debt.md); существующие правила работают и отображаются) |

- **Семантика матчинга (портирована из агрегатора побуквенно):** whole-word, case-**sensitive**, whitespace-нормализация (U+00A0 → пробел, затем `\s+`→` `), явные граничные классы `(^|[^[:alnum:]_])`…`([^[:alnum:]_]|$)` (НЕ `\y`), экранирование метасимволов паттерна, оператор `~` (не `~*`); `sender_contains` матчит `from_addr` **и** `from_name`; `body_contains` — `body_text` **и** strip-tags(`body_html`); `sender_exact` — `LOWER = LOWER`; `match_mode` `any`/`all`; `ON CONFLICT (message_id, tag_id) DO NOTHING`. Известное ограничение (`strip_tags` не декодирует HTML-entities) унаследовано как есть.
- **Применение:** на приёме push'а (к только что вставленному письму) + `POST /api/mail/tags/{id}/apply-to-existing` (bulk по всем письмам, идемпотентно).

## Telegram: доставка, боты, Mini App

**`MailDispatcherService`** — фоновая asyncio-задача в lifespan (без Redis/брокера), интервал `MAIL_DISPATCH_INTERVAL_SEC` (5 с), гейт `MAIL_DISPATCH_ENABLED`. Три прохода:

- **A — новые письма** (`WHERE notified_at IS NULL`, partial-индекс `ix_mail_messages_notify`). Резолв получателей: письмо → `mail_account.team_id` → участники команды (`user_teams`) → их `mail_telegram_links` (`dead_at IS NULL`) минус opt-out (`mail_user_settings.tg_notifications_enabled=false`). Резерв строки `mail_telegram_notifications` (`ON CONFLICT (message_id, telegram_user_id) DO NOTHING`) → отправка основным ботом → `sent`/`failed`/`dead`. Затем `notified_at = now()`.
- **B — recovery** транзиентных сбоев: `mail_telegram_notifications WHERE status IN ('pending','failed') AND attempts < MAIL_TG_MAX_ATTEMPTS` → повторная отправка. Без этого прохода транзиентный сбой Telegram терял бы уведомление навсегда (`notified_at` уже проставлен).
- **C — mailbox-down алерты:** `mail_accounts WHERE is_active=false AND down_alert_sent_at IS NULL` → алерт получателям команды ящика → guarded `UPDATE ... WHERE down_alert_sent_at IS NULL` («ровно один алерт на переход»). Re-enable сбрасывает `down_alert_sent_at = NULL`.

Плюс **reconcile-проход** орфан-линков (`mail_telegram_links.user_id IS NULL` → связать по `username` = `lower(users.telegram)`), раз в `MAIL_DISPATCH_RECONCILE_EVERY` итераций.

**Резолв Telegram ↔ CRM-пользователь (нормативно):** приоритет — иммутабельный `telegram_user_id` (существующий линк); иначе bootstrap по username: `lower(users.telegram) == normalize_telegram(tg_username)`, где `normalize_telegram` снимает ведущий `@` и приводит к нижнему регистру. **Сравнение обязано быть регистронезависимым** (`@Katetown` ↔ `katetown`). `chat_id` (`= telegram_user_id`) первичен для **доставки**; `username` — только для первичного **связывания**.

**Боты (5, токены не менялись):** основной `@ba_mail_bot` (`POST /api/mail/telegram/webhook/{secret}` — `/start`-линковка + callback «Посмотреть сообщение») и **4 push-бота** команд (`POST /api/mail/telegram/push-webhook/{bot_name}`, `bot_name ∈ {ivan, alexandra, andrei, business2}`), маппинг бот→команда — по env `MAIL_BOT_<NAME>_TEAM_ID` (UUID CRM-команды). Дубликат `_TEAM_ID` → fail-fast на старте. Env — [07-deployment.md](../../07-deployment.md#переменные-окружения).

**Mini App `/tg/mail`** — публичный SPA-маршрут вне `AppLayout`/RBAC-guard, **без экрана логина**: `POST /api/mail/telegram/auth` валидирует Telegram `initData` (HMAC-подпись бота — **граница безопасности**, TTL `MAIL_TG_INITDATA_TTL_SEC`) → выдаёт CRM access-JWT. Не сопоставлен с CRM-пользователем → `403 mail_operator_not_provisioned` (понятное сообщение, **не** пустая лента и **не** экран логина). UI-структура и строки — [08-design-system.md](../../08-design-system.md#telegram-mini-app-почты-tgmail-нормативно).

**Opt-out:** `GET`/`PATCH /api/mail/me/settings { tg_notifications_enabled }` (гейт `mail:view`, upsert по `principal.user_id`). Супер-админ из `.env` не имеет БД-строки → `403 forbidden`. Дефолт (нет строки) — уведомления включены.

## Reply (отправка через агрегатор)

`POST /api/mail/messages/{id}/reply` (гейт `mail:view`): письмо берётся из `mail_messages`, threading формирует CRM (`In-Reply-To` = `message_id_header`, `References` = `refs_header` + `message_id_header`), SMTP-отправка делегируется агрегатору (`POST /api/external/mailboxes/{id}/send`), факт отправки пишется в `mail_sent_messages`.

**Нормы (нарушение → `422 unprocessable`):** `body` обязателен, непустой, ≤ 1 MiB; `to` по умолчанию = `[from_addr]` исходного, `subject` по умолчанию = `"Re: " + subject` исходного; каждый адрес — валидный e-mail; суммарно `to`+`cc` ≤ 100 адресов; `subject` ≤ 998 символов; явный пустой `to` **и** пустой `cc` → `422` (письмо без получателей отклоняется **до** вызова агрегатора).

## Изоляция HTML-тела (нормативно — инвариант [ADR-012](../../adr/ADR-012-mail-read-through-proxy.md), НЕ ослаблен)

- `body_html` — **недоверенный** контент. Рендерится **только** в `<iframe srcDoc={…} sandbox="">` — **без** `allow-scripts` и **без** `allow-same-origin` + `referrerPolicy="no-referrer"`. DOMPurify не добавляется.
- Пусто/`null` → `body_text` (моношрифт, `white-space: pre-wrap`), iframe не создаётся. `body_truncated=true` → пометка «Письмо показано не полностью»; `body_present=false` → «Тело письма недоступно».
- **Фон/цвет текста тела следуют теме CRM** ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §6): `dark` → `#161A22`/`#E6E9EF`, `light` → `#F7F8FA`/`#111827` (литералы — iframe не наследует CSS-переменные родителя). **Билдер srcDoc — единый источник** для `MailDetail` и Mail Mini App (дублировать запрещено).
- Удалённые (https) **изображения** отрисовываются (CSP `img-src 'self' data: https:` — [ADR-015](../../adr/ADR-015-csp-img-src-remote-mail-images.md)); скрипты письма по-прежнему не исполняются. Компромисс — трекинг-пиксели. `cid:`-картинки не резолвятся ([TD-026](../../100-known-tech-debt.md)).

## Гейт `mail_enabled`

`settings.mail_enabled = bool(MAIL_API_KEY)`. Гейт применяется к операциям, **требующим агрегатора** (create/test/patch/delete/sync ящика, reply, OAuth-authorize) → `503 mail_not_configured`. **Чтение ленты/ящиков/тегов из БД CRM гейтом НЕ покрывается** — работает независимо от доступности агрегатора. Push-приёмник гейтится отдельно: пустой `MAIL_PUSH_SECRET` → `503 mail_ingest_not_configured`.

## Frontend — ТЗ

Точная композиция, вкладки, словарь строк — [08-design-system.md §Страница «Почты»](../../08-design-system.md#страница-почты). Наружу фронт **не ходит** — только `/api/mail/*` через `lib/api.apiRequest`.

- **`/mail`** — три вкладки (локальный `useState`, ARIA tablist; не роутинг): **Сообщения** (master-detail лента, ~30/70, авто-выбор самого свежего, inline-reply, бесконечная лента по `next_cursor`), **Почты** (таблица ящиков — CRUD/статус/перенос/фильтр активности), **Теги** (CRUD глобальных тегов и правил). Full-bleed layout.
- **Строка ящика** (вкладка «Почты») — референс `screen/1.jpg`, [ADR-047](../../adr/ADR-047-mail-fix-pack.md) §5: строка 1 — индикатор статуса + «Номер» + значение крупно/жирно + «Приложение» + значение пилюлей `ui/Pill tone="accent"`; строка 2 — email. Новый примитив не вводится.
- **Колонка «Команда»** — значение видно **полностью**; `truncate`/`overflow-hidden` на значении команды **запрещены** (переполнение решается размером).
- **Тулбар вкладки «Почты»** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §1) — **рядом** с сегментом «Все/Активные/Неактивные»: **поле поиска** (`ui/Input` + `Search`, плейсхолдер **«Поиск по почтам…»**) — **клиентское**, по `number`/`app_name`/`email` (подстрока, ci); **селектор «Команда»** (`ui/Select`, «Все команды» + команды + «Без команды») — **клиентский**, рендерится **только** при `me.sees_all_mail_teams === true` (норма [ADR-036](../../adr/ADR-036-sms-team-filter-admin-only.md), [TD-058](../../100-known-tech-debt.md)). Оба фильтра **клиентские** — каталог ящиков грузится целиком; **backend не меняется**, параметров `q`/`team_id` у `GET /api/mail/mailboxes` нет. Пустой результат — «Ничего не найдено». Порядок: серверный `is_active` → поиск → команда.
- **Непрочитанные письма** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2.8) — в ленте: **полужирная тема + точка `--accent`** + sr-only «Непрочитано» (новый примитив не вводится; `Badge dot` не переиспользуется). Тумблер **«Непрочитанные»** в тулбаре ленты — **СЕРВЕРНЫЙ** (`unread=true`, сбрасывает пагинацию), в отличие от клиентского «С тегами». Открытие письма при активном фильтре **не удаляет** его из текущего списка (иначе выбранное письмо исчезало бы из-под курсора).
- **`/tg/mail`** — Mini App: без заголовка, без таб-лейбла, лента напрямую; клик по письму → read-only full-text detail внутри того же webview; reply нет. **Индикатор непрочитанного — есть**; **пометка прочитанным при открытии — есть** (тот же `POST …/read`); фильтра «Непрочитанные» и кнопки «Отметить непрочитанным» — **нет**.

## DoD

- [x] Push-приём (`/api/mail/ingest` + HMAC + идемпотентность) и status-канал работают на проде (cut-over 2026-07-10, 2874 письма).
- [x] `MailDispatcherService` (проходы A/B/C) — единственный нотификатор; доставка подтверждена end-to-end.
- [x] 5 ботов переключены на CRM-вебхуки; Mini App `/tg/mail` открывается без экрана логина.
- [ ] `mail_tags.is_builtin` дропнут (`0023`), seed из lifespan убран, любой тег удаляется, «встроенный» из UI убран ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §1).
- [ ] Лейблы типов правил по словарю; `sender_exact` убран из списка создания, но отображается ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §2).
- [ ] `mail_accounts.number`/`app_name` (миграция — файл `0024_mail_accounts_number_app_name.py`, **revision id `0024_mail_accounts_num_app_name`** — ограничение `alembic_version.version_num VARCHAR(32)`, [ADR-047](../../adr/ADR-047-mail-fix-pack.md) §3.5; + backfill); форма — два поля; `display_name` производный; **в агрегатор уходит только `display_name`** (белый список payload) ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §3).
- [x] Одноразовый ETL-скрипт `backend/scripts/migrate_mail_data.py` **удалён** (выполнено 2026-07-11) ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §1): отработал на cut-over, обращался к дропнутой колонке `is_builtin` и удалённому модулю builtin-тегов; повторный прогон невозможен (агрегатор демонтирован).
- [ ] Колонка «Команда» расширена, значение не обрезается ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §4).
- [ ] Новый рендер строки ящика по `screen/1.jpg` ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §5).
- [ ] Тело письма следует теме; билдер srcDoc — единый источник; `sandbox=""` без `allow-scripts`/`allow-same-origin` ([ADR-047](../../adr/ADR-047-mail-fix-pack.md) §6).
- [ ] **Вкладка «Почты»: поиск (`number`/`app_name`/`email`, «Поиск по почтам…») + клиентский селектор «Команда» под `sees_all_mail_teams`** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §1). Backend не менялся; параметры `q`/`team_id` у `GET /api/mail/mailboxes` **не заводились**.
- [ ] **Личная прочитанность** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md) §2): таблица `mail_message_reads` (миграция **`0025_mail_message_reads`**, PK `(user_id, message_id)` + `ix_mail_message_reads_message_id`); `MailMessage += is_unread`; `GET /api/mail/messages += unread`; `POST`/`DELETE /api/mail/messages/{id}/read` → `204` (идемпотентны, гейт `mail:view`, вне scope → `404`); супер-админ из `.env` → `403`.
- [ ] **Пометка при открытии** работает **и в вебе, и в Mini App `/tg/mail`** (тот же эндпоинт); авто-выбор самого свежего письма тоже помечает; повторные рендеры `POST` не шлют; кнопка «Отметить непрочитанным» возвращает в «непрочитано».
- [ ] **Производительность ленты:** `is_unread` — батч-лукап по PK (не JOIN в keyset, не N+1); `unread=true` — `NOT EXISTS` **внутри** keyset-запроса; инвалидэйта ленты после отметки нет; badge-счётчика нет.
- [ ] Lint / type-check / тесты зелёные (backend и frontend).

## Changelog

- 2026-07-13: **Поиск/фильтр на вкладке «Почты» + ЛИЧНАЯ прочитанность писем** ([ADR-050](../../adr/ADR-050-mail-search-team-filter-personal-read-state.md), spec-ready). (1) Вкладка «Почты»: **клиентский** поиск по `number`/`app_name`/`email` («Поиск по почтам…») и **клиентский** селектор «Команда» (гейт `sees_all_mail_teams`, как на вкладке «Сообщения» — [ADR-036](../../adr/ADR-036-sms-team-filter-admin-only.md); [TD-058](../../100-known-tech-debt.md)) — **backend не меняется** (каталог ящиков грузится целиком). (2) **Прочитанность — личная у каждого пользователя**: таблица `mail_message_reads` (миграция `0025`), `MailMessage += is_unread`, `GET /api/mail/messages += unread`, `POST`/`DELETE /api/mail/messages/{id}/read` (`204`, идемпотентны, гейт `mail:view`, вне scope → `404`). **Пометка при открытии — и в вебе, и в Mini App** (Mini App несёт обычный CRM-JWT с `uid` → спец-эндпоинт не нужен); **возврат в «непрочитано» есть**; супер-админ из `.env` — `403`/`is_unread=false`. Non-goal «пометка прочитано/непрочитано» **выведен** из Out of scope (папки/архив остаются). Badge-счётчик непрочитанных **не вводится**.
- 2026-07-13: **Каталог ящиков обслуживает `/teams`** ([ADR-048](../../adr/ADR-048-teams-mailbox-count-mail-row.md), spec-ready): `MailAccountRepository += count_by_teams`/`count_by_team` (агрегат `TeamListItem.mailbox_count`, чип «N почт»); `TeamMailboxItem += number`/`app_name` (строка почты в detail-панели `/teams` рендерится тем же визуальным языком, что строка ящика на `/mail`: крупный жирный «Номер» + «Приложение» пилюлей `ui/Pill tone="accent"`). Гейт этих путей — `teams:view` **без** `MailScope` (§4 ADR-048); креды/статус синка не раскрываются. Новых эндпоинтов и миграций нет — `GET /api/teams/{id}/mailboxes` переиспользован.
- 2026-07-11: **Mail-пакет фиксов + синхронизация docs с ADR-044** (architect, [ADR-047](../../adr/ADR-047-mail-fix-pack.md)). (0) **Закрыт обязательный follow-up [ADR-044](../../adr/ADR-044-mail-full-merge-into-crm.md) §12:** этот README, mail-раздел [04-api.md](../../04-api.md#mail) и [03-data-model.md](../../03-data-model.md) переписаны под фактический код — прежние тексты описывали отменённую read-through-прокси-модель (`group_id`, `MailTeam`, `GET /api/mail/teams`, «без БД, без миграций»); `teams.mail_group_id` объявлен мёртвым легаси-остатком ([TD-051](../../100-known-tech-debt.md)); мёртвая фабрика `mail_group_not_found` снята. (1) **`is_builtin` упразднён** (миграция `0023`: DROP COLUMN + data-seed 10 канонических тегов), **seed в lifespan убран** (иначе удалённый тег воскресал при рестарте — корень фикса), удалять можно любой тег, `409`-ветка снята. (2) Лейблы правил: «Тема письма»/«Текст письма»/«Отправитель»; `sender_exact` убран из UI-списка, но отображается ([TD-055](../../100-known-tech-debt.md)). (3) `number`/`app_name` (миграция `0024` + backfill-разбор `display_name`), `display_name` — производное; **белый список исходящего payload** закрывает ловушку `model_dump(exclude={"team_id"})` ([TD-052](../../100-known-tech-debt.md)). (4) Колонка «Команда» расширена, обрезка запрещена. (5) Новый рендер строки ящика (`screen/1.jpg`, `ui/Pill tone="accent"`). (6) Тело письма по теме CRM, единый билдер srcDoc, изоляция не ослаблена.
- 2026-07-10: **Outlook OAuth headless + инструкция «Как добавить почту?»** ([ADR-045](../../adr/ADR-045-mail-outlook-oauth-headless-reonboarding.md)).
- 2026-07-10: **UX Telegram Mini App почты** (`/tg/mail`): без заголовка, без лейбла «Сообщения», full-text по клику ([ADR-044 поправка](../../adr/ADR-044-mail-full-merge-into-crm.md#поправка-2026-07-10--ux-telegram-mini-app-почты-tgmail-без-заголовка-без-лейбла-сообщения-full-text-по-клику)).
- 2026-07-10: **Полный перенос модуля «Почты» в CRM** ([ADR-044](../../adr/ADR-044-mail-full-merge-into-crm.md)) — cut-over выполнен на проде: письма/теги/уведомления в БД CRM, агрегатор = connector с push, ящик закреплён за командой (`mail_accounts.team_id`), 5 ботов на CRM-вебхуках, Mini App `/tg/mail`. **Отменяет** [ADR-038](../../adr/ADR-038-mail-headless-integration.md)/[ADR-043](../../adr/ADR-043-lazy-mail-group-provisioning.md). Боевой блокер (`413` на батч-приёме) закрыт `client_max_body_size 50m`.
- 2026-07-03 … 2026-07-09: история read-through-прокси и headless-прокси ([ADR-012](../../adr/ADR-012-mail-read-through-proxy.md)/[ADR-013](../../adr/ADR-013-mail-newest-first-master-detail-inline-reply.md)/[ADR-017](../../adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)/[ADR-038](../../adr/ADR-038-mail-headless-integration.md)/[ADR-043](../../adr/ADR-043-lazy-mail-group-provisioning.md)) — **модель отменена [ADR-044](../../adr/ADR-044-mail-full-merge-into-crm.md)**; записи сохранены в самих ADR как история решений.
