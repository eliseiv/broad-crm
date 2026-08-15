# ADR-075: Hourly credit-probe AI-ключей без Admin API key

Статус: accepted · Дата: 2026-08-15

## Контекст

Health-монитор ([ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md)) проверяет валидность
через бесплатный `GET /v1/models`. Для Anthropic биллинг в этом ответе не отражается;
у OpenAI `insufficient_quota` ловится только иногда. Контур оценочного остатка
([ADR-070](ADR-070-ai-key-estimated-balance-monitor.md)) требует Admin API key и якорь —
на проде у всех ключей он выключен.

Владелец: нужны бинарные алерты «есть кредиты / нет» **без Admin key**, через
минимальные платные запросы раз в час.

## Решение

1. **Третий контур** `credit_*` (рядом с health и balance), всегда включён для всех ключей.
2. Фоновый сервис `AiKeyCreditProbeService`, интервал `AI_KEY_CREDIT_PROBE_INTERVAL_SEC`
   (default **3600**).
3. Probe — минимальный inference:
   - OpenAI: `POST {OPENAI_API_BASE}/chat/completions`, модель
     `AI_KEY_CREDIT_PROBE_OPENAI_MODEL` (default `gpt-4o-mini`),
     `messages=[{"role":"user","content":"ping"}]`, `max_tokens=1`.
   - Anthropic: `POST {ANTHROPIC_API_BASE}/messages`, модель
     `AI_KEY_CREDIT_PROBE_ANTHROPIC_MODEL` (default `claude-haiku-4-5-20251001`),
     `messages=[{"role":"user","content":"ping"}]`, `max_tokens=1`.
4. Probe **только** для ключей с `check_status='working'` (забаненные не тратим).
5. Исходы → `credit_status`:
   - `ok` — HTTP 200;
   - `depleted` — исчерпание кредитов/квоты (OpenAI `insufficient_quota` /
     Anthropic billing/credit ошибки);
   - при `unknown` (таймаут/5xx/rate-limit без quota) — **статус не меняем**;
   - `401/403` — статус не меняем (это зона health-монитора).
6. Telegram (антиспам по `credit_status` в БД):
   - `≠ depleted → depleted` → 🔴 «Нет кредитов»;
   - `depleted → ok` → 🟢 «Кредиты восстановлены»;
   - при прочих переходах — молча.
7. **ADR-070 не отменяется** — Admin Cost API остаётся опциональным точным контуром.
   Credit-probe — бинарный «жив ли prepaid» без Admin key.

## Стоимость (ориентир)

~24 probe/сутки/ключ. OpenAI `gpt-4o-mini` / Anthropic Haiku 4.5 — порядка
**$0.0001–0.003/день на ключ** при `max_tokens=1`.

## Альтернативы

| Вариант | Почему нет |
|---------|------------|
| Только ADR-070 | Требует Admin key; на проде выключен |
| Платный probe в health каждые 15 мин | ×4 дороже без выгоды |
| Scraping Console | Небезопасно |

## Ссылки

- [modules/ai-keys](../modules/ai-keys/README.md)
- [ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md), [ADR-070](ADR-070-ai-key-estimated-balance-monitor.md)
