# Модуль `notifier` — Telegram-уведомления о нагрузке и доступности

Статус: `spec-ready` · Исполнитель: backend

## Scope

Фоновая asyncio-задача **внутри backend-процесса** (не отдельный сервис, не Alertmanager — [ADR-009](../../adr/ADR-009-in-backend-notifier-vs-alertmanager.md)), которая периодически опрашивает online-серверы через `MonitoringService` и шлёт в Telegram-группу сообщения **при эскалации** (повышение зоны нагрузки или потеря доступности) **и при восстановлении доступности** (`offline→online`, [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)). Пороги зон — те же, что в UI и backend ([thresholds](../../04-api.md#пороги-зон), `usage_to_zone()`), **переиспользуются без изменений**. Доступность в windowed-режиме нотификатора оценивается по **минимуму `up` за окно опроса** (`min_over_time`, ловит короткие провалы между опросами — [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)). Каждый отправленный серверный алерт пишется в **durable-лог** `notifier_alert_log` (факт отправки + доставка).

Out of scope (Этап 1): деэскалация зон, silencing/grouping, команды бота/интерактив, индивидуальные подписки, метрики самого бота, API-эндпоинт просмотра лога алертов, ретенция лога ([TD-027](../../100-known-tech-debt.md)). (Персистентность состояния нотификатора **входит в scope** — [TD-019](../../100-known-tech-debt.md) закрыт [ADR-014](../../adr/ADR-014-persist-notifier-state-alert-on-first-elevated.md); см. [«State-машина (персистентная)»](#state-машина-персистентная).) (**Recovery-сообщения для серверов теперь в scope** — [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md); ранее были out-of-scope.) (Recovery-сообщения **для AI-ключей** — в scope отдельного сервиса `AiKeyMonitorService`, см. [«Сообщения AI-ключей»](#сообщения-ai-ключей) и [modules/ai-keys](../ai-keys/README.md).)

## Опциональность (активация)

Нотификатор **активен только если заданы обе** переменные окружения `TELEGRAM_BOT_TOKEN` **и** `TELEGRAM_CHAT_ID` (непустые). Иначе фоновая задача **не запускается**, backend стартует штатно, в лог пишется `notifier_disabled` (info). Это не ошибка — бот опционален.

## Источники данных и переиспользование

- Реестр: `ServerRepository.list_online()` — серверы с `provision_status == online` (id, `name`, `ip`, property `instance` = `<ip>:<exporter_port>`). Контракт объявлен в [modules/servers](../servers/README.md#переиспользуемый-контракт-репозитория-и-модели-нормативно).
- Метрики: `MonitoringService.fetch_for_instances(instances, window_sec=NOTIFIER_METRIC_WINDOW_SEC)` → `dict[instance -> InstanceMetrics]`. Метод, типы `InstanceMetrics/ServerMetrics/Metric` и исключение `PrometheusUnavailable` объявлены в [modules/monitoring](../monitoring/README.md#переиспользуемый-внутренний-интерфейс-нормативно) (единый источник). `InstanceMetrics.metrics: ServerMetrics{cpu,ram,ssd: Metric{usage_percent, zone}}` либо `None`. **Нотификатор вызывает windowed-режим** (`window_sec` задан) — в нём оборачиваются **две группы**: CPU/RAM/SSD `usage_percent`/`zone` берутся как **максимум** за окно (`max_over_time`, [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md); см. [«Оценка зоны по max-over-window»](#оценка-зоны-по-max-over-window-нормативно)); `InstanceMetrics.online` берётся как **`min_over_time(up[окно]) == 1`** — сервер offline, если `up` падал в любой точке окна ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md); см. [«Windowed offline-детект»](#windowed-offline-детект-нормативно)). UI-карты вызывают тот же метод **без окна** (`window_sec=None`): и зоны, и `online` (`up_value == 1.0`) — **мгновенные**, не меняется.
- Зоны: `app.domain.thresholds.usage_to_zone()` (`<80 green`, `80..90 yellow`, `>90 red`). **Пороги не дублировать и не менять** — единый источник (там же).
- **Согласованность и нагрузка на Prometheus (точная семантика):** notifier вызывает тот же `MonitoringService` (та же реализация маппинга и порогов → бот трактует метрики идентично карточкам **по одним и тем же порогам**; отличается только окно — max-за-окно у бота vs мгновенное у карт, [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md)). Кэш ключуется по **набору instances и окну** (`(tuple(sorted(instances)), window_sec)`), а у бота и `window_sec` задан, и набор online-instances отличается от UI-агрегата (`GET /api/servers`, `window_sec=None`), поэтому **общего кэш-элемента с карточками нет** (различие уже и по набору, и по окну) — бот делает **собственные** запросы к Prometheus, защищённые своим коротким TTL-кэшем (по своему ключу) и **общим семафором конкуренции** `_MAX_CONCURRENT_QUERIES` (рекомендация 4). Одновременные исходящие PromQL ограничены общим семафором. Семантика кэша — [modules/monitoring](../monitoring/README.md#семантика-кэша-нормативно--важно-для-шаринга).

## Жизненный цикл задачи

- Запуск — в `lifespan` (`backend/app/main.py`), рядом с recovery-hook провижининга: создаётся `asyncio.Task` (только если нотификатор активен).
- Остановка — отмена задачи при shutdown (`task.cancel()` + ожидание; корректная обработка `CancelledError`).
- Цикл: бесконечный `while True`: одна итерация опроса → `asyncio.sleep(NOTIFIER_POLL_INTERVAL_SEC)`. Любое необработанное исключение внутри итерации **логируется и не валит задачу** (цикл продолжается).

## Итерация опроса (нормативно)

1. Открыть короткоживущую сессию БД (`get_sessionmaker()`), получить `list_online()` **и** загрузить персистнутое состояние `notifier_server_state` для этих серверов (`SELECT ... WHERE server_id IN (...)` → `dict[server_id -> ServerState]`), **закрыть сессию** (не держать открытой во время запроса к Prometheus). Снять снимок `{server_id -> (name, ip, instance)}`. **БД — источник истины состояния** (не in-memory dict, живущий между итерациями): база (`prev`) читается из БД каждую итерацию, поэтому рестарт/деплой не сбрасывает состояние ([TD-019](../../100-known-tech-debt.md) закрыт).
2. Собрать `instances` и вызвать `MonitoringService.fetch_for_instances(instances, window_sec=NOTIFIER_METRIC_WINDOW_SEC)` (**windowed-режим**: CPU/RAM/SSD зона по максимуму за окно — [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md); `online` по минимуму `up` за окно — [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md), [«Windowed offline-детект»](#windowed-offline-детект-нормативно)).
   - **`PrometheusUnavailable` → итерация пропускается целиком**: состояние в БД НЕ изменяется (нет записи), алерты не шлются, лог алертов не пишется, в лог `notifier_prometheus_unavailable` (warning). Переход к следующему `sleep`.
3. Для каждого online-сервера вычислить переходы чистой функцией `evaluate(prev, im)`, где `prev` — загруженное из БД состояние (или `None`, если строки нет). Отправить алерты (эскалация зоны, `online→offline`, `offline→online` recovery), **для каждой отправки запомнить** `(server_id, kind, message, delivered)` (результат `send_message`). Затем в **финальной короткой сессии** одним коммитом: **UPSERT** нового состояния в `notifier_server_state` (`INSERT ... ON CONFLICT (server_id) DO UPDATE SET online, zone_cpu, zone_ram, zone_ssd, updated_at = now()`) **и INSERT строк `notifier_alert_log`** для всех отправленных алертов ([«Durable-лог алертов»](#durable-лог-алертов-нормативно)). Персист состояния выполняется **всегда** после итерации по серверу, независимо от результата отправки в Telegram (best-effort доставка не влияет на состояние — см. [«Доставка в Telegram»](#доставка-в-telegram)); строка лога пишется на **каждый** отправленный алерт с фактическим `delivered`.
4. **Очистка не выполняется в коде цикла.** Серверы, исчезнувшие из `list_online()` (сменился `provision_status`, но не удалены), в этой итерации просто не опрашиваются; их строка состояния **сохраняется** (сброс базы при провижининг-флапе дал бы ложный ре-алерт при возврате). При hard-delete сервера строка `notifier_server_state` снимается автоматически через `ON DELETE CASCADE`; строки `notifier_alert_log` сохраняются (`ON DELETE SET NULL`) ([03-data-model.md](../../03-data-model.md#таблица-notifier_alert_log)).

## Оценка зоны по max-over-window (нормативно)

[ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md). Нотификатор оценивает зону CPU/RAM/SSD по **максимуму метрики за окно опроса**, а не по мгновенному значению. Причина: мгновенный сэмплинг раз в `NOTIFIER_POLL_INTERVAL_SEC` «просыпает» всплески короче интервала опроса (подтверждённый кейс: CPU-пик 98.6 % между опросами → red не сэмплировался → алерта не было). Взяв `max_over_time(<usage_expr>[окно])`, нотификатор видит пик, случившийся **в любой момент** окна.

- **Что меняется:** только источник `usage_percent`/`zone` для **CPU/RAM/SSD** на входе `evaluate()`. Пороги `usage_to_zone()`, формат сообщений, `evaluate()`, персист, дедуп, alert-on-first-elevated, offline↔online — **без изменений** (на вход подаётся max-за-окно зона вместо мгновенной).
- **Что НЕ меняется этим ADR:** `max_over_time` к доступности **не** применяется (offline-детект — отдельно, через `min_over_time`, [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md), [«Windowed offline-детект»](#windowed-offline-детект-нормативно)); `detail`/`uptime` — мгновенные; UI-карты продолжают показывать **мгновенное** значение (`window_sec=None`).
- **Окно** — `NOTIFIER_METRIC_WINDOW_SEC` (env, default **90 с**), нормативно **≥ `NOTIFIER_POLL_INTERVAL_SEC`**, чтобы соседние окна перекрывались и любой момент попадал хотя бы в одно окно опроса (окно = `poll_interval` + запас ~50 %). Если задано `< poll_interval` — backend поднимает окно до `poll_interval` при старте и пишет warning. Слишком широкое окно даёт «залипание» алерта (max держит пик всю длину окна, задерживая деэскалацию) — поэтому берётся `poll_interval + запас`, а не десятки минут. PromQL-обёртки — [02-promql.md](../monitoring/02-promql.md#notifier-max-over-window-зоны--min-over-window-offline).
- **Согласованность с ADR-014:** пик 99 % внутри окна → нотификатор видит red → эскалация `green→red` → **один** алерт (дедуп по персистнутой зоне). После спада max за следующее окно вернётся в green → **молчаливая** деэскалация; следующий пик снова алертит. Max-over-window усиливает наблюдение, механику ADR-014 не трогает.
- **Остаточная оговорка (принимается):** пики короче разрешения scrape/`rate` (~15 с / `rate[1m]` для CPU) сглаживаются усреднением внутри `rate`/scrape и могут не достичь порога. Мы сознательно алертим на **устойчивые ~минутные** пики (как этот 98.6 %), а не на микро-спайки — TD не заводится.

## Windowed offline-детект (нормативно)

[ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md). Симметрично max-over-window для зон: в **windowed-режиме** нотификатора доступность оценивается по **минимуму `up` за окно опроса**, а не по мгновенному значению. Причина: до ADR-018 `online` брался мгновенно (`up_value == 1.0`) раз в `poll_interval` — короткое падение **между** опросами терялось (подтверждённый кейс — «Фотобудка», ~4 ч offline без единого алерта; см. [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md#контекст)). Взяв `min_over_time(up[окно])`, нотификатор видит провал, случившийся **в любой момент** окна.

- **Что меняется:** только источник `InstanceMetrics.online` в windowed-режиме: `online = min_over_time(up{matcher}[окно]) == 1` — offline, если `up` был `0` в любой точке окна. PromQL — [02-promql.md](../monitoring/02-promql.md#online--offline-min-за-окно--только-notifier).
- **Что НЕ меняется:** пороги/`usage_to_zone()`, `evaluate()` (кроме recovery-ветки ниже), персист, дедуп, `uptime`/`detail` (мгновенные); UI-карты (`window_sec=None`) — `online` остаётся **мгновенным** (`up_value == 1.0`), не меняется.
- **Окно** — **то же `NOTIFIER_METRIC_WINDOW_SEC`** (нового env нет — [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md#2-выбор-окна--переиспользовать-notifier_metric_window_sec-нового-env-не-вводим)): инвариант `окно ≥ poll_interval` и кламп идентичны окну зон, отдельный env дал бы то же значение без функциональной разницы (NFR-1). Одно «окно наблюдения нотификатора» для зон (max) и доступности (min).
- **Trade-off «залипание recovery» (принимается):** `min_over_time` держит `online=False`, пока в окне остаётся хотя бы один `0`-сэмпл → recovery-алерт (ниже) задерживается на ≤ `окно` (~90 с) + один `poll_interval` после фактического возврата. Полезный побочный эффект — **дебаунс флаппинга** (сервер down/up/down не спамит recovery).
- **Остаточная оговорка (принимается, симметрична ADR-016):** провал `up` короче scrape (~15 с) может лечь между scrape-сэмплами и не дать `0` → сгладится. Ловим устойчивые (≥ scrape) падения.

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
| `online=False` | online + метрики недоступны (`metrics=None`) | **🟢 ВОССТАНОВЛЕНО** (recovery); зоны не оцениваются | `{online:True, zones:None}` |
| `online=False` | online + есть метрики | **🟢 ВОССТАНОВЛЕНО** (recovery), затем база = `green` по всем метрикам → высокая нагрузка **дополнительно** переалертится (zone-алерт). Порядок: recovery → warning/critical | `{online:True, zones:cur}` |
| `online=True` | online + есть метрики | сравнение зон с `prev.zones`, алерт при эскалации | `{online:True, zones:cur}` |

**Recovery (`offline→online`) — только при явном `prev.online == False`** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)). Отсутствие строки (`prev is None`) ≡ здоровый baseline `online=True` → сервер, впервые увиденный online, recovery **НЕ** даёт (ложного «снова в сети» на первой встрече нет). Recovery определяется по факту `up` **независимо от наличия метрик**: если сервер вернулся элевированным — сначала `recovered`, затем зонный алерт (сравнение с `green`-базой). Дедуп — через персист: после recovery `online=True` записывается → следующий опрос видит `base.online=True` → повторного recovery нет.

**Анти-спам / дедуп — алерт при ПОВЫШЕНИИ зоны** (`rank(cur) > rank(base)`): `green→yellow`, `green→red`, `yellow→red`; при `online→offline` (offline-алерт); при `offline→online` (**recovery-алерт**, [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)). Деэскалация зон (`red→yellow`, `*→green`) — **молча**, но **персистится**: `zones`/`online` всегда обновляются до `cur` (в т.ч. при деэскалации в `green`), чтобы последующее повторное повышение снова зафиксировалось как эскалация и **снова алертнуло**. Пока сервер остаётся в той же повышенной зоне / том же offline — база в БД равна текущей → повторных алертов НЕТ (дедуп через БД).

**Alert-on-first-elevated (выкат фичи):** так как миграция `0004` создаёт таблицу **пустой** (backfill не делается — [03-data-model.md](../../03-data-model.md#миграция-0004_create_notifier_state-концепт)), при первом после-деплойном опросе у каждого сервера `prev is None` → здоровый baseline. Серверы, находящиеся сейчас в повышенной зоне/offline, получают **ровно один** catch-up-алерт, после чего их зона персистится и дедуп работает. Это устраняет прежний компромисс «первая встреча — молча», из-за которого «горячие» серверы после рестарта не переалертивались ([TD-019](../../100-known-tech-debt.md)).

**Возврат offline→online (recovery + `переалертится`):** переход шлёт **🟢 ВОССТАНОВЛЕНО** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)). Дополнительно база для зон = `green` по всем метрикам (offline персистит `zones=None ≡ green`), поэтому сервер, вернувшийся уже в нагрузке, **сверх recovery получит зонный алерт** на yellow/red-метрики (порядок: recovery → warning/critical). Поведение **переживает рестарт**.

**Online, но метрики недоступны** (`InstanceMetrics.online == True`, но `metrics is None` — частичный/пустой валидный ответ Prometheus, [04-api.md](../../04-api.md#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down) случай «б»): зоны **не оцениваются** в этой итерации, `zone_*` сохраняются как `NULL` (для существующей строки — обновляются в `NULL`; `online` пишется `True`), зонных алертов нет. Offline-алерт при этом не шлётся (сервер `up==1`). **Recovery-исключение:** если `prev.online == False` (сервер вернулся, но метрики ещё не подъехали) — **🟢 ВОССТАНОВЛЕНО** всё равно шлётся (recovery определяется по факту `up`, не по метрикам); зоны при этом не оцениваются. Когда метрики появятся в следующем опросе, отсутствующая база (`NULL`) трактуется как `green` → нагрузка относительно `green` переалертится; undefined-сравнения с `NULL` не возникает.

**Offline → сброс zones:** при уходе в offline `zone_cpu/ram/ssd` пишутся `NULL`; при offline метрики не оцениваются.

### Очистка и удаление серверов (нормативно)

- **Hard-delete сервера** → строка `notifier_server_state` снимается автоматически (`ON DELETE CASCADE`, [03-data-model.md](../../03-data-model.md#таблица-notifier_server_state)). Строки `notifier_alert_log`, наоборот, **сохраняются** — `server_id` обнуляется (`ON DELETE SET NULL`, [03-data-model.md](../../03-data-model.md#таблица-notifier_alert_log)): durable-лог алертов переживает удаление сервера. Явной очистки в коде цикла нет.
- **Сервер временно покинул `list_online()`** (сменился `provision_status`, но не удалён) → в этой итерации не опрашивается, строка состояния **сохраняется** без изменений. Сброс базы дал бы ложный ре-алерт при возврате; сохранение = корректный дедуп (вернётся в той же зоне → тихо; вернётся выше → алерт). Notifier опрашивает только `provision_status == online`, поэтому «завис» строки безвреден и ограничен числом серверов.
- Recovery-сообщения для серверов (`offline→online`) теперь **в scope** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)).

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

**4. 🟢 ВОССТАНОВЛЕНО (recovery)** — переход `offline→online` ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)); `build_recovered(name, ip)`, стиль как у recovery AI-ключей:

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Сервер "<name>"
IP <ip>

Сервер снова в сети
```

**Классификация и количество сообщений за один опрос одного сервера:** эскалировавшие метрики группируются по их **новой** зоне: `cur == yellow` → в сообщение ПРЕДУПРЕЖДЕНИЕ; `cur == red` → в сообщение СРОЧНО. Offline-сообщение взаимоисключающе с зонными (при offline метрики не оцениваются). Recovery-сообщение (`offline→online`) шлётся при возврате и **может сопровождаться** зонными (если сервер вернулся элевированным) — порядок: recovery → warning → critical. Итого за одну итерацию для одного сервера: `offline` (одно, изолированно) **либо** `recovered` (0–1) + `warning` (0–1) + `critical` (0–1) — каждое сообщение только если его список/условие непусты. Каждая строка метрики: `<LABEL>: Нагрузка более <int(usage_percent)>%`.

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

## Durable-лог алертов (нормативно)

[ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md). Каждый **отправленный серверный алерт** пишется строкой в `notifier_alert_log` ([03-data-model.md](../../03-data-model.md#таблица-notifier_alert_log)) — чтобы факт отправки и результат доставки были проверяемы независимо от ротации stdout-логов (мотивация — «Фотобудка»: нельзя было проверить, слался ли offline-алерт).

- **Одна строка на один отправленный алерт** = один вызов `TelegramClient.send_message`. `send_message` инкапсулирует до 3 HTTP-ретраев и возвращает `bool`; логируется **логическая** попытка (один `Alert`), не каждый HTTP-ретрай. `delivered` = возвращённый флаг.
- **Поля строки:** `server_id` (сервер алерта), `kind` (`offline`/`recovered`/`warning`/`critical` — значение `AlertKind`), `message` (отправленный plain-текст — содержит имя/IP, **без секретов**: токен/`chat_id`/URL в текст не входят), `delivered`, `created_at`.
- **Запись — в финальной короткой сессии итерации**, одним коммитом вместе с UPSERT состояния (собрать список за проход отправок → bulk-insert). Не держать сессию открытой во время отправок в Telegram.
- **Гейт:** лог пишется только когда нотификатор активен (алерты вообще формируются лишь при `notifier_enabled`). Неуспешная доставка (`delivered=False`) **тоже логируется** — в этом и смысл (доказать попытку).
- **Scope — только серверные алерты.** Алерты AI-ключей (`AiKeyMonitorService`) в этот лог **не** пишутся ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md); возможный follow-up — [TD-027](../../100-known-tech-debt.md)).
- **Ретенция** — не реализуется на Этапе 1 ([TD-027](../../100-known-tech-debt.md)). **API-эндпоинта просмотра лога нет** ([04-api.md](../../04-api.md) не затрагивается).

## Backend — ориентиры реализации (нормативно по контракту, структура — на усмотрение)

1. **Настройки** (`config.py`): `telegram_bot_token: str = ""`, `telegram_chat_id: str = ""`, `notifier_poll_interval_sec: int = 60`, `notifier_metric_window_sec: int = 90` (окно max-over-window, [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md); нормативно `≥ notifier_poll_interval_sec` — при `<` эффективное окно поднимается до `poll_interval` с warning-логом); property `notifier_enabled = bool(telegram_bot_token and telegram_chat_id)`.
2. **Telegram-клиент** (`infra/`): тонкая обёртка над httpx (`send_message(text) -> bool`); не бросает наружу (ошибки логируются, возвращается флаг успеха → это и есть `delivered` для лога).
3. **Персистентность состояния** (`models/` + `repositories/`): ORM-модель `notifier_server_state` ([03-data-model.md](../../03-data-model.md#таблица-notifier_server_state)) + репозиторий с двумя операциями — загрузка состояний по набору `server_id` (`dict[server_id -> ServerState]`) и UPSERT состояния (`ON CONFLICT (server_id) DO UPDATE`). Alembic-миграция `0004_create_notifier_state` (уже есть).
3a. **Лог алертов** (`models/` + `repositories/`): ORM-модель `notifier_alert_log` ([03-data-model.md](../../03-data-model.md#таблица-notifier_alert_log)) + репозиторий с операцией bulk-insert строк лога `(server_id, kind, message, delivered)`. Alembic-миграция `0005_create_notifier_alert_log` (`down_revision = "0004_create_notifier_state"` — текущая голова; рабочий `downgrade()` = `DROP TABLE`, [03-data-model.md](../../03-data-model.md#миграция-0005_create_notifier_alert_log-концепт)).
4. **NotifierService** (`services/`): метод опроса (читает базу из БД → `evaluate` → отправка алертов + сбор `(server_id, kind, message, delivered)` → финальная сессия: UPSERT состояния + bulk-insert лога) и **чистая функция перехода** для тестируемости — `evaluate(prev: ServerState | None, im: InstanceMetrics) -> (new_state, alerts)`, где `prev is None` трактуется как здоровый baseline (`online=True`, `green×3`), `alerts` — список готовых сообщений (`kind` + текст, включая новый `kind="recovered"` при `prev.online=False → im.online=True`). qa проверяет матрицу переходов (alert-on-first-elevated при `prev=None`; recovery при `prev.online=False`; recovery+элевированный возврат; recovery НЕ шлётся при `prev=None`) на чистой функции без сети/БД.
5. **Domain** (`domain/notifications.py`): добавить `build_recovered(name, ip)` (🟢 ВОССТАНОВЛЕНО / «Сервер снова в сети»); расширить `AlertKind` значением `recovered`.
6. **Monitoring** (`services/monitoring_service.py`): в windowed-режиме (`window_sec` задан) `online = min_over_time(up{matcher}[window_sec s]) == 1` (прямой range-vector, без subquery); мгновенный режим (`window_sec=None`) не менять. PromQL — [02-promql.md](../monitoring/02-promql.md#online--offline-min-за-окно--только-notifier).
7. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` при `notifier_enabled`; отмена при shutdown.

## DoD

- [ ] Зона CPU/RAM/SSD оценивается по **max-over-window** (`fetch_for_instances(..., window_sec=NOTIFIER_METRIC_WINDOW_SEC)`, [ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md)); `detail`/`uptime` — мгновенные; UI-карты (`window_sec=None`) не меняются; пороги `usage_to_zone()` не меняются. Окно `≥ poll_interval` (при `<` — поднимается до `poll_interval` + warning).
- [ ] **Windowed offline-детект** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)): в windowed-режиме `online = min_over_time(up[окно]) == 1` (offline при провале в любой точке окна); окно = то же `NOTIFIER_METRIC_WINDOW_SEC` (нового env нет); UI-карты (`window_sec=None`) — `online` остаётся мгновенным (`up_value == 1.0`).
- [ ] **Recovery-уведомление** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)): при `prev.online=False → im.online=True` шлётся 🟢 ВОССТАНОВЛЕНО (`build_recovered`, `AlertKind="recovered"`); НЕ шлётся при `prev is None` (baseline online); при элевированном возврате — recovery + зонный алерт (порядок recovery→warning/critical); дедуп через персист.
- [ ] **Durable-лог** ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)): каждый отправленный серверный алерт пишется строкой `notifier_alert_log` (`server_id`, `kind`, `message`, `delivered`, `created_at`) в финальной сессии итерации; `delivered` = результат `send_message`; секретов в `message` нет; миграция `0005` (`down_revision = 0004`, рабочий `downgrade`).
- [ ] Алерт при повышении зоны (`green→yellow`, `green→red`, `yellow→red`), при `online→offline`, при `offline→online` (recovery); деэскалация зон — молча, но **персистится**.
- [ ] Состояние персистится в `notifier_server_state` (per-server `online` + `zone_cpu/ram/ssd`), читается из БД каждую итерацию, переживает рестарт/деплой ([TD-019](../../100-known-tech-debt.md) закрыт); дедуп по зоне через БД — пока сервер в той же повышенной зоне, повторов нет.
- [ ] **Alert-on-first-elevated:** сервер без персистнутой строки (`prev is None`) трактуется как здоровый baseline (`online`, `green×3`); впервые увиденный уже в yellow/red/offline даёт **ровно один** catch-up-алерт, затем персист → дедуп. Миграция `0004` создаёт таблицу пустой (без backfill).
- [ ] Возврат `offline→online` обрабатывается по таблице переходов (база `green` → нагрузка переалертится), поведение переживает рестарт.
- [ ] `PrometheusUnavailable` → итерация пропущена, состояние в БД не тронуто (нет записи).
- [ ] Удаление сервера снимает строку состояния (`ON DELETE CASCADE`); временный выход из `list_online()` строку сохраняет.
- [ ] Формат всех **четырёх** сообщений (warning/critical/offline/recovered) побайтово соответствует спецификации; `n%` = `int(usage_percent)`; зоны — из `usage_to_zone()` (пороги не дублируются).
- [ ] Нотификатор не запускается без `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID`; backend стартует штатно.
- [ ] Сбой Telegram/ошибка итерации не валит фоновую задачу.
- [ ] Coverage ≥90 % для функции перехода/классификации сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).

## Changelog

- 2026-06-30: спецификация создана (architect); решение об in-backend-нотификаторе — [ADR-009](../../adr/ADR-009-in-backend-notifier-vs-alertmanager.md); state in-memory — [TD-019](../../100-known-tech-debt.md).
- 2026-07-04: state-машина переведена на **персистентное состояние в БД** (`notifier_server_state`) + правило **alert-on-first-elevated** (отсутствующая база ≡ здоровый baseline `online`+`green×3`) — [ADR-014](../../adr/ADR-014-persist-notifier-state-alert-on-first-elevated.md); [TD-019](../../100-known-tech-debt.md) закрыт. Пороги зон и `usage_to_zone()` НЕ менялись.
- 2026-07-06: оценка зоны CPU/RAM/SSD переведена на **max-over-window** (`fetch_for_instances(..., window_sec=NOTIFIER_METRIC_WINDOW_SEC)`, окно default 90 с ≥ poll_interval) — ловит транзиентные всплески между опросами ([ADR-016](../../adr/ADR-016-notifier-max-over-window-zone.md)). `evaluate()`/персист/дедуп/ADR-014, offline-детект (мгновенный `up`), UI-карты (мгновенное значение) и пороги `usage_to_zone()` НЕ менялись.
- 2026-07-07: три улучшения ([ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)): (1) **windowed offline-детект** — в windowed-режиме `online = min_over_time(up[окно]) == 1` (то же `NOTIFIER_METRIC_WINDOW_SEC`, нового env нет), ловит короткие провалы `up` между опросами; (2) **recovery-уведомление** `offline→online` (🟢 ВОССТАНОВЛЕНО, `build_recovered`, `AlertKind="recovered"`, дедуп через персист, НЕ шлётся при `prev=None`); (3) **durable-лог** отправленных серверных алертов (`notifier_alert_log`, миграция `0005`, `ON DELETE SET NULL`). UI-карты (мгновенный `up`/значения) и пороги `usage_to_zone()` НЕ менялись. Ретенция лога и AI-key-логирование — [TD-027](../../100-known-tech-debt.md).
