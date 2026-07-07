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
| [ADR-008](ADR-008-admin-iz-env.md) | Учётка администратора только из `.env` (амендмент — супер-админ поверх RBAC, [ADR-021](ADR-021-rbac-users-roles.md)) | accepted | 2026-06-28 |
| [ADR-009](ADR-009-in-backend-notifier-vs-alertmanager.md) | In-backend Telegram-нотификатор vs Alertmanager | accepted | 2026-06-30 |
| [ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md) | Проверка AI-ключей внутри backend vs внешний воркер + Fernet-шифрование | accepted | 2026-07-01 |
| [ADR-011](ADR-011-poryadok-blokov-server-side-dnd-kit.md) | Порядок блоков — server-side `position` + @dnd-kit, drag по задержке | accepted | 2026-07-01 |
| [ADR-012](ADR-012-mail-read-through-proxy.md) | Модуль «Почты» — read-through-прокси без хранения (ключ на backend, HTML-изоляция sandbox-iframe) | accepted | 2026-07-03 |
| [ADR-013](ADR-013-mail-newest-first-master-detail-inline-reply.md) | Почты — newest-first backward-пагинация, master-detail layout, inline-reply (расширяет ADR-012) | accepted | 2026-07-04 |
| [ADR-014](ADR-014-persist-notifier-state-alert-on-first-elevated.md) | Персистентность состояния нотификатора в БД + alert-on-first-elevated (закрывает TD-019) | accepted | 2026-07-04 |
| [ADR-015](ADR-015-csp-img-src-remote-mail-images.md) | CSP `img-src` — разрешение удалённых (https) изображений писем (расширяет ADR-012, XSS-инвариант не ослаблен) | accepted | 2026-07-06 |
| [ADR-016](ADR-016-notifier-max-over-window-zone.md) | Нотификатор оценивает зону по `max_over_time` за окно опроса (ловит транзиентные всплески между опросами; UI-карточки — по-прежнему мгновенное значение; усиливает ADR-014) | accepted | 2026-07-06 |
| [ADR-017](ADR-017-dashboard-client-aggregation-mail-server-filters.md) | Страница «Дашборд» (клиентская агрегация счётчиков, без backend-агрегатора) + серверные фильтры «Почт» по ящику/команде (external ADR-0037; частично снимает TD-024) | accepted | 2026-07-06 |
| [ADR-018](ADR-018-notifier-windowed-offline-recovery-alert-log.md) | Нотификатор: windowed offline-детект (`min_over_time` для `up`) + recovery-уведомления (`offline→online`) + durable-лог отправленных алертов (`notifier_alert_log`); расширяет ADR-016/ADR-014, пороги не меняются | accepted | 2026-07-07 |
| [ADR-019](ADR-019-proxies-availability-monitor.md) | Страница «Прокси» — реестр HTTP/SOCKS-прокси + отдельный in-backend монитор доступности (по образцу AI-ключей ADR-010, статус в БД) + Fernet-шифрование пароля + отдельные поля ввода + Telegram-алерты | accepted | 2026-07-07 |
| [ADR-020](ADR-020-backends-healthcheck-monitor.md) | Страница «Бэки» — реестр сервисов (Код/Название/Домен, `code` UNIQUE) + отдельный in-backend монитор healthcheck `GET https://{домен}/health` (по образцу прокси ADR-019, статус в БД, без секрета/Fernet) + Telegram-алерты | accepted | 2026-07-07 |
| [ADR-021](ADR-021-rbac-users-roles.md) | Пользователи + роли + RBAC на все страницы (каталог прав на сервере, `users`/`roles` в БД, bcrypt-хэш паролей, `.env`-админ → несменяемый супер-админ вне БД, JWT `role`/`uid`/`superadmin`, enforcement `require(page,action)`/`403 forbidden`, свежая загрузка прав из БД; амендмент ADR-008) | accepted | 2026-07-07 |
