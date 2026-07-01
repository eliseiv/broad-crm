"""Бизнес-логика реестра AI-ключей (modules/ai-keys, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid

from app.domain.ai_keys import compute_key_fragments, mask_key
from app.errors import ai_key_not_found
from app.infra.crypto import encrypt_secret
from app.logging import get_logger
from app.models.ai_key import AiKey, AiKeyStatus, AiProvider
from app.repositories.ai_key_repository import AiKeyRepository
from app.schemas.ai_key import (
    AiKeyCreateRequest,
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyStatusResponse,
)
from app.services.ai_key_monitor_service import AiKeyMonitorService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи немедленной проверки, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи) — паттерн server_service.
_background_tasks: set[asyncio.Task[None]] = set()


class AiKeyService:
    """CRUD реестра AI-ключей + запуск немедленной фоновой проверки при создании."""

    def __init__(self, repository: AiKeyRepository, monitor: AiKeyMonitorService) -> None:
        self._repo = repository
        self._monitor = monitor

    async def create_key(self, payload: AiKeyCreateRequest) -> AiKeyListItem:
        """Шифрует ключ, сохраняет (pending) и запускает немедленную проверку."""
        key_prefix, key_last4 = compute_key_fragments(payload.key)
        encrypted = encrypt_secret(payload.key)

        ai_key = await self._repo.create(
            name=payload.name,
            provider=payload.provider.value,
            key_encrypted=encrypted,
            key_prefix=key_prefix,
            key_last4=key_last4,
        )
        await self._repo.session.commit()

        # Немедленная фоновая проверка (asyncio.create_task + сильные ссылки).
        # Ошибка внутри задачи не влияет на ответ 202 — статус отслеживается через
        # GET /api/ai-keys/{id}/status.
        task = asyncio.create_task(self._monitor.check_one(ai_key.id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info("ai_key_created", ai_key_id=str(ai_key.id))
        return self._to_list_item(ai_key)

    async def list_keys(self) -> AiKeyListResponse:
        """Список ключей (created_at DESC), полный ключ не раскрывается."""
        keys = await self._repo.list_all()
        return AiKeyListResponse(items=[self._to_list_item(key) for key in keys])

    async def get_status(self, ai_key_id: uuid.UUID) -> AiKeyStatusResponse:
        """Лёгкий статус проверки; отсутствует → 404 ai_key_not_found."""
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        return AiKeyStatusResponse(
            id=ai_key.id,
            check_status=AiKeyStatus(ai_key.check_status),
            error_message=ai_key.error_message,
            last_checked_at=ai_key.last_checked_at,
        )

    async def delete_key(self, ai_key_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 ai_key_not_found."""
        deleted = await self._repo.delete_by_id(ai_key_id)
        if not deleted:
            raise ai_key_not_found()
        await self._repo.session.commit()
        logger.info("ai_key_deleted", ai_key_id=str(ai_key_id))

    @staticmethod
    def _to_list_item(ai_key: AiKey) -> AiKeyListItem:
        """Собирает элемент ответа; `key_masked` — только маска, без полного ключа."""
        return AiKeyListItem(
            id=ai_key.id,
            name=ai_key.name,
            provider=AiProvider(ai_key.provider),
            key_masked=mask_key(ai_key.key_prefix, ai_key.key_last4),
            check_status=AiKeyStatus(ai_key.check_status),
            error_message=ai_key.error_message,
            last_checked_at=ai_key.last_checked_at,
            created_at=ai_key.created_at,
            updated_at=ai_key.updated_at,
        )
