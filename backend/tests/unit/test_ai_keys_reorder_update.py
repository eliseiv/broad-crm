"""Unit-тесты сервисного слоя AI-ключей: PATCH-семантика секрета, re-check, reorder.

Проверяют `AiKeyService.update_key`/`reorder_keys` с фейковыми репозиторием/монитором
(без БД/сети, реальная крипта из conftest FERNET_KEY):
  - пустой/отсутствующий `key` = не менять (маска и шифртекст прежние, БЕЗ re-check);
  - непустой `key` → re-encrypt + новая маска + re-check (check_status=pending) +
    немедленная фоновая проверка (prev='pending' → неуспех даст 🔴, монитор-тест);
  - смена provider без key → re-check, маска/шифртекст прежние;
  - только name → без re-check; полный ключ НЕ в ответе; 404;
  - reorder-прецеденция: несуществующий id → 404 (ДО полноты группы); чужой провайдер
    / неполная группа → 422; успех присваивает position 0..M-1 внутри провайдера.
04-api.md#patch-apiai-keysid, #patch-apiai-keysorder, modules/ai-keys#редактирование-ключа.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.domain.ai_keys import compute_key_fragments
from app.errors import AppError
from app.infra.crypto import decrypt_secret, encrypt_secret
from app.models.ai_key import AiKeyStatus, AiProvider
from app.schemas.ai_key import AiKeyUpdateRequest
from app.services.ai_key_service import AiKeyService

OLD_KEY = "sk-old-SECRET-MIDDLE-oldX"
NEW_KEY = "sk-new-SECRET-MIDDLE-9QzK"


class FakeAiKey:
    def __init__(
        self,
        *,
        name: str = "OpenAI Prod",
        provider: str = AiProvider.openai.value,
        plaintext: str = OLD_KEY,
        check_status: str = AiKeyStatus.working.value,
        position: int = 0,
    ) -> None:
        now = datetime.now(UTC)
        self.id = uuid.uuid4()
        self.name = name
        self.provider = provider
        self.key_encrypted = encrypt_secret(plaintext)
        prefix, last4 = compute_key_fragments(plaintext)
        self.key_prefix = prefix
        self.key_last4 = last4
        self.check_status = check_status
        self.error_message: str | None = "Ключ недействителен" if check_status == "error" else None
        self.position = position
        self.last_checked_at: datetime | None = now
        self.created_at = now
        self.updated_at = now


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: object) -> None:
        return None


class FakeAiKeyRepo:
    def __init__(self, keys: list[FakeAiKey]) -> None:
        self._session = FakeSession()
        self.keys: dict[uuid.UUID, FakeAiKey] = {k.id: k for k in keys}
        self.reordered: list[uuid.UUID] | None = None

    @property
    def session(self) -> FakeSession:
        return self._session

    async def get_by_id(self, ai_key_id: uuid.UUID) -> FakeAiKey | None:
        return self.keys.get(ai_key_id)

    async def all_ids(self) -> set[uuid.UUID]:
        return set(self.keys)

    async def ids_by_provider(self, provider: str) -> set[uuid.UUID]:
        return {k.id for k in self.keys.values() if k.provider == provider}

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        self.reordered = list(ordered_ids)
        for index, ai_key_id in enumerate(ordered_ids):
            self.keys[ai_key_id].position = index


class FakeMonitor:
    def __init__(self) -> None:
        self.checked: list[uuid.UUID] = []

    async def check_one(self, ai_key_id: uuid.UUID) -> None:
        self.checked.append(ai_key_id)


class _FakeBackends:
    async def count_by_ai_keys(self, ai_key_ids: Any) -> dict[Any, int]:
        return {}

    async def list_by_ai_key(self, ai_key_id: Any) -> list[Any]:
        return []


def _service(repo: FakeAiKeyRepo, monitor: FakeMonitor) -> AiKeyService:
    return AiKeyService(
        repository=cast(Any, repo), monitor=cast(Any, monitor), backends=cast(Any, _FakeBackends())
    )


# --------------------------------------------------------------- update: секрет
async def test_update_empty_key_keeps_secret_and_no_recheck() -> None:
    key = FakeAiKey(check_status=AiKeyStatus.working.value)
    original_cipher = key.key_encrypted
    repo = FakeAiKeyRepo([key])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # Пустой key + новое имя: секрет и маска прежние, re-check НЕ запускается.
    item = await service.update_key(key.id, AiKeyUpdateRequest(name="Renamed", key=""))
    await asyncio.sleep(0)

    assert item.name == "Renamed"
    assert key.key_encrypted == original_cipher
    assert item.key_masked == "sk-o…oldX"
    assert item.check_status == AiKeyStatus.working  # не сброшен в pending
    assert monitor.checked == []  # проверка не перезапущена


async def test_update_name_only_keeps_status_no_recheck() -> None:
    key = FakeAiKey(check_status=AiKeyStatus.error.value)
    repo = FakeAiKeyRepo([key])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_key(key.id, AiKeyUpdateRequest(name="Only name"))
    await asyncio.sleep(0)

    assert item.name == "Only name"
    assert item.check_status == AiKeyStatus.error  # статус сохранён
    assert monitor.checked == []


async def test_update_nonempty_key_reencrypts_new_mask_and_rechecks() -> None:
    key = FakeAiKey(check_status=AiKeyStatus.working.value)
    old_cipher = key.key_encrypted
    repo = FakeAiKeyRepo([key])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_key(key.id, AiKeyUpdateRequest(key=NEW_KEY))
    await asyncio.sleep(0)

    # Новый секрет: re-encrypt (шифртекст изменился, round-trip даёт новый ключ).
    assert key.key_encrypted != old_cipher
    assert decrypt_secret(key.key_encrypted) == NEW_KEY
    # Новая маска по новому ключу.
    assert item.key_masked == "sk-n…9QzK"
    # Re-check: статус pending, error сброшен, немедленная проверка запущена.
    assert item.check_status == AiKeyStatus.pending
    assert item.error_message is None
    assert monitor.checked == [key.id]
    # Полный ключ (и его секретная середина) отсутствует в ответе.
    dumped = item.model_dump_json()
    assert NEW_KEY not in dumped
    assert "SECRET-MIDDLE" not in dumped


async def test_update_provider_change_without_key_rechecks_same_mask() -> None:
    key = FakeAiKey(provider=AiProvider.openai.value, check_status=AiKeyStatus.working.value)
    old_cipher = key.key_encrypted
    repo = FakeAiKeyRepo([key])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_key(key.id, AiKeyUpdateRequest(provider=AiProvider.anthropic))
    await asyncio.sleep(0)

    assert item.provider == AiProvider.anthropic
    # Тот же секрет: шифртекст и маска не меняются.
    assert key.key_encrypted == old_cipher
    assert item.key_masked == "sk-o…oldX"
    # Но проверка идёт против нового провайдера → pending + re-check.
    assert item.check_status == AiKeyStatus.pending
    assert monitor.checked == [key.id]


async def test_update_recheck_prev_status_is_pending_for_red_alert() -> None:
    # После edit статус = pending → первая неуспешная проверка шлёт 🔴 (prev='pending').
    # Матрица переходов и сам 🔴 покрыты в test_ai_key_evaluate/test_ai_key_monitor;
    # здесь фиксируем, что edit выставляет именно prev='pending' и запускает проверку.
    key = FakeAiKey(check_status=AiKeyStatus.working.value)
    repo = FakeAiKeyRepo([key])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_key(key.id, AiKeyUpdateRequest(key=NEW_KEY))
    await asyncio.sleep(0)

    assert key.check_status == AiKeyStatus.pending.value
    assert item.check_status == AiKeyStatus.pending
    assert monitor.checked == [key.id]


async def test_update_missing_key_raises_404() -> None:
    repo = FakeAiKeyRepo([FakeAiKey()])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    with pytest.raises(AppError) as exc:
        await service.update_key(uuid.uuid4(), AiKeyUpdateRequest(name="X"))

    assert exc.value.status_code == 404
    assert exc.value.code == "ai_key_not_found"


# ------------------------------------------------------------------ reorder
async def test_reorder_keys_nonexistent_id_is_404_before_group_completeness() -> None:
    k1 = FakeAiKey(provider=AiProvider.openai.value)
    k2 = FakeAiKey(provider=AiProvider.openai.value)
    repo = FakeAiKeyRepo([k1, k2])
    service = _service(repo, FakeMonitor())

    ghost = uuid.uuid4()
    with pytest.raises(AppError) as exc:
        await service.reorder_keys(AiProvider.openai, [k1.id, ghost])

    assert exc.value.status_code == 404
    assert exc.value.code == "ai_key_not_found"
    assert repo.reordered is None


async def test_reorder_keys_foreign_provider_id_is_422() -> None:
    openai_key = FakeAiKey(provider=AiProvider.openai.value)
    anthropic_key = FakeAiKey(provider=AiProvider.anthropic.value)
    repo = FakeAiKeyRepo([openai_key, anthropic_key])
    service = _service(repo, FakeMonitor())

    # anthropic_key существует (не 404), но чужой для группы openai → 422.
    with pytest.raises(AppError) as exc:
        await service.reorder_keys(AiProvider.openai, [openai_key.id, anthropic_key.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert repo.reordered is None


async def test_reorder_keys_incomplete_group_is_422() -> None:
    k1 = FakeAiKey(provider=AiProvider.openai.value)
    k2 = FakeAiKey(provider=AiProvider.openai.value)
    repo = FakeAiKeyRepo([k1, k2])
    service = _service(repo, FakeMonitor())

    # Пропущен k2 из группы openai → неполная перестановка → 422.
    with pytest.raises(AppError) as exc:
        await service.reorder_keys(AiProvider.openai, [k1.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_reorder_keys_full_group_assigns_position_within_provider() -> None:
    o1 = FakeAiKey(provider=AiProvider.openai.value)
    o2 = FakeAiKey(provider=AiProvider.openai.value)
    a1 = FakeAiKey(provider=AiProvider.anthropic.value, position=7)
    repo = FakeAiKeyRepo([o1, o2, a1])
    service = _service(repo, FakeMonitor())

    # Полная перестановка группы openai [o2, o1] → position 0,1 только внутри неё.
    await service.reorder_keys(AiProvider.openai, [o2.id, o1.id])

    assert repo.reordered == [o2.id, o1.id]
    assert o2.position == 0
    assert o1.position == 1
    # Ключ другого провайдера не тронут.
    assert a1.position == 7
    assert repo.session.commits == 1
