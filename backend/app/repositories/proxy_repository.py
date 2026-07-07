"""Репозиторий реестра прокси (SQLAlchemy 2.0 async, modules/proxies)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proxy import Proxy, ProxyStatus


class ProxyRepository:
    """CRUD-операции над таблицей `proxies` + обновление статуса проверки."""

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
        proxy_type: str,
        host: str,
        port: int,
        username: str | None,
        password_encrypted: bytes | None,
    ) -> Proxy:
        """Создаёт прокси со статусом pending (check_status по умолчанию)."""
        proxy = Proxy(
            name=name,
            proxy_type=proxy_type,
            host=host,
            port=port,
            username=username,
            password_encrypted=password_encrypted,
            check_status=ProxyStatus.pending.value,
        )
        self._session.add(proxy)
        await self._session.flush()
        await self._session.refresh(proxy)
        return proxy

    async def list_all(self) -> list[Proxy]:
        """Все прокси, сортировка `position ASC, created_at DESC, id` (04-api.md).

        Используется как для списка API, так и для снимка прокси монитором
        (для монитора порядок несуществен).
        """
        stmt = select(Proxy).order_by(Proxy.position.asc(), Proxy.created_at.desc(), Proxy.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, proxy_id: uuid.UUID) -> Proxy | None:
        """Возвращает прокси по id или None."""
        return await self._session.get(Proxy, proxy_id)

    async def all_ids(self) -> set[uuid.UUID]:
        """Множество id всех прокси — для валидации полной перестановки (reorder)."""
        result = await self._session.execute(select(Proxy.id))
        return set(result.scalars().all())

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        """Присваивает `position = 0..N-1` по индексу в массиве (одна транзакция).

        Вызывается только после валидации полной перестановки; коммит выполняет
        вызывающий сервис.
        """
        for index, proxy_id in enumerate(ordered_ids):
            await self._session.execute(
                update(Proxy).where(Proxy.id == proxy_id).values(position=index)
            )

    async def delete_by_id(self, proxy_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(Proxy).where(Proxy.id == proxy_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def update_check(
        self,
        proxy_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: datetime,
    ) -> None:
        """Атомарно обновляет результат проверки (check_status, error_message,
        last_checked_at, updated_at) одним UPDATE (modules/proxies)."""
        stmt = (
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(
                check_status=status,
                error_message=error_message,
                last_checked_at=last_checked_at,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
