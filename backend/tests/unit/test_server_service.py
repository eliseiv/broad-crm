from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.errors import AppError
from app.infra.prometheus import PrometheusUnavailable
from app.models.server import ProvisionStatus, ServerAuthMethod
from app.schemas.metrics import Metric, MetricDetail, ServerMetrics
from app.schemas.server import ServerCreateRequest
from app.services.monitoring_service import InstanceMetrics
from app.services.server_service import ServerService


@dataclass
class FakeServer:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Server 01"
    ip: str = "10.0.0.10"
    ssh_user: str = "root"
    # Способ входа + материал ровно одного способа (ADR-067): по умолчанию — парольный
    # сервер, как до ADR-067 (в БД инвариант держит CHECK `ck_servers_auth_material`).
    auth_method: str = ServerAuthMethod.password.value
    ssh_password_encrypted: bytes | None = b"encrypted"
    ssh_private_key_encrypted: bytes | None = None
    ssh_key_passphrase_encrypted: bytes | None = None
    exporter_port: int = 9100
    provision_status: str = ProvisionStatus.online.value
    error_message: str | None = None
    position: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def instance(self) -> str:
        return f"{self.ip}:{self.exporter_port}"


class FakeSession:
    commits = 0
    rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepo:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.servers = [
            FakeServer(name="Online", provision_status=ProvisionStatus.online.value),
            FakeServer(
                name="Pending", ip="10.0.0.11", provision_status=ProvisionStatus.pending.value
            ),
        ]
        self.deleted: list[uuid.UUID] = []

    async def exists_by_ip(self, ip: str) -> bool:
        return any(str(server.ip) == ip for server in self.servers)

    async def create(self, **kwargs: object) -> FakeServer:
        server = FakeServer(
            name=cast(str, kwargs["name"]),
            ip=cast(str, kwargs["ip"]),
            ssh_user=cast(str, kwargs["ssh_user"]),
            auth_method=cast(ServerAuthMethod, kwargs["auth_method"]).value,
            ssh_password_encrypted=cast(bytes | None, kwargs["ssh_password_encrypted"]),
            ssh_private_key_encrypted=cast(bytes | None, kwargs["ssh_private_key_encrypted"]),
            ssh_key_passphrase_encrypted=cast(bytes | None, kwargs["ssh_key_passphrase_encrypted"]),
            exporter_port=cast(int, kwargs["exporter_port"]),
            provision_status=ProvisionStatus.pending.value,
        )
        self.servers.append(server)
        return server

    async def list_all(self, *, status: str | None = None) -> list[FakeServer]:
        if status is None:
            return self.servers
        return [server for server in self.servers if server.provision_status == status]

    async def get_by_id(self, server_id: uuid.UUID) -> FakeServer | None:
        return next((server for server in self.servers if server.id == server_id), None)

    async def delete_by_id(self, server_id: uuid.UUID) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        self.servers.remove(server)
        self.deleted.append(server_id)
        return True


class FakeMonitoring:
    def __init__(
        self, metrics: InstanceMetrics | None = None, *, unavailable: bool = False
    ) -> None:
        self.metrics = metrics
        self.unavailable = unavailable
        self.instances: list[list[str]] = []

    async def fetch_for_instances(self, instances: list[str]) -> dict[str, InstanceMetrics]:
        self.instances.append(instances)
        if self.unavailable:
            raise PrometheusUnavailable("down")
        return {instance: self.metrics for instance in instances if self.metrics is not None}

    async def fetch_one(self, instance: str) -> InstanceMetrics:
        if self.unavailable:
            raise PrometheusUnavailable("down")
        assert self.metrics is not None
        return self.metrics


class FakeProvisioning:
    def __init__(self) -> None:
        self.scheduled: list[uuid.UUID] = []

    async def provision_server(self, server_id: uuid.UUID) -> None:
        self.scheduled.append(server_id)


