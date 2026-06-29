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
