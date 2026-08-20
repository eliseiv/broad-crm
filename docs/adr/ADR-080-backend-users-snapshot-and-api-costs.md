# ADR-080 — Страница «Юзеры бэков»: Postgres-снимок вместо live fan-out, скрытие бэков без Admin API Key, блок «Расходы API»

- **Статус:** **`implemented`** (сверка состава 2026-08-19: миграция `backend/alembic/versions/0040_backend_users_snapshot.py` (`revision="0040_backend_users_snapshot"`, `down_revision="0039_users_full_name_telegram"`; колонка `revenue_supported` — `:62`); `backend/app/models/backend_user_snapshot.py:76` (`revenue_supported: Mapped[bool | None]`); `backend/app/repositories/backend_user_snapshot_repository.py`; `backend/app/services/backend_users_snapshot_service.py`; регистрация воркера — `backend/app/main.py:169-173` (+ отмена задачи `:229-231`); `backend/app/config.py:162` / `:166` — `backend_users_snapshot_interval_sec=900` / `backend_users_snapshot_revenue_batch=2000`; `backend/app/schemas/backend_user.py:93-94` — `snapshot_at` / `api_costs`; регресс-гейт `backend/tests/integration/test_backend_users_keyless_hidden.py` **существует**. **Прогоны — по измерениям `qa`/reviewer, architect'ом не перепроверялись:** backend 1854 passed, frontend зелёный)
- **Дата:** 2026-08-19
- **Контекст-модули:** [backend-users](../modules/backend-users/README.md), [backend-economics](../modules/backend-economics/README.md), [backends](../modules/backends/README.md)
- **Пересматривает:** [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3 (**«CRM — прокси без хранилища» — отменено для этой страницы**) и §4 (семантика `errors[]`); [ADR-072](ADR-072-crm-admin-api-v11-economics.md) §3 (подтверждение §3 ADR-069 — сужено до модуля [backend-economics](../modules/backend-economics/README.md))
- **Соответствует:** [ADR-006](ADR-006-async-provisioning-bez-brokera.md) / NFR-1 — фоновая работа делается **asyncio-воркером в процессе backend**, брокер не вводится
- **Миграция:** `0040_backend_users_snapshot`

## Context

**(а) Цена live fan-out.** Каждый заход на `/backend-users` выполняет конкурентный fan-out по всем бэкам с admin-ключом: постраничное дочитывание `GET {P}/users` до окна merge (≤ 1000) плюс `GET {P}/stats` на каждый источник (`backend/app/services/backend_user_service.py`, сверка 2026-08-19). Кэша нет ни на одном уровне. На бэке с сотнями тысяч пользователей это десятки upstream-запросов на один рендер страницы, а глубокая пагинация дорожает линейно.

**(б) Бэки без ключа занимают `errors[]`.** [ADR-069](ADR-069-backend-users-page-admin-contract.md) §4 и прод-инцидент `selquro` привели к тому, что бэк реестра **без** admin-ключа попадает в `errors[]` с сообщением «Admin API Key не задан в CRM — бэк НЕ опрошен» (`backend_user_service.py:63`, `:111-116`; резолвер `BackendAdminSourceResolver.list_split()` — `backend/app/services/backend_admin_source.py`, сверка 2026-08-19). Владелец держит в реестре бэки, у которых Admin API нет и не планируется, и постоянная жёлтая плашка на странице обесценила предупреждение: реальный сбой источника от штатной конфигурации визуально неотличим.

**(в) Расходов по провайдерам нет ни в списке, ни в сводке.** Обследование контракта (сверка `backend/app/schemas/backend_user.py` 2026-08-19): элемент `GET {P}/users` разбивки не несёт; `GET {P}/stats` — тоже (`users_total`, `paid_users`, `payments_sum_usd`). **Единственный** источник разбивки — `revenue.providers: dict[str, float]` в карточке `GET {P}/users/{id}` (`BackendUserRevenue`), lifetime-агрегат на пользователя. Альтернатива — краулинг `GET {P}/users/{id}/requests` по всем пользователям (как одноразовый скрипт `.github/scripts/claude_request_cost_report.py`) — сотни тысяч запросов на цикл.

Канон фонового воркера в проекте уже есть: `BackendMonitorService.run()` — «итерация → `asyncio.sleep(interval)`, исключение итерации логируется и не валит задачу» (`backend/app/services/backend_monitor_service.py:266-278`), регистрация — `asyncio.create_task(...)` в `lifespan` (`backend/app/main.py:157-162`). Воркеров по этому образцу в `lifespan` уже восемь.

## Decision

### 1. Бэки без Admin API Key **скрываются**, `errors[]` означает только реальный сбой

Отбор делает **backend**, не фронт.

- `BackendUserService._resolve_sources` переходит на `BackendAdminSourceResolver.list_with_admin_key()`; генерация `errors[]` из `unqueried` и константа `_ADMIN_KEY_NOT_SET` удаляются.
- **Нормативная семантика (нормативно):** элемент `errors[]` ⇔ «источник **опрашивался** и **не ответил** (или ответил не по контракту)». Бэка без ключа в `errors[]` **нет никогда**.
- Режим **одного** бэка не меняется: явный `backend_id` без ключа → `409 backend_admin_key_not_set` (это осознанное действие оператора, а не фоновая конфигурация).
- Прод-инцидент `selquro` остаётся закрытым **другим** средством: фильтр приложений на странице уже строится по `has_admin_api_key`, а пустое состояние прямо говорит «подключите бэк с Admin API Key». Оператор, ищущий пользователя, видит, какие приложения вообще участвуют в поиске — то есть исходная неотличимость «ничего не найдено» ↔ «бэк не опрашивался» устраняется составом фильтра, а не жёлтой плашкой.
- `BackendAdminSourceResolver.list_split()` остаётся в резолвере, только если у него сохраняется потребитель; иначе — упрощается до `list_with_admin_key()`. Это решение исполнителя, а не нормы.
- Регресс-гейт **инвертируется**: `tests/integration/test_backend_users_unqueried_sources.py` переписывается в `test_backend_users_keyless_hidden.py` (`errors == []` при наличии бэка без ключа). Файл-гейт **обязан остаться** — иначе возврат старой ветки пройдёт молча.

### 2. Postgres-снимок + фоновый воркер раз в 900 с — [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3 для этой страницы **отменён**

> **Что именно отменено.** [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3 («CRM — прокси без хранилища; собственных таблиц/миграций нет») и его подтверждение в [ADR-072](ADR-072-crm-admin-api-v11-economics.md) §3 — **более не действуют для модуля [backend-users](../modules/backend-users/README.md)**. Для модуля [backend-economics](../modules/backend-economics/README.md) норма **остаётся в силе**: продукты, тарифы и себестоимость **не** копируются в БД CRM. Отмена узкая и намеренная.

**Почему именно так.** Отклонение [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3 было мотивировано «дублированием источника истины, устареванием, объёмом» и явно допускало возврат «при необходимости отчётности» — без заведения TD. Необходимость наступила: страница стала операционным инструментом, а не разовым просмотром. Снимок **не становится источником истины**: он read-only-зеркало с явной меткой свежести в UI, а все точечные и все пишущие пути остаются live (§4).

**Redis отвергнут** — [ADR-006](ADR-006-async-provisioning-bez-brokera.md)/NFR-1: брокер/внешний кэш в стек не вводится; Postgres уже есть, снимок переживает рестарт, а фоновая задача — тот же asyncio-паттерн, что у восьми существующих воркеров.

**Миграция `0040_backend_users_snapshot`** (`revision = "0040_backend_users_snapshot"` — **27 символов** ≤ `VARCHAR(32)`; `down_revision = "0039_users_full_name_telegram"`; `downgrade()` — `DROP TABLE backend_user_snapshots; DROP TABLE backend_user_snapshot_sources;`; полный концепт — [03-data-model.md](../03-data-model.md#миграция-0040_backend_users_snapshot-концепт-adr-080)), две таблицы:

```sql
CREATE TABLE backend_user_snapshot_sources (          -- одна строка на бэк с ключом
    backend_id UUID PRIMARY KEY REFERENCES backends(id) ON DELETE CASCADE,
    refreshed_at TIMESTAMPTZ NULL,                    -- конец последнего успешного цикла
    error_message TEXT NULL, failed_at TIMESTAMPTZ NULL,
    stats_users_total INTEGER NOT NULL DEFAULT 0,
    stats_paid_users INTEGER NOT NULL DEFAULT 0,
    stats_payments_sum_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    api_costs JSONB NOT NULL DEFAULT '{}',            -- {"openai":..,"anthropic":..,"fal":..,"other":..}
    revenue_backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
    revenue_supported BOOLEAN NULL,                   -- отдаёт ли бэк блок revenue; NULL — ни одной карточки не добрано
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE backend_user_snapshots (                 -- зеркало BackendUserItem + экономика
    backend_id UUID NOT NULL REFERENCES backends(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL, external_id TEXT NULL,
    is_paid BOOLEAN, payments_count INT, renewals_count INT, tokens FLOAT8,
    subscription_active BOOLEAN, subscription_expires_at TIMESTAMPTZ,
    plan_id TEXT NULL, registered_at TIMESTAMPTZ NOT NULL,
    api_cost_usd FLOAT8 NULL, api_cost_providers JSONB NULL,  -- из GET /users/{id}.revenue
    revenue_refreshed_at TIMESTAMPTZ NULL,
    PRIMARY KEY (backend_id, user_id)
);
-- индексы: (registered_at DESC), (backend_id, registered_at DESC), (user_id), (external_id)
```

Новые файлы: `app/models/backend_user_snapshot.py`, `app/repositories/backend_user_snapshot_repository.py`, `app/services/backend_users_snapshot_service.py`.

**Воркер `BackendUsersSnapshotService`** — канон `BackendMonitorService`: немедленный `refresh_once()` при старте, затем `while True: refresh_once(); await asyncio.sleep(interval)`; исключение итерации логируется и задачу не валит. Регистрация — `asyncio.create_task` в `lifespan` (`app/main.py`), рядом с существующими мониторами. Fan-out по бэкам с ключом, **`Semaphore(BACKEND_USERS_SNAPSHOT_CONCURRENCY)` — по умолчанию 2** (снижено с 5 хотфиксом 2026-08-20, см. амендмент ниже). Алгоритм на бэк:

1. Загрузить fingerprints снимка одним `SELECT` (`user_id → (tokens, payments_count, renewals_count, is_paid, subscription_active, subscription_expires_at, plan_id)`).
2. **Полный** постраничный обход `GET {P}/users?limit=100` — **без** ограничения `_MAX_WINDOW` (оно защищало интерактивный запрос, здесь запрос фоновый). **Пишутся только новые и изменившиеся строки** (батч-upsert `ON CONFLICT (backend_id, user_id) DO UPDATE`); множество изменившихся = **dirty-set**.
3. `DELETE` строк снимка, отсутствующих в источнике, — **только при полностью успешном обходе**. Оборванный обход снимок не прореживает. **«Полностью успешный» = обход дошёл до конца источника (`len(items) < limit`)**; страница-повтор и упор в `_MAX_PAGES` — **отказ** (флаг `complete`, см. амендмент ниже п.4).
4. `GET {P}/stats` → `stats_*` строки источника.
5. Экономика по dirty-set + backfill-квота (§5); по **первой успешно добранной карточке цикла** выставляется `revenue_supported` (§5).
6. Пересчёт `api_costs` источника: `SUM` по `jsonb_each_text(api_cost_providers)` с **нормализацией ключей** (§5); `refreshed_at = now()`, `error_message = NULL`, `failed_at = NULL`.
7. **Любое исключение** → прошлый снимок **не трогается**, в строку источника пишутся `error_message` / `failed_at`. Устаревшие данные лучше пустого экрана; их возраст виден оператору из `snapshot_at`.
   > ⚠️ **[Q-BU-2](../99-open-questions.md) — сужен после хотфикса 2026-08-20.** Backoff на `429`/`5xx` и `break` при бесполезной странице **реализованы** (амендмент выше). Открытым остаётся одно: `refreshed_at` проставляется **только при полностью успешном** обходе, поэтому бэк, чей обход систематически не доходит до конца, даёт вечное «Снимок формируется…» при **уже накопленных** строках. Кандидат — признак «обход не завершён» в строке источника; по умолчанию **не вводится**.

> **Амендмент «бережность к источнику» (прод-инцидент 2026-08-20, хотфикс; сверка кода `backend/app/services/backend_users_snapshot_service.py` 2026-08-20).**
> На проде воркер **выбивал источники** — бэки отвечали `429`/`500` под его же нагрузкой: он один ходит в бэк тысячами запросов подряд и сам доводит его до rate-limit. Четыре решения:
>
> 1. **Exponential backoff на `429`/`5xx`** (`_with_retry`, все upstream-вызовы цикла): до `BACKEND_USERS_SNAPSHOT_RETRY_ATTEMPTS` (**5**) попыток; задержка — **числовой `Retry-After` источника, если он есть** (уважается как есть, но не дольше потолка `min(retry_after, cap)`), иначе `base * 2^attempt` с потолком `cap` и **джиттером** (итог — от 50 % до 100 % окна). Джиттер обязателен: без него бэки-соседи, упавшие в rate-limit одновременно, вернулись бы к нему тоже одновременно. **Не ретраятся** `401`/`404`/`400`/`422` и транспортные ошибки — повтор там бессмысленен. **Исчерпание попыток контракта отказа НЕ меняет:** наружу уходит та же ошибка ⇒ цикл бэка падает, пишутся `error_message`/`failed_at`, снимок прошлого цикла **цел** (§2 п.7 в силе).
> 2. **Троттлинг** между страницами обхода и карточками добора — `BACKEND_USERS_SNAPSHOT_PAGE_DELAY_SEC` (**0.3 с**; `0` выключает).
> 3. **Конкурентность fan-out снижена с 5 до `BACKEND_USERS_SNAPSHOT_CONCURRENCY` (по умолчанию 2)** — фоновой задаче спешить некуда, а пять параллельных обходов и создавали пик.
> 4. **`break` при странице без единого НОВОГО `user_id`** — закрывает холостой обход при сбитой пагинации источника (`Q-BU-2` вариант «а»); признак — **бесполезность страницы**, а не счётчик. Событие — `backend_users_snapshot_walk_stalled`.
>    > ⛔ **Такой `break` — НЕПОЛНЫЙ обход, то есть ОТКАЗ цикла, а не его успешное завершение** (critical, воспроизведён `backend-reviewer` 2026-08-20; исправлено — сверка `backend_users_snapshot_service.py`: `if not result.complete: raise backend_admin_unavailable(result.incomplete_reason)` **до** шага `DELETE`). Источник, повторяющий окно, отдаёт заведомо **неполный** `tracked_users`; если бы цикл считался успешным, разность `known − tracked` вымела бы из снимка **всех, до кого обход не добрался**, а строка источника получила бы `refreshed_at` без единого признака беды. Поэтому при обрыве: `delete_rows` **пропускается**, цикл пишется через `_record_failure` (`error_message` — «Источник повторяет страницу — обход прерван», `failed_at`), **снимок прошлого цикла цел** (§2 п.7), причина видна оператору в `errors[]`.
>    >
>    > **Штатное завершение обхода — ТОЛЬКО `len(items) < limit`** (и пустая первая страница). Ни `break` по бесполезной странице, ни упор в `_MAX_PAGES` штатными **не являются** — оба ведут в отказ цикла. Инвариант §2 п.3 «`DELETE` отсутствующих — только при полностью успешном обходе» тем самым **усилен, а не ослаблен**: он теперь машинно защищён флагом `complete`, а не дисциплиной вызывающего.
>
> ⚠️ **Ретраи существуют ТОЛЬКО в воркере.** Интерактивные пути (`/users/{id}`, `/payments`, `/requests`, мутации) повторов **не получают**: там ждёт человек, и лишние 30 с хуже честной ошибки.
> ⚠️ **Паузы берутся только при ЗАКРЫТОЙ сессии БД** (обход и добор идут вообще без открытой сессии). Пауза с открытой сессией держала бы транзакцию `idle in transaction` на все секунды backoff'а и выбирала бы пул — против канона коротких сессий и прямо в сторону [TD-088](../100-known-tech-debt.md).
> **Внешний контракт не изменился:** новый подкласс `BackendAdminUpstreamStatus` (`upstream_status`, `retry_after_sec`, `backend/app/errors.py`) сохраняет статус источника **машинно**, но наружу по-прежнему отдаётся `502` с прежними кодами.

**Интервал — 900 с** (`BACKEND_USERS_SNAPSHOT_INTERVAL_SEC`, [07-deployment.md § Переменные окружения](../07-deployment.md#переменные-окружения)). `sleep` **после** завершения итерации (а не фиксированный тик) — так циклы не накладываются, даже если обход крупного бэка занял больше интервала.

### 3. Read-path списка — из снимка; сигнатура эндпоинта не меняется

`GET /api/backend-users` (query, коды, форма ответа) сохраняется; меняется источник данных.

- **`items`/`total`** — один SQL с `JOIN backends` (для `backend_code`/`backend_name`): `WHERE` по `backend_id`, периоду (`registered_at`), `is_paid`, поиску (`user_id ILIKE :q OR external_id ILIKE :q`); **`ORDER BY registered_at DESC, backend_id, user_id`** — tie-break обязателен, иначе `LIMIT/OFFSET` даёт нестабильные страницы; `LIMIT/OFFSET` + `COUNT`.
  - **Окно merge ≤ 1000 упраздняется**: оно было ценой merge-пагинации по нескольким источникам ([ADR-069](ADR-069-backend-users-page-admin-contract.md) §4). В снимке сортировка выполняется одним индексируемым `ORDER BY`, глубина ничем не ограничена.
  - **Паритет поиска (нормативно):** поиск снимка — подстрочный, регистронезависимый, **по `user_id` и `external_id`** — ровно тот набор полей, по которому искали у бэка. Расширять его на ФИО/почту нельзя (в снимке их нет), сужать — тоже. При деградации на больших объёмах — `pg_trgm`-индекс, не смена семантики.
- **`stats`** — **без периода**: суммы `stats_*` по строкам источников (`cr_percent` считает CRM, как и сейчас). **С периодом (`date_from`/`date_to`) — live fan-out ТОЛЬКО `GET {P}/stats`** (один запрос на бэк): периодные суммы из снимка невыводимы — снимок хранит текущее состояние пользователя, а не историю платежей. Это осознанный компромисс: дешёвый путь остаётся дешёвым, дорогой — явно ограничен одним запросом на источник.
- **`errors[]`** — строки источников с `error_message IS NOT NULL` (плюс сбои live-вызова `stats` при периоде). Семантика — §1.
- **`snapshot_at`** — `MIN(refreshed_at)` по участвующим источникам; хотя бы один источник ни разу не обновлялся → `null`.

### 4. Точечные и пишущие пути остаются **live**

`GET …/users/{id}`, `/payments`, `/requests`, `/products`, `POST …/tokens`, `POST …/subscription` ходят в бэк напрямую — как сейчас. Карточка обязана показывать актуальный баланс сразу после начисления.

**Best-effort touch после мутаций (нормативно):** успешный `POST …/tokens` / `…/subscription` обновляет соответствующую строку снимка значениями **из ответа бэка** (`tokens`, `subscription_active`, `subscription_expires_at`).

> **`null` в ответе бэка — «не отдано», и такое поле touch НЕ ПИШЕТ (нормативно).** Поля ответа нормализуются в `null`, если бэк их не прислал ([ADR-072](ADR-072-crm-admin-api-v11-economics.md) §1.1), поэтому `tokens` — **`float | null`**, а `subscription_active`/`subscription_expires_at` — тоже опциональны. **Записывать `null` в снимок запрещено:** это не «баланс обнулился», а «значение неизвестно», и запись превратила бы отсутствие поля у бэка уровня v1 в видимое оператору обнуление баланса — ровно та подмена «`null` ≠ `0`», против которой написан [ADR-072](ADR-072-crm-admin-api-v11-economics.md) §5. Touch обновляет **только непустые** поля ответа; остальные сохраняют прежнее значение строки до следующего цикла воркера. Если непустых полей нет вовсе — touch не выполняется, и это не ошибка. Touch — **best-effort**: его провал логируется и **не** превращает успешную admin-операцию в ошибку (операция у бэка уже состоялась — тот же принцип «сначала факт, затем интерпретация», что в [ADR-073](ADR-073-products-archive-and-price-columns.md) §8). Без touch оператор, начисливший токены, увидел бы в списке старое значение до следующего цикла воркера — самый заметный случай расхождения свежести.

Режим одного бэка читает тот же SQL снимка; проверки `404 backend_not_found` / `409 backend_admin_key_not_set` через резолвер сохраняются.

### 5. Блок «Расходы API» — из `revenue.providers`, инкрементально; показатель **lifetime**

**Источник — `GET {P}/users/{id}.revenue.providers`**, собираемый тем же воркером:

- **Инкрементально по dirty-set.** Обход списка даёт бесплатную детекцию активности: изменился fingerprint → пользователь что-то потратил → один `GET {P}/users/{id}`. Пассивные пользователи не опрашиваются вовсе.
- **Холодный старт — квота.** Строки с `revenue_refreshed_at IS NULL` добираются по `BACKEND_USERS_SNAPSHOT_REVENUE_BATCH` (**2000**) на бэк за цикл. Порядок добора — `registered_at DESC` (свежие пользователи ценнее). При ~305 000 пользователей полный backfill занимает ≈ 1.5–2 суток.
- **`revenue_supported` — отдельный признак источника (нормативно).** По **первой успешно добранной карточке** каждого цикла воркер выставляет `backend_user_snapshot_sources.revenue_supported`: блок `revenue` в ответе присутствует → `true`, отсутствует (`null` по контракту §4.5) → `false`. `NULL` — карточек ещё не добирали. Признак **пересматривается каждый цикл**: бэк, внедривший v1.1, переключается в `true` без ручного вмешательства.
- **Вычисление `api_costs.partial` (нормативно):**

  ```
  partial = ∃ участвующий источник:  revenue_backfill_done = false
                                     OR revenue_supported IS FALSE
           OR ∃ запрошенный бэк БЕЗ строки в backend_user_snapshot_sources
  ```

  > **Третий дизъюнкт — «запрошенный бэк ещё не имеет строки состояния»** (сверка `backend/app/services/backend_user_service.py` 2026-08-19: `len(participating) < len(backend_ids)`). Строка `backend_user_snapshot_sources` заводится воркером, поэтому бэк, добавленный в реестр между циклами, в сумму не входит — и это ровно «сумма неполная». Без третьего дизъюнкта свежедобавленный бэк молча занижал бы итог **без пометки**. **Вырожденный случай:** строк нет **ни у одного** запрошенного бэка ⇒ **`api_costs = null`**, а не `0` с `partial=true` — нулей, которых никто не измерял, показывать нельзя. **Фактическое поведение UI при `api_costs === null` (сверка `frontend/src/pages/BackendUsersPage.tsx` 2026-08-19): блок «Расходы API» РЕНДЕРИТСЯ, но все значения — `—`** (`value={apiCosts ? formatUsdCents(...) : '—'}`); ячейка «Прочее» скрыта (`other_usd > 0` не выполняется), бейдж `partial` не показывается (`apiCosts?.partial`), а причину объясняет подпись свежести «Снимок формируется…» при `snapshot_at === null`. Прочерк честнее скрытия: блок на месте, и видно, что величина не измерена, а не равна нулю.
  >
  > ⚠️ **Почему одного `revenue_backfill_done` НЕДОСТАТОЧНО — и почему это не мелочь.** Очередь backfill выбирается предикатом `revenue_refreshed_at IS NULL`, а карточка бэка **уровня v1** (без блока `revenue`) при добора **тоже получает** `revenue_refreshed_at` и покидает очередь. Значит у v1-бэка `revenue_backfill_done` честно становится `true` — и `partial` схлопнулся бы в `false` при **навсегда заниженной** сумме. Это ровно тот исход, против которого написан флаг: «сумма неполная» перестала бы объявляться именно в том случае, который **никогда** не исправится сам. Второй дизъюнкт (`revenue_supported IS FALSE`) закрывает дыру. **Строго `IS FALSE`, а не `IS NOT TRUE`:** состояние `NULL` (карточек ещё не добирали) уже покрыто первым дизъюнктом `revenue_backfill_done = false`, и дублировать его вторым условием значило бы держать одно состояние в двух местах предиката.
  >
  > **Сканировать строки снимка (`api_cost_usd IS NULL`) ради того же вывода запрещено:** признак живёт на строке источника и читается за `O(число бэков)`, а не за `O(число пользователей)` на каждый рендер списка.

- UI обязан показать `partial` — иначе неполная сумма читается как полная.
- **Нормализация ключей провайдера** (`_normalize_provider`, нормативно): `openai`/`gpt*` → **`openai`**; `anthropic`/`claude*` → **`anthropic`**; `fal`/`fal.ai` → **`fal`**; всё остальное → **`other`**. Сопоставление регистронезависимое: точные алиасы по значению, по префиксу — только семейства моделей `gpt*`/`claude*`. Незнакомый провайдер **не теряется** — он попадает в `other`, а не отбрасывается.

**Компромисс (нормативно, зафиксирован здесь):**

- Показатель **накопительный за всё время (lifetime)**: `revenue.providers` — lifetime-агрегат контракта. **Фильтр периода страницы на этот блок НЕ действует** — период меняет список и `stats`, но не «Расходы API». UI обязан назвать это подписью, иначе оператор прочтёт lifetime как «за выбранный период».
- `refunded` / `estimated` учитываются **так, как их считает бэк** — CRM в агрегат не вмешивается.
- **Бэк уровня v1 без блока `revenue`** отдаёт `null` ⇒ его пользователи в сумму не входят: показатель **занижен, и это состояние постоянно** (само не исправится). Оно помечается тем же `partial` — через дизъюнкт `revenue_supported IS FALSE`, **а не** через `revenue_backfill_done` (см. врезку выше). Отличить «ещё собираем» от «этот бэк не умеет» **в самом флаге** нельзя (он скалярный) — принято осознанно, цена для оператора одинакова: «сумма неполная»; поимённое разведение причин — [TD-085](../100-known-tech-debt.md).
- Краулинг `GET {P}/users/{id}/requests` **отвергнут**: сотни тысяч запросов на цикл против единиц тысяч у инкрементального пути. Скрипт-краулер `.github/scripts/claude_request_cost_report.py` **не трогается**; его роль (разовый отчёт себестоимости) воркер закрывает штатно, но удаление скрипта в scope не входит.

### 6. Контракт ответа

```python
class BackendUsersApiCosts(BaseModel):
    openai_usd: float = 0; anthropic_usd: float = 0; fal_usd: float = 0
    other_usd: float = 0; total_usd: float = 0
    partial: bool = False      # см. §5: незавершённый backfill ИЛИ источник без блока revenue
class BackendUsersListResponse(...):
    ...  # + snapshot_at: datetime | None (MIN(refreshed_at)); + api_costs: BackendUsersApiCosts | None
```

Оба поля — **аддитивные**; существующие `total`/`items`/`stats`/`errors` не меняются. `api_costs: null` — «снимок ещё не сформирован» (тот же случай, что `snapshot_at: null`).

**UI** ([modules/backend-users](../modules/backend-users/README.md#блок-расходы-api-нормативно-adr-080)): второй grid `SummaryCell` под существующей сводкой — «Расход OpenAI» / «Расход Anthropic» / «Расход Fal», плюс «Прочее» **только при `other_usd > 0`**; подпись свежести «Данные на HH:MM» из `snapshot_at`, при `null` — «Снимок формируется…»; при `partial` — пометка «расходы ещё собираются». Примитив `SummaryCell` — **существующий** (`frontend/src/pages/BackendUsersPage.tsx:323`, сверка 2026-08-19); новых примитивов ДС не вводится.

## Consequences

- (+) Рендер списка перестаёт зависеть от доступности и скорости бэков: один SQL вместо десятков upstream-запросов; глубокая пагинация становится дешёвой, окно 1000 снимается.
- (+) `errors[]` снова означает сбой — жёлтая плашка возвращает диагностическую ценность.
- (+) Расходы по провайдерам появляются без нового контракта у бэков (используется уже существующий `revenue`).
- (−) **CRM получает второй дом данных о пользователях бэков.** Митигации: снимок read-only, все точечные и пишущие пути live, возраст данных виден оператору (`snapshot_at`), touch после мутаций.
- (−) **Смешанная свежесть**: список (снимок) ↔ карточка (live). Наиболее заметный случай (начисление токенов) закрыт touch'ем; прочие расхождения живут до 15 минут.
- (−) **Полный обход крупного бэка может превысить интервал.** Наложение исключено паттерном «sleep после завершения»; UI показывает фактический `snapshot_at`. 429/паузы источника обрабатываются как исключение итерации (§2 п.7), снимок при этом сохраняется.
- (−) Показатель расходов **lifetime** и **занижен** для бэков уровня v1 — см. §5; долг — [TD-085](../100-known-tech-debt.md).
- (−) Сдвиг offset-пагинации источника во время обхода может продублировать/пропустить строку: дубли гасит PK-upsert, пропуски — `DELETE` строго при полном успешном обходе.
- Смена нормы затрагивает **два** ADR: [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3/§4 и подтверждающий [ADR-072](ADR-072-crm-admin-api-v11-economics.md) §3 — оба помечены врезкой-амендментом.

## Alternatives

- **Redis-кэш ответа списка** — отвергнуто: [ADR-006](ADR-006-async-provisioning-bez-brokera.md)/NFR-1 (внешний брокер/кэш в стек не вводится), кэш не переживает рестарт, а фильтры/пагинация дают комбинаторный набор ключей.
- **In-memory TTL-кэш в процессе** — отвергнуто: теряется при каждом деплое (деплой = пересборка на сервере), не переживает несколько воркеров uvicorn, поиск по кэшу всё равно требовал бы полного набора в памяти.
- **Оставить live fan-out и просто скрыть keyless-бэки** — отвергнуто: закрывает (б), но не (а) и не (в).
- **Краулинг `/requests` ради расходов** — отвергнуто по стоимости (§5).
- **Хранить историю платежей в снимке ради периодных `stats`** — отвергнуто: это уже не зеркало, а вторая бухгалтерия; периодные суммы решаются одним live-`stats` на бэк.
- **Фильтровать бэки без ключа на фронте** — отвергнуто: `errors[]` остался бы «грязным» для любого другого потребителя API, а норма «errors = сбой» должна держаться на сервере.
- **Отменить [ADR-069](ADR-069-backend-users-page-admin-contract.md) §3 целиком (включая backend-economics)** — отвергнуто: у экономики нет проблемы объёма и частоты, а копия денежных величин там прямо запрещена [ADR-072](ADR-072-crm-admin-api-v11-economics.md) §3.
