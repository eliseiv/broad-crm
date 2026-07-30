"""Бизнес-логика реестра AI-ключей (modules/ai-keys, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, TypedDict

from app.domain.ai_keys import compute_key_fragments, mask_key
from app.errors import ai_key_bad_request, ai_key_not_found, secret_not_set, unprocessable
from app.infra.ai_provider_billing import compute_alert_level, default_low_threshold_usd
from app.infra.crypto import decrypt_secret, encrypt_secret
from app.logging import get_logger
from app.models.ai_key import (
    AiKey,
    AiKeyStatus,
    AiProvider,
    BalanceAlertLevel,
    BalanceSyncStatus,
)
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.schemas.ai_key import (
    AiKeyBalanceResetRequest,
    AiKeyCreateRequest,
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyStatusResponse,
    AiKeyUpdateRequest,
)
from app.schemas.backend import BackendRef, BackendRefListResponse
from app.services.ai_key_balance_sync_service import AiKeyBalanceSyncService
from app.services.ai_key_monitor_service import AiKeyMonitorService

logger = get_logger(__name__)

_background_tasks: set[asyncio.Task[None]] = set()


class AiKeyService:
    """CRUD реестра AI-ключей + фоновые health-check и balance-sync."""

    def __init__(
        self,
        repository: AiKeyRepository,
        monitor: AiKeyMonitorService,
        balance_sync: AiKeyBalanceSyncService,
        backends: BackendRepository,
    ) -> None:
        self._repo = repository
        self._monitor = monitor
        self._balance_sync = balance_sync
        self._backends = backends

    async def create_key(self, payload: AiKeyCreateRequest) -> AiKeyListItem:
        """Шифрует ключ, сохраняет (pending) и запускает немедленную проверку."""
        key_prefix, key_last4 = compute_key_fragments(payload.key)
        encrypted = encrypt_secret(payload.key)

        balance_fields = _balance_create_fields(payload)
        ai_key = await self._repo.create(
            name=payload.name,
            provider=payload.provider.value,
            key_encrypted=encrypted,
            key_prefix=key_prefix,
            key_last4=key_last4,
            **balance_fields,
        )
        await self._repo.session.commit()

        self._spawn_monitor(ai_key.id)
        if ai_key.balance_monitoring_enabled:
            self._spawn_balance_sync(ai_key.id)

        logger.info("ai_key_created", ai_key_id=str(ai_key.id))
        return self._to_list_item(ai_key, 0)

    async def list_keys(self) -> AiKeyListResponse:
        keys = await self._repo.list_all()
        counts = await self._backends.count_by_ai_keys([key.id for key in keys])
        return AiKeyListResponse(
            items=[self._to_list_item(key, counts.get(key.id, 0)) for key in keys]
        )

    async def list_ai_key_backends(self, ai_key_id: uuid.UUID) -> BackendRefListResponse:
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        backends = await self._backends.list_by_ai_key(ai_key_id)
        return BackendRefListResponse(
            backends=[BackendRef(code=b.code, name=b.name, domain=b.domain) for b in backends]
        )

    async def update_key(self, ai_key_id: uuid.UUID, payload: AiKeyUpdateRequest) -> AiKeyListItem:
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()

        provider_changed = (
            payload.provider is not None and payload.provider.value != ai_key.provider
        )
        key_provided = payload.key is not None and payload.key != ""

        if payload.name is not None:
            ai_key.name = payload.name
        if provider_changed:
            assert payload.provider is not None
            ai_key.provider = payload.provider.value
        if key_provided:
            assert payload.key is not None
            key_prefix, key_last4 = compute_key_fragments(payload.key)
            ai_key.key_encrypted = encrypt_secret(payload.key)
            ai_key.key_prefix = key_prefix
            ai_key.key_last4 = key_last4
            ai_key.provider_api_key_id = None

        balance_changed = _apply_balance_patch(ai_key, payload)

        re_check = provider_changed or key_provided
        if re_check:
            ai_key.check_status = AiKeyStatus.pending.value
            ai_key.error_message = None

        await self._repo.session.commit()
        await self._repo.session.refresh(ai_key)

        if re_check:
            self._spawn_monitor(ai_key.id)
        if balance_changed and ai_key.balance_monitoring_enabled:
            self._spawn_balance_sync(ai_key.id)

        logger.info("ai_key_updated", ai_key_id=str(ai_key_id), re_check=re_check)
        counts = await self._backends.count_by_ai_keys([ai_key.id])
        return self._to_list_item(ai_key, counts.get(ai_key.id, 0))

    async def reset_balance(
        self, ai_key_id: uuid.UUID, payload: AiKeyBalanceResetRequest
    ) -> AiKeyListItem:
        """Новый якорь остатка после пополнения (ADR-070)."""
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        if not ai_key.balance_monitoring_enabled:
            raise ai_key_bad_request("Мониторинг баланса не включён для этого ключа")
        if ai_key.billing_admin_key_encrypted is None:
            raise ai_key_bad_request("Admin API key не задан")

        now = datetime.now(UTC)
        threshold = ai_key.balance_low_threshold_usd or default_low_threshold_usd()
        ai_key.balance_initial_usd = payload.balance_initial_usd
        ai_key.balance_remaining_usd = payload.balance_initial_usd
        ai_key.balance_anchor_at = now
        ai_key.balance_last_sync_at = None
        ai_key.balance_sync_status = BalanceSyncStatus.ok.value
        ai_key.balance_sync_error = None
        ai_key.balance_sync_fail_streak = 0
        ai_key.balance_alert_level = compute_alert_level(payload.balance_initial_usd, threshold)
        ai_key.provider_api_key_id = None

        await self._repo.session.commit()
        await self._repo.session.refresh(ai_key)

        self._spawn_balance_sync(ai_key.id)
        counts = await self._backends.count_by_ai_keys([ai_key.id])
        return self._to_list_item(ai_key, counts.get(ai_key.id, 0))

    async def reorder_keys(self, provider: AiProvider, ids: list[uuid.UUID]) -> None:
        all_ids = await self._repo.all_ids()
        for item_id in ids:
            if item_id not in all_ids:
                raise ai_key_not_found()
        group_ids = await self._repo.ids_by_provider(provider.value)
        if len(ids) != len(group_ids) or set(ids) != group_ids:
            raise unprocessable("Список не является полной перестановкой ключей провайдера")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("ai_keys_reordered", provider=provider.value, count=len(ids))

    async def get_status(self, ai_key_id: uuid.UUID) -> AiKeyStatusResponse:
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
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        return decrypt_secret(ai_key.key_encrypted)

    async def reveal_billing_admin_key(self, ai_key_id: uuid.UUID) -> str:
        ai_key = await self._repo.get_by_id(ai_key_id)
        if ai_key is None:
            raise ai_key_not_found()
        if ai_key.billing_admin_key_encrypted is None:
            raise secret_not_set()
        return decrypt_secret(ai_key.billing_admin_key_encrypted)

    async def delete_key(self, ai_key_id: uuid.UUID) -> None:
        deleted = await self._repo.delete_by_id(ai_key_id)
        if not deleted:
            raise ai_key_not_found()
        await self._repo.session.commit()
        logger.info("ai_key_deleted", ai_key_id=str(ai_key_id))

    def _spawn_monitor(self, ai_key_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._monitor.check_one(ai_key_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    def _spawn_balance_sync(self, ai_key_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._balance_sync.sync_one(ai_key_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    def _to_list_item(ai_key: AiKey, backend_count: int) -> AiKeyListItem:
        sync_status = (
            BalanceSyncStatus(ai_key.balance_sync_status) if ai_key.balance_sync_status else None
        )
        alert_level = (
            BalanceAlertLevel(ai_key.balance_alert_level) if ai_key.balance_alert_level else None
        )
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
            balance_monitoring_enabled=ai_key.balance_monitoring_enabled,
            balance_initial_usd=ai_key.balance_initial_usd,
            balance_remaining_usd=ai_key.balance_remaining_usd,
            balance_low_threshold_usd=ai_key.balance_low_threshold_usd,
            balance_anchor_at=ai_key.balance_anchor_at,
            balance_last_sync_at=ai_key.balance_last_sync_at,
            balance_sync_status=sync_status,
            balance_sync_error=ai_key.balance_sync_error,
            balance_alert_level=alert_level,
        )


class _BalanceCreateDisabled(TypedDict):
    balance_monitoring_enabled: Literal[False]


class _BalanceCreateEnabled(TypedDict):
    balance_monitoring_enabled: Literal[True]
    balance_initial_usd: Decimal
    balance_remaining_usd: Decimal
    balance_low_threshold_usd: Decimal
    balance_anchor_at: datetime
    balance_sync_status: str
    balance_alert_level: str
    billing_admin_key_encrypted: bytes


_BalanceCreateFields = _BalanceCreateDisabled | _BalanceCreateEnabled


def _balance_create_fields(payload: AiKeyCreateRequest) -> _BalanceCreateFields:
    if not payload.balance_monitoring_enabled:
        return {"balance_monitoring_enabled": False}
    assert payload.balance_initial_usd is not None
    assert payload.billing_admin_key is not None
    now = datetime.now(UTC)
    threshold = payload.balance_low_threshold_usd or default_low_threshold_usd()
    return {
        "balance_monitoring_enabled": True,
        "balance_initial_usd": payload.balance_initial_usd,
        "balance_remaining_usd": payload.balance_initial_usd,
        "balance_low_threshold_usd": threshold,
        "balance_anchor_at": now,
        "balance_sync_status": BalanceSyncStatus.ok.value,
        "balance_alert_level": compute_alert_level(payload.balance_initial_usd, threshold),
        "billing_admin_key_encrypted": encrypt_secret(payload.billing_admin_key.strip()),
    }


def _apply_balance_patch(ai_key: AiKey, payload: AiKeyUpdateRequest) -> bool:
    """Применяет поля balance из PATCH; True если нужен re-sync."""
    changed = False
    if payload.balance_monitoring_enabled is not None:
        enabling = payload.balance_monitoring_enabled and not ai_key.balance_monitoring_enabled
        disabling = not payload.balance_monitoring_enabled and ai_key.balance_monitoring_enabled
        ai_key.balance_monitoring_enabled = payload.balance_monitoring_enabled
        changed = enabling or disabling
        if disabling:
            ai_key.balance_initial_usd = None
            ai_key.balance_remaining_usd = None
            ai_key.balance_low_threshold_usd = None
            ai_key.balance_anchor_at = None
            ai_key.balance_last_sync_at = None
            ai_key.balance_sync_status = None
            ai_key.balance_sync_error = None
            ai_key.balance_alert_level = None
            ai_key.balance_sync_fail_streak = 0
            ai_key.provider_api_key_id = None
            ai_key.billing_admin_key_encrypted = None

    if not ai_key.balance_monitoring_enabled:
        return changed

    if payload.balance_low_threshold_usd is not None:
        ai_key.balance_low_threshold_usd = payload.balance_low_threshold_usd
        changed = True

    billing_provided = payload.billing_admin_key is not None and payload.billing_admin_key != ""
    if billing_provided:
        assert payload.billing_admin_key is not None
        ai_key.billing_admin_key_encrypted = encrypt_secret(payload.billing_admin_key.strip())
        ai_key.provider_api_key_id = None
        changed = True

    if payload.balance_initial_usd is not None:
        now = datetime.now(UTC)
        threshold = ai_key.balance_low_threshold_usd or default_low_threshold_usd()
        ai_key.balance_initial_usd = payload.balance_initial_usd
        ai_key.balance_remaining_usd = payload.balance_initial_usd
        ai_key.balance_anchor_at = now
        ai_key.balance_last_sync_at = None
        ai_key.balance_sync_status = BalanceSyncStatus.ok.value
        ai_key.balance_sync_error = None
        ai_key.balance_sync_fail_streak = 0
        ai_key.balance_alert_level = compute_alert_level(payload.balance_initial_usd, threshold)
        ai_key.provider_api_key_id = None
        changed = True

    if ai_key.balance_monitoring_enabled:
        if ai_key.billing_admin_key_encrypted is None:
            raise ai_key_bad_request("Укажите Admin API key для мониторинга баланса")
        if ai_key.balance_initial_usd is None or ai_key.balance_anchor_at is None:
            raise ai_key_bad_request("Укажите текущий баланс для мониторинга")

    return changed


__all__ = ["AiKeyService"]
