"""Асинхронный провижининг сервера (modules/provisioning, 09-provisioning.md, ADR-006).

Фоновая задача в backend-процессе: installing → ansible-runner (thread-executor)
→ online/error + file_sd. Без внешнего брокера.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.infra import file_sd
from app.infra.ansible import run_install_node_exporter
from app.infra.crypto import CryptoError, decrypt_password
from app.logging import get_logger
from app.models.server import ProvisionStatus
from app.repositories.server_repository import ServerRepository

logger = get_logger(__name__)


class ProvisioningService:
    """Оркестрация Ansible-провижининга и управление file_sd."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings

    async def provision_server(self, server_id: uuid.UUID) -> None:
        """Полный цикл провижининга одного сервера (фоновая задача)."""
        creds = await self._begin_installing(server_id)
        if creds is None:
            return
        ip, ssh_user, ssh_password, exporter_port, name = creds

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_install_node_exporter,
                    target_ip=ip,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                    exporter_port=exporter_port,
                ),
                timeout=self._settings.ansible_timeout_sec + 30,
            )
        except TimeoutError:
            logger.warning("provisioning_timeout", server_id=str(server_id))
            await self._finish_error(server_id, "provisioning timeout")
            return
        finally:
            del ssh_password

        if result.success:
            await self._finish_online(server_id, ip=ip, exporter_port=exporter_port, name=name)
        else:
            await self._finish_error(server_id, result.error_message or "provisioning failed")

    async def _begin_installing(
        self, server_id: uuid.UUID
    ) -> tuple[str, str, str, int, str] | None:
        """Переводит в installing и расшифровывает креды в памяти."""
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
            encrypted = server.ssh_password_encrypted

        try:
            ssh_password = decrypt_password(encrypted)
        except CryptoError:
            logger.error("provisioning_decrypt_failed", server_id=str(server_id))
            await self._finish_error(server_id, "provisioning failed")
            return None

        logger.info("provisioning_started", server_id=str(server_id))
        return ip, ssh_user, ssh_password, exporter_port, name

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
