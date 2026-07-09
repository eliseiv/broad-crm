"""Бизнес-логика реестра AI-ключей (modules/ai-keys, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid

from app.domain.ai_keys import compute_key_fragments, mask_key
from app.errors import ai_key_not_found, unprocessable
from app.infra.crypto import decrypt_secret, encrypt_secret
from app.logging import get_logger
from app.models.ai_key import AiKey, AiKeyStatus, AiProvider
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.schemas.ai_key import (
    AiKeyCreateRequest,
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyStatusResponse,
    AiKeyUpdateRequest,
)
from app.schemas.backend import BackendRef, BackendRefListResponse
from app.services.ai_key_monitor_service import AiKeyMonitorService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи немедленной проверки, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи) — паттерн server_service.
_background_tasks: set[asyncio.Task[None]] = set()


class AiKeyService:
    """CRUD реестра AI-ключей + запуск немедленной фоновой проверки при создании."""

    def __init__(
        self,
        repository: AiKeyRepository,
        monitor: AiKeyMonitorService,
        backends: BackendRepository,
    ) -> None:
        self._repo = repository
        self._monitor = monitor
        self._backends = backends

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
        # Новый ключ ещё не используется ни одним бэком → backend_count=0.
        return self._to_list_item(ai_key, 0)

    async def list_keys(self) -> AiKeyListResponse:
        """Список ключей (position ASC, created_at DESC, id), полный ключ не раскрывается."""
        keys = await self._repo.list_all()
        counts = await self._backends.count_by_ai_keys([key.id for key in keys])
        return AiKeyListResponse(
            items=[self._to_list_item(key, counts.get(key.id, 0)) for key in keys]
        )

    async def list_ai_key_backends(self, ai_key_id: uuid.UUID) -> BackendRefListResponse:
        """Список бэков, использующих ключ (reverse-lookup, ADR-040, require ai-keys:view).

        Нет ключа → 404 ai_key_not_found. Сортировка `position ASC, created_at DESC, id`.
        """
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        backends = await self._backends.list_by_ai_key(ai_key_id)
        return BackendRefListResponse(
            backends=[BackendRef(code=b.code, name=b.name, domain=b.domain) for b in backends]
        )

    async def update_key(self, ai_key_id: uuid.UUID, payload: AiKeyUpdateRequest) -> AiKeyListItem:
        """Редактирует ключ (04-api.md, modules/ai-keys#редактирование-ключа).

        Семантика секрета: `key` пустой/отсутствует = не менять; непустой → re-encrypt
        + пересчёт `key_prefix`/`key_last4`. Re-check (`check_status='pending'`,
        `error_message=NULL`, немедленная фоновая проверка от `prev='pending'`)
        запускается, если изменился `provider` ИЛИ передан непустой `key`. Только
        смена `name` — без re-check. `updated_at` обновляется через onupdate при
        изменении любого поля. Нет записи → 404.
        """
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()

        provider_changed = (
            payload.provider is not None and payload.provider.value != ai_key.provider
        )
        # «Непустой ключ» = не None и не пустая строка (пустая = «оставить как есть»).
        key_provided = payload.key is not None and payload.key != ""

        if payload.name is not None:
            ai_key.name = payload.name
        if provider_changed:
            # mypy: provider_changed истинно ⇒ payload.provider не None.
            assert payload.provider is not None
            ai_key.provider = payload.provider.value
        if key_provided:
            # mypy: key_provided истинно ⇒ payload.key непустая строка.
            assert payload.key is not None
            key_prefix, key_last4 = compute_key_fragments(payload.key)
            ai_key.key_encrypted = encrypt_secret(payload.key)
            ai_key.key_prefix = key_prefix
            ai_key.key_last4 = key_last4

        re_check = provider_changed or key_provided
        if re_check:
            ai_key.check_status = AiKeyStatus.pending.value
            ai_key.error_message = None

        await self._repo.session.commit()
        await self._repo.session.refresh(ai_key)

        if re_check:
            # Немедленная фоновая проверка (тот же путь, что POST; prev='pending').
            task = asyncio.create_task(self._monitor.check_one(ai_key.id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        logger.info("ai_key_updated", ai_key_id=str(ai_key_id), re_check=re_check)
        counts = await self._backends.count_by_ai_keys([ai_key.id])
        return self._to_list_item(ai_key, counts.get(ai_key.id, 0))

    async def reorder_keys(self, provider: AiProvider, ids: list[uuid.UUID]) -> None:
        """Перестановка ключей ВНУТРИ провайдер-группы: `position = 0..M-1`.

        Прецеденция ошибок (04-api.md#прецеденция-ошибок-валидации): форма тела и
        `provider` вне enum уже обработаны pydantic (400/422); здесь — существование
        всех `id` (404, до полноты), затем полнота перестановки группы провайдера
        (422; чужой провайдер трактуется как «лишний» → 422).
        """
        all_ids = await self._repo.all_ids()
        for ai_key_id in ids:
            if ai_key_id not in all_ids:
                raise ai_key_not_found()
        group_ids = await self._repo.ids_by_provider(provider.value)
        if len(ids) != len(group_ids) or set(ids) != group_ids:
            raise unprocessable("Список не является полной перестановкой ключей провайдера")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("ai_keys_reordered", provider=provider.value, count=len(ids))

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

    async def reveal_key(self, ai_key_id: uuid.UUID) -> str:
        """On-demand reveal ПОЛНОГО ключа (ADR-035, require ai-keys:edit).

        Расшифровка `key_encrypted` в памяти обработчика (в обычных ответах — только
        `key_masked`). Нет записи → 404 ai_key_not_found. Значение возвращается
        роутеру и НЕ логируется здесь.
        """
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        return decrypt_secret(ai_key.key_encrypted)

    async def delete_key(self, ai_key_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 ai_key_not_found."""
        deleted = await self._repo.delete_by_id(ai_key_id)
        if not deleted:
            raise ai_key_not_found()
        await self._repo.session.commit()
        logger.info("ai_key_deleted", ai_key_id=str(ai_key_id))

    @staticmethod
    def _to_list_item(ai_key: AiKey, backend_count: int) -> AiKeyListItem:
        """Собирает элемент ответа; `key_masked` — только маска, без полного ключа."""
        return AiKeyListItem(
            id=ai_key.id,
            name=ai_key.name,
            provider=AiProvider(ai_key.provider),
            key_masked=mask_key(ai_key.key_prefix, ai_key.key_last4),
            check_status=AiKeyStatus(ai_key.check_status),
            error_message=ai_key.error_message,
            position=ai_key.position,
            last_checked_at=ai_key.last_checked_at,
            created_at=ai_key.created_at,
            updated_at=ai_key.updated_at,
            backend_count=backend_count,
        )
