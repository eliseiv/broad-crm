# CRM · Документация

Единственный источник истины (source of truth) проекта. Любой код, тест и инфраструктура должны соответствовать этим документам. При расхождении `docs/` ↔ код — виноват тот, кто не обновил `docs/`.

## О проекте

CRM-система мониторинга backend-сервисов и серверов. **Этап 1** — страница «Серверы»: список карточек серверов с кастомными SVG-спидометрами (CPU / RAM / SSD), двухшаговый вход администратора, добавление сервера с автоматическим провижинингом (Ansible → node_exporter → Prometheus); плюс страница «ИИ - ключи» — реестр API-ключей AI-провайдеров (OpenAI/Anthropic) с шифрованием, маской и автоматической проверкой валидности + Telegram-алерты ([modules/ai-keys](modules/ai-keys/README.md)); плюс страница «Почты» — лента писем из внешнего сервиса `postapp.store` и ответ (reply) через read-through-прокси без хранения, с серверными фильтрами по ящику/команде ([modules/mail](modules/mail/README.md), [ADR-012](adr/ADR-012-mail-read-through-proxy.md), [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)); плюс обзорная страница «Дашборд» (доступна по прямому URL `/dashboard`; со Спринта B убрана из меню, [ADR-022](adr/ADR-022-teams-nav-categories.md)) — сетка кликабельных карточек-разделов со счётчиками (клиентская агрегация из list-эндпоинтов, [08-design-system.md](08-design-system.md#страница-дашборд), [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)); плюс страница «Прокси» — реестр HTTP/HTTPS/SOCKS5-прокси с фоновым монитором доступности и Telegram-алертами при недоступности/восстановлении ([modules/proxies](modules/proxies/README.md), [ADR-019](adr/ADR-019-proxies-availability-monitor.md)).

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
| Auth (двухшаговый вход + JWT + RBAC: пользователи/роли/права) | [modules/auth/README.md](modules/auth/README.md) | backend, frontend |
| Servers (реестр + CRUD) | [modules/servers/README.md](modules/servers/README.md) | backend |
| Monitoring (Prometheus, PromQL) | [modules/monitoring/README.md](modules/monitoring/README.md) | backend |
| Provisioning (Ansible) | [modules/provisioning/README.md](modules/provisioning/README.md) | backend, devops |
| Notifier (Telegram-уведомления) | [modules/notifier/README.md](modules/notifier/README.md) | backend |
| AI Keys (реестр ключей + проверка + алерты) | [modules/ai-keys/README.md](modules/ai-keys/README.md) | backend, frontend |
| Mail (Почты — система-запись в БД CRM: лента/ящики/теги + Telegram-доставка + Mini App) | [modules/mail/README.md](modules/mail/README.md) | backend, frontend, devops |
| Proxies (реестр прокси + монитор доступности + алерты) | [modules/proxies/README.md](modules/proxies/README.md) | backend, frontend |
| Backends (реестр бэков + healthcheck `/health` + алерты) | [modules/backends/README.md](modules/backends/README.md) | backend, frontend |
| Teams (CRM-команды: лидер + участники M2M) | [modules/teams/README.md](modules/teams/README.md) | backend, frontend |
| SMS (СМС: Twilio-приём + Telegram-доставка операторам) | [modules/sms/README.md](modules/sms/README.md) | backend, frontend, devops |
| UI (страница «Серверы», спидометры) | [modules/ui/README.md](modules/ui/README.md) | frontend |
| Documents (Документы — менеджер знаний: дерево папок + Markdown-документы, WYSIWYG, видимость по ролям, внешний read-only API для RAG) | [modules/documents/README.md](modules/documents/README.md) | backend, frontend |

## Статусы модулей

| Модуль | Статус | DoD |
|--------|--------|-----|
| auth | `implemented` (правки A/ADR-025 — `spec-ready`) | Реализован (Спринт 3, [ADR-021](adr/ADR-021-rbac-users-roles.md)): двухшаговый вход + JWT, RBAC, enforcement `require(page,action)`/`require_admin`/`403 forbidden`, bcrypt-хэш, Users/Roles/Permissions API, страница `/users`, миграция 0008, тесты. **Спринт A ([ADR-022](adr/ADR-022-teams-nav-categories.md), spec-ready):** каталог += `roles`/`teams`, роли под матрицей `roles:*`, инвариант эскалации, `RoleListItem.user_count`, миграции 0009/0010. **Правки [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md) (spec-ready):** `users.email`→`users.telegram`, беспарольные пользователи (`password_hash` nullable), вход по Логину/Телеграму, открытый первый вход (`set-password`), миграция 0011. **Правки [ADR-028](adr/ADR-028-user-status-first-login.md)/[ADR-029](adr/ADR-029-ui-login-password-nav-team-form.md) (spec-ready):** тристатус (`first_login_at`, `UserListItem.status`, миграция 0015), экран «Придумайте пароль», убран H1 страниц Users/Roles |
| teams | `spec-ready` | Не реализован — спецификация готова (Спринт A, [ADR-022](adr/ADR-022-teams-nav-categories.md)): CRM-команды `teams`+`user_teams` (M2M), лидер+участники, `/api/teams`, миграция 0009. **Правки [ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md):** команды без лидера (`leader_id` nullable, авто-назначение/передача лидерства), миграция 0012. **Правки [ADR-029](adr/ADR-029-ui-login-password-nav-team-form.md):** форма — лидер из выбранных участников (первый=лидер), убран H1 страницы |
| servers | `spec-ready` | Не реализован — спецификация готова |
| monitoring | `spec-ready` | Не реализован — спецификация готова |
| provisioning | `spec-ready` | Не реализован — спецификация готова |
| notifier | `spec-ready` | Не реализован — спецификация готова |
| ai-keys | `spec-ready` | Не реализован — спецификация готова |
| mail | `spec-ready` | Не реализован — спецификация готова |
| sms | `spec-ready` | Не реализован — спецификация готова ([ADR-030](adr/ADR-030-sms-module-full-merge.md)): полное слияние SMS-агрегатора — 4 таблицы (PK BIGINT + внешние FK UUID), Twilio-приём по подписи + отдельный SMS-delivery Telegram-бот (fan-out по команде/retry/dead-links), отказ от Redis (Mini App-привязка под JWT), поля номера `login`/`app_name`/`note` (системный `label`), видимость по текущей принадлежности номера, RBAC-страница `sms:view/edit/transfer/sync/delete`, `Principal.user_id`, миграция 0017. Доработка `/teams` (`number_count` + detail-панель). Импорт исторических данных — TD. **Операторская Telegram Mini App ([ADR-031](adr/ADR-031-sms-operator-mini-app.md)):** публичный маршрут `/tg/sms` вне `AppLayout`, онбординг — беспарольный Telegram-SSO (`POST /api/sms/telegram/auth` резолвит оператора по `telegram_user_id`/`users.telegram` → CRM-JWT + авто-линк; не сопоставлен → `403 sms_operator_not_provisioned`), просмотр под `sms:view`, self-hosted Telegram SDK; требует backend-доработки `telegram/auth` |
| proxies | `implemented` (правки ADR-023/024/027 — `spec-ready`) | Реализован (Спринт 1) — модель+миграция 0006, монитор, Telegram-алерты, CRUD API, страница `/proxies`, тесты. **Правки:** карточка — только IP ([ADR-023](adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)); overall-deadline проверки против зависания ([ADR-024](adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)); grace-порог 30 мин алерта (`error_since`/`alert_sent`, `PROXY_ALERT_AFTER_SEC`, миграция 0014, унификация с бэками — [ADR-027](adr/ADR-027-proxies-alert-grace.md)) |
| backends | `implemented` (правки ADR-023/024 — `spec-ready`) | Реализован (Спринт 2) — модель+миграция 0007, healthcheck-монитор `GET /health`, Telegram-алерты, CRUD API, страница `/backends`, тесты ([ADR-020](adr/ADR-020-backends-healthcheck-monitor.md)). **Правки:** одна кнопка «Удалить» ([ADR-023](adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)); overall-deadline + grace-порог 30 мин алерта, миграция 0013 ([ADR-024](adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) |
| ui | `spec-ready` | Не реализован — спецификация готова |
| documents | `implemented` | Реализован (Спринт 1–2, [ADR-059](adr/ADR-059-documents-module.md)/[ADR-060](adr/ADR-060-documents-external-readonly-api-key.md)/[ADR-061](adr/ADR-061-documents-sidebar-two-panel-nav.md)/[ADR-062](adr/ADR-062-documents-wysiwyg-tiptap.md)): единая таблица `document_nodes` + `document_node_roles` (миграции 0029/0030), permission-based enforcement, вычисляемое наследование видимости по ролям (рекурсивный CTE), soft-delete для RAG, внутренний API `/api/documents/*` + внешний read-only `X-API-Key`-API `/api/external/documents/*`, двухпанельный сайдбар `/documents` (full-bleed), WYSIWYG (TipTap + `@tiptap/extension-link`). Тесты qa зелёные (backend/frontend). Не задеплоен на прод. **Фикс-пакет клиентского слоя спроектирован — [ADR-063](adr/ADR-063-documents-editor-cache-lifecycle-focus.md)** (слияние ответа `PATCH` в кэш узла вместо перезаписи; ключ ремоунта редактора — только `id` + ресинк по `content_version`; фокус по клику в любое место документа + центрирование колонки; локализация `documents → «Документы»`), **реализация — за `frontend`**; backend/модель/внешний контур не меняются |

## Глоссарий

- **Спидометр (gauge)** — кастомный SVG-компонент с дугой ~270°, отображающий метрику 0–100 %.
- **Провижининг** — автоматическая установка node_exporter на целевой сервер через Ansible и регистрация scrape-таргета Prometheus.
- **file_sd** — file-based service discovery Prometheus: backend пишет JSON-файлы таргетов, Prometheus перечитывает их без рестарта.
- **Drill-down** — детальный просмотр метрик в Grafana. На Этапе 1 ссылки из карточки нет ([ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)); Grafana открывается напрямую через `/grafana`.
- **Notifier** — фоновая asyncio-задача backend, шлёт Telegram-алерты при эскалации нагрузки/доступности и при восстановлении (`offline→online`); windowed offline-детект (`min_over_time` для `up`) + durable-лог отправленных алертов ([ADR-009](adr/ADR-009-in-backend-notifier-vs-alertmanager.md), [ADR-016](adr/ADR-016-notifier-max-over-window-zone.md), [ADR-018](adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)).

## Принципы

1. **Простота превыше всего.** Монолитный backend, без брокеров сообщений на Этапе 1.
2. **Prometheus — единственный источник истины для метрик.** В БД метрики не дублируются.
3. **Безопасность с первого дня.** SSH-пароли шифруются, учётка супер-админа — только в `.env` (bootstrap; в `users` — лишь невидимая системная строка-якорь под личное состояние, [ADR-051](adr/ADR-051-superadmin-db-anchor-personal-state.md)), API под JWT; с Спринта 3 — RBAC (роли/права на все страницы, enforcement на сервере `403`, bcrypt-хэш паролей БД-пользователей — [ADR-021](adr/ADR-021-rbac-users-roles.md)).
4. **Тёмная enterprise-эстетика** (Linear / Vercel / Grafana / Datadog), не «типовой ИИ-сайт». Тёмная тема — основная бренд-идентичность; со Спринта C доступна светлая тема (переключатель в хэдере), **дефолт при первом входе — системная** (`prefers-color-scheme`), явный выбор её переопределяет ([ADR-033](adr/ADR-033-flat-nav-theme-toggle-numbers-table.md)). Навигация — **плоский ряд пунктов** (без категорий-дропдаунов, [ADR-033](adr/ADR-033-flat-nav-theme-toggle-numbers-table.md)).
