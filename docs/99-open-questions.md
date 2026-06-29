# 99 · Открытые вопросы

Формат: `Q-<AREA>-<N>`. Открытые вопросы НЕ блокируют старт реализации Этапа 1 (для каждого есть дефолт), но требуют подтверждения пользователя/решения архитектора.

| ID | Вопрос | Дефолт на Этапе 1 | Статус |
|----|--------|-------------------|--------|
| Q-SEC-1 | Хранить `ADMIN_PASSWORD` как plaintext в `.env` или bcrypt-хэш `ADMIN_PASSWORD_HASH`? | Plaintext + constant-time сравнение | open |
| Q-SEC-2 | Включать ли SSH host key checking (known_hosts) при провижининге? | Выключено (`false`), риск принят ([TD-007](100-known-tech-debt.md)) | open |
| Q-MON-1 | Как отображать CPU `detail` в карточке? | — | **resolved (v2, решение пользователя)**: CPU `detail` **всегда число логических ядер** — `unit:"cores"`, `value:null`, `total`=число ядер (если недоступно → `total:null`). Вариант с частотой (`GHz`) убран ради единообразия между серверами и отложен в [TD-013](100-known-tech-debt.md). См. [04-api.md](04-api.md#схема-объекта-метрики-и-detail), [02-promql.md](modules/monitoring/02-promql.md) |
| Q-MON-2 | Какой mountpoint считать «SSD» при нескольких дисках? | `/` (корневой) | open |
| Q-PROV-1 | Источник бинаря node_exporter в плейбуке (GitHub release vs внутренний mirror)? | — | **resolved**: официальный GitHub release v1.8.2 (`linux-amd64`) + проверка SHA256. См. [02-tech-stack.md](02-tech-stack.md#node_exporter-бинарь-для-ansible) |
| Q-UI-1 | Нужны ли мини-тренды/история на карточке главной или только текущее значение? | Только текущее значение; история — в Grafana drill-down | open |
| Q-DEP-1 | CI-движок (GitHub Actions vs GitLab CI)? | — | **resolved**: **GitHub Actions** (`.github/workflows/ci.yml`), jobs `lint → test → deploy`. См. [07-deployment.md §CI/CD](07-deployment.md#cicd) |
| Q-DEP-2 | Container registry для публикации образов? | — | **resolved (closed)**: registry на Этапе 1 **не используется**. Деплой = `rsync` рабочего дерева на сервер + `docker compose up -d --build` (образы собираются на сервере). См. [07-deployment.md §CI/CD](07-deployment.md#cicd) |
| Q-SEC-3 | Поддержка sudoer с паролем (`ansible_become_password`) для провижининга? | Этап 1 — только `root` или passwordless sudo (`NOPASSWD`); sudoer-с-паролем не поддерживается. См. [09-provisioning.md](09-provisioning.md#привилегии-become) | open |
| Q-SEC-4 | Точное нормативное значение CSP для SPA и разделение ответственности backend/nginx за security-заголовки? | — | **resolved**: CSP ратифицирован (`default-src 'self'; …; frame-ancestors 'none'; …`), backend ставит 4 заголовка для `/api` (`setdefault`), nginx — те же 4 + CSP для SPA (`add_header always`), без дублей. См. [05-security.md](05-security.md#content-security-policy-spa-location-) и [TD-012](100-known-tech-debt.md) |

## Процедура закрытия
Каждый вопрос закрывается либо решением архитектора (с обновлением соответствующего документа/ADR), либо новым ADR. При закрытии — изменить статус на `resolved` и проставить ссылку на решение.
