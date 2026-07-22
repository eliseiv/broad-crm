"""Запуск Ansible-плейбука через ansible-runner (09-provisioning.md, ADR-006/ADR-067).

Блокирующий вызов; выполняется в thread-executor из провижининг-сервиса.
Креды передаются во временный `private_data_dir`, удаляемый в `finally`.

Два способа входа (ADR-067 §5), ветка выбирается вызывающим по `servers.auth_method`:

- **password** — `ansible_password` в host_vars inventory, только в памяти;
- **key** — уже НЕзашифрованный OpenSSH-PEM (парольная фраза снята в памяти вызывающим)
  пишется файлом `0600` внутрь `private_data_dir` (`O_CREAT|O_EXCL`, без окна с
  umask-правами), в inventory уходит ТОЛЬКО путь (`ansible_ssh_private_key_file`);
  `ansible_password` в key-режиме не задаётся вовсе, иначе Ansible потянет `sshpass`-ветку.

`private_data_dir` создаётся в выделенном `ANSIBLE_PRIVATE_DATA_ROOT` (в проде — `tmpfs`),
а НЕ безадресным `mkdtemp()` в `/tmp`. Расшифрованные креды не логируются (05-security.md).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

# Имя файла ключа внутри private_data_dir. Материал в имени не участвует.
_KEY_FILENAME = "ssh_private_key"
_KEY_FILE_MODE = 0o600
_PRIVATE_DATA_DIR_MODE = 0o700


@dataclass(frozen=True)
class PasswordAuth:
    """Вход по SSH-паролю (`auth_method='password'`)."""

    password: str


@dataclass(frozen=True)
class KeyAuth:
    """Вход по приватному ключу: НЕзашифрованный OpenSSH-PEM (фраза уже снята в памяти)."""

    private_key_openssh: str


SshAuth = PasswordAuth | KeyAuth


@dataclass(frozen=True)
class AnsibleResult:
    """Итог прогона плейбука. error_message — без секретов (09-provisioning.md)."""

    success: bool
    error_message: str | None = None


def _classify_failure(runner: Any, target_ip: str) -> str:
    """Человекочитаемое сообщение об ошибке без секретов (таблица 09-provisioning.md)."""
    if runner.status == "timeout":
        return "provisioning timeout"
    stats = runner.stats or {}
    dark = stats.get("dark") or {}
    if target_ip in dark or dark:
        return "SSH connection failed"
    failures = stats.get("failures") or {}
    if failures:
        return "node_exporter installation failed"
    return "provisioning failed"


def _make_private_data_dir() -> str:
    """Временный `private_data_dir` внутри `ANSIBLE_PRIVATE_DATA_ROOT` (ADR-067 §5).

    Безадресный `tempfile.mkdtemp()` (в `/tmp`) ЗАПРЕЩЁН: расшифрованный ключ ушёл бы в
    общий каталог, конкурирующий со спулингом загружаемых файлов, и мимо `tmpfs`.

    В проде корень заводит backend-образ (`chown app:app`, `0700`) и перекрывает `tmpfs`
    (`mode: 0o1777` — норма ADR-067 §5). `mkdir` здесь — fallback для dev-окружения, и он
    обязан воспроизводить норму `0700` (09-provisioning.md, modules/provisioning §3а):
    `mkdir` урезается umask, поэтому режим доводится `chmod`. Режим СУЩЕСТВУЮЩЕГО корня
    не трогается — иначе fallback затирал бы `0o1777` прод-`tmpfs`, права которого задаёт
    devops. Права каталога КОНКРЕТНОГО прогона задаёт сам `mkdtemp` (`0700`), файла — `0600`.
    """
    root = Path(get_settings().ansible_private_data_root)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DATA_DIR_MODE)
        try:
            os.chmod(root, _PRIVATE_DATA_DIR_MODE)
        except OSError:
            logger.warning("ansible_private_data_root_chmod_failed", path=str(root))
    return tempfile.mkdtemp(dir=root, prefix="ansible-runner-")


def _write_key_file(private_data_dir: str, material: str) -> str:
    """Пишет ключ файлом `0600` через `O_CREAT|O_EXCL|O_WRONLY` и возвращает путь.

    Не `open()` + последующий `chmod`: между ними есть окно, когда файл существует с
    umask-правами.
    """
    path = os.path.join(private_data_dir, _KEY_FILENAME)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _KEY_FILE_MODE)
    with os.fdopen(fd, "wb") as key_file:
        key_file.write(material.encode("utf-8"))
        key_file.flush()
        os.fsync(key_file.fileno())
    return path


def _remove_key_file(path: str | None) -> None:
    """Явно удаляет файл ключа ДО `rmtree`, чтобы промах `ignore_errors` его не оставил."""
    if path is None:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("ansible_key_file_remove_failed")


def _host_vars(
    *, target_ip: str, ssh_user: str, auth: SshAuth, key_path: str | None
) -> dict[str, Any]:
    """host_vars inventory одного хоста; в key-режиме несут только ПУТЬ к файлу ключа."""
    host_vars: dict[str, Any] = {
        "ansible_host": target_ip,
        "ansible_user": ssh_user,
        "ansible_connection": "ssh",
    }
    if isinstance(auth, PasswordAuth):
        host_vars["ansible_password"] = auth.password
    else:
        # key-режим: `ansible_password` НЕ задаётся вовсе (иначе Ansible пойдёт в sshpass).
        host_vars["ansible_ssh_private_key_file"] = key_path
    return host_vars


def run_install_node_exporter(
    *,
    target_ip: str,
    ssh_user: str,
    auth: SshAuth,
    exporter_port: int,
) -> AnsibleResult:
    """Ставит node_exporter на целевой сервер. Блокирующий вызов (для to_thread)."""
    # Ленивый импорт: ansible-runner зависит от Unix-only модулей (fcntl) и нужен
    # только в рантайме провижининга (Linux-контейнер backend, 07-deployment.md).
    import ansible_runner

    settings = get_settings()
    playbook_path = Path(settings.ansible_playbook_path).resolve()
    if not playbook_path.is_file():
        logger.error("ansible_playbook_missing", path=str(playbook_path))
        return AnsibleResult(success=False, error_message="provisioning playbook not found")

    try:
        private_data_dir = _make_private_data_dir()
    except OSError:
        logger.error("ansible_private_data_dir_failed", root=settings.ansible_private_data_root)
        return AnsibleResult(success=False, error_message="provisioning failed")

    key_path: str | None = None
    try:
        os.chmod(private_data_dir, _PRIVATE_DATA_DIR_MODE)
        if isinstance(auth, KeyAuth):
            key_path = _write_key_file(private_data_dir, auth.private_key_openssh)
        host_key_checking = "True" if settings.ansible_host_key_checking else "False"
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook=str(playbook_path),
            inventory={
                "all": {
                    "hosts": {
                        target_ip: _host_vars(
                            target_ip=target_ip,
                            ssh_user=ssh_user,
                            auth=auth,
                            key_path=key_path,
                        )
                    }
                }
            },
            extravars={
                "target_ip": target_ip,
                "exporter_port": exporter_port,
                "node_exporter_version": settings.node_exporter_version,
                "node_exporter_url": settings.node_exporter_url,
                "node_exporter_sha256": settings.node_exporter_sha256,
                # Источник скрейпа для firewall на цели (TD-017); пустая строка
                # допустима — плейбук пропустит firewall-шаг.
                "scrape_source_ip": settings.scrape_source_ip,
            },
            envvars={
                "ANSIBLE_HOST_KEY_CHECKING": host_key_checking,
                "ANSIBLE_TIMEOUT": str(settings.ansible_timeout_sec),
            },
            settings={"job_timeout": settings.ansible_timeout_sec},
            quiet=True,
        )

        if runner.rc == 0 and runner.status == "successful":
            logger.info("ansible_install_succeeded", target_ip=target_ip)
            return AnsibleResult(success=True)

        message = _classify_failure(runner, target_ip)
        logger.warning(
            "ansible_install_failed",
            target_ip=target_ip,
            status=runner.status,
            rc=runner.rc,
            reason=message,
        )
        return AnsibleResult(success=False, error_message=message)
    except Exception as exc:
        logger.error("ansible_runner_exception", error_type=type(exc).__name__)
        return AnsibleResult(success=False, error_message="provisioning failed")
    finally:
        _remove_key_file(key_path)
        shutil.rmtree(private_data_dir, ignore_errors=True)
