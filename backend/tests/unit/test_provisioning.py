from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from app.config import get_settings
from app.infra.ansible import AnsibleResult
from app.infra.crypto import encrypt_password
from app.models.server import ProvisionStatus
from app.services import provisioning_service
from app.services.provisioning_service import ProvisioningService


@dataclass
class FakeServer:
    id: uuid.UUID
    name: str = "Server 01"
    ip: str = "10.0.0.10"
    ssh_user: str = "root"
    ssh_password_encrypted: bytes = b""
    exporter_port: int = 9100
    provision_status: str = ProvisionStatus.pending.value
    error_message: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeSession:
    commits = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionMaker:
    def __call__(self) -> FakeSession:
        return FakeSession()


class FakeRepo:
    server: FakeServer
    statuses: ClassVar[list[tuple[ProvisionStatus, str | None]]] = []
    stuck: ClassVar[list[FakeServer]] = []

    def __init__(self, _session: Any) -> None:
        return None

    async def get_by_id(self, server_id: uuid.UUID) -> FakeServer | None:
        return self.server if self.server.id == server_id else None

    async def update_status(
        self,
        server_id: uuid.UUID,
        *,
        status: ProvisionStatus,
        error_message: str | None = None,
    ) -> None:
        assert server_id == self.server.id
        self.server.provision_status = status.value
        self.server.error_message = error_message
        self.server.updated_at = datetime.now(UTC)
        self.statuses.append((status, error_message))

    async def find_stuck_installing(self, *, older_than: datetime) -> list[FakeServer]:
        return [server for server in self.stuck if server.updated_at < older_than]

    async def list_online(self) -> list[FakeServer]:
        return [self.server] if self.server.provision_status == ProvisionStatus.online.value else []


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> type[FakeRepo]:
    FakeRepo.server = FakeServer(
        id=uuid.uuid4(), ssh_password_encrypted=encrypt_password("ssh-secret")
    )
    FakeRepo.statuses = []
    FakeRepo.stuck = []
    monkeypatch.setattr(provisioning_service, "ServerRepository", FakeRepo)
    return FakeRepo


@pytest.mark.asyncio
async def test_provisioning_success_transitions_pending_installing_online_and_writes_file_sd(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: type[FakeRepo],
) -> None:
    writes: list[dict[str, object]] = []
    runner_calls: list[dict[str, object]] = []

    def fake_runner(**kwargs: object) -> AnsibleResult:
        runner_calls.append(kwargs)
        return AnsibleResult(success=True)

    monkeypatch.setattr(provisioning_service, "run_install_node_exporter", fake_runner)
    monkeypatch.setattr(
        "app.services.provisioning_service.file_sd.write_target",
        lambda **kwargs: writes.append(kwargs),
    )

    service = ProvisioningService(FakeSessionMaker(), get_settings())  # type: ignore[arg-type]
    await service.provision_server(fake_repo.server.id)

    assert fake_repo.statuses == [
        (ProvisionStatus.installing, None),
        (ProvisionStatus.online, None),
    ]
    assert runner_calls[0]["ssh_password"] == "ssh-secret"
    assert writes == [
        {
            "server_id": fake_repo.server.id,
            "ip": "10.0.0.10",
            "exporter_port": 9100,
            "name": "Server 01",
        }
    ]


@pytest.mark.asyncio
async def test_provisioning_failure_transitions_to_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: type[FakeRepo],
) -> None:
    monkeypatch.setattr(
        provisioning_service,
        "run_install_node_exporter",
        lambda **_kwargs: AnsibleResult(success=False, error_message="SSH connection failed"),
    )

    service = ProvisioningService(FakeSessionMaker(), get_settings())  # type: ignore[arg-type]
    await service.provision_server(fake_repo.server.id)

    assert fake_repo.statuses == [
        (ProvisionStatus.installing, None),
        (ProvisionStatus.error, "SSH connection failed"),
    ]


@pytest.mark.asyncio
async def test_recover_stuck_installing_marks_old_records_error(
    fake_repo: type[FakeRepo],
) -> None:
    fake_repo.server.provision_status = ProvisionStatus.installing.value
    fake_repo.server.updated_at = datetime.now(UTC) - timedelta(seconds=600)
    fake_repo.stuck = [fake_repo.server]

    service = ProvisioningService(FakeSessionMaker(), get_settings())  # type: ignore[arg-type]

    assert await service.recover_stuck_installing() == 1
    assert fake_repo.statuses == [
        (ProvisionStatus.error, "provisioning interrupted (backend restart)")
    ]
