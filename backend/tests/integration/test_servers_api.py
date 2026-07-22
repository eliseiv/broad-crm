from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.api import deps
from app.errors import prometheus_unavailable, server_conflict, server_not_found
from app.models.server import ProvisionStatus, ServerAuthMethod
from app.schemas.metrics import Metric, MetricDetail
from app.schemas.server import (
    ServerCreatedResponse,
    ServerListItem,
    ServerListResponse,
    ServerMetricsResponse,
    ServerStatusResponse,
)
from conftest import make_principal
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def empty_metric(unit: str) -> Metric:
    return Metric(
        usage_percent=None, zone=None, detail=MetricDetail(value=None, total=None, unit=unit)
    )


class FakeServersService:
    first_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    second_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.items = [
            ServerListItem(
                id=self.second_id,
                name="New server",
                ip="10.0.0.20",
                ssh_user="root",
                # ADR-067: `auth_method` присутствует в каждом элементе списка —
                # UI по нему решает, рендерить ли кнопку-глаз reveal.
                auth_method=ServerAuthMethod.password,
                exporter_port=9100,
                provision_status=ProvisionStatus.online,
                position=0,
                online=False,
                uptime_seconds=None,
                last_updated=now,
                backend_count=0,
                metrics=None,
            ),
            ServerListItem(
                id=self.first_id,
                name="Old server",
                ip="10.0.0.10",
                ssh_user="admin",
                auth_method=ServerAuthMethod.key,
                exporter_port=9100,
                provision_status=ProvisionStatus.pending,
                position=1,
                online=False,
                uptime_seconds=None,
                last_updated=now - timedelta(minutes=5),
                backend_count=0,
                metrics=None,
            ),
        ]
        self.deleted: set[uuid.UUID] = set()

    async def create_server(self, payload: Any) -> ServerCreatedResponse:
        ip = payload.ip
        if str(ip) == "10.0.0.10":
            raise server_conflict()
        return ServerCreatedResponse(
            id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            name=payload.name,
            ip=str(ip),
            ssh_user=payload.ssh_user,
            auth_method=payload.auth_method,
            exporter_port=9100,
            provision_status=ProvisionStatus.pending,
            position=0,
        )

    async def list_servers(self, *, status: str | None = None) -> ServerListResponse:
        items = self.items
        if status is not None:
            items = [item for item in items if item.provision_status.value == status]
        return ServerListResponse(items=items)

    async def get_metrics(self, server_id: uuid.UUID) -> ServerMetricsResponse:
        if server_id == uuid.UUID("00000000-0000-0000-0000-000000000099"):
            raise prometheus_unavailable()
        if server_id not in {self.first_id, self.second_id}:
            raise server_not_found()
        return ServerMetricsResponse(
            id=server_id,
            online=False,
            uptime_seconds=None,
            last_updated=None,
            cpu=empty_metric("cores"),
            ram=empty_metric("GB"),
            ssd=empty_metric("GB"),
        )

    async def get_status(self, server_id: uuid.UUID) -> ServerStatusResponse:
        if server_id not in {self.first_id, self.second_id}:
            raise server_not_found()
        return ServerStatusResponse(
            id=server_id,
            provision_status=ProvisionStatus.installing,
            error_message=None,
            updated_at=datetime.now(UTC),
        )

    async def delete_server(self, server_id: uuid.UUID) -> None:
        if server_id in self.deleted or server_id not in {self.first_id, self.second_id}:
            raise server_not_found()
        self.deleted.add(server_id)


@pytest.fixture
def fake_service() -> FakeServersService:
    return FakeServersService()


@pytest.fixture
def app(fake_service: FakeServersService) -> FastAPI:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    app.dependency_overrides[deps.get_server_service] = lambda: fake_service
    return app


@pytest.mark.asyncio
async def test_servers_create_contract_202_pending_conflict_and_no_password(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/servers",
            json={
                "name": "Created",
                "ip": "10.0.0.30",
                "ssh_user": "root",
                "ssh_password": "plain-secret",
            },
        )
        conflict = await client.post(
            "/api/servers",
            json={
                "name": "Duplicate",
                "ip": "10.0.0.10",
                "ssh_user": "root",
                "ssh_password": "plain-secret",
            },
        )

    assert created.status_code == 202
    assert created.json()["provision_status"] == "pending"
    assert "ssh_password" not in created.text
    assert "plain-secret" not in created.text
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "server_conflict"


@pytest.mark.asyncio
async def test_servers_invalid_ip_is_422_unprocessable(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/servers",
            json={
                "name": "Invalid",
                "ip": "not-an-ip",
                "ssh_user": "root",
                "ssh_password": "plain-secret",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


@pytest.mark.asyncio
async def test_servers_list_sorted_created_at_desc_and_prometheus_degradation(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/servers")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["items"]] == ["New server", "Old server"]
    assert body["items"][0]["metrics"] is None
    assert body["items"][0]["online"] is False


@pytest.mark.asyncio
async def test_server_metrics_prometheus_down_is_502_but_up_zero_is_200(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        offline = await client.get(f"/api/servers/{FakeServersService.first_id}/metrics")
        prom_down = await client.get("/api/servers/00000000-0000-0000-0000-000000000099/metrics")

    assert offline.status_code == 200
    assert offline.json()["online"] is False
    assert offline.json()["cpu"]["usage_percent"] is None
    assert offline.json()["cpu"]["zone"] is None
    assert offline.json()["cpu"]["detail"]["value"] is None
    assert offline.json()["cpu"]["detail"]["total"] is None
    assert offline.json()["ram"]["usage_percent"] is None
    assert offline.json()["ram"]["zone"] is None
    assert offline.json()["ram"]["detail"]["value"] is None
    assert offline.json()["ram"]["detail"]["total"] is None
    assert offline.json()["ssd"]["usage_percent"] is None
    assert offline.json()["ssd"]["zone"] is None
    assert offline.json()["ssd"]["detail"]["value"] is None
    assert offline.json()["ssd"]["detail"]["total"] is None
    assert prom_down.status_code == 502
    assert prom_down.json()["error"]["code"] == "prometheus_unavailable"


@pytest.mark.asyncio
async def test_server_status_and_delete_contracts(
    app: FastAPI, fake_service: FakeServersService
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get(f"/api/servers/{FakeServersService.first_id}/status")
        deleted = await client.delete(f"/api/servers/{FakeServersService.first_id}")
        repeated = await client.delete(f"/api/servers/{FakeServersService.first_id}")

    assert status.status_code == 200
    assert status.json()["provision_status"] == "installing"
    assert deleted.status_code == 204
    assert FakeServersService.first_id in fake_service.deleted
    assert repeated.status_code == 404
    assert repeated.json()["error"]["code"] == "server_not_found"
