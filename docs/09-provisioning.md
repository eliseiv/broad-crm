# 09 · Провижининг (Ansible)

## Цель

При добавлении сервера backend **автоматически**: по SSH ставит node_exporter на целевой Linux-сервер, поднимает его как systemd-сервис на порту 9100, регистрирует scrape-таргет Prometheus через file_sd. Процесс асинхронный, статус отражается в `provision_status` ([ADR-006](adr/ADR-006-async-provisioning-bez-brokera.md)).

## Жизненный цикл

```mermaid
sequenceDiagram
    participant SVC as provisioning service
    participant DB as PostgreSQL
    participant AR as ansible-runner
    participant T as целевой сервер
    participant SD as file_sd
    SVC->>DB: status=installing
    SVC->>SVC: decrypt креда в памяти (пароль ИЛИ ключ+passphrase, по auth_method)
    SVC->>SVC: key-режим: снять passphrase, записать ключ 0600 во временный каталог
    SVC->>AR: run playbook install_node_exporter (host=ip, user, pass ИЛИ private_key_file)
    AR->>T: SSH → скачать/распаковать node_exporter, systemd unit, enable+start
    AR-->>SVC: rc=0 (успех) / rc!=0 (ошибка)
    alt успех
        SVC->>SD: write targets/<id>.json
        SVC->>DB: status=online
    else ошибка
        SVC->>DB: status=error, error_message (без секретов)
    end
```

## Запуск

