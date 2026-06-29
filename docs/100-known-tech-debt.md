# 100 · Реестр технического долга

Формат: `TD-NNN`. Осознанные упрощения Этапа 1. Любой `TODO`/`FIXME` в коде/docs должен ссылаться на запись отсюда или на `Q-NNN-N`.

| ID | Долг | Причина (Этап 1) | Предлагаемое решение | Приоритет |
|----|------|------------------|----------------------|-----------|
| TD-001 | Нет soft-delete и аудит-лога серверов | Простота, один админ | Таблица аудита + soft-delete при многопользовательском режиме | low |
| TD-002 | node_exporter не удаляется с целевого сервера при `DELETE` | Снятие таргета достаточно для мониторинга | Плейбук `uninstall_node_exporter` | low |
| TD-003 | Нет повторного запуска провижининга при `error` (retry) | Минимизация scope | Endpoint `POST /api/servers/{id}/reprovision` | medium |
| TD-004 | Провижининг не масштабируется на несколько воркеров | Монолит, один процесс ([ADR-006](adr/ADR-006-async-provisioning-bez-brokera.md)) | Очередь задач (Celery/RQ) + блокировки | medium |
| TD-005 | Rate-limit входа — in-memory (не переживает рестарт, не распределён) | Один воркер | Вынос в Redis | low |
| TD-006 | Нет ротации `FERNET_KEY` | Простота | `MultiFernet` (новый+старый ключ), процедура ротации | medium |
| TD-007 | SSH host key checking выключен (`ANSIBLE_HOST_KEY_CHECKING=false`) | Новые серверы без known_hosts | Управление known_hosts / fingerprint-pinning ([Q-SEC-2](99-open-questions.md)) | medium |
| TD-008 | ~~Нет кэша метрик~~ | — | **resolved**: реализован короткий TTL-кэш (`METRICS_CACHE_TTL_SEC`=5) + single-flight + семафор конкурентности + ретраи ([modules/monitoring](modules/monitoring/README.md#устойчивость-read-path-нормативно)) | — |
| TD-009 | Нет UI смены пароля админа | Учётка в `.env` ([ADR-008](adr/ADR-008-admin-iz-env.md)) | При миграции на таблицу users | low |
| TD-010 | Grafana — только datasource, нет преднастроенного дашборда node_exporter | Минимизация scope Этапа 1; drill-down через Explore | Автопровижининг дашборда node_exporter в Grafana | low |
| TD-011 | LE-продление через certbot standalone даёт кратковременный downtime (`proxy` останавливается на время валидации :80) | Простой выпуск без reverse-proxy-интеграции | Zero-downtime: webroot-режим certbot или ACME-companion (без остановки `proxy`) | low |
| TD-012 | CSP содержит `style-src 'unsafe-inline'` | Tailwind/Radix используют inline-стили; без этого UI ломается ([05-security.md](05-security.md#content-security-policy-spa-location-)) | Перейти на nonce/hash для стилей (CSP3 `style-src-attr`/`'unsafe-hashes'` или сборка без inline-стилей) | medium |
| TD-013 | CPU `detail` показывает только число ядер, без частоты (GHz) | Частота (`node_cpu_scaling_frequency_*hertz`) недоступна на многих VM → разнобой; стандартизировано на ядра ([Q-MON-1](99-open-questions.md)) | Опционально добавить частоту как доп. поле, когда доступна (не вместо ядер) | low |
| TD-014 | runtime-smoke (поднятие стека до healthy + проверка health/SPA) не автоматизирован в CI | Этап 1 — ручной/полуавтоматический прогон по чек-листу ([07-deployment.md §CI/CD](07-deployment.md#cicd)) | Автоматизировать в CI: `docker compose up` + ожидание healthy + проверки `GET /api/health`/SPA | medium |
| TD-015 | Frontend-проверки не покрыты CI (линт/typecheck/сборка/`vitest`/e2e) | Решение пользователя: в CI только backend-качество + deploy; SPA валидируется сборкой образа на сервере ([07-deployment.md §CI/CD](07-deployment.md#cicd)) | Вернуть в CI frontend lint/typecheck/build + тест-гейты (vitest ≥70 %, e2e) на этапе qa | medium |
| TD-016 | Нет автоматизированного e2e-провижининга (реальный SSH-хост в CI) с проверкой `up=1` | Требует эфемерного Linux+sshd+systemd в CI; моки скрыли отсутствие `sshpass` и обрыв цепочки скрейпа ([06-testing-strategy.md](06-testing-strategy.md)) | Автоматизировать прогон плейбука против тест-хоста с password-SSH + проверку реального `up=1`; на старте — обязательная контейнерная проверка состава backend-образа (ansible/sshpass/ssh) | medium |
| TD-017 | Авто-открытие `:9100` на цели покрывает только ufw/firewalld | **Реализуется** (шаг 6 плейбука открывает `9100` для `SCRAPE_SOURCE_IP` через ufw/firewalld, graceful skip — [09-provisioning.md](09-provisioning.md#шаг-6--открытие-firewall-на-цели-нормативно-реализует-devops)). Остаточный зазор: нестандартные firewall (nftables напрямую, iptables-only, облачные security groups) и тестовое покрытие firewalld | Поддержать nftables/iptables; покрыть e2e-тестом ufw и firewalld | low |
| TD-018 | Нет гранулярной деградации per-server при недоступности Prometheus и нет нагрузочных тестов read-path | Этап 1: устойчивость покрыта кэшем/single-flight/ретраями, но per-server fallback и load-тесты не реализованы | Гранулярная деградация (последнее известное значение per-server со stale-меткой); нагрузочные тесты polling × вкладки против Prometheus | low |

## Процедура
Закрытие TD — отдельная задача исполнителя со ссылкой на `TD-NNN`. При закрытии — отметить статус и сослаться на коммит/PR.
