# ADR-030 · Модуль «СМС» — полное слияние SMS-агрегатора в CRM

Статус: `accepted` · Дата: 2026-07-09 · Амендмент/связки: [ADR-021](ADR-021-rbac-users-roles.md) (RBAC-каталог), [ADR-022](ADR-022-teams-nav-categories.md) (CRM-команды, навигация), [ADR-009](ADR-009-in-backend-notifier-vs-alertmanager.md) (in-backend Telegram)

## Контекст

Есть отдельный рабочий сервис SMS-агрегатора (`sms-agreagtor`): принимает входящие SMS от Twilio (webhook с проверкой подписи), хранит номера/сообщения, привязывает номера к командам и доставляет входящие операторам в Telegram (Mini App SSO + fan-out по команде + retry дохлых доставок). Стек донора идентичен CRM (Python 3.12 / FastAPI / PostgreSQL 16 / SQLAlchemy 2.0 async / Alembic). Требуется перенести сервис в CRM: страница **«СМС»** (вкладки «Сообщения»/«Номера») + доработка `/teams` (кол-во номеров команды + список номеров в detail-панели).

Ключевая развилка — **как** переносить:
- **Вариант 1 (принят): полное слияние** движка в одну БД `crm`, поверх CRM `teams`/`users`/`user_teams`, с JWT+RBAC CRM вместо Redis-сессий донора.
- Вариант 2: независимый бэкенд SMS + интеграция по HTTP. Отклонён: все запрошенные фичи (кол-во номеров у команды, список номеров команды, бейдж команды на сообщении, фильтр по команде) завязаны на **команды CRM**; независимый бэк держал бы собственную таблицу `teams` → постоянная синхронизация двух моделей команд, два деплоя, cross-service auth.

## Решение

### 1. Полное слияние в одну БД `crm`
Переносится **весь** движок: Twilio-приём + Telegram-доставка операторам (Mini App-привязка, fan-out по команде, retry, dead-links). SMS-шные таблицы `teams`/`users`/`user_teams`/`admin_audit`/`service_state` донора **НЕ переносятся** — используются существующие CRM `teams`(UUID)/`users`(UUID)/`user_teams`.

### 2. Новые таблицы SMS-модуля (маппинг BIGINT→UUID)
Четыре таблицы: `sms_phone_numbers`, `sms_inbound`, `sms_deliveries`, `sms_telegram_links` (миграция `0017_create_sms_module`, DDL — [03-data-model.md](../03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links)). **Собственные PK доменных таблиц — `BIGINT Identity`** (донорская идиома, keyset-курсор по `(received_at, id)`); **внешние ссылки на команды/юзеров — `UUID`** (тип CRM):
- `sms_phone_numbers.team_id` → `teams(id)` `ON DELETE SET NULL` (NULL = unassigned-пул);
- `sms_phone_numbers.added_by_user_id` → `users(id)` `ON DELETE SET NULL`;
- `sms_inbound.team_id` → `teams(id)` `ON DELETE SET NULL` (**снимок** команды на момент приёма, определяет получателей fan-out);
- `sms_deliveries.inbound_sms_id` → `sms_inbound(id)` `ON DELETE CASCADE`; `sms_deliveries.user_id` → `users(id)` `ON DELETE CASCADE`;
- `sms_telegram_links.user_id` → `users(id)` `ON DELETE CASCADE`, PK `telegram_user_id BIGINT`.

Все FK-имена команд/юзеров в SMS-таблицах указывают на **CRM**-таблицы; исторические данные SMS-БД в первом переносе **не мигрируются** (BIGINT→UUID id-маппинга нет) — вынесено в TD (см. [100-known-tech-debt.md](../100-known-tech-debt.md), «Импорт исторических данных SMS»).

### 3. Отказ от Redis
CRM однопроцессный; Redis отсутствует и не добавляется. Донорские Redis-сессии/CSRF/lockout/rate-limit заменяются:
- **auth** → JWT+RBAC CRM;
- **pending-токены Mini App SSO** (cookie-флоу «unlinked → войди → допривяжем») **упраздняются редизайном**: под JWT привязка идёт из аутентифицированной CRM-сессии (`POST /api/sms/telegram/link` с `Authorization: Bearer` + `init_data`), pending-механизм не нужен;
- **rate-limit** webhook/link → существующий in-memory лимитер CRM ([TD-005](../100-known-tech-debt.md)) либо отложить.

### 4. Два раздельных Telegram-бота (разные токены)
- **notifier** (существующий, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, ADR-009) — алерты мониторинга в группу. **Не трогается.**
- **SMS-delivery** (новый, `SMS_TELEGRAM_BOT_TOKEN`) — доставка входящих SMS операторам в личку. Отдельный `SmsBotClient` (`sendMessage`/`reply_markup`/`setWebhook`), собственный webhook-секрет и Mini App URL. Namespaced-env, чтобы не пересекаться с notifier.

### 5. Новые редактируемые поля номера + системный `label`
`sms_phone_numbers` несёт **`login`, `app_name`, `note`** — новые редактируемые пользователем поля (`PATCH /api/sms/numbers/{id}`, presence-семантика затирания). **`label` остаётся системным** (никнейм из Twilio `friendly_name`, редактированию через это API не подлежит; синхронизируется `POST /api/sms/numbers/sync`).

### 6. Видимость сообщений по командам (current-ownership scope)
`Principal` расширяется полем **`user_id: UUID | None`** (из claim `uid`; супер-админ → `None` → видит всё). Видимость SMS — по **текущей** принадлежности номера команде (`sms_phone_numbers.team_id`), НЕ по снимку `sms_inbound.team_id`: не-админ видит только SMS номеров своих команд (`user_teams` пользователя); номер, ушедший из команды, перестаёт быть виден её участникам (security-инвариант).

