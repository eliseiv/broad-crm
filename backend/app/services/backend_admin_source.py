"""Общий резолвер источника CRM Admin API: бэк реестра → готовый admin-клиент.

Путь расшифровки admin-ключа бэка — **security-critical** и общий для страниц
«Юзеры бэков» (modules/backend-users) и «Продукты и тарифы» (modules/backend-economics,
ADR-072 §Последствия): дублировать его во второй сервис нельзя. Ключ
(`admin_api_key_encrypted`, Fernet) расшифровывается в памяти обработчика, уходит
только заголовком `X-Admin-Key` и во frontend не попадает.

Единственные исходы отказа — те же, что были у `BackendUserService`: бэка нет в
реестре → `404 backend_not_found`; ключ не задан → `409 backend_admin_key_not_set`.
"""

from __future__ import annotations

import uuid

from app.errors import backend_admin_key_not_set, backend_not_found
from app.infra.backend_admin_client import BackendAdminClient
from app.infra.crypto import decrypt_secret
from app.models.service_backend import Backend
from app.repositories.backend_repository import BackendRepository

# Источник агрегации/транзита: бэк реестра + клиент его admin-эндпоинтов.
BackendSource = tuple[Backend, BackendAdminClient]


class BackendAdminSourceResolver:
    """Резолвит бэки реестра в admin-клиенты (единственная точка расшифровки ключа)."""

    def __init__(self, repository: BackendRepository) -> None:
        self._repo = repository

    async def require(self, backend_id: uuid.UUID) -> BackendSource:
        """Один бэк: обязан существовать и иметь admin-ключ, иначе 404/409."""
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        if backend.admin_api_key_encrypted is None:
            raise backend_admin_key_not_set()
        return backend, self.client(backend)

    async def list_with_admin_key(self) -> list[BackendSource]:
        """Все бэки реестра, у которых задан admin-ключ (fan-out «Все приложения»).

        Прежний `list_split()` (пара «источники + бэки БЕЗ ключа») **удалён вместе со
        своим единственным потребителем** (ADR-080 §1): бэк без ключа больше не попадает
        в `errors[]` и наружу не называется вовсе, поэтому второй список стал мёртвым.
        Оставлять его «на всякий случай» значило бы держать в security-critical резолвере
        путь, которым никто не ходит.
        """
        backends = await self._repo.list_all()
        return [(b, self.client(b)) for b in backends if b.admin_api_key_encrypted is not None]

    @staticmethod
    def client(backend: Backend) -> BackendAdminClient:
        """Клиент admin-эндпоинтов бэка; ключ расшифровывается здесь и только здесь."""
        encrypted = backend.admin_api_key_encrypted
        if encrypted is None:  # защищено фильтрами require/list_with_admin_key
            raise backend_admin_key_not_set()
        return BackendAdminClient(
            backend_id=backend.id,
            domain=backend.domain,
            admin_key=decrypt_secret(encrypted),
        )


__all__ = ["BackendAdminSourceResolver", "BackendSource"]
