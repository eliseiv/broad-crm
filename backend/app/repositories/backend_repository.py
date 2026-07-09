"""Репозиторий реестра бэков (SQLAlchemy 2.0 async, modules/backends)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_key import AiKey
from app.models.server import Server
from app.models.service_backend import Backend, BackendStatus


class BackendRepository:
    """CRUD-операции над таблицей `backends` + обновление статуса проверки."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(
        self,
        *,
        code: str,
        name: str,
        domain: str,
        server_id: uuid.UUID | None = None,
        ai_key_id: uuid.UUID | None = None,
        api_key_encrypted: bytes | None = None,
        admin_api_key_encrypted: bytes | None = None,
        git: str | None = None,
        note: str | None = None,
    ) -> Backend:
        """Создаёт бэк со статусом pending (check_status по умолчанию)."""
        backend = Backend(
            code=code,
            name=name,
            domain=domain,
            server_id=server_id,
            ai_key_id=ai_key_id,
            api_key_encrypted=api_key_encrypted,
            admin_api_key_encrypted=admin_api_key_encrypted,
            git=git,
            note=note,
            check_status=BackendStatus.pending.value,
        )
        self._session.add(backend)
        await self._session.flush()
        await self._session.refresh(backend)
        return backend

    async def server_exists(self, server_id: uuid.UUID) -> bool:
        """True, если сервер с таким id существует (валидация FK `server_id`, 422)."""
        stmt = select(Server.id).where(Server.id == server_id).limit(1)
        return (await self._session.execute(stmt)).first() is not None

    async def ai_key_exists(self, ai_key_id: uuid.UUID) -> bool:
        """True, если ИИ-ключ с таким id существует (валидация FK `ai_key_id`, 422)."""
        stmt = select(AiKey.id).where(AiKey.id == ai_key_id).limit(1)
        return (await self._session.execute(stmt)).first() is not None

    async def server_names(self, server_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
        """`{server_id: name}` для набора серверов (join имён без N+1, ADR-040).

        Принимает и `None` (незаданные связи) — они отфильтровываются.
        """
        ids = [sid for sid in server_ids if sid is not None]
        if not ids:
            return {}
        stmt = select(Server.id, Server.name).where(Server.id.in_(ids))
        rows = (await self._session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def ai_key_names(self, ai_key_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
        """`{ai_key_id: name}` для набора ИИ-ключей (join имён без N+1, ADR-040).

        Принимает и `None` (незаданные связи) — они отфильтровываются.
        """
        ids = [kid for kid in ai_key_ids if kid is not None]
        if not ids:
            return {}
        stmt = select(AiKey.id, AiKey.name).where(AiKey.id.in_(ids))
        rows = (await self._session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def count_by_servers(self, server_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{server_id: число бэков}` для набора серверов (агрегат `backend_count`, ADR-040)."""
        ids = list(server_ids)
        if not ids:
            return {}
        stmt = (
            select(Backend.server_id, func.count())
            .where(Backend.server_id.in_(ids))
            .group_by(Backend.server_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {sid: int(cnt) for sid, cnt in rows if sid is not None}

    async def count_by_ai_keys(self, ai_key_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{ai_key_id: число бэков}` для набора ИИ-ключей (агрегат `backend_count`, ADR-040)."""
        ids = list(ai_key_ids)
        if not ids:
            return {}
        stmt = (
            select(Backend.ai_key_id, func.count())
            .where(Backend.ai_key_id.in_(ids))
            .group_by(Backend.ai_key_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {kid: int(cnt) for kid, cnt in rows if kid is not None}

    async def list_by_server(self, server_id: uuid.UUID) -> list[Backend]:
        """Бэки, связанные с сервером (reverse-lookup, ADR-040).

        Сортировка `position ASC, created_at DESC, id` (как основной список).
        """
        stmt = (
            select(Backend)
            .where(Backend.server_id == server_id)
            .order_by(Backend.position.asc(), Backend.created_at.desc(), Backend.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_ai_key(self, ai_key_id: uuid.UUID) -> list[Backend]:
        """Бэки, использующие ИИ-ключ (reverse-lookup, ADR-040). Та же сортировка."""
        stmt = (
            select(Backend)
            .where(Backend.ai_key_id == ai_key_id)
            .order_by(Backend.position.asc(), Backend.created_at.desc(), Backend.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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
        error_since: datetime | None,
        alert_sent: bool,
    ) -> None:
        """Атомарно обновляет результат проверки одним UPDATE (modules/backends, ADR-024).

        Помимо check_status/error_message/last_checked_at обновляет grace-состояние
        эпизода недоступности (`error_since`/`alert_sent`), персистентно переживающее
        рестарт backend.
        """
        stmt = (
            update(Backend)
            .where(Backend.id == backend_id)
            .values(
                check_status=status,
                error_message=error_message,
                last_checked_at=last_checked_at,
                error_since=error_since,
                alert_sent=alert_sent,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
