# ADR-044 — Полный перенос модуля «Почты» в CRM: агрегатор как чистый connector с push, письма/теги/Telegram в CRM, ящик закреплён за командой

Статус: `accepted` · Дата: 2026-07-10

**Отменяет курс** [ADR-038](ADR-038-mail-headless-integration.md) (headless read+write-прокси без хранения) и [ADR-043](ADR-043-lazy-mail-group-provisioning.md) (ленивый провижининг почтовой группы «команда = группа»). Оба переводятся в статус `superseded by ADR-044`. Парный ADR в mail-агрегаторе — `ADR-0043` (strip-to-connector + push). Решение владельца продукта (дословно и уточнения — см. Context).

> **Ограничение синхронизации docs (важно).** На момент принятия ADR-044 в CRM параллельно работает другой агент с **незакоммиченными** правками в `00-vision.md`, `03-data-model.md`, `04-api.md`, `05-security.md`, `08-design-system.md`, `adr/INDEX.md`, `99-open-questions.md`. Чтобы не затереть его работу, эти файлы **этим ADR не редактируются**. Настоящий ADR — **самодостаточный нормативный источник** новой архитектуры. Синхронизация перечисленных файлов (реестр ADR, mail-раздел `04-api.md`, схемы `03-data-model.md`, security-разделы, дизайн-словарь) — **обязательный follow-up architect'а после мержа правок параллельного агента** (см. §12 «Обязательные follow-up-правки docs»). До выполнения follow-up эти файлы содержат описание отменённой модели ADR-038/043 — приоритет имеет ADR-044.

## Context

Сегодня «Почты» — headless read+write-прокси к агрегатору `postapp.store` без хранения в CRM ([ADR-012](adr/ADR-012-mail-read-through-proxy.md)/[ADR-038](ADR-038-mail-headless-integration.md)/[ADR-043](ADR-043-lazy-mail-group-provisioning.md)): CRM ходит на внешний API за лентой/ящиками/тегами, теги/Telegram/webhooks/forwarding/группы живут в агрегаторе.

**Решение владельца (дословно):** «Почтовый агрегатор остаётся только сервисом, с помощью которого можно добавлять почты. Команды, теги, пользователи мы убираем, все они будут храниться в CRM. Также убираем фронт почтового агрегатора. По сути мы оставляем бэк, в котором прописана логика добавления почт. В CRM интегрируем всё остальное. Команды, Теги и т.д. Теги сможет создавать только Админ и они будут применяться ко всем почтам во всех командах. Телеграм-уведомления также будут реализованы через CRM. Тебе будет необходимо изменить все 5 телеграм-ботов, которые сейчас использует почтовый агрегатор, и сменить им вебхуки на вебхуки CRM.»

