# ADR-027 · Прокси: 30-минутный grace-порог алерта недоступности (снятие immediate-модели, амендмент ADR-024)

- Статус: accepted
- Дата: 2026-07-08

## Контекст

Правка 2 батча — прямое решение пользователя по мониторингу прокси (амендмент к [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md)).

Действующая модель: прокси алертят **немедленно** при первом переходе `pending|working → error` ([ADR-019](ADR-019-proxies-availability-monitor.md), [modules/proxies](../modules/proxies/README.md#переходы-статуса-и-алерты-нормативно)). [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) осознанно оставил прокси immediate (в отличие от бэков, которым дал grace 30 мин), т.к. на тот момент проблема прокси была в **зависании** проверки, а не в ложных срабатываниях, и запроса на grace для прокси не поступало.

Теперь пользователь сообщил о **ложных срабатываниях 🔴 прокси** (кратковременная недоступность прокси при штатных флапах порождает ложную пару 🔴/🟢) и требует для прокси **ту же 30-минутную grace-модель, что у бэков** ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) §2). Различие immediate/grace между прокси и бэками, введённое [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md), **снимается** — обе сущности получают единую grace-модель алерта.

Ограничения — как в [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md): монолит, один процесс, без брокеров ([ADR-001](ADR-001-stack-i-monolit.md)); интервал проверки прокси — 60 с.

## Решение

Прокси получают **grace-порог алерта недоступности** по образцу бэков ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) §2) — точный перенос модели, отличается только именами параметра/полей и сущностью.

### 1. Разделение «статус для UI» и «отправка алерта»

- `proxies.check_status` продолжает переходить в `error` **немедленно** при первом провале проверки — карточка сразу показывает «Не работает» (реальность в UI не скрывается).
- **Telegram-🔴 откладывается:** отправляется только если прокси недоступен **непрерывно ≥ `PROXY_ALERT_AFTER_SEC`** (новый параметр, default **1800 с = 30 мин**, образец `BACKEND_ALERT_AFTER_SEC`).

### 2. Персистентные поля эпизода недоступности

Миграция `0014_proxies_alert_grace` ([03-data-model.md](../03-data-model.md#таблица-proxies), образец `0013_backends_alert_grace`):
- `proxies.error_since timestamptz NULL` — момент начала **текущего непрерывного** эпизода недоступности (устанавливается при переходе `pending|working → error`; сбрасывается в `NULL` при `working`);
- `proxies.alert_sent boolean NOT NULL DEFAULT false` — отправлен ли уже 🔴 для текущего эпизода (защита от повторной отправки; гейтит recovery-🟢).

### 3. Логика (чистая функция перехода, time-aware)

`evaluate_transition(prev_status, result, error_since, alert_sent, now) -> (new_status, error_message, new_error_since, new_alert_sent, alert)` — **идентична** бэковой ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) §2, [modules/backends](../modules/backends/README.md#переходы-статуса-и-алерты-нормативно)), с порогом `PROXY_ALERT_AFTER_SEC`:
- `result = error`: `new_status='error'`. Если `prev ∈ {pending, working}` → `new_error_since = now`, `alert_sent` остаётся `false`. Если `prev == error` → `error_since` сохраняется. Затем: если `(now − error_since) ≥ PROXY_ALERT_AFTER_SEC` **и** `alert_sent == false` → `alert='error'` (🔴), `new_alert_sent = true`; иначе `alert=None` (тихо — grace-окно ещё не истекло).
- `result = working`: `new_status='working'`, `new_error_since = NULL`. Если `alert_sent == true` → `alert='recovery'` (🟢, отбой ранее отправленного алерта); `new_alert_sent = false`. Если `alert_sent == false` (эпизод не «дозрел» до алерта) → `alert=None` (**тихо** — раз 🔴 не слали, 🟢 не нужен).

Матрица переходов прокси приводится к бэковой (см. [03-data-model.md](../03-data-model.md#перечисление-check_status), [modules/proxies](../modules/proxies/README.md#переходы-статуса-и-алерты-нормативно)).

### 4. Персистентность переживает рестарт

`error_since`/`alert_sent` в БД → grace-отсчёт и признак отправки корректны между рестартами backend (сломанный прокси не «переоткрывает» отсчёт, дубль-🔴 нет). Как у бэков.

### 5. Единая модель прокси/бэки

Различие immediate/grace, введённое [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) для прокси, **снимается**: прокси и бэки теперь используют **одинаковую** grace-модель алерта (30 мин, персистентные `error_since`/`alert_sent`, time-aware `evaluate_transition`). Overall-deadline проверки (`PROXY_CHECK_DEADLINE_SEC`) и явный `httpx.Timeout` ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) §1) прокси **сохраняют без изменений** — этот ADR трогает только слой алерта, не слой проверки.

## Обоснование

- **Перенос доказанной модели.** Grace-механика уже спроектирована и валидирована для бэков ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md)); применение к прокси — минимальный, симметричный сдвиг (те же поля, та же чистая функция, тот же порог по умолчанию). Снижает когнитивную нагрузку: обе сущности ведут себя одинаково.
- **Grace через персистентные поля** — тот же приём, что durable-состояние нотификатора ([ADR-014](ADR-014-persist-notifier-state-alert-on-first-elevated.md)): состояние эпизода в БД, чистая функция тестируется без сети/времени (детерминированный `now`).
- **UI не обманывается.** `check_status` мгновенный; откладывается только уведомление.
- **30 минут** — покрывает штатные флапы прокси, но реальная длительная недоступность уведомляет своевременно. Значение — env-параметр.

## Последствия

- (+) Штатные кратковременные флапы прокси больше не порождают ложные 🔴/🟢.
- (+) Прокси и бэки — единая модель алерта (проще поддержка, тесты, документация); различие immediate/grace из [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md) устранено.
- (+) Grace-состояние прокси переживает рестарт; чистая функция перехода тестируема.
- (−) Реальная поломка прокси уведомляется с задержкой до 30 мин (осознанный trade-off против шума; UI-статус — сразу). Тот же trade-off, что принят для бэков.
- (−) Две новые колонки в `proxies` (миграция `0014_proxies_alert_grace`) + новый параметр `PROXY_ALERT_AFTER_SEC`.
- (−) Сигнатура `evaluate_transition` прокси-монитора становится time-aware (пять аргументов: `+error_since, alert_sent, now`) — как у бэков; существующие qa-тесты матрицы прокси требуют обновления под новую сигнатуру (хендофф на qa).

## Альтернативы

- **Оставить прокси immediate (как в [ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md))** — отвергнуто: прямой запрос пользователя на grace из-за ложных срабатываний.
- **Отдельная, отличная от бэков модель grace для прокси** — отвергнуто: незачем плодить сущности; бэковая модель подходит целиком.
- **Считать grace в памяти (без БД)** — отвергнуто по тем же причинам, что для бэков ([ADR-024](ADR-024-monitor-hard-deadline-backend-alert-grace.md#альтернативы)): рестарт сбросил бы отсчёт → сломанный прокси «переоткрыл» бы grace-окно; персистентность обязательна.
- **Другой порог (не 30 мин)** — отвергнуто на Этапе 1: консистентность с бэками важнее; порог — env-параметр, настраивается без кода.
