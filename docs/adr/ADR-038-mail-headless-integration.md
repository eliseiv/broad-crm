# ADR-038 — Headless-интеграция CRM ↔ mail-агрегатор: write-прокси, `teams.mail_group_id`, `MailScope`, RBAC, транзит кредов

Статус: `accepted` · Дата: 2026-07-09

Амендмент [ADR-012](ADR-012-mail-read-through-proxy.md) / [ADR-013](ADR-013-mail-newest-first-master-detail-inline-reply.md) / [ADR-017](ADR-017-dashboard-client-aggregation-mail-server-filters.md). Парный ADR в mail-агрегаторе — `ADR-0039` (external write API + расширение read-фильтров), `ADR-0040` (глобальные теги).

> **Дополнен [ADR-043](ADR-043-lazy-mail-group-provisioning.md) (2026-07-10):** §2 (ручной backfill `mail_group_id` / селектор «Почтовая группа» при создании команды) заменён на **ленивый провижининг** группы по первой почте (селектор убран из создания, сохранён в edit); write-контракт ящика ссылается на команду по **`team_id`** (не `group_id`); §3 `MailScope` расширен `team_ids`. Пары в проде: mail-агрегатор `ADR-0042` (external team create/delete).

## Context

Сегодня страница `/mail` — **read-through-прокси без хранения** (ADR-012/013/017): `api/mail.py` → `services/mail_service.py` → `infra/mail_client.py` ходит на `https://postapp.store` (`MAIL_API_BASE`) с `X-API-Key` в `GET /api/external/{messages,teams,mailboxes}` и `POST /api/external/messages/{id}/reply`. Внешний API — read-only (плюс узкий `reply`).

Требуется превратить агрегатор в **headless mail-connector**: движок IMAP/SMTP/тегов/доставки без собственного UI, а CRM сделать **единственным UI и источником истины по командам и правам**. Три блокера:

1. Фильтр «Команда» на `/mail` работает по группам агрегатора (`groups.id`, int), а не по CRM-командам (`teams.id`, UUID); связи нет нигде → ролевая видимость писем и «почты команды» на `/teams` невозможны.
2. External API — read-only; управление почтами и тегами (write) отсутствует.
3. Теги агрегатора приватны на пользователя; нужен единый админский каталог (решается на стороне агрегатора, `ADR-0040`).

Выбранный вариант интеграции — №1 (агрегатор остаётся отдельным сервисом-коннектором). Обоснование отказа от полного переноса: агрегатор ~29 тыс. строк, 22 ORM-модели, 23 миграции, worker на APScheduler + Redis + MinIO, которые CRM намеренно исключает ([ADR-001](ADR-001-stack-i-monolit.md)/[ADR-006](ADR-006-async-provisioning-bez-brokera.md)). Пользователь подтвердил использование Telegram-уведомлений, webhooks, forwarding; собственный UI агрегатора не используется; вложения в CRM не требуются.

## Decision

### §1. Headless-прокси с write-операциями (амендмент ADR-012)

Модель ADR-012 расширяется с read-only до **read+write прокси без хранения**. CRM остаётся без БД-хранилища писем/ящиков/тегов: `api/mail.py` → `services/mail_service.py` → `infra/mail_client.py` проксирует в новый external write API агрегатора (`ADR-0039`) под тем же системным ключом `MAIL_API_KEY` (`X-API-Key`). Инварианты ADR-012 сохранены: ключ только на backend, JWT на всех CRM-эндпоинтах, sandbox-iframe тела письма, `MAIL_API_KEY` не в ответах/логах/SPA/URL.

