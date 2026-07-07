"""Репозиторий реестра бэков (SQLAlchemy 2.0 async, modules/backends)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_backend import Backend, BackendStatus


class BackendRepository:
    """CRUD-операции над таблицей `backends` + обновление статуса проверки."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(self, *, code: str, name: str, domain: str) -> Backend:
        """Создаёт бэк со статусом pending (check_status по умолчанию)."""
        backend = Backend(
            code=code,
            name=name,
            domain=domain,
            check_status=BackendStatus.pending.value,
        )
        self._session.add(backend)
        await self._session.flush()
        await self._session.refresh(backend)
        return backend

    async def list_all(self) -> list[Backend]:
        """Все бэки, сортировка `position ASC, created_at DESC, id` (04-api.md).

        Используется как для списка API, так и для снимка бэков монитором
        (для монитора порядок несуществен).
        """
        stmt = select(Backend).order_by(
            Backend.position.asc(), Backend.created_at.desc(), Backend.id.asc()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, backend_id: uuid.UUID) -> Backend | None:
        """Возвращает бэк по id или None."""
        return await self._session.get(Backend, backend_id)

    async def exists_by_code(self, code: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Проверяет, занят ли `code` (для 409 backend_code_taken).

        `exclude_id` исключает сам редактируемый бэк из проверки (PATCH): смена
        `code` на занятый ДРУГИМ бэком → конфликт, сохранение своего же — нет.
        """
        stmt = select(Backend.id).where(Backend.code == code)
        if exclude_id is not None:
            stmt = stmt.where(Backend.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def all_ids(self) -> set[uuid.UUID]:
        """Множество id всех бэков — для валидации полной перестановки (reorder)."""
        result = await self._session.execute(select(Backend.id))
        return set(result.scalars().all())

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        """Присваивает `position = 0..N-1` по индексу в массиве (одна транзакция).

        Вызывается только после валидации полной перестановки; коммит выполняет
        вызывающий сервис.
        """
        for index, backend_id in enumerate(ordered_ids):
            await self._session.execute(
                update(Backend).where(Backend.id == backend_id).values(position=index)
            )

    async def delete_by_id(self, backend_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(Backend).where(Backend.id == backend_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def update_check(
        self,
        backend_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: datetime,
    ) -> None:
        """Атомарно обновляет результат проверки (check_status, error_message,
        last_checked_at, updated_at) одним UPDATE (modules/backends)."""
        stmt = (
            update(Backend)
            .where(Backend.id == backend_id)
            .values(
                check_status=status,
                error_message=error_message,
                last_checked_at=last_checked_at,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
