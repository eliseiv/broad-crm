"""Бизнес-логика реестра бэков (modules/backends, 04-api.md#backends)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.exc import IntegrityError

from app.errors import backend_code_taken, backend_not_found, secret_not_set, unprocessable
from app.infra.backend_check import is_valid_domain, normalize_domain
from app.infra.crypto import decrypt_secret, encrypt_secret
from app.logging import get_logger
from app.models.service_backend import Backend, BackendStatus
from app.repositories.backend_repository import BackendRepository
from app.schemas.backend import (
    BackendCreateRequest,
    BackendListItem,
    BackendListResponse,
    BackendStatusResponse,
    BackendUpdateRequest,
)
from app.services.backend_monitor_service import BackendMonitorService

logger = get_logger(__name__)

# Сильные ссылки на фоновые задачи немедленной проверки, чтобы их не собрал GC
# (asyncio хранит только weak ref на задачи) — паттерн proxy_service.
_background_tasks: set[asyncio.Task[None]] = set()

_INVALID_DOMAIN_MESSAGE = "Невалидный формат домена"


def _normalize_and_validate_domain(raw: str) -> str:
    """Нормализует домен к канону `https://<host>/` и валидирует формат host (ADR-042).

    Невалидный host → 422 unprocessable (`details[].field="domain"`).
    """
    if not is_valid_domain(raw):
        raise unprocessable(
            _INVALID_DOMAIN_MESSAGE,
            details=[{"field": "domain", "message": _INVALID_DOMAIN_MESSAGE}],
        )
    return normalize_domain(raw)


class BackendService:
    """CRUD реестра бэков + запуск немедленной фоновой проверки при create/edit."""

    def __init__(self, repository: BackendRepository, monitor: BackendMonitorService) -> None:
        self._repo = repository
        self._monitor = monitor

    async def create_backend(self, payload: BackendCreateRequest) -> BackendListItem:
        """Валидирует/нормализует домен (422), проверяет FK (422) и уникальность `code`
        (409), шифрует секреты, сохраняет (pending), запускает немедленную проверку.

        Прецеденция (04-api.md#post-apibackends): схемная валидация (400/422 — формат
        домена или несуществующий `server_id`/`ai_key_id`) до проверки уникальности
        `code` (409).
        """
        domain = _normalize_and_validate_domain(payload.domain)
        await self._validate_fk(payload.server_id, payload.ai_key_id)

        if await self._repo.exists_by_code(payload.code):
            raise backend_code_taken()

        try:
            backend = await self._repo.create(
                code=payload.code,
                name=payload.name,
                domain=domain,
                server_id=payload.server_id,
                ai_key_id=payload.ai_key_id,
                api_key_encrypted=_encrypt_optional(payload.api_key),
                admin_api_key_encrypted=_encrypt_optional(payload.admin_api_key),
                git=_clean_optional(payload.git),
                note=_clean_optional(payload.note),
            )
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("backend_create_conflict", code=payload.code)
            raise backend_code_taken() from exc

        self._schedule_check(backend.id)
        logger.info("backend_created", backend_id=str(backend.id))
        return await self._to_list_item_resolved(backend)

    async def list_backends(self) -> BackendListResponse:
        """Список бэков (position ASC, created_at DESC, id) + join имён сервера/ключа."""
        backends = await self._repo.list_all()
        server_names = await self._repo.server_names(b.server_id for b in backends)
        ai_key_names = await self._repo.ai_key_names(b.ai_key_id for b in backends)
        items = [self._to_list_item(backend, server_names, ai_key_names) for backend in backends]
        return BackendListResponse(items=items)

    async def update_backend(
        self, backend_id: uuid.UUID, payload: BackendUpdateRequest
    ) -> BackendListItem:
        """Редактирует бэк (04-api.md#patch-apibackendsid, modules/backends).

        Прецеденция: 404 (нет id) → схемная валидация (400/422 — формат домена) →
        уникальность `code` (409). Re-check (pending + немедленная проверка от
        `prev='pending'`) — только при смене `domain`. Смена `code`/`name` статус
        не трогает. `updated_at` обновляется при изменении хотя бы одного поля.
        """
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()

        fields_set = payload.model_fields_set

        # 1) Домен: канонизация + валидация (422) до проверки уникальности code (409).
        domain_changed = False
        if "domain" in fields_set and payload.domain is not None:
            new_domain = _normalize_and_validate_domain(payload.domain)
            domain_changed = new_domain != backend.domain
            backend.domain = new_domain

        # 2) FK: presence-семантика (null → обнулить; uuid → проверить существование, 422).
        if "server_id" in fields_set:
            if payload.server_id is not None:
                await self._validate_server(payload.server_id)
            backend.server_id = payload.server_id
        if "ai_key_id" in fields_set:
            if payload.ai_key_id is not None:
                await self._validate_ai_key(payload.ai_key_id)
            backend.ai_key_id = payload.ai_key_id

        # 3) Код: уникальность (409) при смене на занятый ДРУГИМ бэком.
        if "code" in fields_set and payload.code is not None and payload.code != backend.code:
            if await self._repo.exists_by_code(payload.code, exclude_id=backend.id):
                raise backend_code_taken()
            backend.code = payload.code

        # 4) Имя.
        if "name" in fields_set and payload.name is not None:
            backend.name = payload.name

        # 5) Секреты: непустая строка → зашифровать; null/"" → очистить (NULL).
        if "api_key" in fields_set:
            backend.api_key_encrypted = _encrypt_optional(payload.api_key)
        if "admin_api_key" in fields_set:
            backend.admin_api_key_encrypted = _encrypt_optional(payload.admin_api_key)

        # 6) git/note (не секреты): непустая строка → установить; null/"" → очистить.
        if "git" in fields_set:
            backend.git = _clean_optional(payload.git)
        if "note" in fields_set:
            backend.note = _clean_optional(payload.note)

        # Re-check только при смене домена (единственное поле подключения).
        if domain_changed:
            backend.check_status = BackendStatus.pending.value
            backend.error_message = None

        try:
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("backend_update_conflict", backend_id=str(backend_id))
            raise backend_code_taken() from exc
        await self._repo.session.refresh(backend)

        if domain_changed:
            self._schedule_check(backend.id)

        logger.info("backend_updated", backend_id=str(backend_id), re_check=domain_changed)
        return await self._to_list_item_resolved(backend)

    async def reorder_backends(self, ids: list[uuid.UUID]) -> None:
        """Перестановка единого списка: `position = 0..N-1` в одной транзакции.

        Прецеденция ошибок (04-api.md#прецеденция-ошибок-валидации): форма тела уже
        проверена pydantic (400); здесь — существование всех `id` (404, до полноты),
        затем полнота перестановки множества бэков (422).
        """
        all_ids = await self._repo.all_ids()
        for backend_id in ids:
            if backend_id not in all_ids:
                raise backend_not_found()
        if len(ids) != len(all_ids) or set(ids) != all_ids:
            raise unprocessable("Список не является полной перестановкой бэков")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("backends_reordered", count=len(ids))

    async def get_status(self, backend_id: uuid.UUID) -> BackendStatusResponse:
        """Лёгкий статус проверки; отсутствует → 404 backend_not_found."""
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        return BackendStatusResponse(
            id=backend.id,
            check_status=BackendStatus(backend.check_status),
            error_message=backend.error_message,
            last_checked_at=backend.last_checked_at,
        )

    async def delete_backend(self, backend_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 backend_not_found."""
        deleted = await self._repo.delete_by_id(backend_id)
        if not deleted:
            raise backend_not_found()
        await self._repo.session.commit()
        logger.info("backend_deleted", backend_id=str(backend_id))

    async def reveal_api_key(self, backend_id: uuid.UUID) -> str:
        """On-demand reveal API KEY бэка (ADR-040, require backends:edit).

        Расшифровка `api_key_encrypted` в памяти обработчика. Нет записи → 404
        backend_not_found; ключ не задан (`api_key_encrypted IS NULL`) → 404
        secret_not_set. Значение возвращается роутеру и НЕ логируется здесь.
        """
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        if backend.api_key_encrypted is None:
            raise secret_not_set()
        return decrypt_secret(backend.api_key_encrypted)

    async def reveal_admin_api_key(self, backend_id: uuid.UUID) -> str:
        """On-demand reveal ADMIN API KEY бэка (ADR-040, require backends:edit).

        Аналогично `reveal_api_key`, но для `admin_api_key_encrypted`. Нет записи →
        404 backend_not_found; ключ не задан → 404 secret_not_set.
        """
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        if backend.admin_api_key_encrypted is None:
            raise secret_not_set()
        return decrypt_secret(backend.admin_api_key_encrypted)

    async def _validate_fk(self, server_id: uuid.UUID | None, ai_key_id: uuid.UUID | None) -> None:
        """Проверяет существование заданных FK (`server_id`, затем `ai_key_id`); 422."""
        if server_id is not None:
            await self._validate_server(server_id)
        if ai_key_id is not None:
            await self._validate_ai_key(ai_key_id)

    async def _validate_server(self, server_id: uuid.UUID) -> None:
        """Несуществующий `server_id` → 422 unprocessable (`details[].field`)."""
        if not await self._repo.server_exists(server_id):
            raise unprocessable(
                "Указанный сервер не найден",
                details=[{"field": "server_id", "message": "Сервер не найден"}],
            )

    async def _validate_ai_key(self, ai_key_id: uuid.UUID) -> None:
        """Несуществующий `ai_key_id` → 422 unprocessable (`details[].field`)."""
        if not await self._repo.ai_key_exists(ai_key_id):
            raise unprocessable(
                "Указанный ИИ-ключ не найден",
                details=[{"field": "ai_key_id", "message": "ИИ-ключ не найден"}],
            )

    def _schedule_check(self, backend_id: uuid.UUID) -> None:
        """Fire-and-forget немедленная проверка (asyncio.create_task + сильная ссылка).

        Ошибка внутри задачи не влияет на ответ — статус отслеживается через
        GET /api/backends/{id}/status.
        """
        task = asyncio.create_task(self._monitor.check_one(backend_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    async def _to_list_item_resolved(self, backend: Backend) -> BackendListItem:
        """Собирает элемент ответа для одиночного бэка (create/update), подтягивая имена
        связанных сервера/ключа одним batch-запросом каждого вида (без N+1)."""
        server_names = await self._repo.server_names([backend.server_id])
        ai_key_names = await self._repo.ai_key_names([backend.ai_key_id])
        return self._to_list_item(backend, server_names, ai_key_names)

    @staticmethod
    def _to_list_item(
        backend: Backend,
        server_names: dict[uuid.UUID, str],
        ai_key_names: dict[uuid.UUID, str],
    ) -> BackendListItem:
        """Собирает элемент ответа (ADR-040).

        Секреты `api_key`/`admin_api_key` НЕ отдаются — только `has_*` (шифртекст
        `IS NOT NULL`). `server_name`/`ai_key_name` берутся из переданных map'ов имён
        (`None`, если связи нет). `git`/`note` — не секреты, отдаются как есть.
        """
        server_name = server_names.get(backend.server_id) if backend.server_id is not None else None
        ai_key_name = ai_key_names.get(backend.ai_key_id) if backend.ai_key_id is not None else None
        return BackendListItem(
            id=backend.id,
            code=backend.code,
            name=backend.name,
            domain=backend.domain,
            server_id=backend.server_id,
            server_name=server_name,
            ai_key_id=backend.ai_key_id,
            ai_key_name=ai_key_name,
            has_api_key=backend.api_key_encrypted is not None,
            has_admin_api_key=backend.admin_api_key_encrypted is not None,
            git=backend.git,
            note=backend.note,
            check_status=BackendStatus(backend.check_status),
            error_message=backend.error_message,
            position=backend.position,
            last_checked_at=backend.last_checked_at,
            created_at=backend.created_at,
            updated_at=backend.updated_at,
        )


def _encrypt_optional(value: str | None) -> bytes | None:
    """Шифрует секрет Fernet, если он непустой; `None`/`""` → `None` (очистить/не задавать)."""
    if value is None or value == "":
        return None
    return encrypt_secret(value)


def _clean_optional(value: str | None) -> str | None:
    """Нормализует не-секретное опциональное поле (`git`/`note`): `""` → `None`."""
    if value is None or value == "":
        return None
    return value
