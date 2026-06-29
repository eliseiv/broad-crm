# Модуль `provisioning` — Ansible-провижининг

Статус: `spec-ready` · Исполнители: backend, devops

## Scope
Асинхронная установка node_exporter на целевой Linux-сервер через Ansible и регистрация file_sd-таргета. Детали — [09-provisioning.md](../../09-provisioning.md). Решения — [ADR-004](../../adr/ADR-004-file-sd-registraciya-targetov.md), [ADR-006](../../adr/ADR-006-async-provisioning-bez-brokera.md).

## Backend — ТЗ
1. Сервис `provision_server(id)`: `status=installing` → decrypt пароля → запуск ansible-runner в thread-executor → `online`/`error`.
2. Передача кредов в `extravars` в памяти (`ansible_user`, `ansible_password`, `target_ip`, `exporter_port`); без записи пароля на диск; `no_log`.
3. Таймаут `ANSIBLE_TIMEOUT_SEC` → `error`.
4. Запись `targets/<id>.json` атомарно (temp + `os.replace`) в `FILE_SD_DIR`; формат — [09-provisioning.md](../../09-provisioning.md#регистрация-таргета-file_sd).
5. Удаление: удалить `targets/<id>.json`.
6. Регенерация file_sd из БД при старте (устойчивость к потере volume).
7. Ошибки — человекочитаемые, без секретов; полный вывод — в structlog с маскированием.

## DevOps — ТЗ
1. Плейбук `ansible/install_node_exporter.yml` — идемпотентный (NFR-6), шаги — [09-provisioning.md](../../09-provisioning.md#плейбук-install_node_exporter).
2. node_exporter фиксированной версии ([02-tech-stack.md](../../02-tech-stack.md)), systemd unit, порт `EXPORTER_PORT`.
3. Backend-образ содержит `ansible-core` + `openssh-client` + **`sshpass`** (последний ОБЯЗАТЕЛЕН для password-SSH; без него провижининг падает с `"you must install the sshpass program"`). См. [07-deployment.md](../../07-deployment.md#backend-образ).
4. `ANSIBLE_HOST_KEY_CHECKING=false` на Этапе 1 ([TD-007](../../100-known-tech-debt.md)).
5. **Открытие firewall на цели (шаг 6 плейбука):** если extravar `scrape_source_ip` задан — идемпотентно открыть `exporter_port` ТОЛЬКО для этого IP; поддержать **ufw** (активен) и **firewalld** (активен); firewall не установлен/не активен → graceful skip без ошибки; НИКОГДА не открывать миру. Если `scrape_source_ip` пуст — задача пропускается. Контракт — [09-provisioning.md](../../09-provisioning.md#шаг-6--открытие-firewall-на-цели-нормативно-реализует-devops).
6. Smoke-тест идемпотентности — [06-testing-strategy.md](../../06-testing-strategy.md).

## DoD
- [ ] Провижининг переводит статусы корректно (pending→installing→online/error); `online` только при реальном `up=1`.
- [ ] file_sd регистрируется/удаляется (права `0644`/каталог `0755`), Prometheus подхватывает таргет.
- [ ] Плейбук идемпотентен, секреты не логируются.
- [ ] Firewall цели (шаг 6) открывает `9100` только для `scrape_source_ip` (ufw/firewalld, graceful skip), не миру; при пустом `scrape_source_ip` — пропуск.
- [ ] Тесты ([06-testing-strategy.md](../../06-testing-strategy.md)) зелёные.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
