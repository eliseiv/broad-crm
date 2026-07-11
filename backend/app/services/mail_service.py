"""Сервис модуля «Почты» (ADR-044) — система-запись в CRM + транзит в агрегатор.

CRM хранит письма/теги/каталог ящиков (разворот «без хранения» ADR-012/038):
- **чтение** ленты/ящиков/тегов — из БД CRM (`mail_messages`/`mail_accounts`/
  `mail_tags`), с ролевой видимостью по `MailScope.team_ids` (per-mailbox `team_id`);
- **запись ящика** (create/update/delete/sync/test) — креды транзитом в агрегатор
  (шифрование там, Fernet CRM к почте не применяется), строка каталога — в CRM;
- **reply** — письмо берётся из БД CRM, SMTP-отправка делегируется агрегатору
  (`POST /api/external/mailboxes/{id}/send`), факт отправки пишется в
  `mail_sent_messages`.

Секрет `MAIL_API_KEY` в сервис не попадает — подставляется в заголовок только внутри
клиента (05-security.md). Транзитные IMAP/SMTP-пароли (в телах create/test/update) не
логируются и в ответах не возвращаются.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar, cast

from fastapi import status
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.mail import (
    MAX_REPLY_BODY_BYTES,
    MAX_REPLY_RECIPIENTS,
    MAX_REPLY_SUBJECT_LEN,
    MailCursorError,
    MailReplyError,
    MailScope,
    build_display_name,
    decode_mail_cursor,
    encode_mail_cursor,
    validate_reply_addresses,
)
from app.errors import (
    AppError,
    forbidden,
    invalid_cursor,
    invalid_limit,
    mail_conflict,
    mail_mailbox_not_found,
    mail_message_not_found,
    mail_not_configured,
    mail_tag_not_found,
    mail_unavailable,
    team_not_found,
    unprocessable,
    validation_error,
)
from app.infra.mail_client import MailClient, MailRejected, MailUnavailable
from app.infra.mail_oauth_state import encode_crm_state
from app.logging import get_logger
from app.models.mail_account import MailAccount
from app.models.mail_message import MailMessage as MailMessageModel
from app.models.mail_tag import MailTag as MailTagModel
from app.models.mail_tag import MailTagRule as MailTagRuleModel
from app.repositories.mail_account_repository import MailAccountRepository
from app.repositories.mail_message_repository import MailMessageRepository
from app.repositories.mail_sent_message_repository import MailSentMessageRepository
from app.repositories.mail_tag_repository import MailTagRepository
from app.repositories.team_repository import TeamRepository
from app.schemas.mail import (
    MailAccountRef,
    MailListResponse,
    MailMailbox,
    MailMailboxCreateRequest,
    MailMailboxesResponse,
    MailMailboxSyncResponse,
    MailMailboxTestRequest,
    MailMailboxTestResponse,
    MailMailboxUpdateRequest,
    MailMessage,
    MailOauthAuthorizeRequest,
    MailOauthAuthorizeResponse,
    MailReplyRequest,
    MailReplyResponse,
    MailTag,
    MailTagApplyResponse,
    MailTagCreateRequest,
    MailTagFull,
    MailTagMatchMode,
    MailTagRule,
    MailTagRuleCreateRequest,
    MailTagRuleType,
    MailTagsResponse,
    MailTagUpdateRequest,
    TeamMailboxesResponse,
    TeamMailboxItem,
)

logger = get_logger(__name__)

_LIMIT_MIN = 1
_LIMIT_MAX = 200

_ModelT = TypeVar("_ModelT", bound=BaseModel)

# Исходящий payload в агрегатор строится БЕЛЫМ СПИСКОМ, а не «model_dump() минус пара
# полей» (ADR-047 §3.4): иначе любое новое поле схемы CRM молча утекло бы наружу.
#
# Ключи PATCH, которые транзитом уходят в агрегатор (креды/статус/email/имя).
# `team_id` НЕ входит — перенос между командами локален (агрегатор о командах не знает).
# `number`/`app_name` НЕ входят — наружу уходит только вычисленный `display_name`
# (он в списке; агрегатор знает лишь эту форму имени, ADR-047 §3.3/§3.4).
_MAILBOX_AGGREGATOR_FIELDS = frozenset(
    {
        "email",
        "display_name",
        "imap_host",
        "imap_port",
        "imap_ssl",
        "smtp_host",
        "smtp_port",
        "smtp_ssl",
        "smtp_starttls",
        "smtp_username",
        "password",
        "smtp_password",
        "is_active",
    }
)

# Ключи тела POST, уходящие в агрегатор при СОЗДАНИИ ящика (креды + `email`). Тот же
# белый список (ADR-047 §3.4); `display_name` подставляется вычисленным значением,
# `team_id`/`number`/`app_name` не уходят наружу никогда.
_MAILBOX_CREATE_AGGREGATOR_FIELDS = frozenset(
    {
        "email",
        "imap_host",
        "imap_port",
        "imap_ssl",
        "smtp_host",
        "smtp_port",
        "smtp_ssl",
        "smtp_starttls",
        "smtp_username",
        "password",
        "smtp_password",
    }
)


class MailService:
    """Чтение почты из БД CRM + транзит операций ящика/reply в агрегатор (ADR-044)."""

    def __init__(self, session: AsyncSession, client: MailClient, settings: Settings) -> None:
        self._session = session
        self._client = client
        self._settings = settings
        self._accounts = MailAccountRepository(session)
        self._messages = MailMessageRepository(session)
        self._tags = MailTagRepository(session)
        self._sent = MailSentMessageRepository(session)
        self._teams = TeamRepository(session)

    # --- Лента писем (чтение из БД CRM) ------------------------------------

    async def list_messages(
        self,
        *,
        scope: MailScope,
        before: str | None,
        limit: int,
        mail_account_ids: list[int] | None,
        team_id: uuid.UUID | None,
    ) -> MailListResponse:
        """Лента писем из `mail_messages` (компаундный keyset, ADR-044 §2/§7).

        Порядок `internal_date DESC, id DESC`. Фильтры `mail_account_id` (повторяемый) и
        `team_id` AND-комбинируемы, пересекаются со scope команд пользователя. Вне scope
        (не-admin с пустым `team_ids` / несуществующий фильтр) → пустая страница без
        выборки писем (анти-энумерация). `before` — opaque-курсор пары `(internal_date,
        id)`; битый → 400 invalid_cursor. `limit` вне [1..200] → 400 invalid_limit.
        """
        if limit < _LIMIT_MIN or limit > _LIMIT_MAX:
            raise invalid_limit()
        cursor: tuple[datetime, int] | None = None
        if before:
            try:
                cursor = decode_mail_cursor(before)
            except MailCursorError as exc:
                raise invalid_cursor() from exc

        visible = await self._resolve_visible_accounts(
            scope=scope, mail_account_ids=mail_account_ids, team_id=team_id
        )
        if visible is not None and not visible:
            return MailListResponse(messages=[], next_cursor=None)

        rows = await self._messages.list_feed(
            mail_account_ids=visible, cursor=cursor, limit=limit + 1
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor: str | None = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_mail_cursor(last.internal_date, last.id)

        messages = await self._serialize_messages(page)
        return MailListResponse(messages=messages, next_cursor=next_cursor)

    async def _resolve_visible_accounts(
        self,
        *,
        scope: MailScope,
        mail_account_ids: list[int] | None,
        team_id: uuid.UUID | None,
    ) -> list[int] | None:
        """Множество видимых `mail_account_id` (AND-пересечение scope + фильтров).

        `None` — без ограничения (admin без фильтров → все письма). `[]` — пустой
        результат (вне scope / несуществующий фильтр — анти-энумерация).
        """
        constraints: list[set[int]] = []

        if not scope.sees_all_teams:
            if not scope.team_ids:
                return []
            constraints.append(await self._accounts.ids_by_teams(scope.team_ids))

        if mail_account_ids is not None:
            constraints.append(set(mail_account_ids))

        if team_id is not None:
            constraints.append(await self._accounts.ids_by_team(team_id))

        if not constraints:
            return None
        visible = constraints[0]
        for extra in constraints[1:]:
            visible &= extra
        return list(visible)

    async def _serialize_messages(self, rows: list[MailMessageModel]) -> list[MailMessage]:
        """Проекция строк писем в схему ленты (ящик + теги батч-запросами, ADR-044 §2)."""
        if not rows:
            return []
        account_ids = {row.mail_account_id for row in rows}
        message_ids = [row.id for row in rows]
        accounts = await self._accounts.get_many(account_ids)
        tags_by_message = await self._tags.tags_for_messages(message_ids)

        messages: list[MailMessage] = []
        for row in rows:
            account = accounts.get(row.mail_account_id)
            account_ref = MailAccountRef(
                id=row.mail_account_id,
                email=account.email if account is not None else "",
                display_name=account.display_name if account is not None else None,
            )
            tags = [
                MailTag(id=tag.id, name=tag.name, color=tag.color)
                for tag in tags_by_message.get(row.id, [])
            ]
            messages.append(
                MailMessage(
                    id=row.id,
                    subject=row.subject,
                    internal_date=row.internal_date,
                    from_addr=row.from_addr,
                    from_name=row.from_name,
                    to_addrs=row.to_addrs,
                    cc_addrs=row.cc_addrs,
                    mail_account=account_ref,
                    body_text=row.body_text,
                    body_html=row.body_html,
                    body_present=row.body_present,
                    body_truncated=row.body_truncated,
                    tags=tags,
                )
            )
        return messages

    # --- Каталог ящиков: чтение из БД CRM ----------------------------------

    async def list_mailboxes(
        self, *, scope: MailScope, is_active: bool | None
    ) -> MailMailboxesResponse:
        """Список ящиков из каталога CRM `mail_accounts` (ADR-044 §4/§7).

        Не-admin — только ящики своих команд (`team_id ∈ scope.team_ids`; пустой набор →
        пустой список, анти-энумерация). Admin — все ящики. `is_active` — доп. фильтр.
        """
        team_ids = None if scope.sees_all_teams else scope.team_ids
        accounts = await self._accounts.list_scoped(team_ids=team_ids, is_active=is_active)
        return MailMailboxesResponse(mailboxes=[self._to_mailbox(a) for a in accounts])

    async def list_team_mailboxes(self, team_id: uuid.UUID) -> TeamMailboxesResponse:
        """Ящики команды для detail-панели /teams (ADR-044 §4)."""
        accounts = await self._accounts.list_by_team(team_id)
        return TeamMailboxesResponse(
            mailboxes=[
                TeamMailboxItem(
                    id=a.id, email=a.email, display_name=a.display_name, is_active=a.is_active
                )
                for a in accounts
            ]
        )

    # --- Каталог ящиков: запись (транзит кредов в агрегатор) ----------------

    async def test_mailbox(self, payload: MailMailboxTestRequest) -> MailMailboxTestResponse:
        """Проверка IMAP/SMTP-соединения без сохранения (ADR-044 §4). Гейт mail:create.

        Путь `test` агрегатора отдаёт 422/400 и НИКОГДА не 502. Пароли — транзитом.
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
        """Создание ящика (ADR-044 §4, ADR-047 §3): креды в агрегатор → строка каталога.

        Авторизация: не-admin обязан указать `team_id ∈ scope.team_ids`; `team_id=null`
        (unassigned) — только admin-уровень. Поток: аггрегатор `POST /mailboxes` (без
        `group_id`, владелец `crm-service`) → присвоенный `id` → вставка `mail_accounts`.

        Исходящий payload — БЕЛЫЙ СПИСОК (креды + `email`) плюс вычисленный
        `display_name` (ADR-047 §3.4): `team_id`/`number`/`app_name` наружу не уходят.
        """
        self._ensure_configured()
        await self._ensure_team_writable(scope, payload.team_id)

        display_name = build_display_name(payload.number, payload.app_name)
        creds = {
            key: value
            for key, value in payload.model_dump().items()
            if key in _MAILBOX_CREATE_AGGREGATOR_FIELDS
        }
        creds["display_name"] = display_name
        try:
            raw = await self._client.create_mailbox(creds)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, conflict=True) from exc

        account_id = self._extract_new_id(raw)
        is_active = raw.get("is_active")
        is_active = is_active if isinstance(is_active, bool) else True
        try:
            await self._accounts.create(
                account_id=account_id,
                email=payload.email,
                number=payload.number,
                app_name=payload.app_name,
                display_name=display_name,
                team_id=payload.team_id,
                is_active=is_active,
            )
        except Exception:
            # Ящик уже создан в агрегаторе, но каталог CRM не записан → сирота, которую
            # никто не увидит/не удалит. Best-effort компенсация: удалить его в
            # агрегаторе. Провал компенсации не должен ронять исходную ошибку.
            await self._compensate_orphan_mailbox(account_id)
            raise
        # Ответ собирается из известных значений (а не из ORM-строки): колонки с
        # server_default после flush экспайрятся и их чтение потребовало бы ленивого
        # SELECT (MissingGreenlet в async).
        return MailMailbox(
            id=account_id,
            email=payload.email,
            number=payload.number,
            app_name=payload.app_name,
            display_name=display_name,
            team_id=payload.team_id,
            is_active=is_active,
            last_synced_at=None,
            last_sync_error=None,
            consecutive_failures=0,
        )

    async def update_mailbox(
        self, *, scope: MailScope, mailbox_id: int, payload: MailMailboxUpdateRequest
    ) -> MailMailbox:
        """Правка ящика (presence-семантика, ADR-044 §4, ADR-047 §3). Гейт mail:edit.

        Не-admin: ящик ∈ scope, иначе 403. Смена `team_id` (перенос) — только
        admin-уровень (`scope.sees_all_teams`), иначе 403; новый `team_id` валидируется
        на существование. Креды/статус/email/имя — транзитом в агрегатор (требуют
        `mail_enabled` → иначе 503); `team_id` — локальный `UPDATE` без сетевого вызова.

        Имя: клиент присылает `number`/`app_name` (не `display_name`). При изменении
        любого из них сервер пересчитывает производный `display_name` из ЭФФЕКТИВНЫХ
        значений (новое поле, иначе текущее из БД) и кладёт в агрегаторный payload
        **его** — сами `number`/`app_name` наружу не уходят (ADR-047 §3.3/§3.4).
        """
        account = await self._load_account_in_scope(scope, mailbox_id)

        fields_set = payload.model_fields_set
        team_id_change = "team_id" in fields_set
        if team_id_change:
            if not scope.sees_all_teams:
                raise forbidden()
            if payload.team_id is not None:
                await self._ensure_team_exists(payload.team_id)

        name_change = "number" in fields_set or "app_name" in fields_set
        new_number = payload.number if "number" in fields_set else account.number
        new_app_name = payload.app_name if "app_name" in fields_set else account.app_name
        new_display_name = build_display_name(new_number, new_app_name)

        aggregator_payload: dict[str, object] = {
            key: value
            for key, value in payload.model_dump(exclude_unset=True).items()
            if key in _MAILBOX_AGGREGATOR_FIELDS
        }
        if name_change:
            # Наружу уходит только пересчитанное производное имя (ключ белого списка).
            aggregator_payload["display_name"] = new_display_name
        if aggregator_payload:
            # Сетевой вызов к агрегатору только при изменении кредов/статуса/email/имени.
            self._ensure_configured()
            try:
                await self._client.update_mailbox(mailbox_id, aggregator_payload)
            except MailUnavailable as exc:
                raise mail_unavailable() from exc
            except MailRejected as exc:
                raise self._map_rejected(
                    exc, not_found=mail_mailbox_not_found, conflict=True
                ) from exc

        if "email" in fields_set and payload.email is not None:
            account.email = payload.email
        if name_change:
            account.number = new_number
            account.app_name = new_app_name
            account.display_name = new_display_name
        if "is_active" in fields_set and payload.is_active is not None:
            account.is_active = payload.is_active
        if team_id_change:
            account.team_id = payload.team_id
        await self._session.flush()
        return self._to_mailbox(account)

    async def delete_mailbox(self, *, scope: MailScope, mailbox_id: int) -> None:
        """Удаление ящика (ADR-044 §4). Гейт mail:delete; ящик ∈ scope.

        Агрегатор `DELETE /mailboxes/{id}` + удаление строки каталога (CASCADE удалит
        письма/reply ящика). Агрегаторский 404 (ящик там уже удалён) — best-effort:
        логируется, локальная строка всё равно чистится.
        """
        self._ensure_configured()
        await self._load_account_in_scope(scope, mailbox_id)
        try:
            await self._client.delete_mailbox(mailbox_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise self._map_rejected(exc, not_found=mail_mailbox_not_found) from exc
            logger.warning("mail_delete_aggregator_missing", mailbox_id=mailbox_id)
        await self._accounts.delete(mailbox_id)

    async def sync_mailbox(self, *, scope: MailScope, mailbox_id: int) -> MailMailboxSyncResponse:
        """Форс-синк ящика (ADR-044 §4). Гейт mail:sync; ящик ∈ scope. Проброс в агрегатор."""
        self._ensure_configured()
        await self._load_account_in_scope(scope, mailbox_id)
        try:
            raw = await self._client.sync_mailbox(mailbox_id)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_rejected(exc, not_found=mail_mailbox_not_found) from exc
        queued = raw.get("queued")
        return MailMailboxSyncResponse(queued=queued if isinstance(queued, bool) else True)

    # --- Outlook OAuth (headless инициация, ADR-045) -----------------------

    async def authorize_oauth(
        self,
        *,
        scope: MailScope,
        initiator_user_id: uuid.UUID | None,
        payload: MailOauthAuthorizeRequest,
    ) -> MailOauthAuthorizeResponse:
        """Инициировать headless Outlook-OAuth из CRM (ADR-045 §3). Гейт mail:create.

        Авторизация команды — идентична созданию ящика (ADR-044 §4): `team_id=null` —
        только admin; не-admin обязан указать `team_id ∈ scope.team_ids`; несуществующая
        `team_id` → 404 team_not_found. CRM минтит HMAC-подписанный `crm_state`
        (`{team_id, initiator, exp}` через `MAIL_PUSH_SECRET`, stateless) и запрашивает у
        агрегатора authorize URL. Outlook-OAuth недоступен (`MAIL_API_KEY` пуст → 503 в
        `_ensure_configured`; агрегатор вернул 404 → 503) → единый 503 mail_not_configured
        (§3). Транзиентная недоступность агрегатора → 502 mail_unavailable.
        """
        self._ensure_configured()
        await self._ensure_team_writable(scope, payload.team_id)

        exp = int(time.time()) + self._settings.mail_oauth_state_ttl_sec
        crm_state = encode_crm_state(
            secret=self._settings.mail_push_secret,
            team_id=payload.team_id,
            initiator_user_id=initiator_user_id,
            exp=exp,
        )
        try:
            raw = await self._client.authorize_oauth(crm_state)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_authorize_rejected(exc) from exc

        authorize_url = raw.get("authorize_url")
        if not isinstance(authorize_url, str) or not authorize_url:
            logger.warning("mail_oauth_authorize_missing_url")
            raise mail_unavailable()
        return MailOauthAuthorizeResponse(authorize_url=authorize_url)

    @staticmethod
    def _map_authorize_rejected(exc: MailRejected) -> AppError:
        """Маппинг отклонения authorize агрегатором (ADR-045 §3).

        Внешний 404 = Outlook-OAuth выключен на агрегаторе (нет `OUTLOOK_CLIENT_ID`/
        `_SECRET`) → единый 503 mail_not_configured (конфигурационное «возможность не
        настроена», не транзиентная недоступность). Прочие 4xx — неожиданны при валидном
        `crm_state`/ключе → 502 mail_unavailable.
        """
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            return mail_not_configured()
        logger.warning("mail_oauth_authorize_unexpected_status", status=exc.status_code)
        return mail_unavailable()

    # --- Reply (отправка через агрегатор) ----------------------------------

    async def reply(
        self,
        *,
        scope: MailScope,
        user_id: uuid.UUID | None,
        message_id: int,
        payload: MailReplyRequest,
    ) -> MailReplyResponse:
        """Ответ на письмо (ADR-044 §8). Гейт mail:view; письмо ∈ scope.

        Письмо берётся из БД CRM; threading (`In-Reply-To`/`References`) формирует CRM;
        SMTP-отправку делегирует агрегатору; факт отправки пишет в `mail_sent_messages`.
        Нормы (§8): body непустой ≤1 MiB; ≤100 адресов to+cc, e-mail regex; subject ≤998.
        """
        self._ensure_configured()
        message = await self._messages.get(message_id)
        if message is None:
            raise mail_message_not_found()
        account = await self._accounts.get(message.mail_account_id)
        if account is None:
            raise mail_message_not_found()
        if not scope.sees_all_teams and account.team_id not in scope.team_ids:
            # Анти-энумерация: чужое письмо неотличимо от несуществующего.
            raise mail_message_not_found()

        to_addrs, cc_addrs, subject, body = self._prepare_reply(message, payload)
        in_reply_to = message.message_id_header
        refs = self._build_references(message)

        send_payload: dict[str, object] = {
            "to": to_addrs,
            "cc": cc_addrs,
            "subject": subject,
            "body_text": body,
        }
        if in_reply_to is not None:
            send_payload["in_reply_to"] = in_reply_to
        if refs is not None:
            send_payload["refs"] = refs

        try:
            raw = await self._client.send_message(message.mail_account_id, send_payload)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except MailRejected as exc:
            raise self._map_reply_rejected(exc) from exc

        result = self._parse(MailReplyResponse, raw)
        await self._sent.create(
            mail_account_id=message.mail_account_id,
            user_id=user_id,
            to_addrs=", ".join(to_addrs),
            cc_addrs=", ".join(cc_addrs) if cc_addrs else None,
            subject=subject,
            body_text=body,
            in_reply_to=in_reply_to,
            refs_header=refs,
            smtp_message_id=result.smtp_message_id,
        )
        return result

    @staticmethod
    def _prepare_reply(
        message: MailMessageModel, payload: MailReplyRequest
    ) -> tuple[list[str], list[str], str, str]:
        """Применяет дефолты и нормы reply (ADR-044 §8); нарушение → 422 unprocessable."""
        body = payload.body
        if not body.strip():
            raise unprocessable("Тело ответа не может быть пустым")
        if len(body.encode("utf-8")) > MAX_REPLY_BODY_BYTES:
            raise unprocessable("Тело ответа превышает 1 MiB")

        to_addrs = payload.to if payload.to is not None else [message.from_addr]
        cc_addrs = payload.cc if payload.cc is not None else []
        # Явный пустой `to` (и пустой `cc`) обходит дефолт → письмо без единого
        # получателя. Отклоняем ДО вызова агрегатора, не полагаясь на его валидацию.
        if not to_addrs and not cc_addrs:
            raise unprocessable("Нужен хотя бы один получатель")
        try:
            validate_reply_addresses(to_addrs)
            validate_reply_addresses(cc_addrs)
        except MailReplyError as exc:
            raise unprocessable(str(exc)) from exc
        if len(to_addrs) + len(cc_addrs) > MAX_REPLY_RECIPIENTS:
            raise unprocessable(f"Слишком много адресов (>{MAX_REPLY_RECIPIENTS})")

        subject = payload.subject if payload.subject is not None else f"Re: {message.subject or ''}"
        if len(subject) > MAX_REPLY_SUBJECT_LEN:
            raise unprocessable(f"Тема превышает {MAX_REPLY_SUBJECT_LEN} символов")
        return to_addrs, cc_addrs, subject, body

    @staticmethod
    def _build_references(message: MailMessageModel) -> str | None:
        """`References` = существующие refs + `Message-ID` исходного (RFC threading)."""
        parts = [
            part
            for part in (message.refs_header, message.message_id_header)
            if part and part.strip()
        ]
        return " ".join(parts) if parts else None

    # --- Теги (глобальный каталог, чтение/запись из БД CRM) -----------------

    async def list_tags(self) -> MailTagsResponse:
        """Список глобальных тегов с правилами из БД CRM (ADR-044 §5)."""
        tags = await self._tags.list_tags()
        rules_by_tag = await self._tags.rules_for_tags(tag.id for tag in tags)
        return MailTagsResponse(
            tags=[self._to_tag_full(tag, rules_by_tag.get(tag.id, [])) for tag in tags]
        )

    async def create_tag(self, payload: MailTagCreateRequest) -> MailTagFull:
        """Создание тега (ADR-044 §5). Гейт mail:tags; занятое имя → 409 mail_conflict."""
        if await self._tags.exists_by_name(payload.name):
            raise mail_conflict()
        tag = await self._tags.create_tag(
            name=payload.name, color=payload.color, match_mode=payload.match_mode
        )
        return self._to_tag_full(tag, [])

    async def update_tag(self, tag_id: uuid.UUID, payload: MailTagUpdateRequest) -> MailTagFull:
        """Правка тега (ADR-044 §5). Гейт mail:tags; занятое имя → 409; нет тега → 404."""
        tag = await self._tags.get(tag_id)
        if tag is None:
            raise mail_tag_not_found()
        fields_set = payload.model_fields_set
        if "name" in fields_set and payload.name is not None and payload.name != tag.name:
            if await self._tags.exists_by_name(payload.name, exclude_id=tag_id):
                raise mail_conflict()
            tag.name = payload.name
        if "color" in fields_set and payload.color is not None:
            tag.color = payload.color
        if "match_mode" in fields_set and payload.match_mode is not None:
            tag.match_mode = payload.match_mode
        await self._session.flush()
        # `MailTag.updated_at` объявлен с `onupdate=func.now()` (SQL-выражение): после
        # UPDATE-flush атрибут экспайрится и его чтение в `_to_tag_full` потребовало бы
        # ленивого SELECT, падающего в async без greenlet (MissingGreenlet). Явный
        # await-refresh подгружает пересчитанное значение внутри greenlet-контекста.
        await self._session.refresh(tag, attribute_names=["updated_at"])
        rules_by_tag = await self._tags.rules_for_tags([tag_id])
        return self._to_tag_full(tag, rules_by_tag.get(tag_id, []))

    async def delete_tag(self, tag_id: uuid.UUID) -> None:
        """Удаление тега (ADR-044 §5, ADR-047 §1). Гейт mail:tags; нет тега → 404.

        Удалить можно ЛЮБОЙ тег: признак «встроенный» упразднён, ветки 409 больше нет.
        """
        if await self._tags.get(tag_id) is None:
            raise mail_tag_not_found()
        await self._tags.delete_tag(tag_id)

    async def create_tag_rule(
        self, tag_id: uuid.UUID, payload: MailTagRuleCreateRequest
    ) -> MailTagRule:
        """Добавление правила тегу (ADR-044 §5). Гейт mail:tags; нет тега → 404."""
        tag = await self._tags.get(tag_id)
        if tag is None:
            raise mail_tag_not_found()
        rule = await self._tags.create_rule(
            tag_id=tag_id, rule_type=payload.type, pattern=payload.pattern
        )
        return self._to_tag_rule(rule)

    async def delete_tag_rule(self, tag_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        """Удаление правила (ADR-044 §5). Гейт mail:tags; нет тега/правила → 404."""
        rule = await self._tags.get_rule(tag_id, rule_id)
        if rule is None:
            raise mail_tag_not_found()
        await self._tags.delete_rule(tag_id, rule_id)

    async def apply_tag_to_existing(self, tag_id: uuid.UUID) -> MailTagApplyResponse:
        """Применить правила тега ко всем письмам (ADR-044 §5). Гейт mail:tags; нет тега → 404."""
        tag = await self._tags.get(tag_id)
        if tag is None:
            raise mail_tag_not_found()
        applied = await self._tags.apply_tag_to_existing(tag_id)
        return MailTagApplyResponse(applied_count=applied)

    # --- Scope / общие хелперы ---------------------------------------------

    async def _ensure_team_writable(self, scope: MailScope, team_id: uuid.UUID | None) -> None:
        """Авторизация привязки создаваемого ящика к команде (ADR-044 §4).

        `team_id=null` (unassigned) — только admin-уровень. Не-admin обязан указать
        `team_id ∈ scope.team_ids` (иначе 403). Admin с конкретным `team_id` —
        существование команды валидируется (404 team_not_found).
        """
        if team_id is None:
            if not scope.sees_all_teams:
                raise forbidden()
            return
        if not scope.sees_all_teams:
            if team_id not in scope.team_ids:
                raise forbidden()
            return
        await self._ensure_team_exists(team_id)

    async def _ensure_team_exists(self, team_id: uuid.UUID) -> None:
        """Команда существует в CRM, иначе 404 team_not_found (ADR-044 §4)."""
        if await self._teams.get(team_id) is None:
            raise team_not_found()

    async def _load_account_in_scope(self, scope: MailScope, mailbox_id: int) -> MailAccount:
        """Ящик из каталога с проверкой scope (ADR-044 §7): нет → 404, вне scope → 403."""
        account = await self._accounts.get(mailbox_id)
        if account is None:
            raise mail_mailbox_not_found()
        if not scope.sees_all_teams and account.team_id not in scope.team_ids:
            raise forbidden()
        return account

    @staticmethod
    def _to_mailbox(account: MailAccount) -> MailMailbox:
        """Проекция строки каталога в схему `MailMailbox` (ADR-044 §4, ADR-047 §3)."""
        return MailMailbox(
            id=account.id,
            email=account.email,
            number=account.number,
            app_name=account.app_name,
            display_name=account.display_name,
            team_id=account.team_id,
            is_active=account.is_active,
            last_synced_at=account.last_synced_at,
            last_sync_error=account.last_sync_error,
            consecutive_failures=account.consecutive_failures,
        )

    @staticmethod
    def _to_tag_rule(rule: MailTagRuleModel) -> MailTagRule:
        """Проекция правила тега в схему."""
        return MailTagRule(
            id=rule.id,
            type=cast(MailTagRuleType, rule.type),
            pattern=rule.pattern,
            created_at=rule.created_at,
        )

    def _to_tag_full(self, tag: MailTagModel, rules: list[MailTagRuleModel]) -> MailTagFull:
        """Проекция тега с правилами в схему `MailTagFull`."""
        return MailTagFull(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            match_mode=cast(MailTagMatchMode, tag.match_mode),
            rules=[self._to_tag_rule(rule) for rule in rules],
            created_at=tag.created_at,
            updated_at=tag.updated_at,
        )

    async def _compensate_orphan_mailbox(self, mailbox_id: int) -> None:
        """Best-effort удаление ящика в агрегаторе при провале вставки каталога (§4).

        Провал самого DELETE не должен ронять ответ пользователю (исходная ошибка
        важнее) — логируется и подавляется.
        """
        try:
            await self._client.delete_mailbox(mailbox_id)
        except (MailUnavailable, MailRejected) as exc:
            logger.warning(
                "mail_create_orphan_cleanup_failed",
                mailbox_id=mailbox_id,
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _extract_new_id(raw: dict[str, object]) -> int:
        """Достаёт присвоенный агрегатором `id` из ответа create; иначе 502 (регресс контракта)."""
        value = raw.get("id")
        if isinstance(value, bool) or not isinstance(value, int):
            logger.warning("mail_create_missing_id")
            raise mail_unavailable()
        return value

    @staticmethod
    def _map_rejected(
        exc: MailRejected,
        *,
        not_found: Callable[[], AppError] | None = None,
        conflict: bool = False,
    ) -> AppError:
        """Постатусный маппинг отклонения write-запроса ящика в код CRM (ADR-044 §4).

        400 → validation_error; 404 → `not_found()` (ящик); 409 → mail_conflict (если
        `conflict`, напр. email занят); 422 → unprocessable (IMAP/SMTP); иное → 502.
        """
        code = exc.status_code
        if code == status.HTTP_400_BAD_REQUEST:
            return validation_error("Агрегатор отклонил запрос")
        if code == status.HTTP_404_NOT_FOUND and not_found is not None:
            return not_found()
        if code == status.HTTP_409_CONFLICT and conflict:
            return mail_conflict()
        if code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            return unprocessable("Агрегатор отклонил запрос")
        logger.warning("mail_write_unexpected_aggregator_status", status=code)
        return mail_unavailable()

    @staticmethod
    def _map_reply_rejected(exc: MailRejected) -> AppError:
        """Постатусный маппинг отклонения reply (ADR-044 §8): 400/404/409/422/иное."""
        code = exc.status_code
        if code == status.HTTP_400_BAD_REQUEST:
            return validation_error("Агрегатор отклонил ответ")
        if code == status.HTTP_404_NOT_FOUND:
            return mail_message_not_found()
        if code == status.HTTP_409_CONFLICT:
            return mail_conflict()
        if code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            return unprocessable("Агрегатор отклонил ответ")
        logger.warning("mail_reply_unexpected_aggregator_status", status=code)
        return mail_unavailable()

    def _ensure_configured(self) -> None:
        """Гейт: операции через агрегатор доступны только при заданном MAIL_API_KEY (503).

        Применяется к write-операциям ящика и reply (нужен агрегатор). Чтение ленты/
        ящиков/тегов из БД CRM гейтом не покрывается (работает независимо от агрегатора).
        """
        if not self._settings.mail_enabled:
            raise mail_not_configured()

    @staticmethod
    def _parse(model: type[_ModelT], raw: dict[str, object]) -> _ModelT:
        """Нормализует ответ агрегатора в схему; несовместимое тело → 502."""
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            logger.warning("mail_response_schema_mismatch", model=model.__name__)
            raise mail_unavailable() from exc


__all__ = ["MailService"]