class FakeBackends:
    """Фейк BackendRepository (reverse-lookup/backend_count, ADR-040). Пусто по умолчанию."""

    async def count_by_servers(self, server_ids: Any) -> dict[Any, int]:
        return {}

    async def list_by_server(self, server_id: Any) -> list[Any]:
        return []


def make_service(
    repo: FakeRepo | None = None,
    monitoring: FakeMonitoring | None = None,
    provisioning: FakeProvisioning | None = None,
) -> ServerService:
    return ServerService(
        cast(Any, repo or FakeRepo()),
        cast(Any, monitoring or FakeMonitoring()),
        cast(Any, provisioning or FakeProvisioning()),
        cast(Any, FakeBackends()),
    )


def sample_metrics() -> ServerMetrics:
    return ServerMetrics(
        cpu=Metric(
            usage_percent=65, zone="green", detail=MetricDetail(value=2.6, total=4, unit="GHz")
        ),
        ram=Metric(
            usage_percent=80, zone="yellow", detail=MetricDetail(value=11.5, total=16, unit="GB")
        ),
        ssd=Metric(
            usage_percent=91, zone="red", detail=MetricDetail(value=238, total=500, unit="GB")
        ),
    )


@pytest.mark.asyncio
async def test_create_server_encrypts_password_returns_202_shape_and_schedules_provisioning() -> (
    None
):
    repo = FakeRepo()
    provisioning = FakeProvisioning()
    service = make_service(repo, provisioning=provisioning)

    response = await service.create_server(
        ServerCreateRequest(
            name="Created",
            ip="10.0.0.20",
            ssh_user="root",
            ssh_password="plain-secret",
        )
    )

    created = repo.servers[-1]
    await asyncio.sleep(0)

    assert response.provision_status == ProvisionStatus.pending
    assert response.ip == "10.0.0.20"
    assert created.ssh_password_encrypted != b"plain-secret"
    assert provisioning.scheduled == [created.id]


@pytest.mark.asyncio
async def test_create_server_duplicate_ip_raises_409() -> None:
    service = make_service()

    with pytest.raises(AppError) as exc:
        await service.create_server(
            ServerCreateRequest(
                name="Duplicate",
                ip="10.0.0.10",
                ssh_user="root",
                ssh_password="plain-secret",
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.code == "server_conflict"


@pytest.mark.asyncio
async def test_list_servers_degrades_to_null_metrics_when_prometheus_unavailable() -> None:
    service = make_service(monitoring=FakeMonitoring(unavailable=True))

    response = await service.list_servers()

    assert response.items[0].online is False
    assert response.items[0].metrics is None
    assert response.items[1].metrics is None


@pytest.mark.asyncio
async def test_get_metrics_offline_returns_null_details_without_502() -> None:
    repo = FakeRepo()
    service = make_service(
        repo,
        FakeMonitoring(
            InstanceMetrics(online=False, uptime_seconds=None, last_updated=None, metrics=None)
        ),
    )

    response = await service.get_metrics(repo.servers[0].id)

    assert response.online is False
    assert response.cpu.usage_percent is None
    assert response.cpu.zone is None
    assert response.cpu.detail.value is None
    assert response.cpu.detail.total is None
    assert response.ram.usage_percent is None
    assert response.ram.zone is None
    assert response.ram.detail.value is None
    assert response.ram.detail.total is None
    assert response.ssd.usage_percent is None
    assert response.ssd.zone is None
    assert response.ssd.detail.value is None
    assert response.ssd.detail.total is None


@pytest.mark.asyncio
async def test_get_status_and_delete_missing_server_raise_404() -> None:
    service = make_service()
    missing = uuid.uuid4()

    with pytest.raises(AppError) as status_exc:
        await service.get_status(missing)
    with pytest.raises(AppError) as delete_exc:
        await service.delete_server(missing)

    assert status_exc.value.status_code == 404
    assert delete_exc.value.code == "server_not_found"