**Read vs mutation (нормативно, синхронно с [04-api.md#sms](../04-api.md#sms)):**
- **Read/list** (`GET /api/sms/messages`, `GET /api/sms/numbers`, фильтры `number_id`/`team_id`) — номер/команда вне scope → **пустой результат** (анти-энумерация: не раскрываем существование, не `403`/`404`).
- **Мутации** (`PATCH /api/sms/numbers/{id}` edit, `POST .../transfer`, `DELETE .../{id}`) — номер вне scope → **`403 forbidden`** (адресный доступ по известному `id`; enumeration неактуален — гейт `require("sms", <action>)` уже отсёк роль, а принадлежность номера проверяется в handler'е). Снимок `sms_inbound.team_id` — исторический/для fan-out на момент приёма; **бейдж команды и пилюли `Логин/Приложение/Примечание` на карточке сообщения берутся из текущего номера** (`sms_phone_numbers` по `to_number`), консистентно с фильтром и scope.

### 7. RBAC-страница `sms`
Каталог прав ([permissions.py](../../backend/app/domain/permissions.py), [05-security.md](../05-security.md#каталог-прав-канон-на-сервере)) += `"sms": ("view", "edit", "transfer", "sync", "delete")`:
- `view` — лента сообщений + список номеров;
- `edit` — правка `login`/`app_name`/`note` номера;
- `transfer` — назначение/снятие команды у номера;
- `sync` — синхронизация номеров из Twilio;
- `delete` — удаление номера.

**`create` НЕ вводится** — номера появляются автоматически (из входящих SMS и `sync`), вручную не создаются. **Привязка Telegram (`link`/`auth`) — вне матрицы `sms`**: доставка операторам — функция членства в команде (`user_teams` + живой линк), а не права на страницу; `POST /api/sms/telegram/link` гейтится только аутентификацией (любой валидный JWT привязывает **свой** telegram), webhook/twilio/auth — публичны (подпись/секрет/HMAC).

### 8. Авторизационное сужение полей в `GET /api/teams/{id}/numbers` (нормативно)
Держатель `teams:view` вправе видеть **состав** номеров любой команды (для управления командами), но **НЕ** чувствительный контекст учёток номеров всех команд (`login`/`app_name`/`note`/`label`) — иначе `teams:view` даёт доступ к данным, защищённым отдельной матрицей `sms:*`. **Решение — сузить поля** (не сузить набор строк): `GET /api/teams/{id}/numbers` отдаёт минимальную схему **`TeamNumberItem`** (`id`, `phone_number`, `team`), **без** `login`/`app_name`/`note`/`label`. Полный `SmsNumberItem` (с чувствительным контекстом) доступен **только** на эндпоинтах страницы «СМС» (`GET /api/sms/messages`, `GET /api/sms/numbers`) под матрицей `sms:*` и SMS-scope по командам. Это заменяет прежнюю трактовку «номера в teams-view — как участники → полный `SmsNumberItem`»: team-detail показывает состав номеров команды **без** учётного контекста; полный доступ к `login`/`app_name`/`note` — исключительно через SMS-scope.

## Последствия

**Плюсы:**
- Одна БД, один деплой, одна модель команд; фичи «номера команды», «бейдж команды», «фильтр по команде» — прямые JOIN'ы.
- Переиспользование пайплайна донора почти без адаптации (crash-recoverable fan-out, keyset-курсор, retry-монитор по образцу `proxy_monitor_service.py`).
- Никакого нового инфра-компонента (Redis не добавляется).

**Минусы / риски:**
- Смешанные типы PK (BIGINT доменные / UUID внешние) в одной схеме — осознанная асимметрия ради сохранения донорской пагинации и минимума адаптации.
- Исторические SMS-данные не переносятся в первом релизе (TD).
- Два Telegram-бота = два токена/два webhook-пути наружу (нужен namespaced-конфиг и nginx-проброс `X-Forwarded-Proto/Host` для корректной подписи Twilio).
- `Principal.user_id` — новое поле на auth hot-path (один лишний столбец в уже загружаемом `users`-ряду; стоимость нулевая).
- **Доставка — at-least-once, синхронный fan-out (осознанно отложено).** Пайплайн донора сохраняет две характеристики, зафиксированные как tech-debt: (1) возможна **двойная доставка** одного SMS оператору, если retry-монитор повторит только что зарезервированную pending-доставку раньше отправки webhook'ом (корректность сохранена — UNIQUE `(inbound_sms_id, telegram_user_id)`; дублируется лишь Telegram-месседж) — [TD-031](../100-known-tech-debt.md); (2) **синхронный fan-out** в теле webhook до возврата `200` Twilio может превысить таймаут Twilio на крупной команде/медленном Telegram → ретрай (гасится дедупом по SID + идемпотентным `try_reserve` + retry-монитором) — [TD-032](../100-known-tech-debt.md). Оба — не блокеры при текущем масштабе (NFR-1); fix-направления — claim `sending`+lease/`SKIP LOCKED` и вынос fan-out в фоновую задачу.

## Альтернативы
- **Независимый бэк + интеграция** — отклонён (дублирование модели команд, cross-service auth, два деплоя).
- **Перенос донорской модели teams/users** — отклонён (два реестра пользователей/команд в одной БД, рассинхрон RBAC).
- **Redis для pending/rate-limit** — отклонён (редизайн под JWT устраняет надобность; in-memory лимитер достаточен при NFR-1).
- **`label` как редактируемое поле** — отклонён: `login`/`app_name`/`note` покрывают потребность в пользовательских атрибутах, `label` остаётся детерминированным зеркалом Twilio `friendly_name`.
