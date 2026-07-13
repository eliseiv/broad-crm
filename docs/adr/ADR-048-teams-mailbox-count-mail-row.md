# ADR-048 — Счётчик почт на карточке команды + строка почты в detail-панели `/teams`

- **Статус:** accepted
- **Дата:** 2026-07-13
- **Контекст-модули:** [teams](../modules/teams/README.md), [mail](../modules/mail/README.md)
- **Амендмент:** [ADR-034](ADR-034-teams-number-login-app.md) (расширение detail-панели `/teams` под `teams:view`), [ADR-044](ADR-044-mail-full-merge-into-crm.md) §4 (`GET /api/teams/{id}/mailboxes`, `TeamMailboxItem`), [ADR-047](ADR-047-mail-fix-pack.md) **§3.2** (разворачивается утверждение «`TeamMailboxItem` не меняется»), [ADR-030](ADR-030-sms-module-full-merge.md) (паттерн `number_count`)
- **Переиспользуется без изменений (НЕ амендируется):** [ADR-047](ADR-047-mail-fix-pack.md) **§5** (рендер строки ящика на `/mail`) — служит **референсом** визуального языка (крупный жирный «Номер», «Приложение» пилюлей `ui/Pill tone="accent"`) и остаётся в силе как есть; [ADR-047](ADR-047-mail-fix-pack.md) §3.3 (`display_name` — производное) — тоже в силе

## Контекст

Требование владельца (2026-07-13), страница `/teams`:

1. «На странице /teams у каждой команды отображается количество номеров и участников. Необходимо добавить отображение количество почт».
2. «При раскрытии Почты Команды необходимо чтобы почты отображались в формате: почта, Номер, Приложение — в одну строку. Номер такой же большой как на странице /mail вкладка Почты, Приложение так же подсвечено».

Что уже есть в коде (сверено, `claims-from-code`):

- **Счётчик номеров** — `TeamListItem.number_count` (`backend/app/schemas/team.py:73`), считается `SmsNumberRepository.count_by_teams` (батч для списка, `backend/app/repositories/sms_number_repository.py:100`) и `count_by_team` (одиночный, для тела `201`/`200`, там же `:113`); `TeamService.list_teams` вызывает батч-вариант (`backend/app/services/team_service.py:94`), `create_team`/`update_team` — одиночный (`:165`, `:255`). **Это референс-паттерн для нового счётчика.**
- **Список почт команды** — эндпоинт **уже существует**: `GET /api/teams/{team_id}/mailboxes` под гейтом `require("teams", "view")` (`backend/app/api/teams.py:78-91`), резолв — `MailAccountRepository.list_by_team` (`backend/app/repositories/mail_account_repository.py:63`), сериализация — `MailService.list_team_mailboxes` (`backend/app/services/mail_service.py:296-306`) в схему `TeamMailboxItem { id, email, display_name, is_active }` (`backend/app/schemas/mail.py:300-306`). **Новый эндпоинт не нужен.**
- **Поля ящика «Номер»/«Приложение»** — `mail_accounts.number`/`app_name` существуют (миграция `0024`, [ADR-047](ADR-047-mail-fix-pack.md) §3); в контракте — `MailMailbox.number`/`app_name` (`backend/app/schemas/mail.py:117-118`). В `TeamMailboxItem` их **нет**.
- **Референс-рендер строки ящика на `/mail`** (вкладка «Почты», [ADR-047](ADR-047-mail-fix-pack.md) §5): лейбл «Номер» (`text-[13px] text-text-secondary`) + значение `number` (`text-lg font-bold leading-tight text-text-primary`) — `frontend/src/components/MailboxRow.tsx:135-136`; лейбл «Приложение» + значение `app_name` пилюлей `<Pill label={appName} tone="accent" wrap title={appName} />` — там же `:143-144`.
- **Текущая строка почты в detail-панели `/teams`** — статус-`Badge` + `email` + `display_name` вторичным (`frontend/src/components/TeamDetailPanel.tsx:45-56`). «Номера»/«Приложения» в ней нет.
- **Индекс под агрегат** — `ix_mail_accounts_team_id (team_id)` существует (`backend/app/models/mail_account.py:73`, миграция `backend/alembic/versions/0021_create_mail_module.py:68`).

