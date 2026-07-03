# Модуль `notifier` — Telegram-уведомления о нагрузке и доступности

Статус: `spec-ready` · Исполнитель: backend

## Scope

Фоновая asyncio-задача **внутри backend-процесса** (не отдельный сервис, не Alertmanager — [ADR-009](../../adr/ADR-009-in-backend-notifier-vs-alertmanager.md)), которая периодически опрашивает online-серверы через `MonitoringService` и шлёт в Telegram-группу сообщения **только при эскалации** (повышение зоны нагрузки или потеря доступности). Пороги зон — те же, что в UI и backend ([thresholds](../../04-api.md#пороги-зон), `usage_to_zone()`), **переиспользуются без изменений**.

Out of scope (Этап 1): восстановительные/recovery-сообщения **для серверов**, деэскалация, история алертов, silencing/grouping, команды бота/интерактив, индивидуальные подписки, метрики самого бота. (Персистентность состояния нотификатора **входит в scope** — [TD-019](../../100-known-tech-debt.md) закрыт [ADR-014](../../adr/ADR-014-persist-notifier-state-alert-on-first-elevated.md); см. [«State-машина (персистентная)»](#state-машина-персистентная).) (Recovery-сообщения **для AI-ключей** — в scope отдельного сервиса `AiKeyMonitorService`, см. [«Сообщения AI-ключей»](#сообщения-ai-ключей) и [modules/ai-keys](../ai-keys/README.md).)

## Опциональность (активация)

Нотификатор **активен только если заданы обе** переменные окружения `TELEGRAM_BOT_TOKEN` **и** `TELEGRAM_CHAT_ID` (непустые). Иначе фоновая задача **не запускается**, backend стартует штатно, в лог пишется `notifier_disabled` (info). Это не ошибка — бот опционален.

## Источники данных и переиспользование

- Реестр: `ServerRepository.list_online()` — серверы с `provision_status == online` (id, `name`, `ip`, property `instance` = `<ip>:<exporter_port>`). Контракт объявлен в [modules/servers](../servers/README.md#переиспользуемый-контракт-репозитория-и-модели-нормативно).
- Метрики: `MonitoringService.fetch_for_instances(instances)` → `dict[instance -> InstanceMetrics]`. Метод, типы `InstanceMetrics/ServerMetrics/Metric` и исключение `PrometheusUnavailable` объявлены в [modules/monitoring](../monitoring/README.md#переиспользуемый-внутренний-интерфейс-нормативно) (единый источник). `InstanceMetrics.online` = `up == 1`; `InstanceMetrics.metrics: ServerMetrics{cpu,ram,ssd: Metric{usage_percent, zone}}` либо `None`.
- Зоны: `app.domain.thresholds.usage_to_zone()` (`<80 green`, `80..90 yellow`, `>90 red`). **Пороги не дублировать и не менять** — единый источник (там же).
- **Согласованность и нагрузка на Prometheus (точная семантика):** notifier вызывает тот же `MonitoringService` (та же реализация маппинга и порогов → бот трактует метрики идентично карточкам). Но кэш ключуется по набору instances (`tuple(sorted(instances))`), а наборы у бота (его online-instances) и у UI-агрегата (`GET /api/servers`) **различаются**, поэтому **общего кэш-элемента с карточками нет** — бот делает **собственные** запросы к Prometheus, защищённые своим коротким TTL-кэшем (по своему ключу) и **общим семафором конкуренции** `_MAX_CONCURRENT_QUERIES` (рекомендация 4). То есть бот не «бесплатно переиспользует» кэш UI, но и не создаёт лавину: одновременные исходящие PromQL ограничены общим семафором. Семантика кэша — [modules/monitoring](../monitoring/README.md#семантика-кэша-нормативно--важно-для-шаринга).

## Жизненный цикл задачи

- Запуск — в `lifespan` (`backend/app/main.py`), рядом с recovery-hook провижининга: создаётся `asyncio.Task` (только если нотификатор активен).
- Остановка — отмена задачи при shutdown (`task.cancel()` + ожидание; корректная обработка `CancelledError`).
- Цикл: бесконечный `while True`: одна итерация опроса → `asyncio.sleep(NOTIFIER_POLL_INTERVAL_SEC)`. Любое необработанное исключение внутри итерации **логируется и не валит задачу** (цикл продолжается).

## Итерация опроса (нормативно)

1. Открыть короткоживущую сессию БД (`get_sessionmaker()`), получить `list_online()` **и** загрузить персистнутое состояние `notifier_server_state` для этих серверов (`SELECT ... WHERE server_id IN (...)` → `dict[server_id -> ServerState]`), **закрыть сессию** (не держать открытой во время запроса к Prometheus). Снять снимок `{server_id -> (name, ip, instance)}`. **БД — источник истины состояния** (не in-memory dict, живущий между итерациями): база (`prev`) читается из БД каждую итерацию, поэтому рестарт/деплой не сбрасывает состояние ([TD-019](../../100-known-tech-debt.md) закрыт).
2. Собрать `instances` и вызвать `MonitoringService.fetch_for_instances(instances)`.
   - **`PrometheusUnavailable` → итерация пропускается целиком**: состояние в БД НЕ изменяется (нет записи), алерты не шлются, в лог `notifier_prometheus_unavailable` (warning). Переход к следующему `sleep`.
3. Для каждого online-сервера вычислить переходы чистой функцией `evaluate(prev, im)`, где `prev` — загруженное из БД состояние (или `None`, если строки нет), отправить алерты при эскалации, затем **UPSERT** нового состояния в `notifier_server_state` (`INSERT ... ON CONFLICT (server_id) DO UPDATE SET online, zone_cpu, zone_ram, zone_ssd, updated_at = now()`). Персист выполняется **всегда** после итерации по серверу, независимо от результата отправки в Telegram (best-effort доставка не влияет на состояние — см. [«Доставка в Telegram»](#доставка-в-telegram)).
4. **Очистка не выполняется в коде цикла.** Серверы, исчезнувшие из `list_online()` (сменился `provision_status`, но не удалены), в этой итерации просто не опрашиваются; их строка состояния **сохраняется** (сброс базы при провижининг-флапе дал бы ложный ре-алерт при возврате). При hard-delete сервера строка снимается автоматически через `ON DELETE CASCADE` ([03-data-model.md](../../03-data-model.md#таблица-notifier_server_state)). Recovery-сообщений нет.

## State-машина (персистентная)

Состояние **персистится в БД** (таблица `notifier_server_state`, [03-data-model.md](../../03-data-model.md#таблица-notifier_server_state), [ADR-014](../../adr/ADR-014-persist-notifier-state-alert-on-first-elevated.md)) — переживает рестарт/деплой backend, чем закрывает [TD-019](../../100-known-tech-debt.md). Логическая форма состояния (зеркало строки БД):

```
ServerState = { online: bool, zones: {cpu: Zone, ram: Zone, ssd: Zone} | None }
# online  <- notifier_server_state.online
# zones   <- (zone_cpu, zone_ram, zone_ssd); любая зона NULL ≡ отсутствует
```

Переход вычисляется **чистой функцией** `evaluate(prev: ServerState | None, im: InstanceMetrics) -> (new_state, alerts)`: `prev` читается из БД в начале итерации (или `None`, если строки нет), `new_state` записывается UPSERT'ом в конце. In-memory dict между итерациями **не является источником истины** — база всегда берётся из БД.

Ранг зоны для сравнения эскалации: `green=0 < yellow=1 < red=2`.

**Отсутствующая база ≡ здоровый baseline `{online:True, zones:green×3}` (нормативно).** Если строки состояния нет (`prev is None` — новый сервер или **первый прогон после выката этой фичи**), `evaluate()` трактует базу как «здоровую»: `online=True` и все зоны `green` (rank 0). Это единое правило (без спец-ветки «первая встреча — молча») даёт нужные инварианты:
- сервер, впервые увиденный уже под нагрузкой (`yellow`/`red`), даёт эскалацию `green→elevated` → **алерт ровно один раз**, затем зона персистится → дедуп (следующая итерация видит `base == cur` → повтора нет). Это и есть **alert-on-first-elevated** — целевое поведение выката (кейс «Фотобудка»);
- сервер, впервые увиденный `offline` (`up==0`), даёт `online→offline` → **🔴 offline-алерт один раз**, затем `online=False` персистится → дедуп;
- здоровый сервер (`green`/`online`) — эскалации нет, алерта нет, просто персистится база.

**Отсутствующая база отдельной метрики (`zone_* = NULL`) ≡ `green` при сравнении** (те же случаи: сервер был online без метрик; первый опрос после `offline→online` ещё вернул `metrics=None`): для `rank(cur) > rank(base)` база этой метрики = `green` (rank 0). Устраняет undefined-сравнение с `NULL` и сохраняет инвариант переалерта нагрузки относительно `green`.

**Правила переходов** (для каждого сервера в текущем опросе; `prev` — из БД, `None` подставляется как здоровый baseline выше):

| Предыдущее (`prev`) | Текущее | Действие | Новое состояние (UPSERT) |
|---------------------|---------|----------|----------------|
| нет строки (`None`) → baseline `online, green×3` | online + есть метрики | сравнение с `green`-базой: yellow/red → **алерт**; green → молча | `{online:True, zones:cur}` |
| нет строки (`None`) → baseline `online` | offline (`up==0`) | **🔴 СРОЧНО offline** (baseline online → offline) | `{online:False, zones:None}` |
| `online=True` | offline (`up==0`) | **🔴 СРОЧНО offline** (online→offline) | `{online:False, zones:None}` |
| `online=False` | offline | молча (всё ещё offline) | `{online:False, zones:None}` |
| `online=False` | online + есть метрики | **возврат**: база = `green` по всем метрикам → высокая нагрузка переалертится | `{online:True, zones:cur}` |
| `online=True` | online + есть метрики | сравнение зон с `prev.zones`, алерт при эскалации | `{online:True, zones:cur}` |

**Анти-спам / дедуп — алерт только при ПОВЫШЕНИИ зоны** (`rank(cur) > rank(base)`): `green→yellow`, `green→red`, `yellow→red`, и при `online→offline`. Деэскалация (`red→yellow`, `*→green`) и восстановление (`offline→online`) — **молча, recovery-сообщений НЕТ**, но **персистятся**: `zones`/`online` всегда обновляются до `cur` (в т.ч. при деэскалации в `green`), чтобы последующее повторное повышение снова зафиксировалось как эскалация и **снова алертнуло**. Пока сервер остаётся в той же повышенной зоне — база в БД равна текущей → повторных алертов НЕТ (дедуп по зоне, теперь через БД).

**Alert-on-first-elevated (выкат фичи):** так как миграция `0004` создаёт таблицу **пустой** (backfill не делается — [03-data-model.md](../../03-data-model.md#миграция-0004_create_notifier_state-концепт)), при первом после-деплойном опросе у каждого сервера `prev is None` → здоровый baseline. Серверы, находящиеся сейчас в повышенной зоне/offline, получают **ровно один** catch-up-алерт, после чего их зона персистится и дедуп работает. Это устраняет прежний компромисс «первая встреча — молча», из-за которого «горячие» серверы после рестарта не переалертивались ([TD-019](../../100-known-tech-debt.md)).

**Возврат offline→online (`переалертится`):** при возврате база для сравнения = `green` по всем метрикам (offline персистит `zones=None ≡ green`). Поэтому сервер, вернувшийся уже в нагрузке, **снова получит алерт** на yellow/red-метрики. Теперь это поведение **переживает рестарт**.

**Online, но метрики недоступны** (`InstanceMetrics.online == True`, но `metrics is None` — частичный/пустой валидный ответ Prometheus, [04-api.md](../../04-api.md#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down) случай «б»): зоны **не оцениваются** в этой итерации, `zone_*` сохраняются как `NULL` (для существующей строки — обновляются в `NULL`; `online` пишется `True`), алертов нет. Offline-алерт при этом не шлётся (сервер `up==1`). Когда метрики появятся в следующем опросе, отсутствующая база (`NULL`) трактуется как `green` → нагрузка относительно `green` переалертится; undefined-сравнения с `NULL` не возникает.

**Offline → сброс zones:** при уходе в offline `zone_cpu/ram/ssd` пишутся `NULL`; при offline метрики не оцениваются.

### Очистка и удаление серверов (нормативно)

- **Hard-delete сервера** → строка `notifier_server_state` снимается автоматически (`ON DELETE CASCADE`, [03-data-model.md](../../03-data-model.md#таблица-notifier_server_state)). Явной очистки в коде цикла нет.
- **Сервер временно покинул `list_online()`** (сменился `provision_status`, но не удалён) → в этой итерации не опрашивается, строка состояния **сохраняется** без изменений. Сброс базы дал бы ложный ре-алерт при возврате; сохранение = корректный дедуп (вернётся в той же зоне → тихо; вернётся выше → алерт). Notifier опрашивает только `provision_status == online`, поэтому «завис» строки безвреден и ограничен числом серверов.
- Recovery-сообщений для серверов по-прежнему нет (out of scope Этапа 1).

## Типы сообщений и формат (точно)

Метки метрик: `CPU` / `RAM` / `SSD`. Порядок строк стабильный — CPU, RAM, SSD. `n%` = `int(usage_percent)` (**округление ВНИЗ**, `usage_percent` уже округлён до 1 знака в маппинге). Имя сервера — в двойных кавычках. Текст — plain (без parse_mode/Markdown).

**1. 🟡 ПРЕДУПРЕЖДЕНИЕ** — метрики, эскалировавшие в жёлтую зону (`cur == yellow`):

```
🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡
Сервер "<name>"
IP <ip>

CPU: Нагрузка более 83%
RAM: Нагрузка более 81%
```

**2. 🔴 СРОЧНО (красная зона)** — метрики, эскалировавшие в красную зону (`cur == red`), тот же layout:

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Сервер "<name>"
IP <ip>

CPU: Нагрузка более 95%
```

**3. 🔴 СРОЧНО (offline)** — переход `online→offline`:

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Сервер "<name>"
IP <ip>

Сервер не доступен
```

**Классификация и количество сообщений за один опрос одного сервера:** эскалировавшие метрики группируются по их **новой** зоне: `cur == yellow` → в сообщение ПРЕДУПРЕЖДЕНИЕ; `cur == red` → в сообщение СРОЧНО. За одну итерацию для одного сервера возможны **до двух сообщений** (одно жёлтое + одно красное), каждое — только если соответствующий список метрик непуст. Offline-сообщение взаимоисключающе с зонными (при offline метрики не оцениваются). Каждая строка метрики: `<LABEL>: Нагрузка более <int(usage_percent)>%`.

## Сообщения AI-ключей

Помимо серверных алертов, тот же `TelegramClient` используется **отдельным** фоновым сервисом `AiKeyMonitorService` ([modules/ai-keys](../ai-keys/README.md), [ADR-010](../../adr/ADR-010-ai-key-monitor-vnutri-backend.md)) для уведомлений о валидности AI-ключей. **Важно:** это НЕ часть state-машины серверов из этого модуля — отдельный сервис, отдельное состояние (в БД `ai_keys.check_status`, переживает рестарт), отдельный интервал (`AI_KEY_CHECK_INTERVAL_SEC`). Общее — только `TelegramClient`, гейт `notifier_enabled` и семантика доставки at-least-once.

Формат (точно; plain-текст, имя ключа в кавычках, `<last4>` = `key_last4`):

**🔴 Ключ не работает** (переход `pending|working → error`):

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Ключ "<name>" ****<last4>
Ключ не работает: "<reason>"
```

**🟢 Ключ восстановлен** (переход `error → working`):

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Ключ "<name>" ****<last4>
Ключ снова работает
```

Полная спецификация переходов, маппинга статусов провайдера и правила `unknown` — [modules/ai-keys](../ai-keys/README.md#переходы-статуса-и-алерты-нормативно). Отправка гейтится тем же `notifier_enabled`; при отключённом Telegram монитор ключей всё равно работает и обновляет `check_status` для UI.

## Доставка в Telegram

Раздел применим и к серверным алертам, и к алертам AI-ключей (общий `TelegramClient`).

- HTTP `POST https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/sendMessage`, JSON `{chat_id, text}` (httpx, уже в зависимостях). Короткий таймаут (константа backend, рекомендация 10 с).
- **Best-effort с ограниченными ретраями на транзиентные ошибки, семантика at-least-once:** `TelegramClient.send_message` выполняет ограниченное число попыток (до 3) с короткими backoff-паузами (0.2/0.5 с) на транзиентных ошибках — `429`, `5xx`, таймаут, сетевые ошибки — в пределах фиксированного бюджета попыток (без агрессивного экспоненциального наращивания). Это **осознанный trade-off в пользу at-least-once**: для алертов потеря критического уведомления хуже редкого дубликата, поэтому ретрай после частичной доставки (например, ответ потерян на таймауте/5xx после фактической отправки) может привести к **редкому дублю алерта** — это приемлемо.
- **send_message не пробрасывает ошибки наружу (best-effort):** при исчерпании бюджета ретраев уведомление **пропускается** с warning-логом `notifier_telegram_send_failed` (без секретов — токен/`chat_id`/тело не логируются), цикл опроса **не падает**. `state` обновляется до `cur` независимо от результата отправки → пропущенный из-за устойчивого сбоя Telegram алерт не повторяется отдельно (повторная отправка возможна только при следующей эскалации).
- Токен/`chat_id` — секреты, только из env ([05-security.md](../../05-security.md)); в логи/ответы API не попадают.

## Backend — ориентиры реализации (нормативно по контракту, структура — на усмотрение)

1. **Настройки** (`config.py`): `telegram_bot_token: str = ""`, `telegram_chat_id: str = ""`, `notifier_poll_interval_sec: int = 60`; property `notifier_enabled = bool(telegram_bot_token and telegram_chat_id)`.
2. **Telegram-клиент** (`infra/`): тонкая обёртка над httpx (`send_message(text)`); не бросает наружу (ошибки логируются/возвращаются флагом).
3. **Персистентность состояния** (`models/` + `repositories/`): ORM-модель `notifier_server_state` ([03-data-model.md](../../03-data-model.md#таблица-notifier_server_state)) + репозиторий с двумя операциями — загрузка состояний по набору `server_id` (`dict[server_id -> ServerState]`) и UPSERT состояния (`ON CONFLICT (server_id) DO UPDATE`). Alembic-миграция `0004_create_notifier_state` (`down_revision = "0003_add_position"`, рабочий `downgrade()` = `DROP TABLE`).
4. **NotifierService** (`services/`): метод опроса (читает базу из БД → `evaluate` → UPSERT) и **чистая функция перехода** для тестируемости — `evaluate(prev: ServerState | None, im: InstanceMetrics) -> (new_state, alerts)`, где `prev is None` трактуется как здоровый baseline (`online=True`, `green×3`), `alerts` — список готовых сообщений (тип + текст). qa проверяет матрицу эскалаций (включая alert-on-first-elevated при `prev=None`) на чистой функции без сети/БД.
5. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` при `notifier_enabled`; отмена при shutdown.

## DoD

- [ ] Алерт **только** при повышении зоны (`green→yellow`, `green→red`, `yellow→red`) и при `online→offline`; деэскалация/восстановление — молча, но **персистятся**.
- [ ] Состояние персистится в `notifier_server_state` (per-server `online` + `zone_cpu/ram/ssd`), читается из БД каждую итерацию, переживает рестарт/деплой ([TD-019](../../100-known-tech-debt.md) закрыт); дедуп по зоне через БД — пока сервер в той же повышенной зоне, повторов нет.
- [ ] **Alert-on-first-elevated:** сервер без персистнутой строки (`prev is None`) трактуется как здоровый baseline (`online`, `green×3`); впервые увиденный уже в yellow/red/offline даёт **ровно один** catch-up-алерт, затем персист → дедуп. Миграция `0004` создаёт таблицу пустой (без backfill).
- [ ] Возврат `offline→online` обрабатывается по таблице переходов (база `green` → нагрузка переалертится), поведение переживает рестарт.
- [ ] `PrometheusUnavailable` → итерация пропущена, состояние в БД не тронуто (нет записи).
- [ ] Удаление сервера снимает строку состояния (`ON DELETE CASCADE`); временный выход из `list_online()` строку сохраняет.
- [ ] Формат всех трёх сообщений побайтово соответствует спецификации; `n%` = `int(usage_percent)`; зоны — из `usage_to_zone()` (пороги не дублируются).
- [ ] Нотификатор не запускается без `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID`; backend стартует штатно.
- [ ] Сбой Telegram/ошибка итерации не валит фоновую задачу.
- [ ] Coverage ≥90 % для функции перехода/классификации сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).

## Changelog

- 2026-06-30: спецификация создана (architect); решение об in-backend-нотификаторе — [ADR-009](../../adr/ADR-009-in-backend-notifier-vs-alertmanager.md); state in-memory — [TD-019](../../100-known-tech-debt.md).
- 2026-07-04: state-машина переведена на **персистентное состояние в БД** (`notifier_server_state`) + правило **alert-on-first-elevated** (отсутствующая база ≡ здоровый baseline `online`+`green×3`) — [ADR-014](../../adr/ADR-014-persist-notifier-state-alert-on-first-elevated.md); [TD-019](../../100-known-tech-debt.md) закрыт. Пороги зон и `usage_to_zone()` НЕ менялись.
