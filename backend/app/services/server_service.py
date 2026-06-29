"""Бизнес-логика реестра серверов (modules/servers, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.exc import IntegrityError

from app.errors import (
    provisioning_unavailable,
    server_conflict,
    server_not_found,
)
from app.infra import file_sd
from app.infra.crypto import encrypt_password
from app.infra.prometheus import PrometheusUnavailable
from app.logging import get_logger
from app.models.server import ProvisionStatus, Server
from app.repositories.server_repository import ServerRepository
from app.schemas.server import (
    ServerCreatedResponse,
    ServerCreateRequest,
    ServerListItem,
    ServerListResponse,
    ServerMetricsResponse,
    ServerStatusResponse,
)
from app.services.monitoring_service import InstanceMetrics, MonitoringService
from app.services.provisioning_service import ProvisioningService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи провижининга, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи).
_background_tasks: set[asyncio.Task[None]] = set()


class ServerService:
    """CRUD реестра серверов + интеграция с monitoring и provisioning."""

    def __init__(
        self,
        repository: ServerRepository,
        monitoring: MonitoringService,
        provisioning: ProvisioningService,
    ) -> None:
        self._repo = repository
        self._monitoring = monitoring
        self._provisioning = provisioning

    async def create_server(self, payload: ServerCreateRequest) -> ServerCreatedResponse:
        """Создаёт сервер (pending) и запускает фоновый провижининг."""
        ip = str(payload.ip)
        if await self._repo.exists_by_ip(ip):
            raise server_conflict()

        encrypted = encrypt_password(payload.ssh_password)
        try:
            server = await self._repo.create(
                name=payload.name,
                ip=ip,
                ssh_user=payload.ssh_user,
                ssh_password_encrypted=encrypted,
                exporter_port=self._repo_default_port(),
            )
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("server_create_conflict", ip=ip)
            raise server_conflict() from exc

        try:
            task = asyncio.create_task(self._provisioning.provision_server(server.id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        except RuntimeError as exc:
            logger.error("provisioning_schedule_failed", server_id=str(server.id))
            raise provisioning_unavailable() from exc

        logger.info("server_created", server_id=str(server.id))
        return ServerCreatedResponse(
            id=server.id,
            name=server.name,
            ip=str(server.ip),
            exporter_port=server.exporter_port,
            provision_status=ProvisionStatus(server.provision_status),
        )

    def _repo_default_port(self) -> int:
        from app.config import get_settings

        return get_settings().exporter_port

    async def list_servers(self, *, status: str | None = None) -> ServerListResponse:
        """Список серверов с метриками; graceful degradation при недоступности Prometheus."""
        servers = await self._repo.list_all(status=status)

        online_instances = [
            s.instance for s in servers if s.provision_status == ProvisionStatus.online.value
        ]
        metrics_by_instance: dict[str, InstanceMetrics] = {}
        if online_instances:
            try:
                metrics_by_instance = await self._monitoring.fetch_for_instances(online_instances)
            except PrometheusUnavailable:
                logger.warning("servers_list_prometheus_unavailable")
                metrics_by_instance = {}

        items = [self._to_list_item(server, metrics_by_instance) for server in servers]
        return ServerListResponse(items=items)

    @staticmethod
    def _to_list_item(
        server: Server, metrics_by_instance: dict[str, InstanceMetrics]
    ) -> ServerListItem:
        if server.provision_status != ProvisionStatus.online.value:
            return ServerListItem(
                id=server.id,
                name=server.name,
                ip=str(server.ip),
                exporter_port=server.exporter_port,
                provision_status=ProvisionStatus(server.provision_status),
                online=False,
                uptime_seconds=None,
                last_updated=None,
                metrics=None,
            )

        instance_metrics = metrics_by_instance.get(server.instance)
        if instance_metrics is None:
            return ServerListItem(
                id=server.id,
                name=server.name,
                ip=str(server.ip),
                exporter_port=server.exporter_port,
                provision_status=ProvisionStatus(server.provision_status),
                online=False,
                uptime_seconds=None,
                last_updated=None,
                metrics=None,
            )

        return ServerListItem(
            id=server.id,
            name=server.name,
            ip=str(server.ip),
            exporter_port=server.exporter_port,
            provision_status=ProvisionStatus(server.provision_status),
            online=instance_metrics.online,
            uptime_seconds=instance_metrics.uptime_seconds,
            last_updated=instance_metrics.last_updated,
            metrics=instance_metrics.metrics,
        )

    async def get_metrics(self, server_id: uuid.UUID) -> ServerMetricsResponse:
        """Текущие метрики одного сервера; Prometheus down → 502 (пробрасывается)."""
        server = await self._repo.get_by_id(server_id)
        if server is None:
            raise server_not_found()

        instance_metrics = await self._monitoring.fetch_one(server.instance)
        metrics = instance_metrics.metrics
        if metrics is None:
            # Метрик нет (instance offline / up==0 / отсутствуют в ответе Prometheus,
            # но сам Prometheus доступен) — НЕ подставляем ложные/нулевые значения
            # (04-api.md «Доступность метрик»): usage_percent=null, zone=null,
            # detail.value/total=null; unit оставляем строкой. online=false.
            from app.schemas.metrics import Metric, MetricDetail

            null_cores = Metric(
                usage_percent=None,
                zone=None,
                detail=MetricDetail(value=None, total=None, unit="cores"),
            )
            null_gb = Metric(
                usage_percent=None,
                zone=None,
                detail=MetricDetail(value=None, total=None, unit="GB"),
            )
            return ServerMetricsResponse(
                id=server.id,
                online=False,
                uptime_seconds=instance_metrics.uptime_seconds,
                last_updated=instance_metrics.last_updated,
                cpu=null_cores,
                ram=null_gb,
                ssd=null_gb,
            )

        return ServerMetricsResponse(
            id=server.id,
            online=instance_metrics.online,
            uptime_seconds=instance_metrics.uptime_seconds,
            last_updated=instance_metrics.last_updated,
            cpu=metrics.cpu,
            ram=metrics.ram,
            ssd=metrics.ssd,
        )

    async def get_status(self, server_id: uuid.UUID) -> ServerStatusResponse:
        """Лёгкий статус провижининга."""
        server = await self._repo.get_by_id(server_id)
        if server is None:
            raise server_not_found()
        return ServerStatusResponse(
            id=server.id,
            provision_status=ProvisionStatus(server.provision_status),
            error_message=server.error_message,
            updated_at=server.updated_at,
        )

    async def delete_server(self, server_id: uuid.UUID) -> None:
        """Удаляет file_sd-таргет и запись; повтор → 404."""
        deleted = await self._repo.delete_by_id(server_id)
        if not deleted:
            raise server_not_found()
        await self._repo.session.commit()
        file_sd.delete_target(server_id)
        logger.info("server_deleted", server_id=str(server_id))