- Библиотека **ansible-runner** (вызов из backend-процесса), движок **ansible-core** в backend-образе.
- **Предусловие controller (backend-образ):** установлены `ansible-core`, `openssh-client` и **`sshpass`**. `sshpass` ОБЯЗАТЕЛЕН для password-аутентификации по SSH (`ansible_password`) — без него Ansible завершает таск ошибкой `"you must install the sshpass program"`, а добавление сервера падает (в UI — `«node_exporter installation failed»`). Требование **сохраняется и после [ADR-067](adr/ADR-067-server-ssh-key-auth.md)** (парольный вход остаётся; в key-режиме `sshpass` не задействован). Зафиксировано в [07-deployment.md](07-deployment.md#backend-образ) и [02-tech-stack.md](02-tech-stack.md#backend).
- **Предусловие для key-режима:** `private_data_dir` создаётся в выделенном **`ANSIBLE_PRIVATE_DATA_ROOT`** (`tempfile.mkdtemp(dir=…)`, default `/var/run/crm/ansible`) — каталог заведён в образе (`chown app:app`, `0700`) и в проде перекрыт **`tmpfs`** с **`mode: 0o1777`** (поле числовое: значение в кавычках отвергается на разборе и роняет `docker compose config`/`up` целиком, а голое `1777` разберётся десятично и даст `0o3361`; [07-deployment.md](07-deployment.md#tmpfs-для-приватных-данных-ansible-нормативно-adr-067-5)). ⛔ `mode=1700` (равно как и `0o3361` из голого `1777`) ломает `mkdtemp` под non-root `app` и убивает **весь** провижининг, включая парольный; ⛔ `tmpfs` на `/tmp` не монтируется (там спулятся загружаемые файлы).
- На Этапе 1 — асинхронная фоновая задача (FastAPI background task / `asyncio` task, запускающая ansible-runner в thread/executor, т.к. он блокирующий). Без внешнего брокера ([ADR-006](adr/ADR-006-async-provisioning-bez-brokera.md)).
- Таймаут — `ANSIBLE_TIMEOUT_SEC` (по умолчанию 300 с); по таймауту → `status=error`.

## Передача кредов (безопасно)

Способ входа выбирает **`servers.auth_method`** ([ADR-067](adr/ADR-067-server-ssh-key-auth.md)); ветка определяется **только** этим полем, а не наличием/отсутствию материала (CHECK `ck_servers_auth_material` гарантирует их согласованность).

### Ветка `auth_method='password'` (без изменений)

- SSH-пароль расшифровывается из БД (`FERNET_KEY`) только в памяти, непосредственно перед запуском.
- Передаётся в ansible-runner через inventory/`extravars` **в памяти** (`ansible_user`, `ansible_password`). Не писать в постоянные файлы.
- На тасках с паролем — `no_log: true`. Расшифрованный пароль не логируется ни на каком уровне ([05-security.md](05-security.md)).

### Ветка `auth_method='key'` (нормативно, [ADR-067](adr/ADR-067-server-ssh-key-auth.md) §5)

1. `decrypt_secret(ssh_private_key_encrypted)` (+ `ssh_key_passphrase_encrypted`, если задана) — **в памяти**.
2. **Парольная фраза снимается в памяти:** ключ загружается `cryptography` и **пере-сериализуется в незашифрованный OpenSSH-PEM**. Фраза дальше **не идёт никуда** — ни в файл, ни в env, ни в argv, ни в лог.
3. Ключ пишется файлом **внутри уже существующего временного `private_data_dir`** (`tempfile.mkdtemp` → каталог `0700`), создание — `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)`. **Не `open()` + последующий `chmod`:** между ними есть окно, когда файл существует с umask-правами.
4. В host_vars inventory — `ansible_ssh_private_key_file: "<путь>"` (+ `ansible_user`, `ansible_connection: ssh`). **`ansible_password` в key-режиме не задаётся вовсе** (иначе Ansible потянет `sshpass`-ветку).
5. `finally`: явный `os.remove` файла ключа, затем существующий `shutil.rmtree(private_data_dir, ignore_errors=True)` — чтобы промах `ignore_errors` по каталогу не оставил именно ключ.

> **Почему не `ansible_runner.run(ssh_key=…)`** (штатный параметр, который сам пишет ключ и поднимает `ssh-agent`): на **зашифрованном** ключе `ssh-add` уходит в интерактивное приглашение ввода фразы и висит до `job_timeout` ⇒ добавление сервера молча превращалось бы в `provisioning timeout`. Снятие фразы в памяти (шаг 2) убирает интерактив целиком. `sshpass -P passphrase … ssh-add` отклонён — фраза оказалась бы в argv (видна в `ps`).

> **Логи key-ветки безопасны by construction:** inventory содержит **только путь** к файлу; материал ключа в inventory/`extravars`/логи не попадает. `no_log` здесь ничего не защищает — защищать в inventory нечего.

- `ANSIBLE_HOST_KEY_CHECKING=false` на Этапе 1 ([TD-007](100-known-tech-debt.md)) — одинаково для обеих веток.

## Привилегии (`become`)

Плейбук создаёт системного пользователя, кладёт бинарь в `/usr/local/bin`, ставит systemd unit и перезапускает службу — это требует root-привилегий, поэтому таски выполняются с `become: true`.

**Допущение Этапа 1 (нормативно):** целевой SSH-пользователь (поле «Пользователь» в модалке) — **либо `root`, либо пользователь с passwordless `sudo`** (`NOPASSWD`). В обоих случаях `ansible_become_password` НЕ требуется и НЕ передаётся.

- Если указан `root` — `become` фактически no-op (уже root), плейбук работает.
- Если указан sudoer с `NOPASSWD` — `become: true` поднимает привилегии без пароля.
- **Sudoer, требующий пароль для sudo, на Этапе 1 НЕ поддерживается** — провижининг такого хоста завершится `status=error` с понятным сообщением. Поддержка `ansible_become_password` (отдельный sudo-пароль) — [Q-SEC-3](99-open-questions.md).

Это допущение зафиксировано также в [05-security.md](05-security.md#ansible-и-секреты). UI/модалка добавления может кратко информировать админа о требовании (root или passwordless sudo) — рекомендация для frontend.

## Плейбук `install_node_exporter` (требования, нормативно для devops)

Идемпотентный плейбук (NFR-6). Шаги:
1. Определить архитектуру/ОС (Linux, x86_64/arm64).
2. Создать системного пользователя `node_exporter` (nologin) — идемпотентно.
3. Скачать node_exporter точной версии и **проверить SHA256** (URL и контрольная сумма — [02-tech-stack.md](02-tech-stack.md#node_exporter-бинарь-для-ansible); Ansible `get_url` с `checksum: "sha256:<...>"`), распаковать в `/usr/local/bin/node_exporter` (только если версия/хэш не совпадают).
4. Установить systemd unit `/etc/systemd/system/node_exporter.service` (слушает `:{{ exporter_port }}`, default 9100).
5. `systemd daemon-reload`, `enable` + `start`/`restart при изменении`.
6. **Открыть `exporter_port` на firewall цели ТОЛЬКО для IP CRM-сервера** (если задан `scrape_source_ip` — см. ниже). Идемпотентно.
7. Проверка: порт слушается, сервис `active`.

Параметры передаются как extravars/host_vars: `target_ip`, `ansible_user`, `exporter_port`, **`scrape_source_ip`** и — в зависимости от `auth_method` — **либо** `ansible_password` (password-режим), **либо** `ansible_ssh_private_key_file` (key-режим, [ADR-067](adr/ADR-067-server-ssh-key-auth.md)). Оба одновременно не передаются никогда.

> **Плейбук от способа входа не зависит и правки не требует** — аутентификация целиком в транспорте connection-плагина. Шаги 1–7 и допущение о `become` (root/NOPASSWD-sudo) одинаковы для обеих веток.

Идемпотентность: повторный прогон не даёт `changed` (кроме реальных изменений версии/конфига).

### Шаг 6 — открытие firewall на цели (нормативно, реализует devops)

Реализует [TD-017](100-known-tech-debt.md). Prometheus-контейнер достукивается до `<target_ip>:9100`; исходящий трафик приходит с публичного IP CRM-сервера (SNAT) = `scrape_source_ip`. На цели нужно разрешить `9100` именно с этого IP.

- **Источник `scrape_source_ip`** — extravar из переменной окружения backend `SCRAPE_SOURCE_IP` (на этом деплое `37.27.192.211`).
- **Если `scrape_source_ip` пуст/не задан → задача firewall ПОЛНОСТЬЮ пропускается** (предполагается, что на цели firewall открыт/отсутствует; хосты без firewall не ломаем).
- **Если задан** — идемпотентно открыть `exporter_port` **только для этого IP**, с поддержкой распространённых firewall:
  - **ufw**: если установлен и активен → `ufw allow from {{ scrape_source_ip }} to any port {{ exporter_port }} proto tcp` (идемпотентно).
  - **firewalld**: если активен → rich rule `rule family=ipv4 source address={{ scrape_source_ip }} port port={{ exporter_port }} protocol=tcp accept`, `permanent=yes` + reload (идемпотентно).
  - Firewall не установлен/не активен → задача **пропускается без ошибки** (graceful skip, `failed_when: false`/условия по факту наличия).
- **Никогда не открывать `9100` миру** (`0.0.0.0/0`). Только конкретный `scrape_source_ip`.
- Нестандартные firewall (nftables напрямую, iptables-only, облачные security groups) на Этапе 1 не покрываются плейбуком — остаточный [TD-017](100-known-tech-debt.md).

## Регистрация таргета (file_sd)

После успешной установки backend пишет файл `${FILE_SD_DIR}/<id>.json`:

```json
[
  {
    "targets": ["10.0.0.13:9100"],
    "labels": {
      "server_id": "a1b2c3d4-...",
      "name": "Server 02"
    }
  }
]
```

- Каталог `FILE_SD_DIR` — общий volume с Prometheus (`/etc/prometheus/targets`).
- Prometheus перечитывает каталог (`refresh_interval: 30s`), рестарт не нужен ([ADR-004](adr/ADR-004-file-sd-registraciya-targetov.md)).
- Запись атомарна: писать во временный файл и `os.replace()` на финальный, чтобы Prometheus не прочитал полу-записанный JSON.
- Метки `server_id`/`name` позволяют сопоставлять метрики с реестром и подписывать в Grafana.
- **Права доступа (нормативно, усвоенный урок).** Backend пишет файлы под своим uid (`app`), Prometheus читает под другим uid (read-only mount) → target-файлы ОБЯЗАНЫ быть **world-readable**: файлы `0644`, каталог `${FILE_SD_DIR}` — `0755`. Иначе Prometheus не может прочитать таргеты, скрейп не стартует, `up` отсутствует, сервер показывается «Не в сети» при успешной установке агента. Требование к backend: при атомарной записи явно `chmod 0644` финальный файл (umask может дать `0600`); каталог создавать с `0755`. `uid` пользователя `app` **запинен** (`999`, `backend/Dockerfile`) — [07-deployment.md §Пин uid/gid](07-deployment.md#пин-uidgid-пользователя-app-нормативно); для `file_sd` это менее критично (файлы world-readable), но volume наследует владельца из образа так же, как `documents-attachments`.

## Definition of done провижининга (нормативно)

Провижининг считается **успешным только когда метрики реально текут — `up=1` в Prometheus**, а не только когда node_exporter установлен. Цепочка «установка → file_sd → скрейп» имеет несколько точек отказа (права file_sd, firewall :9100), невидимых на этапе установки агента.

- Рекомендация к backend: перед/при переводе в `provision_status=online` проверять достижимость — например, опрос `up{instance="<ip>:<port>"}==1` через Prometheus API (или прямой запрос `http://<ip>:<port>/metrics`) с коротким ретраем. Если агент установлен, но `up≠1` в отведённое окно — `provision_status=error` с понятной причиной (`exporter not reachable`), а не «online».
- Это согласуется с [03-data-model.md](03-data-model.md): `online` = «провижининг завершён», а фактический online/offline в UI определяется `up` ([04-api.md](04-api.md)). DoD требует, чтобы на момент `online` `up` уже был `1`.
- Автоматизированная e2e-проверка `up=1` — [TD-016](100-known-tech-debt.md) (расширить happy-path провижининга проверкой реального скрейпа).

## Сетевая доступность node_exporter (`:9100`)

Prometheus скрейпит `<target_ip>:${EXPORTER_PORT}` (9100). Порт ОБЯЗАН быть доступен с источника (Prometheus-контейнер); иначе `up=0`, сервер «Не в сети» при успешной установке агента (усвоенный урок).

Два разных случая (важно не путать источник трафика):

- **Remote-цели (другой сервер):** firewall `9100` открывается **автоматически плейбуком** (шаг 6) для IP CRM-сервера `scrape_source_ip` (`SCRAPE_SOURCE_IP`, по умолчанию `37.27.192.211`) — трафик Prometheus-контейнера приходит на цель с публичного IP CRM-сервера (SNAT). Поддержка ufw/firewalld, graceful skip. Если `SCRAPE_SOURCE_IP` пуст — плейбук firewall не трогает (предусловие: порт уже открыт). Детали — [шаг 6](#шаг-6--открытие-firewall-на-цели-нормативно-реализует-devops).
- **Self-host (мониторинг самого CRM-сервера):** источник скрейпа — **docker-подсеть CRM**, НЕ публичный IP, поэтому `SCRAPE_SOURCE_IP`/плейбук здесь **не применимы**. Хостовый ufw разрешает `9100` из docker-сети CRM отдельно — [07-deployment.md](07-deployment.md#сетевая-настройка-сервера-self-monitoring). node_exporter слушает на хосте, Prometheus идёт из docker-сети.
- Рекомендация безопасности: всегда ограничивать `9100` конкретным источником (IP CRM для remote / docker-подсеть для self), не открывать миру — node_exporter наружу не публикуется (NFR-9, [05-security.md](05-security.md)).

## Удаление

- `DELETE /api/servers/{id}` → удалить `${FILE_SD_DIR}/<id>.json` → Prometheus перестаёт скрейпить.
- node_exporter на целевом сервере НЕ удаляется на Этапе 1 ([TD-002](100-known-tech-debt.md)). Плейбук `uninstall_node_exporter` — будущий этап.

## Восстановление file_sd из БД

- file_sd — производное состояние. При старте backend (или по команде) может перегенерировать `targets/*.json` из реестра серверов со `status=online` (устойчивость к потере volume). Рекомендация для backend ([modules/provisioning](modules/provisioning/README.md)).

## Обработка ошибок

| Ситуация | Результат |
|----------|-----------|
| SSH-недоступность / неверные креды (пароль **или** ключ не принят хостом) | `status=error`, `error_message="SSH connection failed"` (без секретов) |
| **Ключ расшифровался, но не загружается `cryptography`** (ротация `FERNET_KEY`, ручная правка строки) — key-режим | `status=error`, `error_message="SSH key unusable"` ([ADR-067](adr/ADR-067-server-ssh-key-auth.md) §5). **Отдельно от `"SSH connection failed"` намеренно:** иначе оператор чинил бы сеть/firewall вместо кредов. До целевого хоста при этом не доходит ни одного пакета |
| Таймаут плейбука | `status=error`, `error_message="provisioning timeout"` |
| Ошибка установки (rc≠0) | `status=error`, краткая причина из stderr (отфильтрованная от секретов) |
| Успех, но порт не слушается | `status=error`, `error_message="exporter not reachable"` |

Сообщения об ошибках — человекочитаемые, без секретов; полные логи ansible-runner — в structlog (с маскированием), не в API-ответе.

## Smoke-тест провижининга (для qa/devops)

- Поднять эфемерный Linux-контейнер с sshd + systemd; прогнать плейбук; проверить `:9100/metrics` отвечает; повторный прогон — идемпотентен. Объём — [06-testing-strategy.md](06-testing-strategy.md).
