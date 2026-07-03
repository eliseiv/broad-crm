"""Сервис модуля «Почты» — тонкая обёртка над MailClient (04-api.md#mail).

Read-through-прокси без хранения (ADR-012, modules/mail): гейт `mail_enabled`,
валидация входа, вызов внешнего клиента и маппинг его исключений в коды CRM
(04-api.md#mail). Состояние не хранится. Секрет `MAIL_API_KEY` в сервис не попадает —
он подставляется в заголовок только внутри клиента (05-security.md).
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import ValidationError

from app.config import Settings
from app.errors import (
    mail_message_not_found,
    mail_not_configured,
    mail_unavailable,
    unprocessable,
    validation_error,
)
from app.infra.mail_client import (
    MailClient,
    MailMessageNotFound,
    MailRejected,
    MailUnavailable,
)
from app.logging import get_logger
from app.schemas.mail import MailListResponse, MailReplyRequest, MailReplyResponse

logger = get_logger(__name__)

_LIMIT_MIN = 1
_LIMIT_MAX = 200

_ResponseT = TypeVar("_ResponseT", MailListResponse, MailReplyResponse)


class MailService:
    """Проксирует запросы ленты/ответа во внешний почтовый сервис."""

    def __init__(self, client: MailClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def list_messages(self, since_id: int | None, limit: int) -> MailListResponse:
        """Лента писем (keyset вперёд). Гейт mail_enabled, затем валидация limit."""
        self._ensure_configured()
        self._validate_limit(limit)
        try:
            raw = await self._client.list_messages(since_id=since_id, limit=limit)
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        except (MailMessageNotFound, MailRejected) as exc:
            # Неожиданный не-2xx на проксируемый list (limit уже провалидирован) —
            # трактуем как недоступность внешнего сервиса (04-api.md#mail).
            logger.warning("mail_list_unexpected_external_status")
            raise mail_unavailable() from exc
        return self._parse(MailListResponse, raw)

    async def reply(self, message_id: int, payload: MailReplyRequest) -> MailReplyResponse:
        """Ответ на письмо. Гейт mail_enabled, проверка непустого body, проксирование."""
        self._ensure_configured()
        self._validate_reply(payload)
        body = payload.model_dump(exclude_none=True)
        try:
            raw = await self._client.reply(message_id=message_id, payload=body)
        except MailMessageNotFound as exc:
            raise mail_message_not_found() from exc
        except MailRejected as exc:
            raise unprocessable("Внешний сервис отклонил ответ") from exc
        except MailUnavailable as exc:
            raise mail_unavailable() from exc
        return self._parse(MailReplyResponse, raw)

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
    def _validate_reply(payload: MailReplyRequest) -> None:
        """Непустой body (иначе 422 unprocessable, 04-api.md#mail)."""
        if not payload.body.strip():
            raise unprocessable("Тело ответа не может быть пустым")

    @staticmethod
    def _parse(model: type[_ResponseT], raw: dict[str, object]) -> _ResponseT:
        """Нормализует ответ внешнего сервиса в схему; несовместимое тело → 502."""
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            logger.warning("mail_response_schema_mismatch", model=model.__name__)
            raise mail_unavailable() from exc


__all__ = ["MailService"]