## Решение

### 1. `TeamListItem += mailbox_count` (счётчик почт команды)

- Новое поле контракта `GET /api/teams` (а также тела `201 POST /api/teams` и `200 PATCH /api/teams/{id}` — они возвращают ту же схему): **`mailbox_count: integer`** = `COUNT(mail_accounts WHERE team_id = teams.id)`. Может быть `0`. Nullable — **нет**.
- **Имя поля — `mailbox_count`** (не `mail_count`): считается сущность «ящик» (`mail_accounts`), тот же корень, что у уже действующих `GET /api/teams/{id}/mailboxes` / `TeamMailboxItem` / `TeamMailboxesResponse`. Соответствие «имя поля = считаемая сущность» повторяет `number_count` ↔ `sms_phone_numbers` ↔ `GET /api/teams/{id}/numbers`.
- **Реализация — строго по паттерну `number_count`:** в `MailAccountRepository` добавляются `count_by_teams(team_ids) -> dict[UUID, int]` (батч, `GROUP BY team_id` — для `list_teams`, без N+1) и `count_by_team(team_id) -> int` (одиночный — для тел `201`/`200`), симметрично `SmsNumberRepository.count_by_teams`/`count_by_team`. `TeamService` получает `MailAccountRepository` через DI (`app/api/deps.py`, фабрика `TeamServiceDep`) — так же, как уже получает `SmsNumberRepository` (`backend/app/services/team_service.py:80-89`).
- **Миграции нет.** Колонка `mail_accounts.team_id` и индекс `ix_mail_accounts_team_id` уже существуют (миграция `0021`); индекс обслуживает и `list_by_team`, и новый агрегат.
- **Инвариант согласованности:** FK `mail_accounts.team_id` объявлен `ON DELETE SET NULL` (`backend/app/models/mail_account.py:47-49`) — при удалении команды ящики становятся unassigned, «висячего» счётчика не остаётся.

### 2. `TeamMailboxItem += number`, `app_name`

- Схема `TeamMailboxItem` расширяется двумя полями: **`number: string | null`**, **`app_name: string | null`** (те же типы, что в `MailMailbox`; `null` — не задано). Источник — `mail_accounts.number`/`app_name` ([ADR-047](ADR-047-mail-fix-pack.md) §3). Новых запросов/JOIN'ов нет — `list_by_team` уже читает строку `mail_accounts` целиком.
- **Поле `display_name` в схеме СОХРАНЯЕТСЯ** (тип не меняется: `string | null`), но в строке detail-панели **больше не рендерится**: оно производное (`"<number> <app_name>"`, [ADR-047](ADR-047-mail-fix-pack.md) §3.3, [TD-052](../100-known-tech-debt.md)), а его составляющие теперь показываются явно — рендерить обе формы значит дублировать одно и то же имя дважды в одной строке. Сужать контракт (удалять поле) не стали: `display_name` остаётся единственной формой имени во внешнем контракте агрегатора, и его наличие в схеме безвредно.
- **Креды/хосты/статус синка по-прежнему НЕ отдаются** этим эндпоинтом (`imap_*`/`smtp_*`/пароли/`last_sync_error`/`consecutive_failures`/`last_synced_at`) — сужение [ADR-044](ADR-044-mail-full-merge-into-crm.md) §4 сохраняется в полном объёме.

### 3. Рендер строки почты в detail-панели команды (нормативно)

