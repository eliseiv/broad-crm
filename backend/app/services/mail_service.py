"""Сервис модуля «Почты» — обёртка над MailClient (04-api.md#mail, ADR-012/038).

Headless read+write прокси без хранения (modules/mail): гейт `mail_enabled`,
валидация входа, применение `MailScope` (ролевая видимость/enforcement мутаций),
вызов внешнего клиента и **постатусный** маппинг его исключений в коды CRM
(04-api.md#mail). Состояние не хранится. Секрет `MAIL_API_KEY` в сервис не попадает —
он подставляется в заголовок только внутри клиента (05-security.md). Транзитные
IMAP/SMTP-пароли (в телах create/test/update) не логируются и в ответах не возвращаются.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import status
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.domain.mail import MailScope
from app.errors import (
    AppError,
    forbidden,
    mail_conflict,
    mail_group_not_found,
    mail_mailbox_not_found,
    mail_message_not_found,
    mail_not_configured,
    mail_tag_not_found,
    mail_unavailable,
    unprocessable,
    validation_error,
)
from app.infra.mail_client import (
    MailClient,
    MailRejected,
    MailUnavailable,
)
from app.logging import get_logger
from app.schemas.mail import (
    MailListResponse,
    MailMailbox,
    MailMailboxCreateRequest,
    MailMailboxesResponse,
    MailMailboxSyncResponse,
    MailMailboxTestRequest,
    MailMailboxTestResponse,
    MailMailboxUpdateRequest,
    MailOrder,
    MailReplyRequest,
    MailReplyResponse,
    MailTagApplyResponse,
    MailTagCreateRequest,
    MailTagFull,
    MailTagRule,
    MailTagRuleCreateRequest,
    MailTagsResponse,
    MailTagUpdateRequest,
    MailTeamsResponse,
    TeamMailboxesResponse,
)

logger = get_logger(__name__)

_LIMIT_MIN = 1
_LIMIT_MAX = 200

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class MailService:
    """Проксирует чтение/запись во внешний почтовый сервис с ролевой видимостью."""

    def __init__(self, client: MailClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    # --- Чтение ленты/справочников -----------------------------------------

    async def list_messages(
        self,
        *,
        scope: MailScope,
        order: MailOrder,
        since_id: int | None,
        before_id: int | None,
        limit: int,
        mail_account_id: int | None,
        group_id: int | None,
    ) -> MailListResponse:
        """Лента писем (04-api.md#mail, ADR-013/017/038).

        Гейт mail_enabled → валидация limit → взаимоисключение режимов пагинации →
        применение `MailScope` (инъекция/пересечение scope-групп) → проксирование →
        нормализация курсоров. Фильтры `mail_account_id`/`group_id` AND-комбинируемы
        (внешний AND: чужой ящик вне scope-групп → пустая страница). Вне scope у
        не-админа → пустая страница без вызова внешнего API (анти-энумерация).
        """
        self._ensure_configured()
        self._validate_limit(limit)
        self._validate_pagination_mode(order=order, since_id=since_id, before_id=before_id)

        should_query, group_ids = self._resolve_group_filter(scope, group_id)
        if not should_query:
            return MailListResponse(
                messages=[], next_since_id=None, next_before_id=None, has_more=False
            )
        try:
            raw = await self._client.list_messages(
                order=order,
                since_id=since_id,
                before_id=before_id,
                limit=limit,
                mail_account_id=mail_account_id,
                group_ids=group_ids,
            )
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            # Внешний 400 (страховка при рассинхроне режимов пагинации) → 400. Прочие
            # отклонения на идемпотентном list не предусмотрены контрактом → 502.
            if exc.status_code == status.HTTP_400_BAD_REQUEST:
                raise validation_error("Недопустимые параметры запроса ленты") from exc
            logger.warning("mail_list_unexpected_external_status", status=exc.status_code)
            raise mail_unavailable() from exc
        return self._parse(MailListResponse, self._normalize_cursors(order, raw))

    async def list_teams(self) -> MailTeamsResponse:
        """Список команд (прокси external /teams, 04-api.md#mail). Только 502/503."""
        self._ensure_configured()
        raw = await self._call_reference(self._client.list_teams())
        return self._parse(MailTeamsResponse, raw)

    async def list_mailboxes(
        self,
        *,
        scope: MailScope,
        is_active: bool | None,
        group_id: int | None,
    ) -> MailMailboxesResponse:
        """Список ящиков (прокси external /mailboxes, 04-api.md#mail, ADR-038).

        Гейт mail_enabled → применение `MailScope` → фильтры `is_active`/`group_id` во
        внешний API. Вне scope у не-админа → пустой список (анти-энумерация).
        """
        self._ensure_configured()
        should_query, group_ids = self._resolve_group_filter(scope, group_id)
        if not should_query:
            return MailMailboxesResponse(mailboxes=[])
        raw = await self._call_reference(
            self._client.list_mailboxes(is_active=is_active, group_ids=group_ids)
        )
        return self._parse(MailMailboxesResponse, raw)

    async def list_tags(self) -> MailTagsResponse:
        """Список глобальных тегов (прокси external /tags, 04-api.md#mail). Только 502/503."""
        self._ensure_configured()
        raw = await self._call_reference(self._client.list_tags())
        return self._parse(MailTagsResponse, raw)

    async def list_team_mailboxes(self, mail_group_id: int | None) -> TeamMailboxesResponse:
        """Ящики команды для detail-панели /teams (04-api.md#teams, ADR-038).

        `mail_group_id=None` или `mail_enabled=false` → пустой список (не 503/404 —
        секция «Почты команды» показывает пустое состояние). Иначе прокси external
        /mailboxes с фильтром группы; недоступность внешнего сервиса → 502.
        """
        if mail_group_id is None or not self._settings.mail_enabled:
            return TeamMailboxesResponse(mailboxes=[])
        raw = await self._call_reference(self._client.list_mailboxes(group_ids=[mail_group_id]))
        return self._parse(TeamMailboxesResponse, raw)

    async def reply(self, message_id: int, payload: MailReplyRequest) -> MailReplyResponse:
        """Ответ на письмо. Гейт mail_enabled, проверка непустого body, проксирование."""
        self._ensure_configured()
        self._validate_reply(payload)
        body = payload.model_dump(exclude_none=True)
        try:
            raw = await self._client.reply(message_id=message_id, payload=body)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise mail_message_not_found() from exc
            raise unprocessable("Внешний сервис отклонил ответ") from exc
        return self._parse(MailReplyResponse, raw)

    # --- Запись: почтовые ящики --------------------------------------------

    async def test_mailbox(self, payload: MailMailboxTestRequest) -> MailMailboxTestResponse:
        """Проверка IMAP/SMTP-соединения без сохранения (04-api.md#mail). Гейт mail:create.

        Путь `test` внешнего сервиса отдаёт 422/400 и НИКОГДА не 502 (502 —
        недоступность самого агрегатора). Пароли — транзитом, не логируются.
        """
        self._ensure_configured()
        try:
            raw = await self._client.test_mailbox(payload.model_dump())
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc) from exc
        return self._parse(MailMailboxTestResponse, raw)

    async def create_mailbox(
        self, *, scope: MailScope, payload: MailMailboxCreateRequest
    ) -> MailMailbox:
        """Создание ящика (04-api.md#mail). Гейт mail:create; для не-admin group_id ∈ scope."""
        self._ensure_configured()
        self._ensure_group_writable(scope, payload.group_id)
        try:
            raw = await self._client.create_mailbox(payload.model_dump())
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_mailbox_not_found, conflict=True) from exc
        return self._parse(MailMailbox, raw)

    async def update_mailbox(
        self, *, scope: MailScope, mailbox_id: int, payload: MailMailboxUpdateRequest
    ) -> MailMailbox:
        """Правка ящика (presence-семантика, 04-api.md#mail). Гейт mail:edit.

        Не-admin: текущий ящик ∈ scope (read-before-write) И новый group_id (если
        меняется) ∈ scope, иначе 403. Пароли — транзитом, не логируются.
        """
        self._ensure_configured()
        await self._ensure_mailbox_in_scope(scope, mailbox_id)
        if "group_id" in payload.model_fields_set:
            self._ensure_group_writable(scope, payload.group_id)
        try:
            raw = await self._client.update_mailbox(
                mailbox_id, payload.model_dump(exclude_unset=True)
            )
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_mailbox_not_found, conflict=True) from exc
        return self._parse(MailMailbox, raw)

    async def delete_mailbox(self, *, scope: MailScope, mailbox_id: int) -> None:
        """Удаление ящика (04-api.md#mail). Гейт mail:delete; не-admin — ящик ∈ scope."""
        self._ensure_configured()
        await self._ensure_mailbox_in_scope(scope, mailbox_id)
        try:
            await self._client.delete_mailbox(mailbox_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_mailbox_not_found) from exc

    async def sync_mailbox(self, *, scope: MailScope, mailbox_id: int) -> MailMailboxSyncResponse:
        """Форс-синк ящика (04-api.md#mail). Гейт mail:sync; не-admin — ящик ∈ scope."""
        self._ensure_configured()
        await self._ensure_mailbox_in_scope(scope, mailbox_id)
        try:
            raw = await self._client.sync_mailbox(mailbox_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_mailbox_not_found) from exc
        return self._parse(MailMailboxSyncResponse, raw)

    # --- Запись: теги (глобальный каталог, гейт mail:tags, scope не применяется) ---

    async def create_tag(self, payload: MailTagCreateRequest) -> MailTagFull:
        """Создание тега (04-api.md#mail). Гейт mail:tags."""
        self._ensure_configured()
        try:
            raw = await self._client.create_tag(payload.model_dump())
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, conflict=True) from exc
        return self._parse(MailTagFull, raw)

    async def update_tag(self, tag_id: int, payload: MailTagUpdateRequest) -> MailTagFull:
        """Правка тега (04-api.md#mail). Гейт mail:tags."""
        self._ensure_configured()
        try:
            raw = await self._client.update_tag(tag_id, payload.model_dump(exclude_unset=True))
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_tag_not_found, conflict=True) from exc
        return self._parse(MailTagFull, raw)

    async def delete_tag(self, tag_id: int) -> None:
        """Удаление тега (04-api.md#mail). Гейт mail:tags; встроенный тег → 409."""
        self._ensure_configured()
        try:
            await self._client.delete_tag(tag_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_tag_not_found, conflict=True) from exc

    async def create_tag_rule(self, tag_id: int, payload: MailTagRuleCreateRequest) -> MailTagRule:
        """Добавление правила тегу (04-api.md#mail). Гейт mail:tags."""
        self._ensure_configured()
        try:
            raw = await self._client.create_tag_rule(tag_id, payload.model_dump())
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_tag_not_found) from exc
        return self._parse(MailTagRule, raw)

    async def delete_tag_rule(self, tag_id: int, rule_id: int) -> None:
        """Удаление правила (04-api.md#mail). Гейт mail:tags."""
        self._ensure_configured()
        try:
            await self._client.delete_tag_rule(tag_id, rule_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_tag_not_found) from exc

    async def apply_tag_to_existing(self, tag_id: int) -> MailTagApplyResponse:
        """Применить правила тега к существующим письмам (04-api.md#mail). Гейт mail:tags."""
        self._ensure_configured()
        try:
            raw = await self._client.apply_tag_to_existing(tag_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_tag_not_found) from exc
        return self._parse(MailTagApplyResponse, raw)

    # --- Scope helpers -----------------------------------------------------

    @staticmethod
    def _resolve_group_filter(
        scope: MailScope, group_id: int | None
    ) -> tuple[bool, list[int] | None]:
        """Эффективный групповой фильтр чтения (ADR-038 §3).

        Возвращает `(query_external, group_ids)`:
        - admin — без сужения (`group_id` пробрасывается как есть, либо None);
        - не-admin с пустым scope → `(False, None)` (пустой результат без вызова);
        - не-admin с выбранной группой ∉ scope → `(False, None)`;
        - не-admin с выбранной группой ∈ scope → только она; иначе — все группы scope.
        """
        if scope.sees_all_teams:
            return True, ([group_id] if group_id is not None else None)
        if not scope.group_ids:
            return False, None
        if group_id is not None:
            if group_id not in scope.group_ids:
                return False, None
            return True, [group_id]
        return True, sorted(scope.group_ids)

    @staticmethod
    def _ensure_group_writable(scope: MailScope, group_id: int | None) -> None:
        """Мутация с привязкой к группе вне scope → 403 (ADR-038 §3).

        Не-admin обязан указать `group_id` ∈ `scope.group_ids`; `None` (без команды)
        не-админу недоступно (симметрично unassigned-номеру SMS).
        """
        if scope.sees_all_teams:
            return
        if group_id is None or group_id not in scope.group_ids:
            raise forbidden()

    async def _ensure_mailbox_in_scope(self, scope: MailScope, mailbox_id: int) -> None:
        """Read-before-write: целевой ящик ∈ группам scope, иначе 403 (ADR-038 §3).

        Локального каталога ящиков в CRM нет — принадлежность проверяется запросом
        ящиков scope-групп во внешний API (анти-энумерация: вне scope → 403).
        """
        if scope.sees_all_teams:
            return
        if not scope.group_ids:
            raise forbidden()
        try:
            raw = await self._client.list_mailboxes(group_ids=sorted(scope.group_ids))
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            logger.warning("mail_scope_guard_unexpected_status", status=exc.status_code)
            raise mail_unavailable() from exc
        ids = {mb.get("id") for mb in raw.get("mailboxes", []) if isinstance(mb, dict)}
        if mailbox_id not in ids:
            raise forbidden()

    # --- Общие хелперы -----------------------------------------------------

    @staticmethod
    def _map_rejected(
        exc: MailRejected,
        *,
        not_found: Callable[[], AppError] | None = None,
        conflict: bool = False,
    ) -> AppError:
        """Постатусный маппинг отклонения write-запроса в код CRM (ADR-038, 04-api.md#mail).

        400 → validation_error; 404 → различается по машиночитаемому коду внешней
        ошибки: `group_not_found` → mail_group_not_found (выбранная команда
        агрегатора не существует), прочий 404 → `not_found()` (контекст
        ящик/тег/письмо); 409 → mail_conflict (если `conflict`); 422 →
        unprocessable; иное → mail_unavailable.
        """
        code = exc.status_code
        if code == status.HTTP_400_BAD_REQUEST:
            return validation_error("Внешний сервис отклонил запрос")
        if code == status.HTTP_404_NOT_FOUND:
            if exc.error_code == "group_not_found":
                return mail_group_not_found()
            if not_found is not None:
                return not_found()
        if code == status.HTTP_409_CONFLICT and conflict:
            return mail_conflict()
        if code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            return unprocessable("Внешний сервис отклонил запрос")
        logger.warning("mail_write_unexpected_external_status", status=code)
        return mail_unavailable()

    @staticmethod
    async def _call_reference(fetch: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        """GET-справочник: любые ошибки клиента → 502 (эндпоинты только 502/503).

        Гейт 503 (mail_enabled) применяется выше; здесь недоступность/отклонение/
        неожиданный статус сводятся к 502 mail_unavailable (04-api.md#mail).
        """
        try:
            return await fetch
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            logger.warning("mail_reference_unexpected_external_status", status=exc.status_code)
            raise mail_unavailable() from exc

    def _ensure_configured(self) -> None:
        """Гейт: почта активна только при заданном MAIL_API_KEY (иначе 503)."""
        if not self._settings.mail_enabled:
            raise mail_not_configured()

    @staticmethod
    def _validate_limit(limit: int) -> None:
        """limit в диапазоне 1..200 (иначе 400 validation_error, 04-api.md#mail)."""
        if limit < _LIMIT_MIN or limit > _LIMIT_MAX:
            raise validation_error(
                details=[
                    {
                        "field": "limit",
                        "message": f"limit должен быть в диапазоне {_LIMIT_MIN}..{_LIMIT_MAX}",
                    }
                ]
            )

    @staticmethod
    def _validate_pagination_mode(
        *, order: MailOrder, since_id: int | None, before_id: int | None
    ) -> None:
        """Взаимоисключение режимов (04-api.md#mail): `before_id` только при `desc`,
        `since_id` только при `asc`. Иначе 400 validation_error ДО внешнего вызова."""
        if order == "desc" and since_id is not None:
            raise validation_error(
                details=[{"field": "since_id", "message": "since_id допустим только при order=asc"}]
            )
        if order == "asc" and before_id is not None:
            raise validation_error(
                details=[
                    {"field": "before_id", "message": "before_id допустим только при order=desc"}
                ]
            )

    @staticmethod
    def _normalize_cursors(order: MailOrder, raw: dict[str, object]) -> dict[str, object]:
        """Оставляет курсор запрошенного режима, второй → null (04-api.md#mail)."""
        normalized = dict(raw)
        if order == "desc":
            normalized["next_since_id"] = None
            normalized.setdefault("next_before_id", None)
        else:
            normalized["next_before_id"] = None
            normalized.setdefault("next_since_id", None)
        return normalized

    @staticmethod
    def _validate_reply(payload: MailReplyRequest) -> None:
        """Непустой body (иначе 422 unprocessable, 04-api.md#mail)."""
        if not payload.body.strip():
            raise unprocessable("Тело ответа не может быть пустым")

    @staticmethod
    def _parse(model: type[_ModelT], raw: dict[str, object]) -> _ModelT:
        """Нормализует ответ внешнего сервиса в схему; несовместимое тело → 502."""
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            logger.warning("mail_response_schema_mismatch", model=model.__name__)
            raise mail_unavailable() from exc


__all__ = ["MailService"]
