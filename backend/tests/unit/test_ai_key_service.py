"""Unit-тесты AiKeyService с фейковым репозиторием (modules/ai-keys).

Проверяют: шифрование ключа Fernet при создании (round-trip), маску в ответе без
полного ключа, статус pending и запуск немедленной проверки (`monitor.check_one`),
список, статус (404), удаление (404 при повторе). Реальная крипта (FERNET_KEY из
conftest), БД/провайдер — стабы.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from app.errors import AppError
from app.infra.crypto import decrypt_secret
from app.models.ai_key import AiKeyStatus, AiProvider
from app.schemas.ai_key import AiKeyCreateRequest
from app.services.ai_key_service import AiKeyService

FULL_KEY = "sk-proj-SECRET-MIDDLE-PART-bA3T"


class _Row:
    def __init__(self, **kw: object) -> None:
        now = datetime.now(UTC)
        self.id = uuid.uuid4()
        self.name = kw["name"]
        self.provider = kw["provider"]
        self.key_encrypted = kw["key_encrypted"]
        self.key_prefix = kw["key_prefix"]
        self.key_last4 = kw["key_last4"]
        self.check_status = AiKeyStatus.pending.value
        self.error_message: str | None = None
        self.position = 0
        self.last_checked_at: datetime | None = None
        self.created_at = now
        self.updated_at = now


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeRepo:
    def __init__(self) -> None:
        self._session = _FakeSession()
        self.rows: list[_Row] = []
        self.captured_encrypted: bytes | None = None

    @property
    def session(self) -> _FakeSession:
        return self._session

    async def create(
        self,
        *,
        name: str,
        provider: str,
        key_encrypted: bytes,
        key_prefix: str | None,
        key_last4: str | None,
    ) -> _Row:
        self.captured_encrypted = key_encrypted
        row = _Row(
            name=name,
            provider=provider,
            key_encrypted=key_encrypted,
            key_prefix=key_prefix,
            key_last4=key_last4,
        )
        self.rows.append(row)
        return row

    async def list_all(self) -> list[_Row]:
        return list(self.rows)

    async def get_by_id(self, ai_key_id: uuid.UUID) -> _Row | None:
        return next((r for r in self.rows if r.id == ai_key_id), None)

    async def delete_by_id(self, ai_key_id: uuid.UUID) -> bool:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.id != ai_key_id]
        return len(self.rows) < before


class _FakeMonitor:
    def __init__(self) -> None:
        self.checked: list[uuid.UUID] = []

    async def check_one(self, ai_key_id: uuid.UUID) -> None:
        self.checked.append(ai_key_id)


def _service() -> tuple[AiKeyService, _FakeRepo, _FakeMonitor]:
    repo = _FakeRepo()
    monitor = _FakeMonitor()
    return AiKeyService(repository=repo, monitor=monitor), repo, monitor  # type: ignore[arg-type]


async def test_create_encrypts_key_fernet_roundtrip_and_masks() -> None:
    svc, repo, monitor = _service()
    item = await svc.create_key(
        AiKeyCreateRequest(name="OpenAI Prod", provider=AiProvider.openai, key=FULL_KEY)
    )

    # Ключ зашифрован Fernet и расшифровывается обратно в исходный (round-trip).
    assert repo.captured_encrypted is not None
    assert repo.captured_encrypted != FULL_KEY.encode("utf-8")  # ciphertext != plaintext
    assert decrypt_secret(repo.captured_encrypted) == FULL_KEY

    # Ответ содержит только маску, статус pending; полного ключа нет.
    assert item.check_status == AiKeyStatus.pending
    assert item.key_masked == "sk-p…bA3T"
    assert FULL_KEY not in item.model_dump_json()
    assert repo.session.commits == 1

    # Немедленная фоновая проверка запущена для созданного ключа (fire-and-forget).
    await asyncio.sleep(0)
    assert monitor.checked == [item.id]


async def test_create_short_key_full_mask() -> None:
    svc, repo, _monitor = _service()
    item = await svc.create_key(
        AiKeyCreateRequest(name="Short", provider=AiProvider.anthropic, key="tiny")
    )
    assert item.key_masked == "********"
    # Короткий ключ: фрагменты не сохраняются.
    assert repo.rows[0].key_prefix is None
    assert repo.rows[0].key_last4 is None


async def test_list_keys_returns_masked_items() -> None:
    svc, _repo, _monitor = _service()
    await svc.create_key(
        AiKeyCreateRequest(name="OpenAI Prod", provider=AiProvider.openai, key=FULL_KEY)
    )
    listed = await svc.list_keys()
    assert len(listed.items) == 1
    assert listed.items[0].key_masked == "sk-p…bA3T"
    assert FULL_KEY not in listed.model_dump_json()


async def test_get_status_ok_and_404() -> None:
    svc, _repo, _monitor = _service()
    item = await svc.create_key(
        AiKeyCreateRequest(name="k", provider=AiProvider.openai, key=FULL_KEY)
    )
    status = await svc.get_status(item.id)
    assert status.check_status == AiKeyStatus.pending

    with pytest.raises(AppError) as exc:
        await svc.get_status(uuid.uuid4())
    assert exc.value.code == "ai_key_not_found"
    assert exc.value.status_code == 404


async def test_delete_then_repeat_404() -> None:
    svc, _repo, _monitor = _service()
    item = await svc.create_key(
        AiKeyCreateRequest(name="k", provider=AiProvider.openai, key=FULL_KEY)
    )
    await svc.delete_key(item.id)  # ok

    with pytest.raises(AppError) as exc:
        await svc.delete_key(item.id)
    assert exc.value.code == "ai_key_not_found"
