# ADR-070: Оценочный остаток баланса AI-ключа (якорь + Admin Cost API)

Статус: accepted · Дата: 2026-07-30

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