Новые CRM-эндпоинты (полные схемы — [04-api.md#mail](../04-api.md#mail)):
- `GET/POST /api/mail/mailboxes`, `PATCH/DELETE /api/mail/mailboxes/{id}`, `POST /api/mail/mailboxes/test`, `POST /api/mail/mailboxes/{id}/sync`;
- `GET/POST /api/mail/tags`, `PATCH/DELETE /api/mail/tags/{id}`, `POST /api/mail/tags/{id}/rules`, `DELETE /api/mail/tags/{id}/rules/{rule_id}`;
- `GET /api/teams/{team_id}/mailboxes` (в `api/teams.py`, гейт `teams:view`) — по образцу `GET /api/teams/{team_id}/numbers`;
- `mail_group_id` в `POST /api/teams` / `PATCH /api/teams/{id}` и в `TeamListItem`.

**Идемпотентность ретраев (нормативно, `mail_client.py`).** GET-методы (`list_messages`/`list_teams`/`list_mailboxes`/`list_tags`) ретраятся на транзиентных `{429,500,502,503,504}` + `ConnectError`/`ConnectTimeout`/read-timeout с backoff `(0.2, 0.5)`. Мутирующие `POST`/`PATCH`/`DELETE` (`create/update/delete/sync` ящика, `reply`, CRUD тегов/правил, `apply-to-existing`) ретраятся **только** на `ConnectError`/`ConnectTimeout` (запрос заведомо не ушёл) — защита от двойной записи; read-timeout/`5xx` на write → сразу `502 mail_unavailable`. `POST /api/mail/mailboxes/test` — тоже мутирующая семантика по ретраям (открывает IMAP/SMTP-сессию), ретрай только connect. Исключение — `apply-to-existing`: идемпотентен на стороне агрегатора (`ON CONFLICT DO NOTHING`), но семантически дорог → политика write (retry только connect).

### §2. Связь «ящик ↔ команда»: `teams.mail_group_id`

CRM добавляет **одно поле** — `teams.mail_group_id INTEGER NULL UNIQUE` (миграция `0018`, [03-data-model.md](../03-data-model.md#teams)). Соответствие CRM-команда (UUID) ↔ группа агрегатора (`groups.id`, int), **1:1**.

- **Источник истины владения ящиком остаётся в агрегаторе** (`mail_accounts.group_id`). Оно уже сегодня драйвит видимость, forwarding и webhooks в агрегаторе; второй источник истины породил бы расхождение. Принадлежность ящика команде выводится: `mailbox.group_id == team.mail_group_id`.
- **Локальный кэш каталога ящиков в CRM НЕ заводится.** Резолв видимости — чисто локальный запрос (`user_teams` → `mail_group_id` → `list[int]`), передаётся в агрегатор повторяемым query-параметром `group_id` (см. `ADR-0039`). Это убирает целую таблицу, фоновый сервис синхронизации и проблему свежести кэша. Цена — одно изменение сигнатуры external API (повторяемый `group_id`).
- `mail_group_id` уникален (`UNIQUE`) — одна группа агрегатора привязана максимум к одной CRM-команде. `NULL` = команда без привязки к почте (валидно).
- **Backfill/сопоставление существующих команд.** Существующие команды создаются с `mail_group_id = NULL`. Сопоставление — **ручное** администратором через `PATCH /api/teams/{id}` (селектор группы из `GET /api/mail/teams`) — по образцу ручного сопоставления/бэкфилла лидеров (коммит `6fad489`). Автоматический backfill невозможен: имя CRM-команды и имя группы агрегатора независимы. Ограничение 1:1 зафиксировано как [TD-033](../100-known-tech-debt.md).

### §3. `MailScope` и ролевая видимость (образец `SmsScope`/ADR-032/036)

По прямому образцу `get_sms_scope`/`SmsScope` ([ADR-032](ADR-032-sms-visibility-admin-full-catalog.md)) и `sees_all_sms_teams` ([ADR-036](ADR-036-sms-team-filter-admin-only.md)):

- `MailScope(sees_all_teams: bool, group_ids: frozenset[int])` в `domain/mail.py` (рядом с `SmsScope`; frozen dataclass, без I/O).
- Фабрика `get_mail_scope(principal, session) -> MailScope` в `api/deps.py`:
  - `sees_all_teams = principal_sees_all_mail_teams(principal)` = `is_superadmin OR permissions_subset(full_catalog_permissions(), principal.permissions)` — тот же предикат admin-уровня, что у SMS (устойчив к переименованию роли, без нового права). Введён общий хелпер `principal_sees_all_mail_teams` (симметрично `principal_sees_all_sms_teams`).
  - Иначе `group_ids` = непустые `teams.mail_group_id` по командам пользователя из `user_teams` (`principal.user_id`); `user_id=None` или нет привязанных групп → пустой набор.
- **Enforcement (граница безопасности — backend, не UI):**
  - **Чтение/список** (`GET /api/mail/messages`, `/mailboxes`, `/tags` в части ленты): вне scope → **пустой результат** (анти-энумерация), не `403`. При `sees_all_teams=false` сервис инъектирует во внешний API `group_id` = `MailScope.group_ids` (если пользователь задал фильтр «Команда» — пересечение выбранной группы со scope; ∉ scope → пустая страница) **вместе** с пользовательским `mail_account_id` (фильтр «Почта»). Внешний API **AND-комбинирует** оба фильтра (mail-агрегатор `ADR-0039` §3 — взаимоисключение ADR-0037 снято), поэтому **чужой `mail_account_id`** (ящик вне scope-групп) даёт **пустое пересечение → пустую страницу**, а не письма чужого ящика — анти-энумерация обеспечена **без** локального маппинга ящик→группа (кэша каталога нет, §2). Дропдауны «Почта»/«Команда» на вкладке «Сообщения» становятся **комбинируемыми** (не взаимоисключающими) — синхронно с backend. Пустой `group_ids` → пустая страница без вызова внешнего API. Это тот же паттерн, что SMS (`GET /sms/messages`: `number_id`+`team_id` комбинируемы AND, вне scope → пусто).
  - **Мутация** ящика (`POST/PATCH/DELETE /mailboxes`, `sync`): целевой ящик обязан принадлежать группе из `MailScope.group_ids` (для не-admin). Ящик вне scope → `403 forbidden` (не `404`: факт мутации требует явного отказа). При `sees_all_teams=true` — доступ ко всем группам.
  - **Теги** — глобальный каталог (админский). Управление каталогом тегов (`POST/PATCH/DELETE /tags`, правила, apply) гейтится действием `mail:tags` (см. §4); scope команд к тегам не применяется (теги не принадлежат команде). Чтение списка тегов доступно под `mail:view` (для рендера чипов и фильтров).
- `GET /api/auth/me` += `sees_all_mail_teams: boolean` = `principal_sees_all_mail_teams(principal)` — фронт по нему решает, показывать ли фильтр «Все команды» на `/mail` (по образцу `sees_all_sms_teams`, ADR-036). Backend — единственный источник истины (фронт не дублирует `permissions_subset`).

### §4. RBAC: расширение `CATALOG["mail"]`

`CATALOG["mail"]` в `domain/permissions.py`: `("view",)` → **`("view", "create", "edit", "delete", "sync", "tags")`**.

| Действие | Гейтит |
|----------|--------|
| `view` | чтение ленты/ящиков/тегов/команд-почты; `GET /api/mail/messages`,`/teams`,`/mailboxes`,`/tags`, `GET /api/mail/mailboxes`; reply на письмо |
| `create` | `POST /api/mail/mailboxes`, `POST /api/mail/mailboxes/test` |
| `edit` | `PATCH /api/mail/mailboxes/{id}` (включая смену кредов, `is_active`, `group_id`) |
| `delete` | `DELETE /api/mail/mailboxes/{id}` |
| `sync` | `POST /api/mail/mailboxes/{id}/sync` (форс-синк) |
| `tags` | управление каталогом тегов: `POST/PATCH/DELETE /api/mail/tags`, `POST /tags/{id}/rules`, `DELETE /tags/{id}/rules/{rule_id}`, `POST /tags/{id}/apply-to-existing` |

Обоснование набора: зеркалит гранулярность SMS (`view/edit/transfer/sync/delete`) и servers (`view/create/edit/delete`), но с двумя отличиями под доменную специфику почты — (1) отдельное `sync` (форс-синхронизация — дорогая операция, разумно отделить от `edit`, как `sms:sync`); (2) отдельное `tags` (управление глобальным каталогом тегов — админская функция, затрагивающая все команды сразу, поэтому НЕ смешивается с per-mailbox `edit`). `reply` на письмо остаётся под `view` (не расширяется) — это существующее поведение ADR-012 (и чтение, и ответ под `mail:view`), контракт reply не меняется. Отдельного `transfer` (как у SMS) нет: перенос ящика между командами — это смена `group_id` в `PATCH`, покрывается `edit`. Зафиксировано в [05-security.md](../05-security.md#каталог-прав-канон-на-сервере).

`reply` под `view` — осознанное сохранение ADR-012 §RBAC; расширение каталога аддитивно и не ломает существующие роли (у роли с `mail:["view"]` поведение не меняется).

### §5. Транзит IMAP/SMTP-кредов без хранения в CRM

CRM **не хранит** IMAP/SMTP-пароли. Пароли (`password`, опц. `smtp_password`) приходят с фронта в `POST/PATCH /api/mail/mailboxes*`, проходят **транзитом** в агрегатор по HTTPS и шифруются **там** (AES-256-GCM с AAD по id строки — mail-агрегатор ADR-0005). **Fernet CRM не задействуется** (`FERNET_KEY` для SSH/proxy-паролей, [ADR-007](ADR-007-shifrovanie-fernet.md), к почте не применяется — почта не хранится в CRM).

Нормативные инварианты (зафиксированы в [05-security.md](../05-security.md#транзит-imapsmtp-кредов-mail-нормативно)):
- Пароль **никогда** не логируется (structlog-фильтр секретов на `password`/`smtp_password`), не возвращается в теле ответов CRM (`MailMailbox`/`MailMailboxDetail` полей пароля не содержат), не пробрасывается обратно в SPA.
- Эндпоинты записи (`POST/PATCH /api/mail/mailboxes*`, `test`) отвечают заголовком **`Cache-Control: no-store`** (тело содержит транзитные креды в запросе; ответ не кэшируется).
- `MAIL_API_KEY` — единственный системный секрет модуля, только из env (инвариант ADR-012 неизменен).
- SSRF-guard хостов IMAP/SMTP выполняет **агрегатор** (`assert_public_host`); CRM креды не валидирует по сети сам — делегирует `POST /mailboxes/test`.

## Consequences

- CRM получает полный CRUD почт и глобальных тегов, ролевую видимость писем по CRM-командам, «Почты команды» на `/teams` — при добавлении **одного** поля `teams.mail_group_id` и без БД-хранилища писем.
- Владение ящиком остаётся единым (агрегатор) — нет рассинхрона видимости/доставки. Смена команды ящика = смена `group_id` в агрегаторе через `PATCH /mailboxes/{id}`. *(Дополнено [ADR-043](ADR-043-lazy-mail-group-provisioning.md): CRM-write ссылается на команду по `team_id`, не `group_id`; перенос — только admin-уровень.)*
- **1:1 команда↔группа** — ограничение: команде нельзя привязать ящики из нескольких групп. Запасной путь (per-mailbox владение через `mail_account_teams`) — [TD-033](../100-known-tech-debt.md).
- **Вложения писем недоступны из CRM** (MinIO агрегатора не проксируется) — [TD-034](../100-known-tech-debt.md).
- Ролевая видимость — **серверная граница безопасности**: вне scope чтение отдаёт пусто (анти-энумерация), мутация — `403`. Фронт-гейтинг (`permissions`, `sees_all_mail_teams`) — только UX.
- `MailScope` резолвит `group_ids` локально из `user_teams`+`mail_group_id` — свежесть гарантирована (нет кэша). Стоимость — один локальный SQL на запрос ленты у не-admin.
- Расширение `CATALOG["mail"]` аддитивно; существующие роли `mail:["view"]` не затронуты. Валидатор прав (`validate_permissions`) примет новые действия автоматически (каталог — единый источник).

## Alternatives considered

- **Полный перенос агрегатора в CRM (вариант 2).** Отклонён: 3–4 месяца, затаскивание Redis/MinIO/брокера в CRM (против ADR-001/006) или ампутация Telegram/webhooks/forwarding (используются). Сравнение с переносом SMS (ADR-030) обманчиво: там 4 таблицы и входящий webhook, здесь — IMAP-поллинг с circuit-breaker, Outlook OAuth, санитизация HTML, бинарные вложения.
- **Локальный кэш каталога ящиков в CRM (таблица + фоновый sync).** Отклонён: лишняя таблица, фоновый сервис, проблема свежести. Резолв видимости и так локальный (`user_teams`→`mail_group_id`); каталог ящиков нужен лишь для селекторов и рендерится по требованию из прокси.
- **Второй источник истины владения (дублировать `group_id` ящика в CRM).** Отклонён: гарантированный дрейф с forwarding/webhooks агрегатора.
- **Хранить IMAP/SMTP-пароли в CRM (Fernet, как SSH/proxy).** Отклонён: агрегатор — источник истины ящиков и уже шифрует их (AES-GCM); дублирование секрета в CRM — лишняя поверхность атаки без пользы (CRM не открывает IMAP-сессии).
- **Новое право `mail:view_all` вместо предиката полного каталога.** Отклонён симметрично ADR-032/036: предикат `permissions_subset(full_catalog, ...)` не плодит право и устойчив к переименованию роли.
