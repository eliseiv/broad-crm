# CRM — Мониторинг серверов · Документация

Единственный источник истины (source of truth) проекта. Любой код, тест и инфраструктура должны соответствовать этим документам. При расхождении `docs/` ↔ код — виноват тот, кто не обновил `docs/`.

## О проекте

CRM-система мониторинга backend-сервисов и серверов. **Этап 1** — страница «Серверы»: список карточек серверов с кастомными SVG-спидометрами (CPU / RAM / SSD), двухшаговый вход администратора, добавление сервера с автоматическим провижинингом (Ansible → node_exporter → Prometheus); плюс страница «ИИ - ключи» — реестр API-ключей AI-провайдеров (OpenAI/Anthropic) с шифрованием, маской и автоматической проверкой валидности + Telegram-алерты ([modules/ai-keys](modules/ai-keys/README.md)); плюс страница «Почты» — лента писем из внешнего сервиса `postapp.store` и ответ (reply) через read-through-прокси без хранения, с серверными фильтрами по ящику/команде ([modules/mail](modules/mail/README.md), [ADR-012](adr/ADR-012-mail-read-through-proxy.md), [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)); плюс обзорная стартовая страница «Дашборд» — сетка кликабельных карточек-разделов со счётчиками (клиентская агрегация из list-эндпоинтов, [08-design-system.md](08-design-system.md#страница-дашборд), [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)).

## Карта документации

| Документ | Назначение |
|----------|-----------|
| [00-vision.md](00-vision.md) | Цели продукта, scope Этапа 1, нефункциональные требования (NFR) |
| [01-architecture.md](01-architecture.md) | Компоненты, диаграммы, потоки данных, deployment topology |
| [02-tech-stack.md](02-tech-stack.md) | Выбранный стек с версиями, команды lint/format/test/build |
| [03-data-model.md](03-data-model.md) | Схема БД, DDL, миграции, шифрование полей |
| [04-api.md](04-api.md) | REST API контракты: запросы, ответы, коды ошибок |
| [05-security.md](05-security.md) | Auth, JWT, шифрование SSH-кредов, секреты, угрозы |
| [06-testing-strategy.md](06-testing-strategy.md) | Пирамида тестов, coverage gate, что и чем тестируем |
| [07-deployment.md](07-deployment.md) | docker-compose состав, переменные окружения, запуск |
| [08-design-system.md](08-design-system.md) | UI-гайд: палитра, типографика, токены, компоненты, спидометры |
| [09-provisioning.md](09-provisioning.md) | Ansible-плейбук, file_sd, жизненный цикл провижининга |
| [99-open-questions.md](99-open-questions.md) | Открытые вопросы (Q-NNN-N) |
| [100-known-tech-debt.md](100-known-tech-debt.md) | Реестр технического долга (TD-NNN) |
| [adr/INDEX.md](adr/INDEX.md) | Реестр архитектурных решений (ADR) |

## Модули (ТЗ для исполнителей)

| Модуль | Документ | Ответственный исполнитель |
|--------|----------|---------------------------|
| Auth (двухшаговый вход + JWT) | [modules/auth/README.md](modules/auth/README.md) | backend, frontend |
| Servers (реестр + CRUD) | [modules/servers/README.md](modules/servers/README.md) | backend |
| Monitoring (Prometheus, PromQL) | [modules/monitoring/README.md](modules/monitoring/README.md) | backend |
| Provisioning (Ansible) | [modules/provisioning/README.md](modules/provisioning/README.md) | backend, devops |
| Notifier (Telegram-уведомления) | [modules/notifier/README.md](modules/notifier/README.md) | backend |
| AI Keys (реестр ключей + проверка + алерты) | [modules/ai-keys/README.md](modules/ai-keys/README.md) | backend, frontend |
| Mail (Почты — read-through-прокси + reply) | [modules/mail/README.md](modules/mail/README.md) | backend, frontend |
| UI (страница «Серверы», спидометры) | [modules/ui/README.md](modules/ui/README.md) | frontend |

## Статусы модулей

| Модуль | Статус | DoD |
|--------|--------|-----|
| auth | `spec-ready` | Не реализован — спецификация готова |
| servers | `spec-ready` | Не реализован — спецификация готова |
| monitoring | `spec-ready` | Не реализован — спецификация готова |
| provisioning | `spec-ready` | Не реализован — спецификация готова |
| notifier | `spec-ready` | Не реализован — спецификация готова |
| ai-keys | `spec-ready` | Не реализован — спецификация готова |
| mail | `spec-ready` | Не реализован — спецификация готова |
| ui | `spec-ready` | Не реализован — спецификация готова |

## Глоссарий

- **Спидометр (gauge)** — кастомный SVG-компонент с дугой ~270°, отображающий метрику 0–100 %.
- **Провижининг** — автоматическая установка node_exporter на целевой сервер через Ansible и регистрация scrape-таргета Prometheus.
- **file_sd** — file-based service discovery Prometheus: backend пишет JSON-файлы таргетов, Prometheus перечитывает их без рестарта.
- **Drill-down** — детальный просмотр метрик в Grafana. На Этапе 1 ссылки из карточки нет ([ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)); Grafana открывается напрямую через `/grafana`.
- **Notifier** — фоновая asyncio-задача backend, шлёт Telegram-алерты при эскалации нагрузки/доступности и при восстановлении (`offline→online`); windowed offline-детект (`min_over_time` для `up`) + durable-лог отправленных алертов ([ADR-009](adr/ADR-009-in-backend-notifier-vs-alertmanager.md), [ADR-016](adr/ADR-016-notifier-max-over-window-zone.md), [ADR-018](adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)).

## Принципы

1. **Простота превыше всего.** Монолитный backend, без брокеров сообщений на Этапе 1.
2. **Prometheus — единственный источник истины для метрик.** В БД метрики не дублируются.
3. **Безопасность с первого дня.** SSH-пароли шифруются, админ-учётка только в `.env`, API под JWT.
4. **Тёмная enterprise-эстетика** (Linear / Vercel / Grafana / Datadog), не «типовой ИИ-сайт».