Нормативный рендер зафиксирован в [08-design-system.md §Страница «Команды»](../08-design-system.md#страница-команды). Кратко: одна логическая строка — индикатор статуса → `email` → «Номер» + значение (крупно, полужирно, **те же классы, что на `/mail`**) → «Приложение» + значение **пилюлей [`ui/Pill`](../08-design-system.md#компонент-uipill) `tone="accent"`**.

- **Примитив подсветки — существующий `ui/Pill` с `tone="accent"`** (тот же, что задан для «Приложения» на вкладке «Почты», [ADR-047](ADR-047-mail-fix-pack.md) §5). **Новый примитив НЕ вводится.** Тег-чип не подходит (он выводит цвет из `tag.color`, у «Приложения» цвета нет) — обоснование уже зафиксировано в [08-design-system.md](../08-design-system.md#вкладка-почты-нормативно) и здесь не дублируется.
- **«Такой же большой»** трактуется буквально: значение `number` рендерится **теми же** классами, что в `MailboxRow.tsx:136` (`text-lg font-bold leading-tight text-text-primary`) — визуальный язык `/mail` и `/teams` совпадает.
- **Пустые значения:** `number = null` → пара «лейбл + значение» не рендерится; то же для `app_name` (правило [ADR-047](ADR-047-mail-fix-pack.md) §5 «лейбл без значения не показывается никогда»). Строка при обоих `null` = статус + `email` (текущее поведение, без `display_name`).
- **Индикатор статуса** остаётся тем же, что сейчас: `Badge` `dot` с **видимым текстом** «Активна»/«Неактивна» (`tone="green"`/`tone="red"`). Текст статуса — **видимый, НЕ `sr-only`** (единственный допустимый вариант; текущее поведение секции не регрессирует). Критерий — **только `is_active`**: «есть ошибки синка» (`consecutive_failures`/`last_sync_error`) здесь неприменим — этих полей в `TeamMailboxItem` нет и они намеренно не раскрываются (см. §2). Осознанное расхождение с [ADR-047](ADR-047-mail-fix-pack.md) §5, а не пропуск.
- **⚠️ Расхождение кодировки «Приложения» ВНУТРИ одной detail-панели — осознанное решение.** В секции «Номера команды» «Приложение» рендерится пилюлей с лейблом **внутри** (`Приложение: {value}`, `tone="yellow"`) — норма [ADR-034](ADR-034-teams-number-login-app.md); в секции «Почты команды» — **внешний** лейбл + значение пилюлей `tone="accent"`. Причина: требование владельца — строка почты обязана повторять визуальный язык **вкладки «Почты» на `/mail`** («Приложение так же подсвечено»), а там нормативен именно внешний лейбл + `tone="accent"` ([ADR-047](ADR-047-mail-fix-pack.md) §5). **Норма [ADR-034](ADR-034-teams-number-login-app.md) для строки номера НЕ меняется**; унификация двух секций отвергнута (потребовала бы разворота либо ADR-034, либо требования владельца — цена выше выгоды от косметической однородности).
- **Переполнение решается размером, не обрезкой** (CLAUDE.md): при нехватке ширины строка переносится (`flex-wrap`), значения `email`/`number`/`app_name` видны **полностью**; `truncate`/`overflow-hidden`/клиппирование значений — **запрещены**.

### 4. Видимость (гейт прав)

- **Счётчик `mailbox_count`** — отдаётся в `GET /api/teams` под существующим гейтом **`teams:view`**, без сужения `MailScope`. Новых прав/эндпоинтов нет.
- **Список почт команды** — остаётся под существующим гейтом **`teams:view`** (`backend/app/api/teams.py:83`), `MailScope` в нём не участвует. Расширенные поля `number`/`app_name` раскрываются тем же держателям.
- **Обоснование.** Держатель `teams:view` **уже сегодня** видит полный состав ящиков **любой** команды (эндпоинт `GET /api/teams/{id}/mailboxes` под `teams:view` — норма [ADR-044](ADR-044-mail-full-merge-into-crm.md) §4). Счётчик — производный агрегат **того же самого** множества: он не раскрывает ничего, что уже не раскрыто списком. `number`/`app_name` ящика — ровно тот класс «слабо-чувствительного идентифицирующего контекста», который [ADR-034](ADR-034-teams-number-login-app.md) уже разрешил под `teams:view` для `login`/`app_name` **номера**: сами по себе доступа к ящику не дают (пароля/токена в них нет). Секреты и операционный контекст (IMAP/SMTP-хосты, пароли, статус/ошибки синка) остаются **только** под матрицей `mail:*` и `MailScope`.
- **Анти-энумерация** здесь неприменима: `teams:view` и так отдаёт **все** команды (scope команд у модуля `teams` не существует), поэтому «сужать до своих команд» нечего. Несуществующая команда → `404 team_not_found` (поведение не меняется).
- **Отклонено — гейтить `mail:view` и/или сужать по `MailScope.team_ids`:** (а) раскололо бы карточку команды (участники и номера видны, почты — нет) и detail-панель (секция «Номера команды» под `teams:view`, «Почты команды» — под другим правом) без выигрыша в безопасности; (б) прямо разошлось бы с **уже действующим** гейтом `GET /api/teams/{id}/mailboxes` (`teams:view`) — пришлось бы разворачивать норму [ADR-044](ADR-044-mail-full-merge-into-crm.md) §4; (в) `MailScope` — граница видимости **модуля «Почты»** (лента, каталог ящиков, мутации на `/mail`), а не страницы `/teams`.

## Последствия

- **Миграций нет.** Схема БД не меняется: `mail_accounts.number`/`app_name` (`0024`), `team_id` + `ix_mail_accounts_team_id` (`0021`) уже есть.
- **Контракт `/api/teams` расширяется аддитивно** (`TeamListItem += mailbox_count`, `TeamMailboxItem += number/app_name`) — существующие поля/коды ошибок/прецеденция **не меняются**; ломающих изменений нет.
- **`TeamService` получает зависимость от `MailAccountRepository`** — модуль `teams` начинает читать таблицу модуля `mail` (как уже читает `sms_phone_numbers`). Направление зависимости то же, что у `number_count`; новых слоёв/сервисов не заводится.
- **Производительность:** один дополнительный агрегирующий запрос на `GET /api/teams` (батч `GROUP BY team_id` по индексу) — симметрично `number_count`. N+1 не появляется.
- **Frontend:** карточка команды получает третий чип — «N почт» (склонение — хелпер `mailsPlural` в `lib/plural.ts` по образцу `numbersPlural`/`membersPlural`); строка секции «Почты команды» перерисовывается по §3. `TeamMailboxItem`/`TeamListItem` в `types/api.ts` расширяются.
- **QA:** существующие тесты `TeamMailboxItem` (`frontend/src/components/__tests__/TeamDetailPanel.test.tsx`) и `TeamListItem` (`backend/tests/integration/test_teams_api.py`) требуют дополнения новыми полями — контракт аддитивен, ломающего удаления полей нет.

## Альтернативы

1. **Новый эндпоинт `GET /api/teams/{id}/mail` / отдельный счётчик-эндпоинт** — отвергнуто: `GET /api/teams/{id}/mailboxes` уже покрывает задачу (переиспользуется как есть), а счётчик по паттерну `number_count` живёт в `TeamListItem` и не требует отдельного round-trip на каждую карточку.
2. **Считать `mailbox_count` на клиенте** (запрашивать `/mailboxes` для каждой команды) — отвергнуто: N+1 запросов на рендер списка + счётчик нужен **до** раскрытия панели (ленивая загрузка теряет смысл).
3. **Денормализовать счётчик в колонку `teams.mailbox_count`** — отвергнуто: требует триггеров/синхронизации при каждом create/delete/transfer ящика; агрегат по индексу дешевле и не может рассинхронизироваться (тот же выбор, что для `number_count`).
4. **Новый примитив подсветки «Приложение»** — отвергнуто: `ui/Pill tone="accent"` уже нормативен для этого значения на `/mail` ([ADR-047](ADR-047-mail-fix-pack.md) §5); второй примитив с той же семантикой = расщепление дизайн-системы.
5. **Убрать `display_name` из `TeamMailboxItem`** — отвергнуто: сужение публичной схемы без выгоды (поле производное и безвредное); достаточно нормативно не рендерить его в строке.
