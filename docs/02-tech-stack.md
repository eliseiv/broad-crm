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
| ansible-runner | 2.4.x | Программный запуск Ansible из backend |
| ansible-core | 2.17.x | Движок плейбуков |
| httpx | 0.27.x | HTTP-клиент к Prometheus API |
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
| React Router | 6.27.x | Роутинг (`/login`, `/servers`) |
| Zustand | 4.5.x | Лёгкое клиентское состояние (auth/токен в памяти) |
| lucide-react | 0.460.x | Иконки (server, cpu, memory, hard-drive, clock) |
| sonner | 1.7.x | Toast-уведомления |

Спидометры — **собственные SVG-компоненты** (без chart-библиотек), см. [08-design-system.md](08-design-system.md) и [ADR-005](adr/ADR-005-custom-gauge-vs-grafana-embed.md).

> **Select для формы AI-ключей — нативный `<select>`**, стилизованный Tailwind (без новой зависимости; `@radix-ui/react-select` НЕ добавляется). Причина — простота (NFR-1): два значения (OpenAI/Anthropic), доступность обеспечивает нативный контрол ([08-design-system.md](08-design-system.md#компонент-select), [modules/ai-keys](modules/ai-keys/README.md#новый-ui-примитив-select)).

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
| TTL JWT | `JWT_EXPIRES_MIN` | `60` (минут) |
| Алгоритм JWT | `JWT_ALGORITHM` | `HS256` |
| Таймаут Ansible-плейбука | `ANSIBLE_TIMEOUT_SEC` | `300` (5 мин) |
| Таймаут запроса к Prometheus | `PROM_QUERY_TIMEOUT_SEC` | `10` |
| TTL кэша ответа `GET /api/servers` | `METRICS_CACHE_TTL_SEC` | `5` (секунд) |
| Интервал проверки AI-ключей | `AI_KEY_CHECK_INTERVAL_SEC` | `900` (15 мин) |
| Таймаут запроса к AI-провайдеру | `AI_PROVIDER_TIMEOUT_SEC` | `10` (секунд) |
| Базовый URL OpenAI API | `OPENAI_API_BASE` | `https://api.openai.com/v1` |
| Базовый URL Anthropic API | `ANTHROPIC_API_BASE` | `https://api.anthropic.com/v1` |
| Версия Anthropic API | `ANTHROPIC_API_VERSION` | `2023-06-01` |
| Конкурентность исходящих PromQL (семафор) | (константа backend) | `4` |
| `--query.max-concurrency` Prometheus | (флаг запуска) | `50` |
| Окно rate() для CPU | (в PromQL) | `1m` |

## Структура репозитория (целевая)

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
│       ├── pages/      # LoginPage, ServersPage
│       ├── components/ # Gauge, ServerCard, AddServerCard, AddServerModal
│       ├── features/   # auth, servers (api+hooks)
│       └── lib/        # api client, theme
├── infra/
│   ├── docker-compose.yml
│   ├── prometheus/     # prometheus.yml, targets/ (file_sd volume)
│   └── grafana/        # provisioning, dashboards
└── docs/
```
