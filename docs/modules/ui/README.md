# Модуль `ui` — Страница «Серверы» и спидометры

Статус: `spec-ready` · Исполнитель: frontend

## Scope
SPA: двухшаговый вход, страница «Серверы» (сетка карточек), кастомные SVG-спидометры, карточка «+ Добавить» + модалка. Дизайн — [08-design-system.md](../../08-design-system.md), по референсу [`docs/assets/reference.png`](../../assets/reference.png). API — [04-api.md](../../04-api.md).

## Компоненты (нормативно)
- `LoginPage` — двухшаговый вход ([modules/auth](../auth/README.md), [08-design-system.md](../../08-design-system.md#экран-входа-двухшаговый)). **Только карточка с формой — без заголовка продукта, подзаголовка и логотипа над формой.**
- `ServersPage` — **адаптивная сетка 1 → 2 → 3 колонки** (`grid-cols-1 md:grid-cols-2 xl:grid-cols-3`, gap 24px, до 3 карточек в ряд); данные через TanStack Query с `refetchInterval = VITE_POLL_INTERVAL_MS` (15000) на запрос `GET /api/servers` (см. стратегию polling ниже).
- `ServerCard` — шапка (иконка, имя, статус-точка «В сети/Не в сети», «Аптайм», «Обновлено») + 3 под-карточки в ряд (`grid-cols-3`).
- `MetricSubCard` — заголовок метрики + `Gauge` + абсолютные значения (CPU — `N ядер`, RAM/SSD — `used / total ГБ`).
- `Gauge` — кастомный SVG, дуга 270°, цвет по `usageToZone(value)`, **в центре только число (без `%`, без подписи «Usage», без меток `0%`/`100%`)**, анимация, `role="meter"` (ARIA сохраняет проценты). Спецификация — [08-design-system.md](../../08-design-system.md#компонент-gauge-кастомный-svg).
- `AddServerCard` — glass/blur, по центру «+ Добавить», клик → модалка.
- `AddServerModal` — Radix Dialog, 4 поля (Название, IP, Пользователь, Пароль), валидация, loading, обработка `409`/`422`.

## Требования
1. Тёмная тема по умолчанию, токены/типографика/сетка строго из [08-design-system.md](../../08-design-system.md).
2. Цвет дуги — ТОЛЬКО по нагрузке (зелёный<80 / жёлтый 80–90 / красный>90), одинаково для CPU/RAM/SSD; единая функция `usageToZone` (совпадает с backend).
3. Состояния: loading(skeleton), empty, provisioning(pending/installing с прогрессом), error(провижининг), offline, hover/focus/disabled, toast (sonner).
4. **Стратегия polling (нормативно):**
   - **Routine-метрики:** один запрос `GET /api/servers` через TanStack Query с `refetchInterval = VITE_POLL_INTERVAL_MS` (15000 мс). Список уже содержит метрики всех серверов → **per-card `GET /api/servers/{id}/metrics` в routine-цикле НЕ используется** (избегаем N запросов). `GET /api/servers/{id}/metrics` зарезервирован на будущее (детальное обновление одной карточки) и на Этапе 1 фронтом не вызывается в цикле.
   - **Provisioning-статус:** `GET /api/servers/{id}/status` опрашивается (interval ~2–3 с) **только для карточек в статусе `pending`/`installing`** и прекращается при переходе в `online`/`error`. Для `online`/`error` статус-polling не запускается.
   - После `POST /api/servers` (202) карточка появляется в `pending`; запускается status-polling до `online`/`error`, затем карточка обновляется из общего `GET /api/servers`.
5. **Drill-down ссылки из карточки на Grafana НЕТ** (удалена на Этапе 1 — [ADR-005, поправка](../../adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)). Конфигурация `VITE_GRAFANA_URL` удалена. Grafana доступна администратору напрямую через `/grafana`; карточка внешних ссылок не содержит.
6. a11y: focus-ring, aria для gauge/модалки, контраст ≥AA, `prefers-reduced-motion` (NFR-8).
7. Формат uptime: секунды → `Nд Nч Nм` (рус.). Метрики/IP/числа — моношрифт.
8. На `401` — редирект на `/login`.
9. **Локализация UI — русский.** Все пользовательские строки строго по [словарю локализации](../../08-design-system.md#локализация-ui-русский-словарь-строк): статусы («В сети»/«Не в сети»/«Ожидание»/«Установка…»/«Ошибка»), «Аптайм»/«Обновлено», относительное время («только что»/«N мин назад»/«N ч назад»/«N дн назад»), единицы (`ГБ`, `ядра` с формами мн.ч.), кнопки, empty state, toast, ошибки. Технические идентификаторы API (`provision_status`, коды ошибок, `unit`) не локализуются — переводится только отображение.
10. **CPU detail — всегда число ядер** (`unit:"cores"`, `value:null`, `total`=ядра → UI «N ядер»); GHz не отображается. RAM/SSD — `used / total ГБ`.

## DoD
- [ ] Визуальное соответствие референсу и enterprise-уровню ([08-design-system.md](../../08-design-system.md)).
- [ ] Все состояния UI реализованы.
- [ ] Цвет дуги строго по зоне нагрузки.
- [ ] Unit/компонентные + E2E тесты ([06-testing-strategy.md](../../06-testing-strategy.md)) зелёные; coverage ≥70 % (Gauge/зоны ≥90 %).
- [ ] lint/typecheck/format/build проходят.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
- 2026-06-29: UI-изменения — сетка до 3 колонок; gauge без `%`/«Usage»/меток `0-100%`; CPU detail всегда «ядра»; локализация UI на русский (словарь строк).
- 2026-06-30: удалена drill-down ссылка на Grafana из карточки + конфиг `VITE_GRAFANA_URL` ([ADR-005, поправка](../../adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)).
