# ADR-070: Оценочный остаток баланса AI-ключа (якорь + Admin Cost API)

Статус: accepted · Дата: 2026-07-30 · Амендмент: [2026-08-07](#амендмент-2026-08-07--reveal-admin-key-tri-state-выключения-и-порог-по-умолчанию) (reveal Admin key + `resource_type` аудита, tri-state выключения, `400 ai_key_bad_request`, порог по умолчанию)

## Контекст

Модуль `ai-keys` проверяет только **валидность** inference-ключа (`GET /v1/models`, ADR-010).
Точный баланс провайдера официально **не отдаётся** inference-ключом (TD-020).
Операторы пополняют prepaid credits вручную и хотят видеть **оценочный остаток** и алерты
до исчерпания.

## Решение

1. **Два независимые контуры мониторинга:**
   - **Health** (`check_status`) — inference-ключ, `GET /v1/models`, как раньше.
   - **Balance** (`balance_*`) — Admin API key, Cost/Usage endpoints провайдера.

2. **Формула остатка (оценка, не выписка провайдера):**
   ```
   balance_remaining_usd = balance_initial_usd − spent_usd_since(balance_anchor_at)
   ```
   - `balance_initial_usd` и `balance_anchor_at` задаются оператором при создании/редактировании
     или через `POST /api/ai-keys/{id}/balance/reset` после пополнения.
   - `spent_usd` — сумма из Admin Cost API, фильтр по `provider_api_key_id` (резолв через
     List API Keys + матч `key_prefix`/`key_last4`).

3. **Секреты:**
   - Inference-ключ — `key_encrypted` (как раньше).
   - Admin API key — отдельное поле `billing_admin_key_encrypted` (Fernet), не в list-ответах.

4. **Алерты Telegram** (отдельно от health):
   - `normal → low` — 🟡 низкий остаток.
   - `→ depleted` (≤ $0) — 🔴 баланс исчерпан.
   - `depleted/low → normal` — 🟢 баланс восстановлен (после reset).
   - 3+ последовательных ошибок sync — 🟠 не удалось обновить баланс.

5. **Интервал sync:** `AI_KEY_BALANCE_SYNC_INTERVAL_SEC` (default 3600). Usage/cost buckets
   у провайдеров отстают — частый poll не даёт «точный баланс».

## Ограничения (нормативно для UI)

- Не «баланс провайдера», а **«оценочный остаток»**.
- После пополнения в Console — **обновить баланс** в CRM.
- Anthropic Priority Tier и часть line items могут не попасть в cost_report.
- Per-key точность требует успешного резолва `provider_api_key_id`.

## Амендмент 2026-08-07 · reveal Admin key, tri-state выключения и порог по умолчанию

Решение выше **не отменяется**; амендмент фиксирует нормы, которые были реализованы вместе с ним, но в тексте ADR и в `docs/` отсутствовали (расхождение docs↔код закрыто 2026-08-07). Полные контракты — [04-api.md §AI Keys](../04-api.md#ai-keys), поведение — [modules/ai-keys §Контур оценочного остатка](../modules/ai-keys/README.md#контур-оценочного-остатка-adr-070-нормативно).

1. **Admin API key раскрывается по требованию** — `GET /api/ai-keys/{id}/billing-admin-key` под `require("ai-keys","edit")`, по общим правилам reveal ([ADR-035](ADR-035-detail-view-secret-reveal.md)): `Cache-Control: no-store`, `SecretRevealResponse`, аудит без значения. **Расширение [ADR-035](ADR-035-detail-view-secret-reveal.md):** перечень `resource_type` аудита пополняется пятым значением — **`ai_key_billing_admin`** (у ИИ-ключа два секрета в двух разных контурах, и различать их в аудите нужно; у бэка два секрета пишутся одним `resource_type="backend"` — там контур один). Симметрия «держатель `edit` и так может перезаписать секрет» соблюдена.
2. **`balance_monitoring_enabled` в `PATCH` — tri-state, а не флаг.** Отсутствует/`null` = не трогать контур; `true` = включить; **`false` = выключить и стереть данные контура вместе с `billing_admin_key_encrypted`**. Следствие, нормативное для UI: форма редактирования обязана отправлять флаг **безусловно** (иначе выключение недостижимо). В теле `POST` то же имя — обычное поле с `default=false`, в ответе — обязательное `bool`.
3. **Полнота контура — ошибка `400 ai_key_bad_request`** (не `422`): включённый мониторинг без Admin API key или без якоря отклоняется **до коммита**, ни одно поле запроса не сохраняется. `422` в роутере ИИ-ключей остаётся зарезервирован за `provider` вне enum.
4. **Порог по умолчанию — `10.0000`** (`default_low_threshold_usd()`), проставляется при включении контура без явного значения. Ограничение: порог `0` неотличим от «не задан» — [TD-082](../100-known-tech-debt.md).
5. **Уточнение к §4 «Алерты».** Обозначения 🟡/🔴/🟢/🟠 в §4 — **виды** алертов, а не текст сообщения. Фактические баннеры: 🟡-предупреждение для «Низкий остаток» **и** для «Не удалось обновить остаток» (отдельного оранжевого баннера в билдерах нет), 🔴 для «Остаток исчерпан», 🟢 для «Остаток восстановлен». Триггер «3+ ошибок sync» считает подряд идущие исходы `error` **и** `unknown` (`balance_sync_fail_streak ≥ 3`). Точные тексты — [modules/ai-keys §Формат сообщений Telegram остатка](../modules/ai-keys/README.md#формат-сообщений-telegram-остатка-точно) (единственное нормативное место).
6. **Инвариант полноты контура не выражен табличным CHECK** — держится сервисным слоем ([TD-083](../100-known-tech-debt.md)); фоновая синхронизация неполные строки молча пропускает.

## Альтернативы

| Вариант | Почему нет |
|---------|------------|
| Scraping session cookie Console | Хрупкий, небезопасный |
| Inference key → usage API | 401 — нет доступа |
| Суммировать только `usd_cost` бэков | Не покрывает direct API usage |

## Ссылки

- [modules/ai-keys](../modules/ai-keys/README.md)
- [TD-020](../100-known-tech-debt.md) — сужается до «exact balance out of scope»
- [ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md)