**Уточнения владельца:**
1. Вложения **не переносим** в CRM.
2. Агрегатор **сам присылает** новое письмо в CRM (**push**, не поллинг CRM'ом).
3. Переносим **историю уведомлений** и **сами письма**.
4. **«Почты закреплены за командами, а не за пользователями.»** Владение ящиком = команда; привязка ящик↔команда живёт в CRM. Пользователи агрегатора не нужны.

**Факты прода** (снапшот, растут — синк идёт): в агрегаторе — **≥2871 писем**, 646 вложений, 121 ящик, **≥12982 `telegram_notifications` (все доставлены)**, 76 `tags`, 8 `telegram_links`, **0 webhooks**, 1 правило `group_forwarding` (группа «Команда ivan» → `springtechco99@gmail.com`), 1 `sent_messages`, 0 сирот. **Разметка тегами:** привязок глобальных тегов к письмам — 9, персональных — 2284 (почти вся разметка на персональных; см. §10 — переносим 16 глобальных тегов и воспроизводим разметку пере-применением). `mail_accounts.user_id` уже НЕ отражает владельца (техническая привязка). CRM: нет Redis/S3/брокера/планировщика; фон = `asyncio.create_task` в lifespan (`backend/app/main.py`); секреты — Fernet.

## Decision

### §1. Целевая граница сервисов

**Агрегатор (`postapp.store`) остаётся чистым mail-connector'ом.** Оставляет: подключение ящиков (IMAP/SMTP-креды, AES-256-GCM), sync-worker (IMAP-поллинг, UIDNEXT-инкремент), SMTP-отправку, **push нового письма в CRM**. Убирает: `groups`/`user_groups`, теги (`tags`/`tag_rules`/`message_tags`), Telegram (`telegram_links`/`telegram_notifications`/SSO/боты/callback), `webhooks`/`webhook_deliveries`, `group_forwarding`/`message_forwards`, `attachments`/`sent_attachments`/MinIO, Jinja-UI, Mini App, пользователей (кроме служебного `crm-service`). Детали агрегатора — mail-агрегатор `ADR-0043`.

**CRM становится единственным UI и системой-записью (system of record)** по письмам, тегам, командам, правам, Telegram-уведомлениям. Письма/теги/уведомления **хранятся в БД CRM** (разворот «без хранения» ADR-012/038).

**Ящик закреплён за командой напрямую (per-mailbox `team_id`), без групп-индирекции.** Это возврат к варианту (a) ADR-038 §2 (per-mailbox владение) и **полное закрытие [TD-033](../100-known-tech-debt.md)** (1:1 команда↔группа снято — теперь ящик привязан к одной команде через поле, а группы агрегатора упразднены). Групповая модель ADR-043 (`teams.mail_group_id`, ленивый провижининг, `ensure_team_mail_group`, CAS, external `POST/DELETE /teams`) **упразднена целиком** — см. §11.

**`mail_accounts.user_id` в агрегаторе → единый служебный `crm-service`** (super_admin, уже сидируется, `ADR-0039`). Колонка `user_id` сохраняется NOT NULL (FK на `crm-service`) — минимизирует миграцию (не удаляем колонку/FK), а `UNIQUE(user_id, email)` превращается в глобальную уникальность email (штатно для headless, `ADR-0039` §409). Владение командой в агрегаторе **не хранится** — только в CRM. `mail_accounts.group_id` + таблица `groups` — **удаляются** (mail-агрегатор `ADR-0043`).

### §2. Данные в CRM: новые таблицы (миграции с `0021`)

Все id CRM — UUID, кроме связки с агрегатором (см. ниже). Порт из агрегатора **минус вложения, минус пользователи/группы агрегатора**. Точные DDL — в §12-follow-up `03-data-model.md`; здесь — нормативная спецификация.

- **`mail_accounts`** (локальный каталог ящиков; CRM — источник истины привязки к команде):
  - `id INTEGER PRIMARY KEY` — **равен id ящика в агрегаторе** (агрегатор присваивает при создании; см. §4-создание). Единый ключ упрощает связь писем (`mail_account_id` в push — тот же int).
  - `email TEXT NOT NULL`, `display_name TEXT NULL`,
  - `team_id UUID NULL REFERENCES teams(id) ON DELETE SET NULL` — **команда-владелец** (per-mailbox; `NULL` = ящик без команды, пул unassigned),
  - `is_active BOOLEAN NOT NULL DEFAULT true`,
  - `last_synced_at TIMESTAMPTZ NULL`, `last_sync_error TEXT NULL`, `consecutive_failures INTEGER NOT NULL DEFAULT 0` (зеркало статуса синка из агрегатора — обновляются status-каналом §3),
  - `down_alert_sent_at TIMESTAMPTZ NULL` — идемпотентность mailbox-down алерта «ровно один на переход» (§3/§6 проход C; guarded set `WHERE ... IS NULL`, reset в `NULL` при re-enable; воспроизводит агрегаторский `mail_accounts.disabled_alert_sent_at` из `ADR-0033`). **При миграции переносится из `disabled_alert_sent_at`** (§10) — иначе по 2 уже оталерченным ящикам прода алерт уйдёт повторно,
  - `created_at`/`updated_at TIMESTAMPTZ`.
  - Индекс `ix_mail_accounts_team_id`.
- **`mail_messages`** (system of record писем):
  - `id BIGSERIAL PRIMARY KEY` — ключ ленты/пагинации (заменяет внешний id). При миграции — **preserve** id из агрегатора (§10).
  - `mail_account_id INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE`,
  - `uidvalidity BIGINT NOT NULL`, `uid BIGINT NOT NULL`,
  - `message_id_header TEXT NULL`, `subject TEXT NULL`, `from_addr TEXT NOT NULL`, `from_name TEXT NULL`, `to_addrs TEXT NOT NULL DEFAULT ''`, `cc_addrs TEXT NULL`, `internal_date TIMESTAMPTZ NOT NULL`,
  - `body_text TEXT NOT NULL DEFAULT ''`, `body_html TEXT NULL`, `body_truncated BOOLEAN NOT NULL DEFAULT false`, `body_present BOOLEAN NOT NULL DEFAULT true`,
  - `in_reply_to TEXT NULL`, `refs_header TEXT NULL`,
  - `notified_at TIMESTAMPTZ NULL` — high-water для Telegram-диспетчера (§6): `NULL` = уведомление ещё не разослано,
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
  - **`UNIQUE (mail_account_id, uidvalidity, uid)`** = `uq_mail_messages_account_uidv_uid` — идемпотентность push (тот же ключ, что в агрегаторе).
  - **Порядок ленты — по `(internal_date DESC, id DESC)`, НЕ по `id`** (MAJOR-8 fix): `id BIGSERIAL` отражает **порядок прихода push'а**, а не дату письма; push порядок не гарантирует, а `crm_push_recovery` ре-пушит письма позже → старое письмо получило бы больший `id` и «всплыло» бы в топ как новейшее. Сортировка/keyset — по **`internal_date`** (истинная дата письма), tie-break `id`. Индексы: `ix_mail_messages_account_feed (mail_account_id, internal_date DESC, id DESC)` (лента по ящику), `ix_mail_messages_feed (internal_date DESC, id DESC)` (глобальная лента admin-scope), `ix_mail_messages_notify (id) WHERE notified_at IS NULL` (диспетчер — тут id-порядок ок, это очередь обработки, не лента). Вложений НЕТ.
  - **Keyset-предикат курсора (нормативно, MINOR-2 — компаундный, row-wise; НЕ «только по `internal_date`»).** `internal_date` **не уникален** (массовая рассылка от одного отправителя приходит с одинаковой секундой) → пагинация по одному `internal_date` даёт **пропуски/дубли на границах страниц**. Курсор несёт **обе** компоненты, сравнение — row-wise:
    ```sql
    -- следующая страница (older), desc-режим:
    WHERE (internal_date, id) < (:cursor_internal_date, :cursor_id)
    ORDER BY internal_date DESC, id DESC
    LIMIT :limit
    ```
    **Формат курсора (клиентский):** непрозрачная строка, кодирующая пару `(internal_date_iso8601, id)` (напр. base64 от `"<internal_date>|<id>"`); клиент передаёт её как `before` (desc) обратно без интерпретации. Ответ ленты несёт `next_cursor` = пара `(min(internal_date), соответствующий id)` последнего элемента страницы, либо `null` если старее нет. Точная сериализация курсора и имя query-параметра — follow-up `04-api.md` (§12), но **компаундность `(internal_date, id)` — нормативна здесь** (классический boundary-баг при совпадающих датах, тестами на случайных данных не ловится).
- **`mail_tags`** (глобальный админский каталог — **у тега нет владельца**, применяется ко всем письмам всех команд):
  - `id UUID PK`, `name TEXT NOT NULL`, `color TEXT NOT NULL CHECK (color ~ '^#[0-9A-Fa-f]{6}$')`, `match_mode TEXT NOT NULL DEFAULT 'any' CHECK (match_mode IN ('any','all'))`, `is_builtin BOOLEAN NOT NULL DEFAULT false`, `created_at`/`updated_at`.
  - `UNIQUE (name)` = `uq_mail_tags_name` (глобально уникальное имя — упрощение: `tags.user_id` из агрегатора **не переносится вовсе**, глобальность абсолютна).
- **`mail_tag_rules`**: `id UUID PK`, `tag_id UUID NOT NULL REFERENCES mail_tags(id) ON DELETE CASCADE`, `type TEXT NOT NULL CHECK (type IN ('subject_contains','body_contains','sender_contains','sender_exact'))`, `pattern TEXT NOT NULL`, `created_at`.
- **`mail_message_tags`**: `message_id BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE`, `tag_id UUID NOT NULL REFERENCES mail_tags(id) ON DELETE CASCADE`, `PRIMARY KEY (message_id, tag_id)`.
- **`mail_telegram_links`** (по образцу `sms_telegram_links`, multi-link ADR-0024, **+ ленивый резолв**): `telegram_user_id BIGINT PRIMARY KEY` (**= `chat_id`** для приватного чата — стабильный ключ **доставки**), `user_id UUID **NULL** REFERENCES users(id) ON DELETE CASCADE` (1:N, без UNIQUE; **NULLABLE** — orphan-линк без CRM-пользователя, §резолв ниже), `username TEXT NULL` (**нормализованный lower-case Telegram-username** — ключ **первичного связывания**), `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `dead_at TIMESTAMPTZ NULL`. Индексы: `ix_mail_tg_links_user_id (user_id) WHERE user_id IS NOT NULL`, `ix_mail_tg_links_username (username) WHERE user_id IS NULL` (для сверки orphan'ов).
  - **`chat_id` первичен для ДОСТАВКИ, `username` — для первичного СВЯЗЫВАНИЯ.** Bot API шлёт только по числовому `chat_id` (не по username), поэтому `telegram_user_id`/`chat_id` — стабильный ключ отправки. `username` меняется у пользователя со временем → используется **только** для начального связывания orphan-линка с CRM-пользователем; после проставления `user_id` доставка идёт по `chat_id` и не зависит от смены username. Устаревший `username` у уже связанного линка на доставку не влияет.
- **`mail_telegram_notifications`** (дедуп доставки + история): `id UUID PK`, `message_id BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE`, `telegram_user_id BIGINT NOT NULL` (без FK — снапшот чата), `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','dead'))`, `attempts INTEGER NOT NULL DEFAULT 0`, `last_error TEXT NULL`, `sent_at TIMESTAMPTZ NULL`, `created_at`/`updated_at`. **`UNIQUE (message_id, telegram_user_id)`** = `uq_mail_tg_notif_msg_chat` (идемпотентность «ровно один на переход», ADR-0024). История уведомлений мигрируется сюда (§10).
- **`mail_user_settings`** (opt-out уведомлений, по образцу агрегаторского `users_settings`): `user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE`, `tg_notifications_enabled BOOLEAN NOT NULL DEFAULT true`, `updated_at`. **В обязательном scope (MAJOR-4).** На проде `users_settings` = **0 строк** (никто не отписан) → данных для миграции нет, но **механизм отключения обязан существовать**: в агрегаторе был `PATCH /api/me/settings`; без CRM-аналога пользователь после переезда не сможет отписаться вовсе — регресс. Норматив: **`PATCH /api/mail/me/settings { tg_notifications_enabled: bool }`** (`mail:view`, upsert строки по `principal.user_id`), read через `GET /api/auth/me` или отдельный `GET /api/mail/me/settings`. Дефолт (нет строки) = уведомления включены.
- **`mail_forwarding` / `mail_message_forwards` — НЕ создаются на старте (forwarding отложен владельцем, [TD-040](../100-known-tech-debt.md)).** Миграции `0021`/`0022` их **не заводят**. Проектная форма (для будущей реализации): `mail_forwarding { team_id UUID PK REFERENCES teams ON DELETE CASCADE, forward_to TEXT, is_active BOOL, created_at/updated_at }` + `mail_message_forwards { message_id BIGINT REFERENCES mail_messages ON DELETE CASCADE, team_id UUID REFERENCES teams, forward_to TEXT, sent_at TIMESTAMPTZ NULL, error TEXT NULL, created_at, PK(message_id, team_id) }` — создаются **вместе с реализацией forwarding** (образец агрегаторского ADR-0034, минус вложения), НЕ этими миграциями. До тех пор пересылка не работает, правило прода не мигрируется (TD-040).
- **`mail_sent_messages`** (миграция `0022`; запись отправленных reply — CRM теперь инициатор): `id UUID PK`, `mail_account_id INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE`, `user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL`, `to_addrs TEXT NOT NULL`, `cc_addrs TEXT NULL`, `subject TEXT NULL`, `body_text TEXT NOT NULL`, `in_reply_to TEXT NULL`, `refs_header TEXT NULL`, `smtp_message_id TEXT NULL`, `sent_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

**`teams.mail_group_id`** (миграция 0018, ADR-038) — **удаляется отдельной миграцией ПОСЛЕ ETL** (не в `0021`/`0022`): групп больше нет, привязка ушла в `mail_accounts.team_id`. **Тайминг критичен:** §10 использует `teams.mail_group_id` как **ключ маппинга** ящик→команда (`aggregator group_id → CRM team` через `mail_group_id`); удалить его до ETL нельзя. Порядок: `0021`/`0022` (создание схемы) → ETL S6 (маппинг по `mail_group_id`) → **post-ETL миграция** `drop column mail_group_id + uq_teams_mail_group_id`.

### §3. Push-контракт «агрегатор → CRM»

**Эндпоинт CRM:** `POST /api/mail/ingest` (машинный, **без JWT**, CSRF-exempt). Приёмник нового письма от агрегатора.

**Аутентификация — HMAC-SHA256 (тело-связанная), НЕ статический bearer:**
- Заголовки: `X-Mail-Signature: sha256=<hex>`, `X-Mail-Timestamp: <unix_seconds>`.
- **Каноническая форма подписи (нормативно, байтами — ИДЕНТИЧНО mail-агрегатор `ADR-0043` §2; f-string над `bytes` ЗАПРЕЩЁН — он даёт `repr` `b'...'`, а не сами байты):**
  ```python
  mac_input = str(timestamp).encode("ascii") + b"." + raw_body_bytes
  signature = hmac.new(secret.encode("utf-8"), mac_input, hashlib.sha256).hexdigest()
  ```
  где `timestamp` — целое из `X-Mail-Timestamp` (десятичное ASCII), `raw_body_bytes` — **сырое** тело запроса до JSON-парсинга (не re-serialized), разделитель — один байт `b"."`. Ключ — общий секрет **`MAIL_PUSH_SECRET`** (env обеих сторон, класса секретов, только env, не в БД/логах/ответах/URL). Обе стороны обязаны строить `mac_input` этим выражением побайтно.
- Проверка: `abs(now - timestamp) <= MAIL_PUSH_MAX_SKEW_SEC` (default `300`) иначе `401`; `secrets.compare_digest(signature, expected)` иначе `401 not_authenticated`. Пустой `MAIL_PUSH_SECRET` ⇒ эндпоинт выключен (`503 mail_ingest_not_configured`).
- **О timestamp-окне (честно):** `abs(now - ts) <= 300` — это **ограничение окна валидности**, а НЕ полноценный анти-replay: без nonce перехваченный валидный запрос в пределах 300 с можно воспроизвести. Практически безвредно за счёт **идемпотентности приёмника** (`ON CONFLICT (mail_account_id,uidvalidity,uid) DO NOTHING` — повтор не создаёт дубля письма). Так и трактуем: окно ограничивает поверхность, идемпотентность гасит повтор; отдельного nonce-стора не вводим (NFR-1).
- Обоснование HMAC (а не bearer): тело-связанная подпись + окно симметричны security-модели SMS-webhook (Twilio-подпись, `05-security.md`); ключ не «утекает» как переиспользуемый bearer.

**Тело** `MailIngestRequest`: `{ "messages": MailIngestMessage[] }`, батч `1..MAIL_INGEST_MAX_BATCH` (default `100`).

`MailIngestMessage`:
| Поле | Тип | Примечание |
|------|-----|-----------|
| `mail_account_id` | int | id ящика (= `mail_accounts.id` в CRM и агрегаторе) |
| `uidvalidity` | int | часть ключа идемпотентности |
| `uid` | int | часть ключа идемпотентности |
| `message_id_header` | string \| null | RFC Message-ID |
| `subject` | string \| null | |
| `from_addr` | string | |
| `from_name` | string \| null | |
| `to_addrs` | string | |
| `cc_addrs` | string \| null | |
| `internal_date` | string (ISO 8601 UTC) | |
| `body_text` | string | |
| `body_html` | string \| null | |
| `in_reply_to` | string \| null | threading |
| `refs_header` | string \| null | threading |

Вложения **не передаются**.

**Идемпотентность:** вставка `mail_messages` `ON CONFLICT (mail_account_id, uidvalidity, uid) DO NOTHING`. Повтор доставки не дублирует письмо.

**Неизвестный ящик:** если `mail_account_id` отсутствует в CRM-каталоге (`mail_accounts`) — сообщение **пропускается** (счётчик `unknown_mailbox`), батч НЕ отклоняется (иначе агрегатор ретраил бы вечно). Т.к. все ящики заводятся через CRM (§4), catalog всегда существует до первого письма; unknown — аномалия (логируется). Остаток — [TD-041](../100-known-tech-debt.md) (см. §12).

**Ответ `200`** `MailIngestResponse`: `{ "accepted": int, "duplicate": int, "unknown_mailbox": int }`. `2xx` ⇒ агрегатор считает батч доставленным (не ре-пушит). **Ошибки:** `401 not_authenticated` (подпись/skew), `400 validation_error` (битое тело/батч > лимита), `503 mail_ingest_not_configured` (`MAIL_PUSH_SECRET` пуст).

**Порядок/очередь ретраев — на стороне АГРЕГАТОРА** (у него есть Redis; CRM однопроцессная, без брокера — по NFR-1 очередь на её стороне не заводим). Агрегатор: после sync-вставки нового письма (в свою `messages` как **push-outbox** + колонка `pushed_at`) — LPUSH в Redis `crm_push_queue`; APScheduler-job `crm_push_dispatch` (интервал ~5с) POST'ит батч в `/api/mail/ingest`; `2xx` → `pushed_at=now()`; ошибка → ре-enqueue/оставить для recovery; job `crm_push_recovery` (hourly) ре-enqueue писем с `pushed_at IS NULL`. Это зеркалит существующий `tg_notify_dispatch`/`tg_notify_recovery`. **Строгий порядок доставки push не гарантируется** — CRM присваивает свой `mail_messages.id` (BIGSERIAL) при вставке (≈ порядок прихода). **Поэтому лента сортируется по `internal_date DESC, id DESC`, а НЕ по `id`** (§2): ре-пуш старого письма recovery-джобой не «поднимает» его в топ (у него старый `internal_date`). Детали агрегатора — `ADR-0043`.

**Обработка на приёме (быстрая, синхронная в хендлере — только дешёвый SQL):** (1) HMAC; (2) на каждое письмо — insert `ON CONFLICT DO NOTHING`; (3) если **вставлено** (RETURNING id) — применить теги (§5, INSERT `mail_message_tags`). **Telegram-рассылку хендлер НЕ делает** — оставляет `notified_at IS NULL`, её берёт фоновый диспетчер (§6). Так `/ingest` отвечает быстро (агрегаторская очередь дренится), а доставка durable и вынесена из request-пути.

**Status-канал ящика (нормативно — воспроизводит mailbox-down алерт `ADR-0033`; MAJOR-2).** На проде функция живая: 7 ящиков `is_active=false`, 2 уже оталерчены. Канал зеркалит статус синка ящика из агрегатора в CRM `mail_accounts` и триггерит алерт при переходе `is_active: true→false`. **Разобраны два варианта:**
- **(A) Push статуса агрегатором (выбран).** Агрегатор на каждое изменение статуса синка ящика POST'ит **`POST /api/mail/mailbox-status`** (тот же HMAC, §выше) `{ mail_account_id, is_active, last_synced_at, last_sync_error, consecutive_failures }`. CRM: `UPDATE mail_accounts SET ...`; при переходе `is_active` true→false (было `true`/`is_active` в строке до апдейта) — оставляет `down_alert_sent_at` как есть (NULL) → проход C §6 разошлёт алерт **один раз** (guarded set). При переходе false→true — `down_alert_sent_at=NULL` (сброс, готов к следующему падению). **Идемпотентность «ровно один на переход» — на стороне CRM** (`down_alert_sent_at`), не зависит от повторов push. **Выбран** — событийный, без лишнего поллинга, реюз push-инфраструктуры/HMAC.
- **(B) CRM поллит `GET /api/external/mailboxes`** (pull-эндпоинт остаётся, §владелец) и детектит переход, храня предыдущее `is_active`. Отклонён как основной: лишний периодический поллинг + хранение предыдущего состояния = дублирование того, что даёт событийный push; но **оставляется как fallback-reconcile** (тот же `GET` для сверки миграции) — при рассинхроне статусов CRM может подтянуть актуальный `is_active` пуллом.
- **Коды/ошибки `/api/mail/mailbox-status`:** `200` (обновлено), `401` (HMAC), `503` (`MAIL_PUSH_SECRET` пуст), неизвестный `mail_account_id` → `200` с no-op (аномалия, лог, [TD-041]).

### §4. Ящики: CRUD в CRM + креды транзитом в агрегатор

Write-эндпоинты `/api/mail/mailboxes*` сохраняются, но **семантика меняется**: CRM хранит каталог локально (`mail_accounts`), а IMAP/SMTP-креды **по-прежнему транзитом** в агрегатор (шифрование AES-GCM ТАМ; Fernet CRM к почте не применяется — инвариант ADR-038 §5 сохранён).

- **`POST /api/mail/mailboxes`** (`mail:create`): тело — схема **`MailMailboxCreateRequest`** (инлайн, self-contained; MAJOR-5):

  | Поле | Тип | Правила |
  |------|-----|---------|
  | `email` | string | required, адрес ящика (IMAP-логин по умолчанию) |
  | `imap_host` | string | required |
  | `imap_port` | integer | required, `1..65535` |
  | `imap_ssl` | boolean | required |
  | `smtp_host` | string | required |
  | `smtp_port` | integer | required, `1..65535` |
  | `smtp_ssl` | boolean | required (взаимоискл. с `smtp_starttls`) |
  | `smtp_starttls` | boolean | required (взаимоискл. с `smtp_ssl`) |
  | `smtp_username` | string \| null? | опц.; `null` → `email` |
  | `password` | string | required, IMAP-пароль (**транзит**, §5-security) |
  | `smtp_password` | string \| null? | опц.; `null` → `password` (транзит) |
  | `display_name` | string \| null? | опц., имя ящика |
  | `team_id` | string(uuid) \| null? | команда-владелец; `null` — без команды (unassigned) |

  Поток: CRM вызывает агрегатор `POST /api/external/mailboxes {...creds}` (владелец в агрегаторе = `crm-service`, **без** `group_id`) → агрегатор возвращает `id` → CRM вставляет `mail_accounts { id, email, display_name, team_id, is_active }`. **Авторизация (закрыто, MAJOR-6 — развилки нет):** не-admin обязан указать `team_id ∈ MailScope.team_ids` (участник), иначе `403`; **`team_id = null` (ящик без команды) — ТОЛЬКО admin-уровень** (`sees_all_teams`), не-admin `null` → `403`. Обоснование: unassigned-ящик не даёт командной видимости/уведомлений (TD-042) — заводить «безхозный» ящик вправе только админ; рядовой участник всегда привязывает к своей команде. **Response 201** — схема `MailMailbox` (см. §GET). Ошибки: `401`, `403 forbidden`, `400 validation_error`, `404 team_not_found`, `409 mail_conflict` (email занят), `422 unprocessable` (IMAP/SMTP-логин/коннект), `502 mail_unavailable`, `503`.
- **`PATCH /api/mail/mailboxes/{id}`** (`mail:edit`): правка кредов (транзит в агрегатор `PATCH /api/external/mailboxes/{id}`), `is_active`, `display_name`, `team_id`. **Смена `team_id` (перенос между командами) — ТОЛЬКО admin-уровень** (`MailScope.sees_all_teams`, требование владельца, симметрично ADR-043 §5). Перенос = локальный `UPDATE mail_accounts.team_id` (агрегатор о команде не знает — сетевой вызов к нему только если меняются креды/`is_active`).
- **`DELETE /api/mail/mailboxes/{id}`** (`mail:delete`): агрегатор `DELETE /api/external/mailboxes/{id}` + удаление `mail_accounts` (CASCADE удалит письма ящика — ретенционно приемлемо; либо soft — на усмотрение реализации, но нормативно hard-delete с CASCADE).
- **`POST /api/mail/mailboxes/{id}/sync`** (`mail:sync`): проброс `POST /api/external/mailboxes/{id}/sync` (форс-синк в агрегаторе).
- **`POST /api/mail/mailboxes/test`** (`mail:create`): проброс `POST /api/external/mailboxes/test` (без сохранения).
- **`GET /api/mail/mailboxes`** (`mail:view`): читает **из CRM `mail_accounts`** (не из агрегатора), фильтр `MailScope` по `team_id`. `team_id` заменяет прежний `group_id` в схеме ответа.
- **`GET /api/teams/{id}/mailboxes`** (`teams:view`): ящики команды из `mail_accounts WHERE team_id=:id`.

Все write-эндпоинты кредов — `Cache-Control: no-store` (инвариант ADR-038 §5). Внешний `MAIL_API_KEY`/`X-API-Key` для управляющих вызовов агрегатора сохраняется (create/patch/delete/sync/test/send).

### §5. Теги: перенос движка матчинга в CRM (побуквенно из агрегатора)

Теги — **глобальный админский каталог** (владельца у тега нет, `mail_tags` без `user_id`). Создание/правка — только под `mail:tags` (админ-функция, ADR-038 §4). Применяются ко **всем** письмам всех команд.

Эндпоинты `/api/mail/tags*` (CRUD + rules + apply-to-existing) — **работают против БД CRM** (не проксируют агрегатор): `GET` (`mail:view`), `POST/PATCH/DELETE/rules/apply-to-existing` (`mail:tags`).

**Схемы (инлайн, self-contained; MAJOR-5):**
- **`MailTagRule`** `{ id: uuid, type: enum, pattern: string, created_at: datetime }`; `type ∈ {subject_contains, body_contains, sender_contains, sender_exact}`; `pattern` 1..256.
- **`MailTagFull`** `{ id: uuid, name: string(1..64), color: string(^#[0-9A-Fa-f]{6}$), match_mode: enum{any,all}, is_builtin: bool, rules: MailTagRule[], created_at, updated_at }`. `is_builtin=true` — удалять нельзя (`409`), правка имени/правил разрешена.
- `MailTagCreateRequest` `{ name, color, match_mode? (default any) }`; `MailTagUpdateRequest` `{ name?, color?, match_mode? }`; `MailTagRuleCreateRequest` `{ type, pattern }`. Ответы: create → `201 MailTagFull`; apply-to-existing → `200 { applied_count: int }`.

**Семантика матчинга — перенести ПОБУКВЕННО** из `D:\BA\mail-agregator\backend\app\tags\sql.py` (не переизобретать): whole-word, case-**sensitive**, whitespace-нормализация (`translate` U+00A0 → пробел, затем `\s+`→` `), явные граничные классы `(^|[^[:alnum:]_])`…`([^[:alnum:]_]|$)` (НЕ `\y`), экранирование метасимволов `regexp_replace(pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g')`, `~` (не `~*`), `sender_contains` матчит `from_addr` **и** `from_name`, `body_contains` матчит `body_text` **и** strip-tags(`body_html`), `sender_exact` = `LOWER=LOWER`, `match_mode` any/all (`EXISTS`/`NOT EXISTS`), `ON CONFLICT (message_id, tag_id) DO NOTHING`.

**Упрощение относительно агрегатора:** т.к. все теги глобальные и владельца/видимости нет, из SQL **выпадают** ветки visibility (`LEFT JOIN users`, `user_groups`, `u.role='super_admin'`, `is_super_admin`, `t.user_id IS NULL`) — остаётся чистый предикат матчинга правил над одним письмом. `APPLY_TAGS_TO_MESSAGE` (на приёме push'а) и `APPLY_TAG_TO_EXISTING` (apply-to-existing по всем письмам) — обе без visibility-фильтра (теги видят все письма системы). Известные ограничения матчинга (`strip_tags` не декодит HTML-entities) наследуются как [TD-024] (порт as-is).

**Применение тегов — в CRM на приёме push'а** (§3 шаг 3), в той же короткой транзакции, best-effort try/except (по образцу sync-hook агрегатора). `apply-to-existing` — bulk-INSERT по всем `mail_messages`.

**Builtin-теги, 6 кастомных и seed (нормативно).** Итоговый каталог — **16 глобальных**: **10 builtin** (`is_builtin=true`) + **6 кастомных** (`is_builtin=false`: `Поддержка`, `Билд в коннекте`, `Small Business`, `Билд не дошёл`, `Ждет Ревью 2`, `Билд не приняли` — §10).
- **`seed_builtin_tags` (S1, lifespan, по образцу агрегаторского `seed_builtin_tags`/`seed_super_admin`)** сидирует **только 10 builtin** по каноническому набору `mail_builtin_tags.py`, идемпотентно (`INSERT ... ON CONFLICT (name) DO NOTHING`). Ленивого per-login `ensure_builtin_tags` нет (в CRM нет per-login-хука почты).
- **Порядок и идемпотентность на проде:** миграция (§10) заводит **все 16** тегов (10 builtin + 6 кастомных) как данные. Если миграция идёт **до** первого lifespan-старта — `seed_builtin_tags` но-опит (10 builtin уже есть, `UNIQUE (name)` глушит вставку). Если lifespan стартовал **раньше** миграции — seed создаст 10 builtin, миграция builtin-части затем но-опит по тому же `UNIQUE (name)`, а 6 кастомных доедут миграцией. Любой порядок сходится к 16 без дублей (ключ разрешения конфликтов — `uq_mail_tags_name`).
- **6 кастомных в `mail_builtin_tags.py` — НЕ добавляем (нормативное решение, ответ на вопрос backend'у).** Они **org-специфичны** (`Поддержка`/`Билд в коннекте`/… — рабочая классификация конкретной команды), а не универсальные встроенные → в canonical builtin-набор не входят и на **чистой** установке НЕ пересоздаются (там их и не должно быть). На проде они появляются **только миграцией** (одноразовый ETL в единственный prod). `is_builtin=false` → админ вправе их удалить/переименовать. Если в будущем какой-то из них решат сделать универсальным — это отдельное решение (добавить в `mail_builtin_tags.py` + `is_builtin=true`), не в scope этого ADR.

### §6. Telegram в CRM: доставка через фоновый asyncio-диспетчер (без Redis)

CRM однопроцессная, без брокера. Доставка — **фоновая asyncio-задача в lifespan** (`asyncio.create_task`, по образцу `NotifierService`/`SmsDeliveryMonitorService`), НЕ синхронно в хендлере push'а (иначе таймаут агрегаторской очереди и риск потери).

**`MailDispatcherService`** (`run()` = `while True: poll_once(); await asyncio.sleep(MAIL_DISPATCH_INTERVAL_SEC)`, default 5с; `CancelledError` → лог+re-raise; ошибка итерации логируется, цикл живёт). `poll_once` выполняет **три прохода** (по образцу `SmsDeliveryMonitorService`, `sms_ingest_service.py`):

**Проход A — новые письма** (`SELECT ... FROM mail_messages WHERE notified_at IS NULL ORDER BY id LIMIT MAIL_DISPATCH_BATCH`, partial-index; критерий — флаг `notified_at`, не high-water по id → out-of-order push безопасен):
1. **Резолв получателей (нормативно, по CRM-командам):** письмо → `mail_account_id` → `mail_accounts.team_id` → участники команды из **`user_teams`** → их **`mail_telegram_links`** (`dead_at IS NULL`) → LEFT JOIN `mail_user_settings` opt-out (`tg_notifications_enabled=false` → исключить). Плюс super_admin с живым линком. `team_id IS NULL` (unassigned) → получателей нет (кроме super_admin) — [TD-042]. Опция «только по тегам» — `MAIL_TG_NOTIFY_ALL_MESSAGES` (default `true`, паритет `TG_NOTIFY_ALL_MESSAGES`); `false` → только письма с ≥1 тегом.
2. На каждого получателя: reserve `mail_telegram_notifications` (`INSERT ... ON CONFLICT (message_id, telegram_user_id) DO NOTHING`, `status='pending'`) → send основным ботом (`format_notification`: 🆔 ящик, #️⃣ теги, Клиент, Тема, preview; callback «Посмотреть сообщение» `callback_data=mail:{message_id}`) → `status='sent'`/`sent_at`; транзиентный сбой (429/сеть/5xx) → `status='failed'`, `attempts++`, `last_error` (**строка остаётся для прохода B**); перманентный (403/blocked/chat-not-found) → `status='dead'` + `mail_telegram_links.dead_at=now()`.
3. **Push-бот команды (fan-out):** по `team_id` письма найти push-бота (env, §9) → `MAIL_ADMIN_TELEGRAM_IDS` (fire-and-forget, без трекинга — [TD-043]).
4. После обработки письма: `UPDATE mail_messages SET notified_at = now()`. Крэш посреди рассылки безопасен (дедуп-таблица + проход B).

**Проход B — recovery транзиентных сбоев (нормативно, устраняет MAJOR-1 регрессию доставки).** Отдельный `SELECT ... FROM mail_telegram_notifications WHERE status IN ('pending','failed') AND attempts < MAIL_TG_MAX_ATTEMPTS (default 6) ORDER BY updated_at LIMIT MAIL_DISPATCH_BATCH` → повторная отправка → `sent`/`failed(attempts++)`/`dead` (при `attempts >= MAX` → `dead`). Это восстанавливает `tg_notify_recovery` из снятого агрегатора (`ADR-0043` §4): без прохода B транзиентный сбой Telegram оставлял бы `failed`, а письмо уже несло `notified_at` → **уведомление терялось навсегда**. `notified_at` помечает, что письмо **обработано** (delivery-строки заведены), но фактическую доставку гарантирует ретрай по delivery-строкам.

**Проход C — mailbox-down алерты (нормативно, воспроизводит `ADR-0033`; §детект — ниже).** `SELECT mail_accounts WHERE is_active=false AND down_alert_sent_at IS NULL` → на каждый отправить получателям команды ящика (тот же резолв, что проход A, БЕЗ per-message-фильтров) алерт «⚠️ Почта … не работает: <last_sync_error>» основным ботом → **guarded** `UPDATE mail_accounts SET down_alert_sent_at=now() WHERE id=:id AND down_alert_sent_at IS NULL` (идемпотентность «ровно один на переход»). Re-enable (`is_active=true`) сбрасывает `down_alert_sent_at=NULL` (см. §3 status-канал). Fire-and-forget на доставку алерта приемлемо (best-effort, паритет агрегаторского TD-042).

**Резолв CRM-пользователя по Telegram-username (нормативно, единый механизм для бота и Mini App).** Владелец: «резолв по username телеграм и CRM». Сопоставление — **регистронезависимое**, с нормализацией ведущего `@`:
- `normalize_telegram(x)` = `strip_leading_at(x).strip().casefold()` (снять ведущий `@`, если есть; в нижний регистр). Согласовано с нормализацией `users.telegram` из ADR-041 (вход и с `@`, и без).
- Резолв: `SELECT ... FROM users WHERE lower(telegram) = normalize_telegram(tg_username)`. **Критично:** `@Katetown` (Telegram) ↔ `katetown` (CRM) — совпадают только регистронезависимо; сравнение обязано быть case-insensitive (хранить/сравнивать в нижнем регистре).
- Резолв по иммутабельному `telegram_user_id` (существующий линк с `user_id IS NOT NULL`) имеет приоритет; при отсутствии — bootstrap по username (выше). Не сопоставлен → пользователь не линкуется/не авторизуется в интерактиве (см. обработку not-found ниже и в Mini App), но **orphan-линк из миграции сохраняется** для ленивого резолва.

**Самопривязка нового сотрудника через `/start` (нормативно — обязана существовать).** Пользователь CRM, у которого ещё нет `mail_telegram_links` (напр. `Алтынай`/`ashaykenova` — есть в CRM и в «Команде Мухамеда», но привязки нет ни в агрегаторе, ни в CRM), пишет боту `/start` (или открывает Mini App) → SSO резолвит его по `telegram_user_id` (нет) → по username → `users.telegram` (ci) → upsert `mail_telegram_links { telegram_user_id(=chat_id), user_id, username }`. Так новые сотрудники подключаются без миграции. Без этого механизма подключить нового сотрудника нельзя — он обязателен.

**Членство в команде гейтит уведомления (НЕ дефект).** Линк с проставленным `user_id`, но пользователь которого **не состоит ни в одной команде** (напр. `Никита`/`Анна`/`Юля` — привязка перенесётся с `user_id`, но команд у них пока нет; владелец «позже раскидает по командам»), уведомлений **не получает**: права на почту выводятся из членства (`user_teams` → `mail_accounts.team_id`). Как только владелец добавит пользователя в команду — начнёт получать (линк уже есть). Это корректное поведение, а не потеря.

**Ленивый резолв orphan-линков (runtime, forward-looking).** Если в CRM попадёт линк без соответствия (`user_id = NULL`) — напр. будущий `/start` от Telegram-username, которого ещё нет в `users.telegram`, — он сохраняется с валидными `chat_id` и `username` (lower-case) и связывается автоматически, когда появится пользователь с этим `users.telegram` — **без повторного `/start`**. (На миграции прода orphan'ов нет — все 8 резолвятся, §10.) Механизм — **два триггера, оба без планировщика:**
1. **Синхронный хук в user-сервисе (первичный):** при **создании/правке** CRM-пользователя (или изменении `users.telegram`) выполнить `UPDATE mail_telegram_links SET user_id = :uid WHERE user_id IS NULL AND username = normalize_telegram(:telegram)`. Детерминированно, мгновенно, реюз существующего пути записи `users`.
2. **Reconcile-проход в `MailDispatcherService` (safety-net):** та же фоновая asyncio-задача (§6, уже крутится) раз в N итераций делает `UPDATE mail_telegram_links l SET user_id = u.id FROM users u WHERE l.user_id IS NULL AND u.telegram IS NOT NULL AND lower(u.telegram) = l.username` — подхватывает случаи, пропущенные хуком (импорт пользователей мимо сервиса, гонки). Дешёвый partial-index-скан по orphan'ам.

Смена username у **связанного** пользователя доставку не ломает (ключ — `chat_id`). Резолв orphan'а завязан на `username` — если пользователь сменит username до связывания, orphan свяжется по НОВОМУ username только если `users.telegram` тоже обновят; это осознанный редкий кейс (orphan'ы — известный конечный список из миграции).

**Основной бот (`@ba_mail_bot`) webhook + SSO + callback** (порт ADR-0022):
- **`POST /api/mail/telegram/webhook/{secret}`** (namespaced под `/api/mail/telegram`, чтобы не конфликтовать с SMS `/api/sms/telegram`; CSRF/JWT-exempt): `{secret}` сравнивается constant-time (`secrets.compare_digest`) с `MAIL_BOT_WEBHOOK_SECRET`; дополнительно, если присутствует, проверяется заголовок `X-Telegram-Bot-Api-Secret-Token`. Mismatch → `404` (анти-энумерация). `/start` → SSO-линковка (резолв по `telegram_user_id`, иначе username → `users.telegram` ci) → upsert `mail_telegram_links`; `callback_query` `mail:{id}` → отправить тело письма (порт «Посмотреть сообщение»).
- Основной бот — классический webhook-бот; линковка через `/start` (initData-HMAC тут не нужен — это не Mini App).

**Telegram Mini App `/tg/mail` — без экрана авторизации (требование владельца).** При открытии Mini App пользователь сразу попадает на `/mail`, вкладка «Сообщения» — **никакого экрана логина**. По образцу существующего SMS Mini App `/tg/sms` (ADR-031/037: публичный SPA-маршрут вне `AppLayout`/RBAC-guard, без redirect на `/login`):
- **`POST /api/mail/telegram/auth`** (Mini App SSO, CSRF/JWT-exempt): валидирует Telegram **`initData`** (HMAC-подпись бота — **граница безопасности**, §см. `05-security.md` follow-up; НЕ доверять факту открытия), TTL по `auth_date` (`MAIL_TG_INITDATA_TTL_SEC`, default 300). Валидная подпись → резолв пользователя по `telegram_user_id` (линк), иначе по `username → users.telegram` (case-insensitive, `normalize_telegram`) → auto-upsert `mail_telegram_links` + выдача **CRM access-JWT** (`issue_access_token`, как SMS). Пустой/битый initData → `400`; неверная подпись → `401 invalid_init_data`; протухший → `401 init_data_expired`.
- **Frontend `/tg/mail`** (публичный маршрут, self-hosted Telegram WebApp SDK, CSP `script-src 'self'` не ослабляется — как `/tg/sms`): на входе шлёт `initData` в `/api/mail/telegram/auth`, при `200` — сохраняет JWT и рендерит ленту `GET /api/mail/messages` под обычным `MailScope` (письма команд пользователя). Наследует тему Telegram.

> **UI-структура экрана уточнена [Поправкой 2026-07-10](#поправка-2026-07-10--ux-telegram-mini-app-почты-tgmail-без-заголовка-без-лейбла-сообщения-full-text-по-клику):** экран рендерится **без `h1`-заголовка** «Почта — уведомления» и **без таб-лейбла «Сообщения»** (лента показывается напрямую — это единственный вид, переключать не на что); клик по письму открывает **read-only full-text detail-экран**. Формулировки «вкладка "Сообщения" по умолчанию» выше относятся к тому, что **лента писем** — стартовый вид после SSO (а не экран логина); отдельного таб-лейбла в UI нет. Нормативные строки/структура — [08-design-system.md «Telegram Mini App почты»](../08-design-system.md#telegram-mini-app-почты-tgmail-нормативно).
- **Username не найден в CRM** (нет `users.telegram` = username): показать **понятное сообщение**, НЕ пустую ленту и НЕ экран логина. Нормативный текст — [08-design-system.md](08-design-system.md) (follow-up §12): например «Ваш Telegram не привязан к пользователю CRM. Обратитесь к администратору.» Backend отвечает `403 mail_operator_not_provisioned` (симметрично SMS `sms_operator_not_provisioned`, ADR-031).
- `TELEGRAM_WEBAPP_URL` ботов сейчас указывает на агрегатор — переключается на CRM `.../tg/mail` (env `MAIL_BOT_WEBAPP_URL`) в рамках cut-over вебхуков (§9).

### §7. `MailScope` и RBAC (по `team_id`, `CATALOG["mail"]` без изменений)

`CATALOG["mail"] = ("view","create","edit","delete","sync","tags")` — **без изменений** (ADR-038 §4). Маппинг действий — как в ADR-038.

`MailScope` **упрощается** (групп нет): `MailScope(sees_all_teams: bool, team_ids: frozenset[UUID])` — поле `group_ids` **удаляется** (интеграция шла через группы; теперь ящик знает `team_id` напрямую). `get_mail_scope`: `sees_all_teams = principal_sees_all_mail_teams` (тот же admin-предикат, ADR-038 §3, без изменений); `team_ids` = команды пользователя из `user_teams`.

Enforcement (граница безопасности — backend):
- **Чтение ленты/ящиков** (`GET /api/mail/messages`, `/mailboxes`): не-admin — только письма/ящики, чей `mail_account.team_id ∈ MailScope.team_ids` (JOIN `mail_accounts` в БД CRM — теперь **локальный**, кэша каталога больше не нужно, всё в CRM). Вне scope → пусто (анти-энумерация). `sees_all_teams` → все.
- **Создание ящика**: `team_id ∈ team_ids` (не-admin), иначе `403`.
- **Перенос ящика** (смена `team_id`): только admin-уровень (§4).
- **Мутация/синк/удаление ящика по `id`**: ящик `team_id ∈ team_ids` (не-admin), иначе `403`.
- **Теги**: глобальны, scope команд не применяется; чтение под `mail:view`, управление под `mail:tags`.

`GET /api/auth/me += sees_all_mail_teams` — **уже есть** (ADR-038 §3), поведение не меняется.

### §8. Reply / Forward: отправка через агрегатор (SMTP-креды там)

CRM — инициатор, но SMTP-креды в агрегаторе. Агрегатор добавляет **обобщённый send-эндпоинт** `POST /api/external/mailboxes/{id}/send { to, cc, subject, body_text, in_reply_to?, refs? } → { sent_id, smtp_message_id }` (mail-агрегатор `ADR-0043`); он заменяет message-scoped reply ADR-0035 (письма живут в CRM, threading-заголовки формирует CRM).

- **`POST /api/mail/messages/{id}/reply`** (`mail:view`): CRM берёт письмо из `mail_messages`, резолвит `mail_account_id` + threading (`In-Reply-To`=`message_id_header`, `References`=`refs_header`+id), вызывает агрегатор send, пишет `mail_sent_messages`.
  - **`MailReplyRequest`** (инлайн + нормы из снятого `ADR-0035`, self-contained; MAJOR-5): `{ to?: string[], cc?: string[]|null, subject?: string, body: string }`. Нормы (переносятся из ADR-0035, не теряются): `to` default `[оригинал.from_addr]`, каждый адрес — валидный e-mail (regex), **≤100 адресов** суммарно `to+cc`; `subject` default `"Re: "+оригинал.subject`, **≤998** символов; `body` **обязателен, непустой, ≤1 MiB**. Нарушение → `422 unprocessable`.
  - **`MailReplyResponse`** `{ sent_id: int, smtp_message_id: string }`. Коды: `200`, `400 validation_error`, `422 unprocessable` (нормы выше), `404 mail_message_not_found`, `409 mail_conflict` (проброс SMTP-конфликта), `502 mail_unavailable`, `503`.
  - **Rate-limit (осознанное изменение, НЕ молчаливая релаксация; MAJOR-5):** per-IP `EXTERNAL_REPLY_RATE_LIMIT=30/min` из ADR-0035 **больше не применяется** — reply в новой модели это **CRM JWT/RBAC-эндпоинт** (`mail:view`), а не анонимный external-эндпоинт под статическим ключом. Abuse-поверхность закрыта аутентификацией пользователя; апстрим-вызов CRM→агрегатор (`POST /api/external/mailboxes/{id}/send`) идёт под общим `LIMIT_EXTERNAL_WRITE` (60/min) агрегатора как машинный. Это осознанный сдвиг границы (аноним-IP → JWT-юзер), задокументирован, а не побочная релаксация.
- **Forwarding — ОТЛОЖЕНО (решение владельца, MAJOR/owner-2).** Пересылка лидеру НЕ реализуется сейчас; правило прода `Команда Ивана → springtechco99@gmail.com` **не мигрируется**. Таблицы `mail_forwarding`/`mail_message_forwards` **не создаются** миграциями `0021`/`0022` (§2) — заводятся вместе с реализацией. **Явно зафиксировано:** с момента cut-over и до реализации forwarding **пересылка входящих на почту лидера НЕ работает** — [TD-040](../100-known-tech-debt.md). Спринт S5 удалён из плана.

### §9. Пять Telegram-ботов: переключение вебхуков на CRM

Токены ботов **не меняются** (те же боты); меняются URL вебхука и принимающий сервис. Конфиг CRM (env, класс секретов):
- Основной `@ba_mail_bot`: `MAIL_BOT_TOKEN`, `MAIL_BOT_WEBHOOK_SECRET`, **`MAIL_BOT_WEBAPP_URL`** (→ CRM `.../tg/mail`, для Mini App-кнопки). Webhook: `POST /api/mail/telegram/webhook/{secret}` (§6); Mini App SSO: `POST /api/mail/telegram/auth`. `mail_bot_enabled ⇔ bool(MAIL_BOT_TOKEN)`.
- 4 push-бота (`ivan`/`alexandra`/`andrei`/`business2`): на каждого `MAIL_BOT_<NAME>_TOKEN`, `MAIL_BOT_<NAME>_WEBHOOK_SECRET`, **`MAIL_BOT_<NAME>_TEAM_ID`** (UUID CRM-команды — заменяет прежний `_GROUP_ID`). Плюс `MAIL_ADMIN_TELEGRAM_IDS` (CSV). Webhook: `POST /api/mail/telegram/push-webhook/{bot_name}`; secret — per-bot `MAIL_BOT_<NAME>_WEBHOOK_SECRET`, **header-only fail-closed** (missing/mismatch → 404), как в агрегаторе. `bot_name ∈ {ivan,alexandra,andrei,business2}`. Маппинг бот→команда — по `_TEAM_ID` (UUID), не по group. Дубликат `_TEAM_ID` → fail-fast на старте.
- **`setWebhook`** — вручную/скриптом (по образцу агрегатора, `07-deployment.md`): на каждый бот `setWebhook` на CRM-URL с CRM-секретом. Идемпотентно, повтор при деплое.

**Порядок cut-over вебхуков (без потери сообщений):**
1. Задеплоить CRM `/api/mail/telegram/*` (идемпотентны, могут принимать до переключения).
2. Пер-бот `setWebhook` на CRM-URL (Telegram доставляет апдейты только на один URL — как только переключили, идёт на CRM). Опция `drop_pending_updates=false` — не терять очередь.
3. **Переключить `TELEGRAM_WEBAPP_URL` ботов** (сейчас указывает на агрегатор) на CRM `.../tg/mail` (Mini App-кнопка `/start` открывает CRM-ленту без экрана логина, §6). Это часть cut-over вебхуков.
4. После переключения агрегатор перестаёт получать апдейты этих ботов (его telegram-роутер демонтируется, `ADR-0043`).
- **Rollback:** `setWebhook` обратно на агрегаторский URL (эндпоинты агрегатора живут до decommission). Токены/секреты можно переиспользовать; при желании — сгенерировать новые секреты и обновить обе стороны.

### §10. Миграция данных (агрегатор → CRM)

> **Статус исполнения (прод, 2026-07-10): cut-over ВЫПОЛНЕН.** Миграция данных и переключение push/вебхуков выполнены: **2874 письма** мигрированы, **5 Telegram-ботов** переключены на CRM-вебхуки, push-приёмник `/api/mail/ingest` + `MailDispatcherService` активны, доставка подтверждена end-to-end (**7 реальных уведомлений**). Боевой блокер cut-over — nginx `413 Request Entity Too Large` на батч-приёме почты — устранён `client_max_body_size 50m` на `location /api` (коммит `de9f64e`; нормативное требование — [07-deployment.md §reverse-proxy](../07-deployment.md#reverse-proxy-nginx--требования)). Остаточные пункты: byte-aware чанкинг батча — [TD-045](../100-known-tech-debt.md); косметический дрейф `pushed_at` 24 писем — [TD-046](../100-known-tech-debt.md) (данные не потеряны, все 24 в CRM). Порядок ниже — исходный план cut-over (исполнен).

Объёмы (снапшот прода, **растут — синк идёт; это НИЖНЯЯ граница, НЕ точное равенство**): **≥2871 писем**, 8 links, **16 глобальных тегов** (10 builtin + 6 кастомных — разметка воспроизводится пере-применением, не переносом привязок; см. ниже), **≥12982 notifications** (все доставлены), 121 ящик, 0 сирот (0 писем без ящика, 0 уведомлений без письма — FK-миграция безопасна). **Сверочные константы ETL (`EXPECTED_MESSAGES`/`EXPECTED_NOTIFICATIONS`) — нижняя граница (`>=`), а НЕ `==`:** к cut-over чисел будет больше (письма продолжают приходить), точное равенство уронило бы миграцию в день cut-over. **Переносим:** письма, ящики, теги (пере-применение), уведомления, links, `sent_messages`. **НЕ переносим:** вложения (646, [TD-034]), `message_tags` (воспроизводятся), `admin_audit` (агрегаторский TD-050), forwarding-правило (отложено, [TD-040]) — детали ниже.

**Маппинги:**
- **Ящик → команда (немаппящихся НЕТ — проверено на проде):** источник — существующий `teams.mail_group_id` (`group_id → CRM team_id`). На проде **все 121 ящик** привязаны к группам `1/2/8`, всем есть соответствие в CRM (по `mail_group_id`, связь по id — имена намеренно расходятся): **`1` (агрегатор «Команда ivan») → CRM «Команда Ивана», 49 ящиков**; **`2` (агрегатор «Команда alexandra») → CRM «Команда Мухамеда» (`mail_group_id=2`), 69 ящиков**; **`8` (Business 2) → CRM «Business 2», 3 ящика** (49+69+3 = 121). `Команда Андрея` — `mail_group_id=NULL`, но **ящиков у неё 0** → маппить нечего. **Fail-fast (нормативно):** если скрипт встретит ящик, чей `group_id` не соответствует ни одной `teams.mail_group_id` — **STOP с ошибкой**, НЕ `team_id=NULL` молча. (Открытых blocking-вопросов по маппингу нет — секции таковых в ADR тоже нет.) `id` ящика **preserve** (= агрегаторский id).
  - **`down_alert_sent_at` на миграции — выставить `now()` ВСЕМ ящикам с `is_active=false` (все 7), а НЕ только 2 с перенесённым `disabled_alert_sent_at` (MINOR-1).** Факт прода: 7 ящиков `is_active=false`, но `disabled_alert_sent_at` проставлен лишь у 2 → перенос штампа «как есть» оставил бы у 5 оставшихся `down_alert_sent_at IS NULL`, и проход C §6 (`WHERE is_active=false AND down_alert_sent_at IS NULL`) на первом запуске разослал бы участникам их команд до 5 алертов о падениях **недельной давности**. **Cut-over — это импорт уже известного состояния, а НЕ транзиция** (алерт означает «ящик только что упал», а он упал давно). Обоснование глушения: (а) администратор и так видит эти 7 ящиков красными на вкладке «Почты» — информация не теряется; (б) алерт о давнем падении бесполезен и подрывает доверие к каналу; (в) первое **новое** падение после cut-over отработает штатно (проход C сработает при следующем переходе, т.к. status-канал §3 сбросит `down_alert_sent_at=NULL` на re-enable, а новое падение — на переходе). Guarded-условие «ровно один на переход» этим не нарушается: миграция трактует весь импортируемый down-набор как «уже оповещённый» baseline.
- **Письма:** copy 1:1, **preserve `id`** + `internal_date` (лента сортируется по `internal_date`, §2); после импорта — `setval mail_messages_id_seq = max(id)+1`. `mail_account_id` — тот же int. **`notified_at = now()` ВСЕМ мигрированным письмам (CRITICAL-1):** иначе диспетчер (проход A, `WHERE notified_at IS NULL`) возьмёт **всю историю (≥2871 писем)** в рассылку → живые люди получат поток уведомлений за месяцы. Дедуп-таблица гасит только ранее уведомлённые **пары**, а получатели резолвятся заново из `user_teams` (Никита/Анна/Юля, добавленные позже, получили бы всё). Выставление `notified_at` — обязательный шаг миграции.
- **Теги — 16 глобальных + пере-применение разметки (НЕ перенос `message_tags`; уточнённое решение после проверки прода).** Ревизия ответа «персональные не переносим»: на проде **привязок глобальных тегов к письмам — всего 9, персональных — 2284** (практически вся разметка архива висит на персональных тегах, в т.ч. `Поддержка` — 884 письма, треть архива; это рабочая классификация, а не «личный тег»).
  - **Каталог = 16 глобальных тегов:** **10 builtin** (10 из 16 уникальных имён персональных совпадают со встроенными) + **6 кастомных, повышенных до глобальных** (глобального аналога нет): `Поддержка` (5 правил, 884 письма), `Билд в коннекте` (2, 81), `Small Business` (3, 9), `Билд не дошёл` (2, 9), `Ждет Ревью 2` (2, 7), `Билд не приняли` (2, 3). Переносим их **правила** (10 builtin → 20 правил + 6 кастомных → 16 правил). 6 кастомных — `is_builtin=false` (org-специфичные, админ вправе удалить), builtin — `is_builtin=true`.
  - **Личные копии builtin НЕ переносим** (по ~6 копий на каждое из 10 builtin-имён — дубликаты; их разметка воспроизведётся).
  - **Идемпотентность вставки тегов/правил (нормативно — тот же класс, что `mail_sent_messages`).** `mail_tags` имеет натуральный ключ `UNIQUE (name)` → вставка `ON CONFLICT (name) DO NOTHING` (re-run не дублирует тег). `mail_tag_rules` натурального уникального ключа **не имеет** (`id UUID` случаен) → повторный прогон ETL мог бы задублировать правила. Норматив: ETL вставляет правила **только для впервые созданных тегов** (INSERT tag `... RETURNING id` по факту вставки), ЛИБО делает upsert-guard по `(tag_id, type, pattern)` (напр. предварительный `DELETE FROM mail_tag_rules WHERE tag_id = :id` перед вставкой набора правил тега). `apply-to-existing` (ниже) идемпотентен by design (`ON CONFLICT (message_id, tag_id) DO NOTHING`). Так весь тег-шаг миграции re-run-безопасен.
  - **`message_tags` НЕ переносим вовсе.** Вместо этого — **`apply-to-existing` по всем 16 глобальным тегам на всём корпусе** (весь корпус, ≥2871 писем) в миграционном скрипте. **Обоснование пере-применения vs перенос привязок:** (а) `message_tags` персональных тегов ссылаются на `tag_id`, которых в CRM не будет (теги пересозданы с новыми UUID); (б) пере-применение гарантирует консистентность разметки с **действующими** правилами (перенесённые привязки могли разойтись, если правило меняли после применения); (в) движок матчинга портирован **побуквенно** (§5) → результат идентичен агрегатору. 9 глобальных привязок тоже воспроизводятся (те же правила) — переносить их отдельно не нужно.
  - **Нагрузка/лимит:** `apply-to-existing` = bulk `INSERT ... SELECT` (§5, `APPLY_TAG_TO_EXISTING` без visibility-веток) по всему корпусу (≥2871) × 16 тегов. В агрегаторе лимит `APPLY_TO_EXISTING_LIMIT = 100_000` — корпус на порядки ниже, укладывается с запасом. **Выполняется в миграционном скрипте** (offline, прямой SQL по корпусу), **НЕ в HTTP-запросе** `POST /api/mail/tags/{id}/apply-to-existing` (тот — для интерактивного применения одного тега админом; корпусный прогон 16 тегов — часть ETL).
- **`admin_audit` (248 записей, MAJOR-3) — НЕ мигрируется в CRM by design.** У CRM нет БД-таблицы аудита (аудит CRM — **лог-based**, structlog; DB-аналога `admin_audit` нет). Журнал остаётся в БД агрегатора, доступен до decommission; при decommission — **дамп в бэкап** для ретенции. Заведено как агрегаторский `TD-050` (`ADR-0043`). Молчаливой потери нет — фиксируем экспорт.
- **`sent_messages` → `mail_sent_messages`: ПЕРЕНОСИМ (исполнимый маппинг).** Целевая таблица `mail_sent_messages` **существует** (миграция `0022`) → перенос не противоречит схеме. На проде — 1 строка, но к cut-over **число растёт** (люди отвечают). История reply — часть архива («переносим историю» — владелец). **⚠️ Правка кода для backend:** S6-ETL-скрипт **дополнить шагом переноса `sent_messages`** (сейчас не покрывает — источник противоречия). Это не forwarding (reply-sent ≠ пересылка), S3-таблица `mail_sent_messages`. Маппинг колонок (нормативно, имена проверены по коду):
  - **`from_account_id` (агрегатор, `shared/models/sent_message.py:33`) → `mail_account_id` (CRM)** — значение int сохраняется (тот же id ящика). НЕ `mail_account_id` в источнике — там колонка называется `from_account_id`.
  - **`id` — детерминированный UUID из исходного id, НЕ `gen_random_uuid()` (нормативно, разрешает противоречие «новый UUID» ↔ идемпотентность ETL).** Исходный `BIGSERIAL`-int **не сохраняется** (id остаётся «новым UUID» по форме — не тот же int, что у писем/ящиков, где id preserve). Но `mail_sent_messages` **не имеет натурального уникального ключа** (нет аналога `UNIQUE(mail_account_id,uidvalidity,uid)` писем) → случайный `gen_random_uuid()` при **повторном** прогоне ETL создал бы новую строку с новым id = **дубль**, ломая обязательную идемпотентность миграции. Поэтому механизм миграции — **детерминированная генерация из исходного id + `ON CONFLICT (id) DO NOTHING`**:
    - **namespace-константа (побуквенно, чтобы повторный прогон через месяц дал те же id):** `NS = uuid.uuid5(uuid.NAMESPACE_URL, "adr-044:mail_sent_messages")`. Константа **фиксирована**, НЕ производна от `uuid4`/времени/окружения.
    - `id = uuid.uuid5(NS, str(src_id))` (где `src_id` — агрегаторский `sent_messages.id`); вставка `INSERT ... ON CONFLICT (id) DO NOTHING`. Повторный прогон даёт **те же** id → без дублей.
    - **Разграничение (важно, иначе снова «упростят»):** `DEFAULT gen_random_uuid()` в DDL `mail_sent_messages.id` (`0022:30-35`) остаётся — он для **обычных вставок из приложения** (reply, у которого нет исходного id). Это **не** механизм миграции: ETL задаёт `id` явно детерминированным `uuid5`, дефолт таблицы в ETL не используется. «Новый UUID» ≠ «случайный UUID»: форма новая, значение воспроизводимо.
    - Внешних ссылок на этот id нет (`sent_attachments`=0, не мигрируются) → детерминированный id ничего не ломает, только обеспечивает re-run-безопасность.
  - **`user_id` (автор) — резолв через нормативный мост, НЕ подмена участником команды.** Прямой связки по username нет (CRM `Екатерина` кириллицей ≠ агрегатор `ekaterina` латиницей). Рабочий мост (проверен на проде): агрегаторский `sent_messages.user_id` → агрегаторский `telegram_links.telegram_user_id` (`chat_id`) → нормативная таблица §10 `NORMATIVE_TG_USERNAMES[chat_id]` (Bot API username) → CRM `users.telegram` (case-insensitive, без ведущего `@`) → CRM `users.id`. Пример прод-строки: `user_id=3` → `chat_id=1028365903` → `Katetown` → CRM `Екатерина`. **Правило неоднозначности (нормативно):** у автора должна быть **ровно одна** агрегаторская Telegram-привязка → резолв; **ноль или >1** привязок → `user_id = NULL`. (Агрегаторский `admin`/`users.id=1` имеет две привязки — `1604863121` Елисей и `453350292` Никита — мост неоднозначен → NULL.) Безопасно: `mail_sent_messages.user_id` nullable, FK `ON DELETE SET NULL` (`0022:37,58-63`).
  - **1:1 без преобразования:** `to_addrs`, `cc_addrs`, `subject`, `body_text`, `in_reply_to`, `refs_header`, `smtp_message_id`, `sent_at`.
  - **Осознанно ОТБРАСЫВАЕМЫЕ колонки источника** (в `mail_sent_messages` их нет — решение, а не случайная потеря; на проде все безвредны): `bcc_addrs` (reply-модель CRM без BCC; прод `NULL`), `appended_to_sent` / `appended_error` (внутреннее состояние IMAP-аппенда агрегатора — не переносимая семантика; прод `true`/`NULL`). Реальной потери данных на проде нет.
- **`users_settings`: 0 строк** (MAJOR-4) — opt-out переносить нечего; механизм `PATCH /api/mail/me/settings` в scope (§2), таблица `mail_user_settings` создана `0021`.
- **История уведомлений (≥12982, ВСЕ доставлены → все `status='sent'`; критично для прохода B):** `telegram_notifications` → `mail_telegram_notifications` **по ключу `(message_id, telegram_user_id)`** — **маппинг пользователей НЕ требуется** (`telegram_user_id` — глобальный Telegram-id; `sent_at` из истории). Факт прода: **все записи доставлены** (`telegram_message_id IS NOT NULL`) → мигрируются со **`status='sent'`**. **Явно (иначе повторная рассылка всей истории):** проход B диспетчера (§6) выбирает `WHERE status IN ('pending','failed')` → записи со `status='sent'` он **не подберёт** → повторной рассылки истории НЕТ. Обязательно выставить `status='sent'` всем — если оставить дефолт `'pending'`, проход B разошлёт всю историю заново.
- **`telegram_links` (8) → `mail_telegram_links`: мигрируем ВСЕ 8; на актуальных данных прода резолвятся 8 из 8** (владелец завёл недостающих пользователей CRM и поправил username Александры). Для каждого линка: `telegram_user_id` (= chat_id) как есть; `username` — нормализованный lower-case (`normalize_telegram`); `user_id` = резолв `SELECT id FROM users WHERE lower(telegram) = username` (case-insensitive, ведущий `@` снят).
  - **Нормативная таблица соответствия (основа миграционного скрипта):**

    | `chat_id` | Telegram-username (Bot API) | `username` (норм.) | → пользователь CRM |
    |---|---|---|---|
    | 1604863121 | `not_ryan_reynolds` | `not_ryan_reynolds` | Елисей |
    | 1028365903 | `Katetown` | `katetown` | Екатерина |
    | 164692303 | `novikov_iwan` | `novikov_iwan` | Иван |
    | 399743086 | `m_niyazov` | `m_niyazov` | Мухамед |
    | 453350292 | `michtl` | `michtl` | Никита |
    | 63356836 | `Loveink` | `loveink` | Алекандра |
    | 1039984194 | `Anellie_sss` | `anellie_sss` | Анна |
    | 290151018 | `yuliya_2704` | `yuliya_2704` | Юля |

  - **Регистр — реальная проблема:** `Katetown`/`Anellie_sss`/`Loveink` в Telegram — с заглавными, в CRM `users.telegram` — строчными; сопоставление **обязано** быть регистронезависимым (нормативно, §6).
  - **Fail-fast скрипта (нормативно):** если при миграции **хоть одна** привязка НЕ резолвится в CRM-пользователя — скрипт **останавливается с ошибкой**, а не молча пропускает/оставляет orphan. (Orphan-состояние `user_id=NULL` — легальный **runtime**-механизм для будущих self-bind/несопоставленных, §6, но НЕ для миграции: на текущих данных все 8 обязаны резолвиться.)
  - **Авто-маппинг по ИМЕНАМ пользователей запрещён** (кириллица CRM ≠ латиница агрегатора, `alexandra`↔«Команда Мухамеда» обманывает) — связывание строго по Telegram-username ↔ `users.telegram`.
  - SSO-`/start`/Mini App после cut-over также линкуют (idempotent upsert по `telegram_user_id`) — дополняют миграцию, не конфликтуют.
- **Forwarding — НЕ мигрируется** (forwarding отложен, таблиц `mail_forwarding`/`mail_message_forwards` нет — §2). Правило прода (агрегаторская группа «Команда ivan» = CRM **«Команда Ивана»** → `springtechco99@gmail.com`) **не переносится**; его параметры зафиксированы в [TD-040](../100-known-tech-debt.md) для повторного ввода при реализации. Устраняет противоречие «миграция в несуществующую таблицу».

**Порядок cut-over (без потери писем и БЕЗ массовой/двойной рассылки — CRITICAL-1):**
1. Задеплоить CRM-схему (миграции 0021+) и `/api/mail/ingest`+`/api/mail/mailbox-status` (принимают, идемпотентны). **CRM `MailDispatcherService` пока НЕ запускать** (или запустить с флагом `MAIL_DISPATCH_ENABLED=false`) — чтобы он не слал во время миграции.
2. Bulk-copy: `mail_accounts` (с `team_id` + `down_alert_sent_at=now()` всем down — MINOR-1) → `mail_messages` (preserve id/internal_date, **`notified_at = now()` всем** — CRITICAL-1) → `mail_tags`+`mail_tag_rules` (**16 глобальных**: 10 builtin + 6 кастомных, с правилами) → **`apply-to-existing` по 16 тегам на всём корпусе** (воспроизводит разметку; `message_tags` НЕ копируем) → `mail_telegram_links` (8, резолв) → `mail_telegram_notifications` (историч., **все `status='sent'`** — иначе проход B перешлёт всю историю) → `mail_sent_messages`.
3. **Заглушить Telegram-нотификации АГРЕГАТОРА** (единый флаг: `tg_notify` + push-боты + mailbox-alert выключаются) — с этого момента агрегатор **не уведомляет** (продолжает только синк + push писем/статуса в CRM). Устраняет **окно двойной доставки** (CRITICAL-1b): по каждому новому письму уведомляет РОВНО ОДИН нотификатор.
4. **Включить push** в агрегаторе (`crm_push_dispatch` + status-push); новые письма/статусы идут в CRM (идемпотентно — дубли с bulk-copy отсекаются UNIQUE). Новые письма приходят с `notified_at IS NULL` (в отличие от мигрированных).
5. Переключить вебхуки + `TELEGRAM_WEBAPP_URL` 5 ботов на CRM (§9). Даже если апдейт кратко попадёт на старый агрегатор — тот уже не уведомляет (шаг 3), обработает только SSO (безвредно).
6. **Запустить CRM `MailDispatcherService`** (`MAIL_DISPATCH_ENABLED=true`) — он **единственный** нотификатор; берёт только новые письма (`notified_at IS NULL`), историю (шаг 2) НЕ трогает.
7. Финальный delta-reconcile: письма, синканные во время copy, приходят push'ем (идемпотентно). Сверка по счётчикам — **по нижней границе** (`CRM >= EXPECTED_*`, снапшот-константы растут; НЕ `==`) + pull `GET /api/external/mailboxes` для сверки статусов.
8. Наблюдать доставку end-to-end (реальное новое письмо → CRM → Telegram-уведомление `ok:true`). Только после подтверждённой доставки — decommission.
9. Decommission агрегатора: **дамп `admin_audit` в бэкап**; демонтаж tags/telegram/webhooks/forwarding/groups/UI/MinIO (`ADR-0043`), удаление 646 вложений.

**Инвариант единственного нотификатора (нормативно):** между шагами 3 и 6 может быть краткий интервал, когда НИ агрегатор (заглушён), НИ CRM (диспетчер ещё не стартовал) не уведомляют — это **приемлемо** (письма копятся в CRM с `notified_at IS NULL`, диспетчер разошлёт их по старту на шаге 6, без потери и без дублей). Что НЕДОПУСТИМО и исключено порядком — одновременная работа обоих нотификаторов.

**Откат:** до шага 5 агрегатор полностью функционален (таблицы целы, его нотификации можно вернуть, сняв флаг шага 3) — откат бесплатный (reverse setWebhook + drop CRM mail-таблиц). **Точки невозврата:** (i) переключение вебхуков (шаг 5 — апдейты идут в CRM); (ii) decommission агрегатора (шаг 9 — удаление таблиц/MinIO/646 вложений). Между (i) и (ii) — откат через reverse setWebhook + снятие заглушки нотификаций агрегатора; после (ii) — только restore из бэкапа агрегатора.

### §11. Судьба уже написанного незакоммиченного кода (ADR-043 / ADR-0042)

- **`MailScope.team_ids`** — **ПЕРЕИСПОЛЬЗУЕТСЯ** (стал основным механизмом scope; `group_ids` удаляется).
- **`team_id` в write-контракте ящика** (`POST/PATCH /mailboxes`) — **ПЕРЕИСПОЛЬЗУЕТСЯ** (ящик привязан к `team_id` напрямую).
- **Actionable-пустые-состояния** («команда без почты», CTA «Добавить первую почту») — **ПЕРЕИСПОЛЬЗУЮТСЯ** (текст «команда и почтовая группа создадутся автоматически» → правится: групп нет, «ящик привяжется к команде»).
- **Уборка селектора «Почтовая группа»** из создания команды — **ПЕРЕИСПОЛЬЗУЕТСЯ** (групп нет вовсе; селектор удаляется **и из edit** тоже — в ADR-043 он оставался в edit для ручной привязки к существующей группе; теперь групп нет → удаляется полностью).
- **CAS на `teams.mail_group_id` / `ensure_team_mail_group` / ленивый провижининг / external `POST/DELETE /teams` / TOCTOU-guard** — **ВЫБРАСЫВАЕТСЯ** (групп нет; `teams.mail_group_id` удаляется). Парный агрегаторский `ADR-0042` (external team create/delete) — **superseded** `ADR-0043` (эндпоинты `POST/DELETE /api/external/teams` удаляются).
- **CRM ADR-043 целиком** — `superseded by ADR-044`.

### §12. Обязательные follow-up-правки docs (после мержа параллельного агента) + отложенное (TD)

**Follow-up architect'а (ОБЯЗАТЕЛЬНО, после того как параллельный агент закоммитит свои хунки — чтобы не затереть):**
- `docs/adr/INDEX.md`: зарегистрировать ADR-044; проставить `superseded by ADR-044` в строках **ADR-038** и **ADR-043** (и forward-ссылку в шапках их статусов).
- `docs/04-api.md` §Mail: переписать — `POST /api/mail/ingest`; чтение `messages`/`tags`/`mailboxes` из БД CRM (не проксирование); `team_id` вместо `group_id` в `MailMailbox`/write-контракте; убрать `MailTeam`/`GET /api/mail/teams` (групп нет); `/api/mail/telegram/*`. Коды ошибок: убрать `mail_group_not_found`, `team_mail_group_taken`; добавить `mail_ingest_not_configured`.
- `docs/03-data-model.md`: добавить фактически созданные таблицы — `mail_accounts`/`mail_messages`/`mail_tags`/`mail_tag_rules`/`mail_message_tags`/`mail_telegram_links`/`mail_telegram_notifications`/`mail_user_settings` (миграция `0021`) + `mail_sent_messages` (`0022`); **удалить** `teams.mail_group_id`. `mail_forwarding`/`mail_message_forwards` — **НЕ добавлять** (не созданы; вносятся вместе с реализацией forwarding, [TD-040](../100-known-tech-debt.md)).
- `docs/05-security.md`: раздел «Push-контракт агрегатор→CRM (HMAC, `MAIL_PUSH_SECRET`, timestamp-anti-replay)»; **Mini App `/tg/mail` — обязательная проверка подписи `initData` (граница безопасности, не доверять факту открытия)** по образцу SMS Mini App; Telegram-боты почты (5 ботов, env); транзит кредов — сохранить.
- `docs/08-design-system.md`: обновить пустые состояния («команда без почты» — про привязку к команде, не про группу); **нормативный текст Mini App-not-found** («Ваш Telegram не привязан к пользователю CRM…» — НЕ пустая лента, НЕ экран логина); словарь тегов сохранить.
- `docs/00-vision.md`: user stories — почта хранится в CRM, уведомления через CRM, Mini App `/tg/mail` без логина.
- `docs/02-tech-stack.md`/`07-deployment.md`: новые env (`MAIL_PUSH_SECRET`, `MAIL_PUSH_MAX_SKEW_SEC`, `MAIL_INGEST_MAX_BATCH`, `MAIL_DISPATCH_INTERVAL_SEC`, `MAIL_DISPATCH_BATCH`, `MAIL_DISPATCH_ENABLED`, `MAIL_TG_MAX_ATTEMPTS`, `MAIL_TG_NOTIFY_ALL_MESSAGES`, `MAIL_TG_INITDATA_TTL_SEC`, `MAIL_BOT_TOKEN`/`_WEBHOOK_SECRET`/`_WEBAPP_URL`, `MAIL_BOT_<NAME>_TOKEN`/`_WEBHOOK_SECRET`/`_TEAM_ID`, `MAIL_ADMIN_TELEGRAM_IDS`); `setWebhook`+`TELEGRAM_WEBAPP_URL`-runbook для 5 ботов; **cut-over-runbook с заглушкой нотификаций агрегатора (§10)**.
- `docs/04-api.md`: добавить `POST /api/mail/mailbox-status`, `PATCH /api/mail/me/settings`, `POST /api/mail/messages/{id}/reply` (нормы), инлайн-схемы (перенесены в ADR); сортировка ленты по `internal_date` + **компаундный keyset-курсор `(internal_date, id)`** (§2, MINOR-2) — зафиксировать формат курсора и имя query-параметра.
- **Снять остаточные ссылки на superseded `ADR-038` для ДЕЙСТВУЮЩЕГО поведения (MINOR-3).** ADR-044 для стабильных уже-реализованных инвариантов ссылается на отменённый ADR-038: транзит IMAP/SMTP-кредов + `Cache-Control: no-store` (§4/§security), «маппинг действий — как в ADR-038» (§7), admin-предикат `get_mail_scope`/`principal_sees_all_mail_teams` (§3/§7), `sees_all_mail_teams` в `GET /api/auth/me` (§7). Per-endpoint права инлайнены (исполнителю ADR-038 читать не нужно), но при синхронизации `05-security.md`/`04-api.md` эти инварианты **перенести в актуальные docs и снять ссылки на ADR-038** — иначе читатель откроет ADR-038, увидит «superseded by ADR-044» и решит, что нормы отменены вместе с ним. Инварианты действуют — меняется только их адрес прописки.

**Tech-debt (заводится в `100-known-tech-debt.md` этим ADR — файл НЕ off-limits):**
- **TD-033** → пометить **закрыт** (ADR-044: per-mailbox `team_id`, групп нет).
- **TD-036 / TD-037** → пометить **устаревшими** (групповая модель ADR-043 упразднена; ленивый провижининг/сироты-группы больше не существуют).
- **TD-034** (вложения недоступны) → переклассифицировать: вложения **не переносятся by design**; при появлении требования — отдельный ADR.
- **TD-038 (new):** каталог тегов = **16 глобальных** (10 builtin + 6 кастомных, повышенных до глобальных — `Поддержка`/`Билд в коннекте`/`Small Business`/`Билд не дошёл`/`Ждет Ревью 2`/`Билд не приняли`, §10). Личные копии builtin (дубликаты) НЕ переносятся; `message_tags` НЕ переносятся — разметка воспроизводится **пере-применением** 16 тегов по корпусу (детерминированные правила, побуквенный движок §5). 6 кастомных — `is_builtin=false`, НЕ в `mail_builtin_tags.py` (org-специфичны). *(Ревизия исходного «персональные не переносим» после проверки прода: 2284 привязки висят на персональных тегах — не терять.)*
- **TD-039 (new):** пользователи CRM без членства в команде (`Никита`/`Анна`/`Юля` на момент миграции) — линк перенесён, но уведомления пойдут только после включения в команду (владелец «позже раскидает»). Не дефект; вести до включения.
- **TD-040 (new):** **forwarding ОТЛОЖЕН (решение владельца).** Пересылка входящих на почту лидера (правило `Команда Ивана → springtechco99@gmail.com`) **не реализуется на старте и НЕ мигрируется**. **Явно: с момента cut-over и до реализации пересылка НЕ работает.** Таблицы `mail_forwarding`/`mail_message_forwards` миграциями `0021`/`0022` **НЕ созданы** (заводятся вместе с реализацией). Параметры правила сохранены в реестре TD-040 для повторного ввода. Реализовать по образцу ADR-0034 (минус вложения) при возврате приоритета.
- **TD-041 (new):** push `/ingest` (и `/mailbox-status`) с неизвестным `mail_account_id` роняет письмо/no-op статус (`unknown_mailbox`) — редкая аномалия (каталог заводится через CRM до писем).
- **TD-042 (new):** ящик без `team_id` (unassigned) / команда без участников → уведомлений не получит никто (super-admin-фолбэк НЕ вводится без решения владельца). **Известное следствие переноса (НЕ дефект):** `Business 2` маппится штатно (`mail_group_id=8`, 3 ящика получают `team_id`), НО в CRM у неё **0 участников** → по её ящикам уведомления временно не идут никому (раньше — Юля `@yuliya_2704`, теперь вне команд), пока владелец не добавит участников. `Алтынай` (`ashaykenova`) в «Команде Мухамеда», но без Telegram-привязки — не получит до `/start` (самопривязка §6).
- **TD-043 (new):** push-бот команды + mailbox-down алерт (проход C) — fire-and-forget на доставку алерта (без трекинга/recovery, паритет агрегаторского TD-041/TD-042). Само письмо/статус durable в БД CRM.
- **TD-044 (new):** `MailDispatcherService` синхронно шлёт в цикле (нет параллелизма/лимита конкуренции) — при большой команде/медленном Telegram итерация растягивается (паритет SMS TD-032).

*(MAJOR-1 recovery-проход, MAJOR-2 status-канал/mailbox-down, MAJOR-4 opt-out-механизм, MAJOR-5 инлайн-схемы/reply-нормы, MAJOR-6 правило null-team, MAJOR-8 сортировка по `internal_date` — решены В СПЕЦИФИКАЦИИ выше, НЕ отложены в TD.)*

## Consequences

- CRM — единственная система-запись почты; агрегатор — тонкий connector (IMAP/SMTP/push). Дрейф «двух источников истины» устранён: команда↔ящик только в CRM, креды только в агрегаторе.
- **TD-033 закрыт**, групповая индирекция и весь аппарат ADR-043/ADR-0042 упразднён (проще).
- Письма/теги/уведомления хранятся в CRM (разворот «без хранения» ADR-012/038) — цена: новые таблицы + фоновый диспетчер + push-приёмник. Оправдано требованием владельца (полный контроль в CRM).
- Telegram-доставка durable без Redis (asyncio-диспетчер + дедуп-таблица) — по образцу существующих сервисов CRM.
- Вложения выпадают полностью (в т.ч. MinIO у агрегатора убирается — минус зависимость).
- **Маппинг пользователей агрегатор↔CRM** — на актуальных данных **однозначен и полон** (8/8 линков резолвятся по Telegram-username↔`users.telegram`, ci; §10). История уведомлений маппинга не требует (by tg id). Blocking-вопрос снят; остаётся известное следствие: `Business 2` без участников, `Никита`/`Анна`/`Юля` вне команд, `Алтынай` без линка (§6/TD-042).
- **Cut-over безопасен для 8 живых пользователей (CRITICAL-1):** миграция ставит `notified_at=now()` всей истории (нет рассылки за месяцы), а порядок «заглушить агрегатор → включить push → старт CRM-диспетчера» исключает двойную доставку. Инвариант единственного нотификатора зафиксирован (§10).
- **Надёжность доставки не деградирует (MAJOR-1):** recovery-проход B по `mail_telegram_notifications(status IN pending/failed)` восстанавливает `tg_notify_recovery` — транзиентный сбой Telegram не теряет уведомление.
- **mailbox-down алерты сохранены (MAJOR-2):** status-канал `/api/mail/mailbox-status` + проход C воспроизводят `ADR-0033` (идемпотентность через `down_alert_sent_at`, миграция `disabled_alert_sent_at`). Функция живая на проде (7 down, 2 alerted).
- **opt-out не регрессирует (MAJOR-4):** `PATCH /api/mail/me/settings` в scope (данных 0, механизм обязателен). **`admin_audit` (248)** — экспорт в бэкап при decommission (агрегаторский TD-050), не теряется молча.
- **Синхронизация нормативных docs CRM отложена** (параллельный агент) — ADR самодостаточен, follow-up обязателен (§12). Это осознанный компромисс против затирания чужих незакоммиченных хунков, а не молчаливый пропуск.

## Alternatives considered

- **Оставить прокси-модель ADR-038 (не хранить в CRM).** Отклонён владельцем: команды/теги/пользователи/Telegram должны жить в CRM; агрегатор — только «добавлять почты».
- **CRM поллит агрегатор за новыми письмами.** Отклонён владельцем прямо: «агрегатор сам присылает» (push). Push экономит пустые опросы и даёт near-real-time доставку.
- **Очередь ретраев push на стороне CRM.** Отклонён: CRM без Redis/брокера (NFR-1); у агрегатора Redis есть — очередь и recovery живут там (зеркало `tg_notify`).
- **Сохранить группы агрегатора как слой владения.** Отклонён: владелец — «почты закреплены за командами»; per-mailbox `team_id` проще и закрывает TD-033. `user_id` уже фиктивен.
- **Синхронная Telegram-рассылка в хендлере `/ingest`.** Отклонён: блокирует агрегаторскую очередь, риск таймаута/потери; фоновый диспетчер durable.
- **Статический bearer вместо HMAC на `/ingest`.** Отклонён: bearer реплеится; HMAC+timestamp тело-связан и симметричен SMS-webhook security-модели.
- **Перенести `message_tags` (привязки) напрямую вместо пере-применения.** Отклонён: (а) привязки персональных тегов ссылаются на `tag_id`, которых в CRM нет (теги пересозданы); (б) перенос закрепил бы возможно-устаревшую разметку (правило могли менять после применения); (в) пере-применение по побуквенному движку даёт разметку, консистентную с действующими правилами. Корпус (≥2871)×16 укладывается в `APPLY_TO_EXISTING_LIMIT=100_000` с запасом.
- **Не переносить персональные теги вовсе (исходный ответ владельца).** Отклонён после проверки прода: 2284 привязки (почти вся разметка, вкл. `Поддержка` — 884 письма) висят на персональных тегах; 6 из них не имеют глобального аналога и являются рабочей классификацией → повышаем до глобальных (16 итого). Личные дубликаты builtin — не переносим (разметка воспроизводится).
- **Мигрировать `telegram_links` по имени пользователя CRM.** Отклонён: имена CRM (кириллица) ≠ агрегатор (латиница), множества не эквивалентны — авто-маппинг запрещён; связывание по Telegram-username↔`users.telegram` (ci) детерминировано (8/8, §10).
- **Синхронная mailbox-down проверка поллингом `GET /external/mailboxes` в CRM как основной канал.** Отклонён в пользу событийного status-push (§3 вариант B) — поллинг оставлен только как reconcile-fallback.
- **Отложить mailbox-down/opt-out в TD.** Отклонён: обе функции живые/ожидаемые (7 down-ящиков; opt-out был `PATCH /api/me/settings`) — их пропажа = регресс, а не долг. Реализуются в scope.
- **Сортировать ленту по `id` (порядок прихода push).** Отклонён (MAJOR-8): recovery-ре-пуш поднял бы старое письмо в топ; сортировка по `internal_date` (истинная дата).

## Поправка 2026-07-10 — UX Telegram Mini App почты (`/tg/mail`): без заголовка, без лейбла «Сообщения», full-text по клику

Ревизия UI-части §6 «Telegram Mini App `/tg/mail`» по прямому решению владельца (дословно): «в MiniApp телеграм полностью убрать текст "Почта — уведомления", а также "Сообщения". При клике на сообщение необходимо чтобы полный текст сообщения отображался». Затрагивает **только UI Mini App почты** — контракт (`GET /api/mail/messages`, `POST /api/mail/telegram/auth`), backend, RBAC, SMS Mini App, десктопная страница `/mail` **не меняются**. Нормативные UI-строки и структура экрана — [08-design-system.md «Telegram Mini App почты»](../08-design-system.md#telegram-mini-app-почты-tgmail-нормативно).

1. **Заголовок экрана убран полностью.** Провизорный `h1` «Почта — уведомления» (frontend поставил его при первичной реализации §6 и эскалировал отсутствие нормативной строки как blocking-question) — **удаляется целиком**. Экран Mini App почты рендерится **без `h1`-заголовка**. В отличие от SMS Mini App (там заголовок «СМС — уведомления» сохраняется, [08-design-system.md](../08-design-system.md#нормативный-словарь-ui-строк-mini-app)), у почтового Mini App заголовка нет вовсе. Blocking-question по заголовку почтового Mini App этим **закрыт** (решение владельца: заголовка нет).

2. **Лейбл «Сообщения» в Mini App убран.** В Mini App почты «Сообщения» — это **единственный декоративный таб-пилюля** над лентой (не настоящая вкладка-переключатель — вид ровно один; §6 говорит о «вкладке "Сообщения" по умолчанию», но переключать не на что). Пилюля-лейбл **удаляется**: после SSO Mini App показывает ленту писем **напрямую**, без таб-лейбла/секционного заголовка. **Важно (без регрессии):** это касается ТОЛЬКО Mini App `/tg/mail`. Вкладка «Сообщения» десктопной страницы `/mail` (одна из трёх: «Сообщения»/«Почты»/«Теги», [08-design-system.md](../08-design-system.md#вкладки-страницы-почты-нормативно)) **остаётся** — там она настоящая вкладка-переключатель и осмысленна.

3. **Полный текст письма по клику — read-only detail-экран внутри Mini App.** До поправки карточка ленты Mini App была read-only и показывала только метаданные (отправитель, тема, дата, теги, «Получено на»), **без тела письма и без клика**. Теперь:
   - Клик по карточке письма → переход на **full-width detail-экран внутри того же webview** (локальный `useState` выбранного письма, **НЕ роутинг** — как адаптивный одноколоночный режим десктопного `/mail`). Detail занимает весь узкий экран (Telegram-webview ~390px), master-detail рядом не показывается.
   - Detail показывает **полный сохранённый текст письма**: шапка (отправитель `from_name`/`from_addr`, полная дата, тема, теги-чипы, «Получено на: {display_name} <{email}>») + тело. Тело: `body_html` → **sandbox-iframe** (`srcDoc`+`sandbox`, без `allow-scripts`/`allow-same-origin`, `referrerPolicy="no-referrer"`, серый фон инъекцией — инварианты изоляции HTML [modules/mail](../modules/mail/README.md#изоляция-html-тела-нормативно)/[ADR-012](ADR-012-mail-read-through-proxy.md) **не ослабляются**); иначе `body_text` (моно, `white-space: pre-wrap`). Кнопка **«Назад»** вверху → возврат к ленте.
   - **Read-only:** формы reply в Mini App **нет** (Mini App только читает — §6; owner просил показ текста, не ответ). Reply остаётся функцией десктопной `/mail` ([08-design-system.md](../08-design-system.md#inline-ответ-reply-chat-like)).
   - **Нового запроса/эндпоинта НЕТ.** Лента `GET /api/mail/messages` уже возвращает **полный** `MailMessage` (включая `body_text`/`body_html`/`body_present`/`body_truncated`) — detail рендерится из уже загруженного объекта письма.

**Источник «полного текста» ограничен тем, что хранится в БД CRM (нормативно).** «Полный текст» = **полный сохранённый** `body_text`/`body_html` письма, а НЕ оригинал у отправителя. Если при синхронизации/приёме тело было усечено агрегатором (`body_truncated=true`) — detail показывает пометку **«Письмо показано не полностью»** и **не пытается** дозагрузить больше (такого API/данных нет). `body_present=false` → **«Тело письма недоступно»**. Frontend не проектирует показ того, чего нет в БД.

**Follow-up для qa (не блокер, штатный хендофф):** существующий `frontend/src/pages/__tests__/MailMiniAppPage.test.tsx` ассертит `getByText('Сообщения')` (лейбл, который удаляется) — после правки этот ассерт устаревает; qa переписывает тест под новую структуру (лента без таб-лейбла + detail по клику).
