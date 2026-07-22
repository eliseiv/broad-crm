"""Бизнес-логика реестра серверов (modules/servers, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from fastapi import status
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.ssh_keys import (
    FIELD_PASSPHRASE,
    FIELD_PRIVATE_KEY,
    SshKeyError,
    normalize_private_key,
    validate_private_key,
)
from app.errors import (
    AppError,
    provisioning_unavailable,
    secret_not_set,
    server_conflict,
    server_not_found,
    unprocessable,
)
from app.infra import file_sd
from app.infra.crypto import decrypt_secret, encrypt_password, encrypt_secret
from app.infra.prometheus import PrometheusUnavailable
from app.logging import get_logger
from app.models.server import ProvisionStatus, Server, ServerAuthMethod
from app.repositories.backend_repository import BackendRepository
from app.repositories.server_repository import ServerRepository
from app.schemas.backend import BackendRef, BackendRefListResponse
from app.schemas.server import (
    ServerCreatedResponse,
    ServerCreateRequest,
    ServerListItem,
    ServerListResponse,
    ServerMetricsResponse,
    ServerStatusResponse,
    ServerSummaryResponse,
    ServerUpdateRequest,
)
from app.services.monitoring_service import InstanceMetrics, MonitoringService
from app.services.provisioning_service import ProvisioningService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи провижининга, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи).
_background_tasks: set[asyncio.Task[None]] = set()

# Лимиты полей материала входа (04-api.md#post-apiservers). Размер ключа — из env
# (`SSH_KEY_MAX_BYTES`), пароль и парольная фраза — 1..256 символов.
_SECRET_MAX_LEN = 256

_FIELD_PASSWORD = "ssh_password"


def _auth_material_error(field: str, message: str) -> AppError:
    """`422 validation_error` с точным `details[].field` (ADR-067 §3, 04-api.md).

    Код контракта — `validation_error` при статусе 422 (значение присутствует/отсутствует
    семантически недопустимо), а не 400 `errors.validation_error` (структурная форма тела)
    и не `unprocessable`. Тот же приём, что `_content_md_error` в модуле «Документы».
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message=message,
        details=[{"field": field, "message": message}],
    )


@dataclass(frozen=True, slots=True)
class _AuthMaterial:
    """Зашифрованный материал входа ровно одного способа (ADR-067 §1)."""

    auth_method: ServerAuthMethod
    ssh_password_encrypted: bytes | None
    ssh_private_key_encrypted: bytes | None
    ssh_key_passphrase_encrypted: bytes | None


