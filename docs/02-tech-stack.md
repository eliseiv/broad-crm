# 02 · Технологический стек

> Это **единственное** место, где фиксируется стек, версии и команды. Другие агенты language-agnostic и берут команды отсюда. Если чего-то нет здесь — это open question, а не повод угадывать.

Базовый стек (Python+FastAPI, React+Vite, PostgreSQL, Prometheus+node_exporter+Grafana, Ansible, кастомные SVG-спидометры) зафиксирован пользователем и подтверждён в [ADR-001](adr/ADR-001-stack-i-monolit.md). Версии и вспомогательные библиотеки выбраны архитектором ниже.

## Backend

| Технология | Версия | Назначение |
|-----------|--------|-----------|
| Python | 3.12.x | Язык backend |
| FastAPI | 0.115.x | Web-framework, REST API |
| Uvicorn | 0.32.x | ASGI-сервер |
| Pydantic | 2.9.x | Валидация, схемы запросов/ответов |
| pydantic-settings | 2.6.x | Конфигурация из `.env` |
| SQLAlchemy | 2.0.x (async) | ORM / Core |
| asyncpg | 0.30.x | Async-драйвер PostgreSQL |
| Alembic | 1.14.x | Миграции БД |
| PyJWT | 2.10.x | Выпуск/валидация JWT (HS256) |
| cryptography | 43.x | Fernet-шифрование SSH-паролей |
| bcrypt | 4.2.x | Хэширование паролей БД-пользователей (`app/infra/passwords.py`, RBAC) — напрямую, без `passlib` ([ADR-021](adr/ADR-021-rbac-users-roles.md), [05-security.md](05-security.md#хэширование-паролей-bcrypt)) |
| ansible-runner | 2.4.x | Программный запуск Ansible из backend |
| ansible-core | 2.17.x | Движок плейбуков |
| httpx | 0.27.x | HTTP-клиент к Prometheus/провайдерам/прокси **и к Telegram Bot API SMS-бота** ([modules/sms](modules/sms/README.md)). **Экстра `httpx[socks]`** (транзитивно `socksio`) — обязательна для проверки `socks5`-прокси ([ADR-019](adr/ADR-019-proxies-availability-monitor.md), [modules/proxies](modules/proxies/README.md)) и опционального прокси SMS-бота (`SMS_TELEGRAM_PROXY_URL`); HTTP/HTTPS работают без неё |
| twilio | 9.x | Twilio Python SDK — валидация подписи webhook (`RequestValidator`) и синхронизация входящих номеров (`POST /api/sms/numbers/sync`), модуль «СМС» ([ADR-030](adr/ADR-030-sms-module-full-merge.md), [modules/sms](modules/sms/README.md)). SDK **синхронный** → сетевые вызовы из async-хендлера через `asyncio.to_thread`. Приём SMS и Telegram-доставка используют `httpx`/stdlib (SDK — только для подписи и Numbers API) |
| structlog | 24.x | Структурированное логирование (без секретов) |

Менеджер зависимостей: **uv** (`uv.lock` + `pyproject.toml`). Допустима `pip` + `requirements.txt`, если devops так решит — фиксируется в этом файле при изменении.

**Системные пакеты backend-образа (apt):** `openssh-client`, **`sshpass`** (обязателен для Ansible password-SSH — см. [07-deployment.md](07-deployment.md#backend-образ), [09-provisioning.md](09-provisioning.md)). Без `sshpass` провижининг по паролю падает (`"you must install the sshpass program"`).

## Frontend

| Технология | Версия | Назначение |
|-----------|--------|-----------|
| Node.js | 20 LTS | Среда сборки (целевая для CI и Docker-сборки) |
| React | 18.3.x | UI-библиотека |
| TypeScript | 5.6.x | Типизация (strict) |
| Vite | 5.4.x | Сборка/dev-сервер |
| Tailwind CSS | 3.4.x | Стилизация, дизайн-токены |
| Radix UI (primitives) | `@radix-ui/react-dialog` 1.1.x, `@radix-ui/react-tooltip` 1.1.x | Headless-примитивы (Dialog, Tooltip) |
| shadcn/ui | подход (копируемые компоненты) | Button, Input, Card, Dialog поверх Radix+Tailwind |
| TanStack Query | 5.59.x | Серверное состояние, polling, кэш метрик |
| @dnd-kit/core | 6.1.x | Drag-and-drop ядро (перестановка карточек серверов и AI-ключей) |
| @dnd-kit/sortable | 8.0.x | Sortable-пресет (список/группа) поверх `@dnd-kit/core` |
| @dnd-kit/utilities | 3.2.x | Хелперы (`CSS.Transform`) для @dnd-kit |
| React Router | 6.27.x | Роутинг (`/login`, `/servers`, `/ai-keys`, `/mail`; `/servers`, `/ai-keys`, `/mail` — под общим `AppLayout` со вкладками) |
| Zustand | 4.5.x | Лёгкое клиентское состояние (auth/токен в памяти) |
| lucide-react | 0.460.x | Иконки (server, cpu, memory, hard-drive, clock) |
| sonner | 1.7.x | Toast-уведомления |

Спидометры — **собственные SVG-компоненты** (без chart-библиотек), см. [08-design-system.md](08-design-system.md) и [ADR-005](adr/ADR-005-custom-gauge-vs-grafana-embed.md).

> **Select для формы AI-ключей — нативный `<select>`**, стилизованный Tailwind (без новой зависимости; `@radix-ui/react-select` НЕ добавляется). Причина — простота (NFR-1): два значения (OpenAI/Anthropic), доступность обеспечивает нативный контрол ([08-design-system.md](08-design-system.md#компонент-select), [modules/ai-keys](modules/ai-keys/README.md#новый-ui-примитив-select)).

> **Страница «Пользователи» — `/users` (admin-only), RBAC** ([ADR-021](adr/ADR-021-rbac-users-roles.md), [08-design-system.md](08-design-system.md#страница-пользователи)). Пункт категории «Пользователи» нового категоризированного меню (со Спринта B, [ADR-022](adr/ADR-022-teams-nav-categories.md)), **не-full-bleed**, виден только супер-админу / роли `admin`. Добавляются: маршрут `/users`, фича `features/users` (`api.ts`/`hooks.ts` на TanStack Query: users/roles/permissions-catalog/me), страница со списком пользователей (модалка create/edit: логин, **опц. пароль** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)), `Select` роли; со Спринта A — опц. **Телеграм** ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md); заменяет прежний email) и мультивыбор команд). Управление **ролями** вынесено на отдельную страницу `/roles`, **командами** — `/teams` ([ADR-022](adr/ADR-022-teams-nav-categories.md)). Новый UI-примитив **`Checkbox`** — нативный стилизованный `<input type="checkbox">` в `components/ui` (**без новой зависимости**, по образцу нативного `Select`; `@radix-ui/react-checkbox` НЕ добавляется). UI-гейтинг пунктов/кнопок — по правам из `GET /api/auth/me`; безопасность — на сервере (`403`). Backend RBAC зависимостей не добавляет (bcrypt — уже в backend-таблице выше).

> **CRM-команды + категоризированная навигация — Спринт A/B** ([ADR-022](adr/ADR-022-teams-nav-categories.md), [modules/teams](modules/teams/README.md)). **Backend (Спринт A):** новых зависимостей нет — CRM-команды (`teams`+`user_teams` M2M), `users.telegram` (замена `users.email`, [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)), `roles.user_count`, каталог RBAC += `roles`/`teams`, `/api/teams` — переиспользуют существующий стек (SQLAlchemy async, Pydantic, Alembic мигр. 0009/0010, далее 0011–0015 для беспарольных пользователей/telegram, команд без лидера, grace-алерта бэков и прокси ([ADR-027](adr/ADR-027-proxies-alert-grace.md)), метки первого входа пользователя ([ADR-028](adr/ADR-028-user-status-first-login.md))). **Frontend (Спринт B):** страницы `/roles`, `/teams`; фича `features/teams`; навигация — **3 категории-дропдауна** (Агрегатор/Мониторинг/Пользователи) взамен плоского ряда — вводит UI-примитив дропдаун-меню **`NavMenu`** (radix `@radix-ui/react-dropdown-menu` — консистентно с radix Dialog — **либо** кастом без новой зависимости; выбор — за frontend) и примитив **мультивыбор** (`MultiSelect`/checkbox-список на базе `Checkbox`, без обязательной новой зависимости) для полей «Команды»/«Участники». «Дашборд» убран из меню (доступен по прямому URL `/dashboard`), дефолт после логина — permission-aware первая доступная вкладка.

> **Модуль «Почты» — `/mail`, read-through без новых зависимостей** ([ADR-012](adr/ADR-012-mail-read-through-proxy.md), [modules/mail](modules/mail/README.md)). Добавляются: маршрут `/mail` (пункт **«Почты»** категории **«Агрегатор»** нового меню `AppLayout`, со Спринта B — [ADR-022](adr/ADR-022-teams-nav-categories.md); дефолт после логина — **permission-aware первая доступная вкладка**, не `/dashboard` — [ADR-022](adr/ADR-022-teams-nav-categories.md)), фича `features/mail` (`api.ts`/`hooks.ts` на TanStack Query, `useInfiniteQuery` для бесконечной ленты) и страница `MailPage` (master-detail, inline-reply, тулбар фильтров «С тегами» + дропдауны «Почта»/«Команда»). Новый UI-примитив **`Textarea`** (нативный стилизованный `<textarea>` в `components/ui`, для поля ответа) — **без новой зависимости**; дропдауны фильтров используют существующий примитив **`Select`**. **HTML-тело письма изолируется sandbox-iframe** (`<iframe srcDoc sandbox>` без `allow-scripts`/`allow-same-origin`) — **без DOMPurify и без иных новых зависимостей** ([08-design-system.md](08-design-system.md#компонент-textarea), [08-design-system.md](08-design-system.md#страница-почты)). Backend модуля почты **не добавляет** зависимостей и БД: переиспользует `httpx` (клиент к `postapp.store`); прокси-эндпоинты `teams`/`mailboxes` и фильтры `mail_account_id`/`group_id` — [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md).

> **Страница «Дашборд» — `/dashboard`, клиентская агрегация без backend** ([ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md), [08-design-system.md](08-design-system.md#страница-дашборд)). Со Спринта B ([ADR-022](adr/ADR-022-teams-nav-categories.md)) **убрана из меню и больше не дефолтный маршрут** — доступна только по **прямому URL** `/dashboard` под page-level view-guard `dashboard:view`; идёт по **не-full-bleed** ветке (обычный поток документа). Фича `features/dashboard` (карточки-разделы на TanStack Query) считает счётчики клиентски из существующих `GET /api/mail/mailboxes`, `GET /api/servers`, `GET /api/ai-keys` — **без нового backend-эндпоинта и без новых зависимостей**. Клик по карточке — навигация в раздел (`react-router`).

> **@dnd-kit — перестановка карточек drag-and-drop** (серверы и AI-ключи), решение — [ADR-011](adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md). Активация drag — `PointerSensor` с `activationConstraint: { delay: 200, tolerance: 5 }`: короткий клик (< 200 мс) открывает edit-модалку, зажатие ~200 мс + движение запускает перетаскивание. Вся карточка — хват (отдельной drag-ручки нет). Порядок хранится на сервере (колонка `position`, [03-data-model.md](03-data-model.md#колонка-position-порядок-карточек)). UX — [08-design-system.md](08-design-system.md#перестановка-карточек-drag-and-drop). Клавиатурная перестановка (`KeyboardSensor`) — опциональна на Этапе 1 ([TD-022](100-known-tech-debt.md)).

### Шрифты
- Основной: **Inter** (переменный, self-hosted через `@fontsource`).
- Моноширинный (метрики, IP, числа): **JetBrains Mono**.

## База данных

| Технология | Версия |
|-----------|--------|
| PostgreSQL | 16.x |

## Инфраструктура мониторинга

| Технология | Версия (Docker image) | Назначение |
|-----------|----------------------|-----------|
| Prometheus | `prom/prometheus:v2.54.1` | Хранилище метрик, file_sd |
| Grafana | `grafana/grafana:11.2.2` | Детальные дашборды (drill-down) |

### node_exporter (бинарь для Ansible)

node_exporter ставится Ansible'ом на целевые серверы как бинарь (НЕ Docker-образ, НЕ в compose). Версия зафиксирована точно — плейбук скачивает и верифицирует по SHA256.

| Параметр | Значение |
|----------|----------|
| Версия | `1.8.2` |
| Платформа | `linux-amd64` (целевые серверы — Linux x86_64, см. [00-vision.md](00-vision.md)) |
| URL | `https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz` |
| SHA256 | `6809dd0b3ec45fd6e992c19071d6b5253aed3ead7bf0686885a51d85c6643c66` |
| Порт | `9100` (`EXPORTER_PORT`) |

> SHA256 соответствует официальному `node_exporter-1.8.2.linux-amd64.tar.gz` из релиза v1.8.2 (файл `sha256sums.txt` релиза). Плейбук обязан проверять checksum после скачивания (Ansible `get_url` + `checksum: sha256:...`). Поддержка `linux-arm64` — будущий этап ([Q-PROV-1](99-open-questions.md) закрыт: источник — официальный GitHub release).

## Контейнеризация и оркестрация

| Технология | Версия |
|-----------|--------|
| Docker Engine | 27.x |
| Docker Compose | v2 (plugin) |
| nginx (frontend/proxy) | `nginx:1.27-alpine` |

## Команды (lint / format / type-check / test / build)

> Исполнители обязаны использовать ровно эти команды.

### Backend (из каталога `backend/`)
- Форматирование: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Type-check: `uv run mypy app`
- Тесты: `uv run pytest`
- Покрытие: `uv run pytest --cov=app --cov-report=term-missing`
- Миграции: `uv run alembic upgrade head`
- Dev-запуск: `uv run uvicorn app.main:app --reload`

Инструменты качества backend: **ruff** 0.7.x (lint+format), **mypy** 1.13.x (strict), **pytest** 8.x + **pytest-asyncio** + **pytest-cov**.

### Frontend (из каталога `frontend/`)
- Форматирование: `npm run format` (Prettier 3.x)
- Lint: `npm run lint` (ESLint 9.x flat config + `@typescript-eslint`)
- Type-check: `npm run typecheck` (`tsc --noEmit`)
- Тесты (unit): `npm run test` (Vitest 2.x + Testing Library)
- E2E: `npm run e2e` (Playwright 1.4x.x)
- Сборка: `npm run build`
- Dev-запуск: `npm run dev`

## Значения по умолчанию (конфиг)

Задаются через `.env` / переменные окружения. Полный перечень — [07-deployment.md](07-deployment.md#переменные-окружения).

| Параметр | Переменная | Значение по умолчанию |
|----------|-----------|------------------------|
| Scrape-интервал Prometheus | (в `prometheus.yml`) | `15s` |
| Polling-интервал UI | `VITE_POLL_INTERVAL_MS` | `15000` (15 с) |
| Порт node_exporter | `EXPORTER_PORT` | `9100` |
| TTL JWT | `JWT_EXPIRES_MIN` | `1440` (минут, 24 ч; [05-security.md](05-security.md#jwt)) |
| Алгоритм JWT | `JWT_ALGORITHM` | `HS256` |
| Таймаут Ansible-плейбука | `ANSIBLE_TIMEOUT_SEC` | `300` (5 мин) |
| Таймаут запроса к Prometheus | `PROM_QUERY_TIMEOUT_SEC` | `10` |
| TTL кэша ответа `GET /api/servers` | `METRICS_CACHE_TTL_SEC` | `5` (секунд) |
| Интервал проверки AI-ключей | `AI_KEY_CHECK_INTERVAL_SEC` | `900` (15 мин) |
| Таймаут запроса к AI-провайдеру | `AI_PROVIDER_TIMEOUT_SEC` | `10` (секунд) |
| Базовый URL OpenAI API | `OPENAI_API_BASE` | `https://api.openai.com/v1` |
| Базовый URL Anthropic API | `ANTHROPIC_API_BASE` | `https://api.anthropic.com/v1` |
| Версия Anthropic API | `ANTHROPIC_API_VERSION` | `2023-06-01` |
| Базовый URL внешнего почтового сервиса | `MAIL_API_BASE` | `https://postapp.store` |
| Таймаут запроса к почтовому сервису | `MAIL_API_TIMEOUT_SEC` | `10` (секунд) |
| Интервал проверки прокси | `PROXY_CHECK_INTERVAL_SEC` | `60` (секунд) |
| Таймаут проверки прокси (per-attempt, все фазы `httpx`) | `PROXY_CHECK_TIMEOUT_SEC` | `10` (секунд) |
| **Overall-deadline** проверки одного прокси (анти-зависание, `asyncio.wait_for`; [ADR-024](adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) | `PROXY_CHECK_DEADLINE_SEC` | `30` (секунд) |
| Эталонный URL проверки прокси | `PROXY_CHECK_URL` | `https://www.gstatic.com/generate_204` |
| **Grace-порог** непрерывной недоступности прокси перед 🔴-алертом ([ADR-027](adr/ADR-027-proxies-alert-grace.md)) | `PROXY_ALERT_AFTER_SEC` | `1800` (30 мин) |
| Интервал проверки бэков | `BACKEND_CHECK_INTERVAL_SEC` | `60` (секунд) |
| Таймаут проверки бэка (per-attempt, все фазы `httpx`) | `BACKEND_CHECK_TIMEOUT_SEC` | `10` (секунд) |
| **Overall-deadline** проверки одного бэка (анти-зависание, `asyncio.wait_for`; [ADR-024](adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) | `BACKEND_CHECK_DEADLINE_SEC` | `30` (секунд) |
| **Grace-порог** непрерывной недоступности бэка перед 🔴-алертом ([ADR-024](adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) | `BACKEND_ALERT_AFTER_SEC` | `1800` (30 мин) |
| TTL setup-токена первого входа ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)) | `PWD_SETUP_TOKEN_EXPIRES_MIN` | `10` (минут) |
| Конкурентность исходящих PromQL (семафор) | (константа backend) | `4` |
| `--query.max-concurrency` Prometheus | (флаг запуска) | `50` |
| Окно rate() для CPU | (в PromQL) | `1m` |

## Структура репозитория (целевая)

> Иллюстративная карта ключевых каталогов, не исчерпывающий листинг файлов. Отражает добавленные Этапом 1 фичи (edit, группировка AI-ключей, drag-and-drop).

```
d:\BA\CRM
├── backend/            # FastAPI приложение (app/), tests/, pyproject.toml
│   ├── app/
│   │   ├── api/        # роутеры
│   │   ├── services/   # бизнес-логика
│   │   ├── repositories/
│   │   ├── infra/      # prometheus client, ansible runner, crypto
│   │   ├── models/     # SQLAlchemy
│   │   ├── schemas/    # Pydantic
│   │   └── main.py
│   ├── alembic/
│   └── ansible/        # плейбуки и роли (см. 09-provisioning.md)
├── frontend/           # React + Vite
│   └── src/
│       ├── pages/      # LoginPage, ServersPage, AiKeysPage, MailPage
│       ├── layouts/    # AppLayout (вкладки Servers/AI Keys/Mail, общий для /servers, /ai-keys, /mail)
│       ├── components/ # Gauge, ServerCard, AddServerCard, AddServerModal,
│       │               #   AiKeyCard, AddAiKeyCard, AddAiKeyModal (add+edit), Select,
│       │               #   Sortable-обёртки (@dnd-kit) для карточек
│       ├── features/   # auth, servers, ai-keys, mail (api+hooks; reorder-мутации)
│       └── lib/        # api client, theme
├── infra/
│   ├── docker-compose.yml
│   ├── prometheus/     # prometheus.yml, targets/ (file_sd volume)
│   └── grafana/        # provisioning, dashboards
└── docs/
```
