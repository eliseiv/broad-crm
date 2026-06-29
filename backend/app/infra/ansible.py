"""Запуск Ansible-плейбука через ansible-runner (09-provisioning.md, ADR-006).

Блокирующий вызов; выполняется в thread-executor из провижининг-сервиса.
Креды передаются через extravars во временный private_data_dir, удаляемый в finally.
Расшифрованный пароль не логируется (05-security.md).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)


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


def run_install_node_exporter(
    *,
    target_ip: str,
    ssh_user: str,
    ssh_password: str,
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

    private_data_dir = tempfile.mkdtemp(prefix="ansible-runner-")
    try:
        host_key_checking = "True" if settings.ansible_host_key_checking else "False"
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook=str(playbook_path),
            inventory={
                "all": {
                    "hosts": {
                        target_ip: {
                            "ansible_host": target_ip,
                            "ansible_user": ssh_user,
                            "ansible_password": ssh_password,
                            "ansible_connection": "ssh",
                        }
                    }
                }
            },
            extravars={
                "target_ip": target_ip,
                "exporter_port": exporter_port,
                "node_exporter_version": settings.node_exporter_version,
                "node_exporter_url": settings.node_exporter_url,
                "node_exporter_sha256": settings.node_exporter_sha256,
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
        shutil.rmtree(private_data_dir, ignore_errors=True)
