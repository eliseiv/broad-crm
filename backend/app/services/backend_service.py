"""Бизнес-логика реестра бэков (modules/backends, 04-api.md#backends)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.exc import IntegrityError

from app.errors import backend_code_taken, backend_not_found, unprocessable
from app.infra.backend_check import is_valid_domain, normalize_domain
from app.logging import get_logger
from app.models.service_backend import Backend, BackendStatus
from app.repositories.backend_repository import BackendRepository
from app.schemas.backend import (
    BackendCreateRequest,
    BackendListItem,
    BackendListResponse,
    BackendStatusResponse,
    BackendUpdateRequest,
)
from app.services.backend_monitor_service import BackendMonitorService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи немедленной проверки, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи) — паттерн proxy_service.
_background_tasks: set[asyncio.Task[None]] = set()

_INVALID_DOMAIN_MESSAGE = "Невалидный формат домена"


def _normalize_and_validate_domain(raw: str) -> str:
    """Нормализует домен и валидирует формат; невалидный → 422 unprocessable."""
    domain = normalize_domain(raw)
    if not is_valid_domain(domain):
        raise unprocessable(
            _INVALID_DOMAIN_MESSAGE,
            details=[{"field": "domain", "message": _INVALID_DOMAIN_MESSAGE}],
        )
    return domain


class BackendService:
    """CRUD реестра бэков + запуск немедленной фоновой проверки при create/edit."""

    def __init__(self, repository: BackendRepository, monitor: BackendMonitorService) -> None:
        self._repo = repository
        self._monitor = monitor

    async def create_backend(self, payload: BackendCreateRequest) -> BackendListItem:
        """Валидирует/нормализует домен (422), проверяет уникальность `code` (409),
        сохраняет (pending), запускает немедленную проверку.

        Прецеденция (04-api.md#post-apibackends): схемная валидация (400/422) до
        проверки уникальности `code` (409).
        """
        domain = _normalize_and_validate_domain(payload.domain)

        if await self._repo.exists_by_code(payload.code):
            raise backend_code_taken()

        try:
            backend = await self._repo.create(
                code=payload.code,
                name=payload.name,
                domain=domain,
            )
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("backend_create_conflict", code=payload.code)
            raise backend_code_taken() from exc

        self._schedule_check(backend.id)
        logger.info("backend_created", backend_id=str(backend.id))
        return self._to_list_item(backend)

    async def list_backends(self) -> BackendListResponse:
        """Список бэков (position ASC, created_at DESC, id)."""
        backends = await self._repo.list_all()
        return BackendListResponse(items=[self._to_list_item(backend) for backend in backends])

    async def update_backend(
        self, backend_id: uuid.UUID, payload: BackendUpdateRequest
    ) -> BackendListItem:
        """Редактирует бэк (04-api.md#patch-apibackendsid, modules/backends).

        Прецеденция: 404 (нет id) → схемная валидация (400/422 — формат домена) →
        уникальность `code` (409). Re-check (pending + немедленная проверка от
        `prev='pending'`) — только при смене `domain`. Смена `code`/`name` статус
        не трогает. `updated_at` обновляется при изменении хотя бы одного поля.
        """
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()

        fields_set = payload.model_fields_set

        # 1) Домен: нормализация + валидация (422) до проверки уникальности code (409).
        domain_changed = False
        if "domain" in fields_set and payload.domain is not None:
            new_domain = _normalize_and_validate_domain(payload.domain)
            domain_changed = new_domain != backend.domain
            backend.domain = new_domain

        # 2) Код: уникальность (409) при смене на занятый ДРУГИМ бэком.
        if "code" in fields_set and payload.code is not None and payload.code != backend.code:
            if await self._repo.exists_by_code(payload.code, exclude_id=backend.id):
                raise backend_code_taken()
            backend.code = payload.code

        # 3) Имя.
        if "name" in fields_set and payload.name is not None:
            backend.name = payload.name

        # Re-check только при смене домена (единственное поле подключения).
        if domain_changed:
            backend.check_status = BackendStatus.pending.value
            backend.error_message = None

        try:
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("backend_update_conflict", backend_id=str(backend_id))
            raise backend_code_taken() from exc
        await self._repo.session.refresh(backend)

        if domain_changed:
            self._schedule_check(backend.id)

        logger.info("backend_updated", backend_id=str(backend_id), re_check=domain_changed)
        return self._to_list_item(backend)

    async def reorder_backends(self, ids: list[uuid.UUID]) -> None:
        """Перестановка единого списка: `position = 0..N-1` в одной транзакции.

        Прецеденция ошибок (04-api.md#прецеденция-ошибок-валидации): форма тела уже
        проверена pydantic (400); здесь — существование всех `id` (404, до полноты),
        затем полнота перестановки множества бэков (422).
        """
        all_ids = await self._repo.all_ids()
        for backend_id in ids:
            if backend_id not in all_ids:
                raise backend_not_found()
        if len(ids) != len(all_ids) or set(ids) != all_ids:
            raise unprocessable("Список не является полной перестановкой бэков")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("backends_reordered", count=len(ids))

    async def get_status(self, backend_id: uuid.UUID) -> BackendStatusResponse:
        """Лёгкий статус проверки; отсутствует → 404 backend_not_found."""
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        return BackendStatusResponse(
            id=backend.id,
            check_status=BackendStatus(backend.check_status),
            error_message=backend.error_message,
            last_checked_at=backend.last_checked_at,
        )

    async def delete_backend(self, backend_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 backend_not_found."""
        deleted = await self._repo.delete_by_id(backend_id)
        if not deleted:
            raise backend_not_found()
        await self._repo.session.commit()
        logger.info("backend_deleted", backend_id=str(backend_id))

    def _schedule_check(self, backend_id: uuid.UUID) -> None:
        """Fire-and-forget немедленная проверка (asyncio.create_task + сильная ссылка).

        Ошибка внутри задачи не влияет на ответ — статус отслеживается через
        GET /api/backends/{id}/status.
        """
        task = asyncio.create_task(self._monitor.check_one(backend_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    def _to_list_item(backend: Backend) -> BackendListItem:
        """Собирает элемент ответа (все поля публичны, ADR-020)."""
        return BackendListItem(
            id=backend.id,
            code=backend.code,
            name=backend.name,
            domain=backend.domain,
            check_status=BackendStatus(backend.check_status),
            error_message=backend.error_message,
            position=backend.position,
            last_checked_at=backend.last_checked_at,
            created_at=backend.created_at,
            updated_at=backend.updated_at,
        )
