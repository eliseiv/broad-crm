"""Асинхронный провижининг сервера (modules/provisioning, 09-provisioning.md, ADR-006/067).

Фоновая задача в backend-процессе: installing → ansible-runner (thread-executor)
→ online/error + file_sd. Без внешнего брокера.

Ветка входа выбирается **ТОЛЬКО по `servers.auth_method`**, а не по «что не `NULL`»
(согласованность материала гарантирует CHECK `ck_servers_auth_material`). В key-режиме
парольная фраза снимается **в памяти** (пере-сериализация в незашифрованный OpenSSH-PEM)
и дальше не идёт никуда — ни в файл, ни в env, ни в argv, ни в лог.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.ssh_keys import SshKeyError, to_openssh_unencrypted
from app.infra import file_sd
from app.infra.ansible import KeyAuth, PasswordAuth, SshAuth, run_install_node_exporter
from app.infra.crypto import CryptoError, decrypt_secret
from app.logging import get_logger
from app.models.server import ProvisionStatus, ServerAuthMethod
from app.repositories.server_repository import ServerRepository

logger = get_logger(__name__)

# Ключ расшифровался/не расшифровался, но непригоден к использованию — ОТДЕЛЬНОЕ сообщение
# от "SSH connection failed" (09-provisioning.md#обработка-ошибок): иначе оператор чинил бы
# сеть вместо кредов. До целевого хоста при этом не уходит ни одного пакета.
_KEY_UNUSABLE = "SSH key unusable"


@dataclass(frozen=True, slots=True)
class _ProvisionTarget:
    """Расшифрованные в памяти параметры одного прогона плейбука."""

    ip: str
    ssh_user: str
    exporter_port: int
    name: str
    auth: SshAuth


class ProvisioningService:
    """Оркестрация Ansible-провижининга и управление file_sd."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings

    async def provision_server(self, server_id: uuid.UUID) -> None:
        """Полный цикл провижининга одного сервера (фоновая задача)."""
        target = await self._begin_installing(server_id)
        if target is None:
            return
        ip, ssh_user, exporter_port, name = (
            target.ip,
            target.ssh_user,
            target.exporter_port,
            target.name,
        )
        # Материал входа держим отдельной ссылкой, чтобы снять её сразу после прогона.
        auth = target.auth
        del target

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_install_node_exporter,
                    target_ip=ip,
                    ssh_user=ssh_user,
                    auth=auth,
                    exporter_port=exporter_port,
                ),
                timeout=self._settings.ansible_timeout_sec + 30,
            )
        except TimeoutError:
            logger.warning("provisioning_timeout", server_id=str(server_id))
            await self._finish_error(server_id, "provisioning timeout")
            return
        finally:
            del auth

        if result.success:
            await self._finish_online(server_id, ip=ip, exporter_port=exporter_port, name=name)
        else:
            await self._finish_error(server_id, result.error_message or "provisioning failed")

    async def _begin_installing(self, server_id: uuid.UUID) -> _ProvisionTarget | None:
        """Переводит в installing и расшифровывает материал входа в памяти."""
        async with self._sessionmaker() as session:
            repo = ServerRepository(session)
            server = await repo.get_by_id(server_id)
            if server is None:
                logger.warning("provisioning_server_missing", server_id=str(server_id))
                return None
            await repo.update_status(server_id, status=ProvisionStatus.installing)
            await session.commit()
            ip = str(server.ip)
            ssh_user = server.ssh_user
            exporter_port = server.exporter_port
            name = server.name
            auth_method = server.auth_method
            password_encrypted = server.ssh_password_encrypted
            key_encrypted = server.ssh_private_key_encrypted
            passphrase_encrypted = server.ssh_key_passphrase_encrypted

        if auth_method == ServerAuthMethod.key.value:
            auth = await self._key_auth(server_id, key_encrypted, passphrase_encrypted)
        else:
            auth = await self._password_auth(server_id, password_encrypted)
        if auth is None:
            return None

        logger.info("provisioning_started", server_id=str(server_id), auth_method=auth_method)
        return _ProvisionTarget(
            ip=ip, ssh_user=ssh_user, exporter_port=exporter_port, name=name, auth=auth
        )

    async def _password_auth(
        self, server_id: uuid.UUID, password_encrypted: bytes | None
    ) -> SshAuth | None:
        """Password-ветка: расшифровка пароля в памяти. Сбой → `error` (без секретов)."""
        if password_encrypted is None:
            logger.error("provisioning_material_missing", server_id=str(server_id))
            await self._finish_error(server_id, "provisioning failed")
            return None
        try:
            return PasswordAuth(password=decrypt_secret(password_encrypted))
        except CryptoError:
            logger.error("provisioning_decrypt_failed", server_id=str(server_id))
            await self._finish_error(server_id, "provisioning failed")
            return None

    async def _key_auth(
        self,
        server_id: uuid.UUID,
        key_encrypted: bytes | None,
        passphrase_encrypted: bytes | None,
    ) -> SshAuth | None:
        """Key-ветка: расшифровка ключа (+фразы) и снятие фразы **в памяти** (ADR-067 §5).

        Битая расшифровка (например, после ротации `FERNET_KEY`) и неразбираемый ключ →
        `status=error`, `error_message="SSH key unusable"` — отдельно от «SSH connection
        failed». Ни материал, ни текст исключения в лог не идут.
        """
        if key_encrypted is None:
            logger.error("provisioning_material_missing", server_id=str(server_id))
            await self._finish_error(server_id, _KEY_UNUSABLE)
            return None
        try:
            key_material = decrypt_secret(key_encrypted)
            passphrase = (
                decrypt_secret(passphrase_encrypted) if passphrase_encrypted is not None else None
            )
        except CryptoError:
            logger.error("provisioning_decrypt_failed", server_id=str(server_id))
            await self._finish_error(server_id, _KEY_UNUSABLE)
            return None

        try:
            return KeyAuth(private_key_openssh=to_openssh_unencrypted(key_material, passphrase))
        except SshKeyError:
            logger.error("provisioning_key_unusable", server_id=str(server_id))
            await self._finish_error(server_id, _KEY_UNUSABLE)
            return None
        finally:
            del key_material
            del passphrase

    async def _finish_online(
        self, server_id: uuid.UUID, *, ip: str, exporter_port: int, name: str
    ) -> None:
        """Регистрирует file_sd-таргет и помечает online."""
        try:
            file_sd.write_target(server_id=server_id, ip=ip, exporter_port=exporter_port, name=name)
        except OSError:
            logger.error("file_sd_write_failed", server_id=str(server_id))
            await self._finish_error(server_id, "target registration failed")
            return
        async with self._sessionmaker() as session:
            repo = ServerRepository(session)
            await repo.update_status(server_id, status=ProvisionStatus.online)
            await session.commit()
        logger.info("provisioning_succeeded", server_id=str(server_id))

    async def _finish_error(self, server_id: uuid.UUID, message: str) -> None:
        """Помечает сервер статусом error с человекочитаемым сообщением (без секретов)."""
        async with self._sessionmaker() as session:
            repo = ServerRepository(session)
            await repo.update_status(server_id, status=ProvisionStatus.error, error_message=message)
            await session.commit()
        logger.info("provisioning_error", server_id=str(server_id), reason=message)

    async def recover_stuck_installing(self) -> int:
        """Recovery-hook: зависшие installing старше таймаута → error (ADR-006)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.ansible_timeout_sec)
        async with self._sessionmaker() as session:
            repo = ServerRepository(session)
            stuck = await repo.find_stuck_installing(older_than=cutoff)
            for server in stuck:
                await repo.update_status(
                    server.id,
                    status=ProvisionStatus.error,
                    error_message="provisioning interrupted (backend restart)",
                )
            await session.commit()
        if stuck:
            logger.info("provisioning_recovery", count=len(stuck))
        return len(stuck)

    async def regenerate_file_sd(self) -> int:
        """Перегенерирует targets/*.json из online-серверов (устойчивость к потере volume)."""
        async with self._sessionmaker() as session:
            repo = ServerRepository(session)
            online_servers = await repo.list_online()
        for server in online_servers:
            try:
                file_sd.write_target(
                    server_id=server.id,
                    ip=str(server.ip),
                    exporter_port=server.exporter_port,
                    name=server.name,
                )
            except OSError:
                logger.error("file_sd_regen_failed", server_id=str(server.id))
        if online_servers:
            logger.info("file_sd_regenerated", count=len(online_servers))
        return len(online_servers)
