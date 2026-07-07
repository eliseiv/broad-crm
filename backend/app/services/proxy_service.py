"""Бизнес-логика реестра прокси (modules/proxies, 04-api.md)."""

from __future__ import annotations

import asyncio
import uuid

from app.errors import proxy_not_found, unprocessable
from app.infra.crypto import encrypt_secret
from app.logging import get_logger
from app.models.proxy import Proxy, ProxyStatus, ProxyType
from app.repositories.proxy_repository import ProxyRepository
from app.schemas.proxy import (
    ProxyCreateRequest,
    ProxyListItem,
    ProxyListResponse,
    ProxyStatusResponse,
    ProxyUpdateRequest,
)
from app.services.proxy_monitor_service import ProxyMonitorService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи немедленной проверки, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи) — паттерн ai_key_service.
_background_tasks: set[asyncio.Task[None]] = set()


def _normalize_optional(value: str | None) -> str | None:
    """Пустая строка/`None` → `None` (означает «убрать значение»); иначе — как есть."""
    return value if value else None


class ProxyService:
    """CRUD реестра прокси + запуск немедленной фоновой проверки при create/edit."""

    def __init__(self, repository: ProxyRepository, monitor: ProxyMonitorService) -> None:
        self._repo = repository
        self._monitor = monitor

    async def create_proxy(self, payload: ProxyCreateRequest) -> ProxyListItem:
        """Шифрует пароль (если задан), сохраняет (pending), запускает проверку."""
        username = _normalize_optional(payload.username)
        password = _normalize_optional(payload.password)
        password_encrypted = encrypt_secret(password) if password is not None else None

        proxy = await self._repo.create(
            name=payload.name,
            proxy_type=payload.proxy_type.value,
            host=payload.host,
            port=payload.port,
            username=username,
            password_encrypted=password_encrypted,
        )
        await self._repo.session.commit()

        self._schedule_check(proxy.id)
        logger.info("proxy_created", proxy_id=str(proxy.id))
        return self._to_list_item(proxy)

    async def list_proxies(self) -> ProxyListResponse:
        """Список прокси (position ASC, created_at DESC, id); пароль не раскрывается."""
        proxies = await self._repo.list_all()
        return ProxyListResponse(items=[self._to_list_item(proxy) for proxy in proxies])

    async def update_proxy(self, proxy_id: uuid.UUID, payload: ProxyUpdateRequest) -> ProxyListItem:
        """Редактирует прокси (04-api.md#patch-apiproxiesid, modules/proxies).

        Семантика пароля: не передан = не менять; `null`/`""` = очистить; непустой →
        re-encrypt. `username`: не передан = не менять; передан (`null`/`""` → убрать;
        значение → установить). Re-check (pending + немедленная проверка от
        `prev='pending'`) — при смене `proxy_type`/`host`/`port`/`username` ИЛИ любой
        передаче `password`. Только смена `name` — без re-check. Нет записи → 404.
        """
        proxy = await self._repo.get_by_id(proxy_id)
        if proxy is None:
            raise proxy_not_found()

        fields_set = payload.model_fields_set

        if payload.name is not None:
            proxy.name = payload.name

        proxy_type_changed = (
            payload.proxy_type is not None and payload.proxy_type.value != proxy.proxy_type
        )
        if proxy_type_changed:
            assert payload.proxy_type is not None
            proxy.proxy_type = payload.proxy_type.value

        host_changed = payload.host is not None and payload.host != proxy.host
        if host_changed:
            assert payload.host is not None
            proxy.host = payload.host

        port_changed = payload.port is not None and payload.port != proxy.port
        if port_changed:
            assert payload.port is not None
            proxy.port = payload.port

        # username: «передано» — по множеству заданных полей (null/"" ⇒ убрать логин).
        username_changed = False
        if "username" in fields_set:
            new_username = _normalize_optional(payload.username)
            username_changed = new_username != proxy.username
            proxy.username = new_username

        # password (секрет): любая передача = связанное с подключением изменение.
        #   непустой → re-encrypt; null/"" → очистить (NULL). Не передан → не трогать.
        password_touched = "password" in fields_set
        if password_touched:
            new_password = _normalize_optional(payload.password)
            proxy.password_encrypted = (
                encrypt_secret(new_password) if new_password is not None else None
            )

        re_check = (
            proxy_type_changed
            or host_changed
            or port_changed
            or username_changed
            or password_touched
        )
        if re_check:
            proxy.check_status = ProxyStatus.pending.value
            proxy.error_message = None

        await self._repo.session.commit()
        await self._repo.session.refresh(proxy)

        if re_check:
            self._schedule_check(proxy.id)

        logger.info("proxy_updated", proxy_id=str(proxy_id), re_check=re_check)
        return self._to_list_item(proxy)

    async def reorder_proxies(self, ids: list[uuid.UUID]) -> None:
        """Перестановка единого списка: `position = 0..N-1` в одной транзакции.

        Прецеденция ошибок (04-api.md#прецеденция-ошибок-валидации): форма тела уже
        проверена pydantic (400); здесь — существование всех `id` (404, до полноты),
        затем полнота перестановки множества прокси (422).
        """
        all_ids = await self._repo.all_ids()
        for proxy_id in ids:
            if proxy_id not in all_ids:
                raise proxy_not_found()
        if len(ids) != len(all_ids) or set(ids) != all_ids:
            raise unprocessable("Список не является полной перестановкой прокси")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("proxies_reordered", count=len(ids))

    async def get_status(self, proxy_id: uuid.UUID) -> ProxyStatusResponse:
        """Лёгкий статус проверки; отсутствует → 404 proxy_not_found."""
        proxy = await self._repo.get_by_id(proxy_id)
        if proxy is None:
            raise proxy_not_found()
        return ProxyStatusResponse(
            id=proxy.id,
            check_status=ProxyStatus(proxy.check_status),
            error_message=proxy.error_message,
            last_checked_at=proxy.last_checked_at,
        )

    async def delete_proxy(self, proxy_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 proxy_not_found."""
        deleted = await self._repo.delete_by_id(proxy_id)
        if not deleted:
            raise proxy_not_found()
        await self._repo.session.commit()
        logger.info("proxy_deleted", proxy_id=str(proxy_id))

    def _schedule_check(self, proxy_id: uuid.UUID) -> None:
        """Fire-and-forget немедленная проверка (asyncio.create_task + сильная ссылка).

        Ошибка внутри задачи не влияет на ответ — статус отслеживается через
        GET /api/proxies/{id}/status.
        """
        task = asyncio.create_task(self._monitor.check_one(proxy_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    def _to_list_item(proxy: Proxy) -> ProxyListItem:
        """Собирает элемент ответа; `has_password` — из `password_encrypted IS NOT NULL`.

        Пароль (в любом виде) НЕ включается — только производный флаг.
        """
        return ProxyListItem(
            id=proxy.id,
            name=proxy.name,
            proxy_type=ProxyType(proxy.proxy_type),
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            has_password=proxy.password_encrypted is not None,
            check_status=ProxyStatus(proxy.check_status),
            error_message=proxy.error_message,
            position=proxy.position,
            last_checked_at=proxy.last_checked_at,
            created_at=proxy.created_at,
            updated_at=proxy.updated_at,
        )