class ServerService:
    """CRUD реестра серверов + интеграция с monitoring и provisioning."""

    def __init__(
        self,
        repository: ServerRepository,
        monitoring: MonitoringService,
        provisioning: ProvisioningService,
        backends: BackendRepository,
    ) -> None:
        self._repo = repository
        self._monitoring = monitoring
        self._provisioning = provisioning
        self._backends = backends

    async def create_server(self, payload: ServerCreateRequest) -> ServerCreatedResponse:
        """Создаёт сервер (pending) и запускает фоновый провижининг.

        Материал входа — ровно одного способа (`auth_method`, ADR-067 §3): пароль ЛИБО
        приватный ключ с опциональной парольной фразой. Всё шифруется Fernet одним и тем
        же `FERNET_KEY` и в ответ не возвращается.
        """
        ip = str(payload.ip)
        material = self._build_auth_material(payload)
        if await self._repo.exists_by_ip(ip):
            raise server_conflict()

        try:
            server = await self._repo.create(
                name=payload.name,
                ip=ip,
                ssh_user=payload.ssh_user,
                auth_method=material.auth_method,
                ssh_password_encrypted=material.ssh_password_encrypted,
                ssh_private_key_encrypted=material.ssh_private_key_encrypted,
                ssh_key_passphrase_encrypted=material.ssh_key_passphrase_encrypted,
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

        logger.info("server_created", server_id=str(server.id), auth_method=server.auth_method)
        return ServerCreatedResponse(
            id=server.id,
            name=server.name,
            ip=str(server.ip),
            ssh_user=server.ssh_user,
            auth_method=ServerAuthMethod(server.auth_method),
            exporter_port=server.exporter_port,
            provision_status=ProvisionStatus(server.provision_status),
            position=server.position,
        )

    @staticmethod
    def _build_auth_material(payload: ServerCreateRequest) -> _AuthMaterial:
        """Валидация «ровно один способ» + лимиты + разбор ключа, затем шифрование.

        Прецеденция строго сверху вниз (04-api.md#post-apiservers): (1) ровно один способ
        — лишнее поле «чужого» режима (даже `null`/`""`) и отсутствующее/пустое
        обязательное поле дают `422` с именем ИМЕННО этого поля; (2) лимиты размера — ДО
        разбора (анти-DoS); (3) нормализация ключа; (4) реальный разбор `cryptography`
        (4 шага, `app/domain/ssh_keys.py`); (5) шифрование Fernet.
        """
        provided = payload.model_fields_set
        if payload.auth_method is ServerAuthMethod.key:
            return ServerService._key_material(payload, provided)
        return ServerService._password_material(payload, provided)

    @staticmethod
    def _password_material(payload: ServerCreateRequest, provided: set[str]) -> _AuthMaterial:
        """Материал парольного режима (прежнее поведение + правило «ровно один способ»)."""
        for foreign in (FIELD_PRIVATE_KEY, FIELD_PASSPHRASE):
            if foreign in provided:
                raise _auth_material_error(
                    foreign, "Поле недопустимо при входе по паролю — уберите его"
                )
        password = payload.ssh_password or ""
        if not password:
            raise _auth_material_error(_FIELD_PASSWORD, "Укажите пароль")
        if len(password) > _SECRET_MAX_LEN:
            raise _auth_material_error(_FIELD_PASSWORD, "Пароль длиннее допустимого")
        return _AuthMaterial(
            auth_method=ServerAuthMethod.password,
            ssh_password_encrypted=encrypt_password(password),
            ssh_private_key_encrypted=None,
            ssh_key_passphrase_encrypted=None,
        )

    @staticmethod
    def _key_material(payload: ServerCreateRequest, provided: set[str]) -> _AuthMaterial:
        """Материал key-режима: лимит → нормализация → разбор → шифрование (ADR-067 §3).

        **Семантика `ssh_key_passphrase` (нормативно для этой реализации).** Поле
        опционально, поэтому «не задана» и «задана» различаются так: `null` (или поле
        отсутствует) = НЕ задана; непустая строка = задана (1–256, 04-api.md); **пустая
        строка/пробелы — ошибка**, а не молчаливое «не задана». Иначе фраза длиной 0
        нарушала бы объявленный диапазон 1–256 незаметно для клиента и гасила бы исход
        «Ключ не защищён парольной фразой — уберите её» (шаг 2 процедуры разбора), при
        том что симметричный `ssh_password: ""` даёт `422`.
        """
        if _FIELD_PASSWORD in provided:
            raise _auth_material_error(
                _FIELD_PASSWORD, "Поле недопустимо при входе по ключу — уберите его"
            )
        raw_key = payload.ssh_private_key or ""
        if not raw_key.strip():
            raise _auth_material_error(FIELD_PRIVATE_KEY, "Укажите приватный SSH-ключ")
        # Размер — ДО разбора: не отдавать многомегабайтную строку в разбор (анти-DoS).
        if len(raw_key.encode("utf-8")) > get_settings().ssh_key_max_bytes:
            raise _auth_material_error(FIELD_PRIVATE_KEY, "Приватный ключ длиннее допустимого")

        passphrase = payload.ssh_key_passphrase
        if passphrase is not None:
            if not passphrase.strip():
                raise _auth_material_error(
                    FIELD_PASSPHRASE, "Парольная фраза не может быть пустой — уберите поле"
                )
            if len(passphrase) > _SECRET_MAX_LEN:
                raise _auth_material_error(FIELD_PASSPHRASE, "Парольная фраза длиннее допустимой")

        normalized = normalize_private_key(raw_key)
        try:
            validate_private_key(normalized, passphrase)
        except SshKeyError as exc:
            # Наружу идёт только фиксированное сообщение контракта; текст исключения
            # `cryptography` не пробрасывается ни в ответ, ни в лог (ADR-067 §3 п.4).
            raise _auth_material_error(exc.field, exc.message) from exc

        return _AuthMaterial(
            auth_method=ServerAuthMethod.key,
            ssh_password_encrypted=None,
            ssh_private_key_encrypted=encrypt_secret(normalized),
            ssh_key_passphrase_encrypted=(
                encrypt_secret(passphrase) if passphrase is not None else None
            ),
        )

    async def reveal_ssh_password(self, server_id: uuid.UUID) -> str:
        """On-demand reveal SSH-пароля сервера (ADR-035, require servers:edit).

        Расшифровка `ssh_password_encrypted` в памяти обработчика. Нет записи → 404
        `server_not_found`; сервер с `auth_method='key'` → 404 `secret_not_set` (пароля у
        него нет — ADR-067 §4). Парного эндпоинта для приватного ключа и парольной фразы
        НЕ существует: это write-only секреты. Значение возвращается вызывающему роутеру
        и НЕ логируется здесь.
        """
        server = await self._repo.get_by_id(server_id)
        if server is None:
            raise server_not_found()
        if server.auth_method != ServerAuthMethod.password.value:
            raise secret_not_set()
        if server.ssh_password_encrypted is None:
            # Недостижимо при живом CHECK `ck_servers_auth_material`; страховка от 500,
            # если граница целостности когда-либо будет снята вручную.
            raise secret_not_set()
        return decrypt_secret(server.ssh_password_encrypted)

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

        backend_counts = await self._backends.count_by_servers([s.id for s in servers])
        items = [
            self._to_list_item(server, metrics_by_instance, backend_counts.get(server.id, 0))
            for server in servers
        ]
        return ServerListResponse(items=items)

    async def list_server_backends(self, server_id: uuid.UUID) -> BackendRefListResponse:
        """Список бэков сервера (reverse-lookup, ADR-040, require servers:view).

        Нет сервера → 404 server_not_found. Сортировка `position ASC, created_at DESC, id`.
        """
        server = await self._repo.get_by_id(server_id)
        if server is None:
            raise server_not_found()
        backends = await self._backends.list_by_server(server_id)
        return BackendRefListResponse(
            backends=[BackendRef(code=b.code, name=b.name, domain=b.domain) for b in backends]
        )

    @staticmethod
    def _to_list_item(
        server: Server,
        metrics_by_instance: dict[str, InstanceMetrics],
        backend_count: int,
    ) -> ServerListItem:
        if server.provision_status != ProvisionStatus.online.value:
            return ServerListItem(
                id=server.id,
                name=server.name,
                ip=str(server.ip),
                ssh_user=server.ssh_user,
                auth_method=ServerAuthMethod(server.auth_method),
                exporter_port=server.exporter_port,
                provision_status=ProvisionStatus(server.provision_status),
                position=server.position,
                online=False,
                uptime_seconds=None,
                last_updated=None,
                metrics=None,
                backend_count=backend_count,
            )

        instance_metrics = metrics_by_instance.get(server.instance)
        if instance_metrics is None:
            return ServerListItem(
                id=server.id,
                name=server.name,
                ip=str(server.ip),
                ssh_user=server.ssh_user,
                auth_method=ServerAuthMethod(server.auth_method),
                exporter_port=server.exporter_port,
                provision_status=ProvisionStatus(server.provision_status),
                position=server.position,
                online=False,
                uptime_seconds=None,
                last_updated=None,
                metrics=None,
                backend_count=backend_count,
            )

        return ServerListItem(
            id=server.id,
            name=server.name,
            ip=str(server.ip),
            ssh_user=server.ssh_user,
            auth_method=ServerAuthMethod(server.auth_method),
            exporter_port=server.exporter_port,
            provision_status=ProvisionStatus(server.provision_status),
            position=server.position,
            online=instance_metrics.online,
            uptime_seconds=instance_metrics.uptime_seconds,
            last_updated=instance_metrics.last_updated,
            metrics=instance_metrics.metrics,
            backend_count=backend_count,
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

    async def update_server(
        self, server_id: uuid.UUID, payload: ServerUpdateRequest
    ) -> ServerSummaryResponse:
        """Меняет только `name` (04-api.md). Нет записи → 404. `updated_at` обновляется.

        `ip`/SSH/провижининг не трогаются. Немедленная перезапись file_sd-таргета для
        переименования не требуется (скрейп идёт по `instance`, label `name`
        информативный) — modules/servers.
        """
        server = await self._repo.update_name(server_id, name=payload.name)
        if server is None:
            raise server_not_found()
        response = ServerSummaryResponse(
            id=server.id,
            name=server.name,
            ip=str(server.ip),
            ssh_user=server.ssh_user,
            auth_method=ServerAuthMethod(server.auth_method),
            exporter_port=server.exporter_port,
            provision_status=ProvisionStatus(server.provision_status),
            position=server.position,
            created_at=server.created_at,
            updated_at=server.updated_at,
        )
        await self._repo.session.commit()
        logger.info("server_renamed", server_id=str(server_id))
        return response

    async def reorder_servers(self, ids: list[uuid.UUID]) -> None:
        """Перестановка серверов: `position = 0..N-1` в одной транзакции.

        Прецеденция ошибок (04-api.md#прецеденция-ошибок-валидации): форма тела
        уже проверена pydantic (400); здесь — существование всех `id` (404, до
        полноты), затем полнота перестановки множества серверов (422).
        """
        all_ids = await self._repo.all_ids()
        for server_id in ids:
            if server_id not in all_ids:
                raise server_not_found()
        if len(ids) != len(all_ids) or set(ids) != all_ids:
            raise unprocessable("Список не является полной перестановкой серверов")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("servers_reordered", count=len(ids))

    async def delete_server(self, server_id: uuid.UUID) -> None:
        """Удаляет file_sd-таргет и запись; повтор → 404."""
        deleted = await self._repo.delete_by_id(server_id)
        if not deleted:
            raise server_not_found()
        await self._repo.session.commit()
        file_sd.delete_target(server_id)
        logger.info("server_deleted", server_id=str(server_id))
