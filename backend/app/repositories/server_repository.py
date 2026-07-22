"""Репозиторий реестра серверов (SQLAlchemy 2.0 async)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import ProvisionStatus, Server, ServerAuthMethod


class ServerRepository:
    """CRUD-операции над таблицей `servers`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(
        self,
        *,
        name: str,
        ip: str,
        ssh_user: str,
        auth_method: ServerAuthMethod,
        ssh_password_encrypted: bytes | None,
        ssh_private_key_encrypted: bytes | None,
        ssh_key_passphrase_encrypted: bytes | None,
        exporter_port: int,
    ) -> Server:
        """Создаёт запись сервера со статусом pending.

        Согласованность материала («ровно один способ», ADR-067 §1) сверх сервиса
        гарантирует CHECK `ck_servers_auth_material` в БД.
        """
        server = Server(
            name=name,
            ip=ip,
            ssh_user=ssh_user,
            auth_method=auth_method.value,
            ssh_password_encrypted=ssh_password_encrypted,
            ssh_private_key_encrypted=ssh_private_key_encrypted,
            ssh_key_passphrase_encrypted=ssh_key_passphrase_encrypted,
            exporter_port=exporter_port,
            provision_status=ProvisionStatus.pending.value,
        )
        self._session.add(server)
        await self._session.flush()
        await self._session.refresh(server)
        return server

    async def list_all(self, *, status: str | None = None) -> list[Server]:
        """Список серверов, сортировка `position ASC, created_at DESC, id` (04-api.md)."""
        stmt = select(Server)
        if status is not None:
            stmt = stmt.where(Server.provision_status == status)
        stmt = stmt.order_by(Server.position.asc(), Server.created_at.desc(), Server.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, server_id: uuid.UUID) -> Server | None:
        """Возвращает сервер по id или None."""
        return await self._session.get(Server, server_id)

    async def update_name(self, server_id: uuid.UUID, *, name: str) -> Server | None:
        """Меняет `name` (updated_at обновляется через onupdate). None, если нет записи."""
        server = await self._session.get(Server, server_id)
        if server is None:
            return None
        server.name = name
        await self._session.flush()
        await self._session.refresh(server)
        return server

    async def all_ids(self) -> set[uuid.UUID]:
        """Множество id всех серверов — для валидации полной перестановки (reorder)."""
        result = await self._session.execute(select(Server.id))
        return set(result.scalars().all())

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        """Присваивает `position = 0..N-1` по индексу в массиве (одна транзакция).

        Коммит выполняет вызывающий сервис. Валидация полноты перестановки —
        тоже в сервисе (04-api.md#прецеденция-ошибок-валидации).
        """
        for index, server_id in enumerate(ordered_ids):
            await self._session.execute(
                update(Server).where(Server.id == server_id).values(position=index)
            )

    async def exists_by_ip(self, ip: str) -> bool:
        """Проверяет наличие сервера с таким IP (для 409)."""
        stmt = select(Server.id).where(Server.ip == ip).limit(1)
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def delete_by_id(self, server_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(Server).where(Server.id == server_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def update_status(
        self,
        server_id: uuid.UUID,
        *,
        status: ProvisionStatus,
        error_message: str | None = None,
    ) -> None:
        """Атомарно обновляет provision_status (+error_message, +updated_at)."""
        stmt = (
            update(Server)
            .where(Server.id == server_id)
            .values(
                provision_status=status.value,
                error_message=error_message,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)

    async def find_stuck_installing(self, *, older_than: datetime) -> list[Server]:
        """Зависшие installing старше порога — для recovery-hook (ADR-006)."""
        stmt = select(Server).where(
            Server.provision_status == ProvisionStatus.installing.value,
            Server.updated_at < older_than,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_online(self) -> list[Server]:
        """Серверы со статусом online — для регенерации file_sd при старте."""
        stmt = select(Server).where(Server.provision_status == ProvisionStatus.online.value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
