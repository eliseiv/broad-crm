"""Unit-тесты сервисного слоя серверов: PATCH name, reorder-прецеденция, position.

Проверяют бизнес-логику `ServerService.update_server`/`reorder_servers` с фейковым
репозиторием (без БД):
  - update_server меняет ТОЛЬКО `name` (ip/ssh/exporter/статус не тронуты), 404 при
    отсутствии, ответ содержит `position` (04-api.md#patch-apiserversid);
  - reorder-прецеденция строго по 04-api: несуществующий id → 404 (ДО полноты);
    все существуют, но не полная перестановка → 422; успех присваивает
    `position = 0..N-1` в порядке массива (04-api.md#перестановка).
Компиляция ORDER BY репозитория — в test_repositories_ordering.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.errors import AppError
from app.models.server import ProvisionStatus
from app.schemas.server import ServerUpdateRequest
from app.services.server_service import ServerService


@dataclass
class FakeServer:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Server 01"
    ip: str = "10.0.0.10"
    ssh_user: str = "root"
    ssh_password_encrypted: bytes = b"encrypted"
    exporter_port: int = 9100
    provision_status: str = ProvisionStatus.online.value
    error_message: str | None = None
    position: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeServerRepo:
    def __init__(self, servers: list[FakeServer]) -> None:
        self._session = FakeSession()
        self.servers: dict[uuid.UUID, FakeServer] = {s.id: s for s in servers}
        self.reordered: list[uuid.UUID] | None = None

    @property
    def session(self) -> FakeSession:
        return self._session

    async def all_ids(self) -> set[uuid.UUID]:
        return set(self.servers)

    async def get_by_id(self, server_id: uuid.UUID) -> FakeServer | None:
        return self.servers.get(server_id)

    async def update_name(self, server_id: uuid.UUID, *, name: str) -> FakeServer | None:
        server = self.servers.get(server_id)
        if server is None:
            return None
        server.name = name
        return server

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        # Зеркалит реальный репозиторий: position = индекс в массиве (0..N-1).
        self.reordered = list(ordered_ids)
        for index, server_id in enumerate(ordered_ids):
            self.servers[server_id].position = index


def _service(repo: FakeServerRepo) -> ServerService:
    # reorder/update используют только репозиторий; monitoring/provisioning не нужны.
    return ServerService(cast(Any, repo), cast(Any, None), cast(Any, None))


# --------------------------------------------------------------- update_server
async def test_update_server_changes_only_name_and_returns_position() -> None:
    server = FakeServer(name="Old", ip="10.0.0.55", exporter_port=9100, position=3)
    repo = FakeServerRepo([server])
    service = _service(repo)

    response = await service.update_server(server.id, ServerUpdateRequest(name="New name"))

    assert response.name == "New name"
    # ip/exporter_port/provision_status НЕ тронуты.
    assert response.ip == "10.0.0.55"
    assert response.exporter_port == 9100
    assert response.provision_status == ProvisionStatus.online
    # position присутствует в ответе PATCH (04-api.md).
    assert response.position == 3
    assert repo.session.commits == 1
    # SSH-поля объекта не менялись.
    assert server.ssh_user == "root"
    assert server.ssh_password_encrypted == b"encrypted"


async def test_update_server_missing_raises_404() -> None:
    repo = FakeServerRepo([FakeServer()])
    service = _service(repo)

    with pytest.raises(AppError) as exc:
        await service.update_server(uuid.uuid4(), ServerUpdateRequest(name="X"))

    assert exc.value.status_code == 404
    assert exc.value.code == "server_not_found"


# -------------------------------------------------------------- reorder_servers
async def test_reorder_nonexistent_id_is_404_before_completeness() -> None:
    s1 = FakeServer(name="A")
    s2 = FakeServer(name="B")
    repo = FakeServerRepo([s1, s2])
    service = _service(repo)

    # Несуществующий id присутствует И нарушает полноту — но проверка существования
    # идёт раньше, поэтому 404 (04-api.md прецеденция), а не 422.
    ghost = uuid.uuid4()
    with pytest.raises(AppError) as exc:
        await service.reorder_servers([s1.id, ghost])

    assert exc.value.status_code == 404
    assert exc.value.code == "server_not_found"
    assert repo.reordered is None  # перестановка не выполнена


async def test_reorder_incomplete_permutation_all_existing_is_422() -> None:
    s1 = FakeServer(name="A")
    s2 = FakeServer(name="B")
    repo = FakeServerRepo([s1, s2])
    service = _service(repo)

    # Все id существуют, но список неполный (пропущен s2) → 422.
    with pytest.raises(AppError) as exc:
        await service.reorder_servers([s1.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert repo.reordered is None


async def test_reorder_duplicate_all_existing_is_422() -> None:
    s1 = FakeServer(name="A")
    s2 = FakeServer(name="B")
    repo = FakeServerRepo([s1, s2])
    service = _service(repo)

    # Дубликат существующего id (все существуют) — не полная перестановка → 422.
    with pytest.raises(AppError) as exc:
        await service.reorder_servers([s1.id, s1.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_reorder_full_permutation_assigns_position_0_to_n_minus_1() -> None:
    s1 = FakeServer(name="A")
    s2 = FakeServer(name="B")
    s3 = FakeServer(name="C")
    repo = FakeServerRepo([s1, s2, s3])
    service = _service(repo)

    # Полная перестановка в порядке [s3, s1, s2] → position 0,1,2 по индексу.
    await service.reorder_servers([s3.id, s1.id, s2.id])

    assert repo.reordered == [s3.id, s1.id, s2.id]
    assert s3.position == 0
    assert s1.position == 1
    assert s2.position == 2
    assert repo.session.commits == 1
