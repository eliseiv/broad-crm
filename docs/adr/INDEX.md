# ADR · Реестр архитектурных решений

Architecture Decision Records. Каждое значимое решение — отдельный файл `ADR-NNN-<slug>.md`. Статусы: `accepted` / `superseded` / `deprecated`.

| ID | Решение | Статус | Дата |
|----|---------|--------|------|
| [ADR-001](ADR-001-stack-i-monolit.md) | Стек проекта и монолитная архитектура | accepted | 2026-06-28 |
| [ADR-002](ADR-002-dvuhshagovyy-auth.md) | Двухшаговый вход + JWT HS256 (единый endpoint) | accepted | 2026-06-28 |
| [ADR-003](ADR-003-prometheus-istochnik-metrik.md) | Prometheus — единственный источник метрик | accepted | 2026-06-28 |
| [ADR-004](ADR-004-file-sd-registraciya-targetov.md) | file_sd для динамической регистрации таргетов | accepted | 2026-06-28 |
| [ADR-005](ADR-005-custom-gauge-vs-grafana-embed.md) | Кастомные SVG-спидометры vs Grafana embed | accepted | 2026-06-28 |
| [ADR-006](ADR-006-async-provisioning-bez-brokera.md) | Асинхронный провижининг без брокера сообщений | accepted | 2026-06-28 |
| [ADR-007](ADR-007-shifrovanie-fernet.md) | Шифрование SSH-паролей через Fernet | accepted | 2026-06-28 |
| [ADR-008](ADR-008-admin-iz-env.md) | Учётка администратора только из `.env` | accepted | 2026-06-28 |
| [ADR-009](ADR-009-in-backend-notifier-vs-alertmanager.md) | In-backend Telegram-нотификатор vs Alertmanager | accepted | 2026-06-30 |
| [ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md) | Проверка AI-ключей внутри backend vs внешний воркер + Fernet-шифрование | accepted | 2026-07-01 |
| [ADR-011](ADR-011-poryadok-blokov-server-side-dnd-kit.md) | Порядок блоков — server-side `position` + @dnd-kit, drag по задержке | accepted | 2026-07-01 |
