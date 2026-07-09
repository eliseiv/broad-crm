"""Unit-тесты сервисного слоя бэков (modules/backends, 04-api.md#backends).

Фейковые репозиторий/монитор (без БД/сети):
  - create: нормализация домена (422 на невалидном), уникальность `code` (409),
    статус pending, немедленная фоновая проверка; прецеденция 422 (домен) → 409 (код);
  - list/status/delete: 404 при отсутствии/повторе;
  - PATCH: 404 (нет id) → 422 (домен) → 409 (код занят другим); re-check ТОЛЬКО при
    смене `domain`; смена `code`/`name` статус не трогает; смена code на занятый другим → 409;
  - reorder-прецеденция: несуществующий id → 404 (до полноты); все существуют, но не
    полная перестановка → 422; успех присваивает position 0..N-1.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.errors import AppError
from app.models.service_backend import BackendStatus
from app.schemas.backend import BackendCreateRequest, BackendUpdateRequest
from app.services.backend_service import BackendService


class FakeBackend:
    def __init__(
        self,
        *,
        code: str = "api-eu",
        name: str = "API EU",
        domain: str = "api.example.com",
        check_status: str = BackendStatus.working.value,
        position: int = 0,
        server_id: uuid.UUID | None = None,
        ai_key_id: uuid.UUID | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.id = uuid.uuid4()
        self.code = code
        self.name = name
        self.domain = domain
        self.check_status = check_status
        self.error_message: str | None = "Бэк недоступен" if check_status == "error" else None
        self.position = position
        # ADR-040/042: связи + секреты (шифртекст) + git/note.
        self.server_id = server_id
        self.ai_key_id = ai_key_id
        self.api_key_encrypted: bytes | None = None
        self.admin_api_key_encrypted: bytes | None = None
        self.git: str | None = None
        self.note: str | None = None
        self.last_checked_at: datetime | None = now
        self.created_at = now
        self.updated_at = now


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, _obj: object) -> None:
        return None


class FakeBackendRepo:
    def __init__(self, backends: list[FakeBackend] | None = None) -> None:
        self._session = FakeSession()
        self.backends: dict[uuid.UUID, FakeBackend] = {b.id: b for b in (backends or [])}
        self.reordered: list[uuid.UUID] | None = None
        self.captured_domain: str | None = None

    @property
    def session(self) -> FakeSession:
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
    ) -> FakeBackend:
        self.captured_domain = domain
        backend = FakeBackend(
            code=code,
            name=name,
            domain=domain,
            check_status=BackendStatus.pending.value,
            server_id=server_id,
            ai_key_id=ai_key_id,
        )
        backend.api_key_encrypted = api_key_encrypted
        backend.admin_api_key_encrypted = admin_api_key_encrypted
        backend.git = git
        backend.note = note
        self.backends[backend.id] = backend
        return backend

    async def list_all(self) -> list[FakeBackend]:
        return list(self.backends.values())

    async def server_names(self, server_ids: Any) -> dict[uuid.UUID, str]:
        return {}

    async def ai_key_names(self, ai_key_ids: Any) -> dict[uuid.UUID, str]:
        return {}

    async def server_exists(self, server_id: uuid.UUID) -> bool:
        return True

    async def ai_key_exists(self, ai_key_id: uuid.UUID) -> bool:
        return True

    async def get_by_id(self, backend_id: uuid.UUID) -> FakeBackend | None:
        return self.backends.get(backend_id)

    async def exists_by_code(self, code: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        for backend in self.backends.values():
            if backend.code == code and backend.id != exclude_id:
                return True
        return False

    async def all_ids(self) -> set[uuid.UUID]:
        return set(self.backends)

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        self.reordered = list(ordered_ids)
        for index, backend_id in enumerate(ordered_ids):
            self.backends[backend_id].position = index

    async def delete_by_id(self, backend_id: uuid.UUID) -> bool:
        return self.backends.pop(backend_id, None) is not None


class FakeMonitor:
    def __init__(self) -> None:
        self.checked: list[uuid.UUID] = []

    async def check_one(self, backend_id: uuid.UUID) -> None:
        self.checked.append(backend_id)


def _service(repo: FakeBackendRepo, monitor: FakeMonitor) -> BackendService:
    return BackendService(repository=cast(Any, repo), monitor=cast(Any, monitor))


# ------------------------------------------------------------------------- create
async def test_create_pending_normalizes_domain_and_schedules_check() -> None:
    repo = FakeBackendRepo()
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.create_backend(
        BackendCreateRequest(code="api-eu", name="API EU", domain="HTTPS://API.Example.com/")
    )
    await asyncio.sleep(0)

    # Домен нормализован в канон `https://<host>/` перед сохранением (ADR-042).
    assert repo.captured_domain == "https://api.example.com/"
    assert item.domain == "https://api.example.com/"
    assert item.check_status == BackendStatus.pending
    assert item.code == "api-eu"
    assert repo.session.commits == 1
    # Немедленная фоновая проверка запущена (fire-and-forget).
    assert monitor.checked == [item.id]


async def test_create_invalid_domain_is_422() -> None:
    repo = FakeBackendRepo()
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.create_backend(
            BackendCreateRequest(code="api-eu", name="API EU", domain="bad domain with spaces")
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_create_duplicate_code_is_409() -> None:
    existing = FakeBackend(code="api-eu")
    repo = FakeBackendRepo([existing])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.create_backend(
            BackendCreateRequest(code="api-eu", name="Another", domain="other.example.com")
        )

    assert exc.value.status_code == 409
    assert exc.value.code == "backend_code_taken"


async def test_create_precedence_invalid_domain_before_code_conflict() -> None:
    # Прецеденция: невалидный домен (422) проверяется ДО уникальности кода (409).
    existing = FakeBackend(code="api-eu")
    repo = FakeBackendRepo([existing])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.create_backend(
            BackendCreateRequest(code="api-eu", name="Dup", domain="bad domain/with slash")
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


# --------------------------------------------------------------------------- list
async def test_list_backends_returns_items() -> None:
    repo = FakeBackendRepo([FakeBackend(code="a"), FakeBackend(code="b")])
    service = _service(repo, FakeMonitor())

    listed = await service.list_backends()

    assert len(listed.items) == 2
    assert {i.code for i in listed.items} == {"a", "b"}


# ---------------------------------------------------------------------- PATCH
async def test_update_domain_change_rechecks() -> None:
    backend = FakeBackend(
        domain="https://old.example.com/", check_status=BackendStatus.working.value
    )
    repo = FakeBackendRepo([backend])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_backend(
        backend.id, BackendUpdateRequest(domain="https://new.example.com/")
    )
    await asyncio.sleep(0)

    assert backend.domain == "https://new.example.com/"  # нормализован (канон)
    assert item.check_status == BackendStatus.pending
    assert backend.error_message is None
    assert monitor.checked == [backend.id]


async def test_update_domain_same_after_normalize_no_recheck() -> None:
    backend = FakeBackend(
        domain="https://api.example.com/", check_status=BackendStatus.working.value
    )
    repo = FakeBackendRepo([backend])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # Тот же домен после нормализации → не считается изменением → без re-check.
    item = await service.update_backend(
        backend.id, BackendUpdateRequest(domain="HTTPS://API.Example.com/")
    )
    await asyncio.sleep(0)

    assert item.check_status == BackendStatus.working
    assert monitor.checked == []


async def test_update_invalid_domain_is_422() -> None:
    backend = FakeBackend()
    repo = FakeBackendRepo([backend])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.update_backend(backend.id, BackendUpdateRequest(domain="bad domain"))

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_update_code_change_checks_uniqueness_409() -> None:
    a = FakeBackend(code="api-eu")
    b = FakeBackend(code="api-us")
    repo = FakeBackendRepo([a, b])
    service = _service(repo, FakeMonitor())

    # Смена code на занятый ДРУГИМ бэком → 409.
    with pytest.raises(AppError) as exc:
        await service.update_backend(a.id, BackendUpdateRequest(code="api-us"))

    assert exc.value.status_code == 409
    assert exc.value.code == "backend_code_taken"


async def test_update_code_to_own_value_no_conflict_no_recheck() -> None:
    backend = FakeBackend(code="api-eu", check_status=BackendStatus.working.value)
    repo = FakeBackendRepo([backend])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # Тот же собственный code → не конфликт, статус не трогается.
    item = await service.update_backend(backend.id, BackendUpdateRequest(code="api-eu"))
    await asyncio.sleep(0)

    assert item.code == "api-eu"
    assert item.check_status == BackendStatus.working
    assert monitor.checked == []


async def test_update_code_and_name_only_no_recheck() -> None:
    backend = FakeBackend(code="api-eu", name="API EU", check_status=BackendStatus.error.value)
    repo = FakeBackendRepo([backend])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_backend(
        backend.id, BackendUpdateRequest(code="api-new", name="Renamed")
    )
    await asyncio.sleep(0)

    assert backend.code == "api-new"
    assert backend.name == "Renamed"
    assert item.check_status == BackendStatus.error  # статус сохранён (домен не менялся)
    assert monitor.checked == []


async def test_update_missing_backend_raises_404() -> None:
    repo = FakeBackendRepo([FakeBackend()])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.update_backend(uuid.uuid4(), BackendUpdateRequest(name="X"))

    assert exc.value.status_code == 404
    assert exc.value.code == "backend_not_found"


async def test_update_precedence_404_before_validation() -> None:
    # Несуществующий id даёт 404 даже при невалидном домене в теле (404 до 422).
    repo = FakeBackendRepo([FakeBackend()])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.update_backend(uuid.uuid4(), BackendUpdateRequest(domain="bad domain"))

    assert exc.value.status_code == 404
    assert exc.value.code == "backend_not_found"


# --------------------------------------------------------------- get_status/delete
async def test_get_status_ok_and_404() -> None:
    backend = FakeBackend(check_status=BackendStatus.error.value)
    repo = FakeBackendRepo([backend])
    service = _service(repo, FakeMonitor())

    status = await service.get_status(backend.id)
    assert status.check_status == BackendStatus.error
    assert status.error_message == "Бэк недоступен"

    with pytest.raises(AppError) as exc:
        await service.get_status(uuid.uuid4())
    assert exc.value.code == "backend_not_found"
    assert exc.value.status_code == 404


async def test_delete_then_repeat_404() -> None:
    backend = FakeBackend()
    repo = FakeBackendRepo([backend])
    service = _service(repo, FakeMonitor())

    await service.delete_backend(backend.id)  # ok

    with pytest.raises(AppError) as exc:
        await service.delete_backend(backend.id)
    assert exc.value.code == "backend_not_found"
    assert exc.value.status_code == 404


# --------------------------------------------------------------------- reorder
async def test_reorder_nonexistent_id_is_404_before_completeness() -> None:
    a = FakeBackend(code="a")
    b = FakeBackend(code="b")
    repo = FakeBackendRepo([a, b])
    service = _service(repo, FakeMonitor())

    ghost = uuid.uuid4()
    with pytest.raises(AppError) as exc:
        await service.reorder_backends([a.id, ghost])

    assert exc.value.status_code == 404
    assert exc.value.code == "backend_not_found"
    assert repo.reordered is None


async def test_reorder_incomplete_all_existing_is_422() -> None:
    a = FakeBackend(code="a")
    b = FakeBackend(code="b")
    repo = FakeBackendRepo([a, b])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.reorder_backends([a.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert repo.reordered is None


async def test_reorder_duplicate_all_existing_is_422() -> None:
    a = FakeBackend(code="a")
    b = FakeBackend(code="b")
    repo = FakeBackendRepo([a, b])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.reorder_backends([a.id, a.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_reorder_full_permutation_assigns_position_0_to_n_minus_1() -> None:
    a = FakeBackend(code="a")
    b = FakeBackend(code="b")
    c = FakeBackend(code="c")
    repo = FakeBackendRepo([a, b, c])
    service = _service(repo, FakeMonitor())

    await service.reorder_backends([c.id, a.id, b.id])

    assert repo.reordered == [c.id, a.id, b.id]
    assert c.position == 0
    assert a.position == 1
    assert b.position == 2
    assert repo.session.commits == 1
