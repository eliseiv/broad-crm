"""Аудитория и fan-out рассылки через Telegram ИИ-бота (ADR-076 §3)."""

from __future__ import annotations

from app.config import Settings
from app.errors import knowledge_bot_not_configured, unprocessable
from app.infra.knowledge_bot_telegram import (
    KnowledgeBotClient,
    TelegramApiError,
    TelegramForbiddenError,
)
from app.logging import get_logger
from app.repositories.knowledge_bot_link_repository import KnowledgeBotLinkRepository
from app.repositories.role_repository import RoleRepository
from app.schemas.broadcast import (
    BroadcastAudienceResponse,
    BroadcastAudienceRole,
    BroadcastCreateRequest,
    BroadcastSendResponse,
)

logger = get_logger(__name__)

_TEXT_MAX_LEN = 4096


class BroadcastService:
    """GET audience + POST fan-out. Токен пуст → 503; частичный успех → 200."""

    def __init__(
        self,
        *,
        links: KnowledgeBotLinkRepository,
        roles: RoleRepository,
        bot: KnowledgeBotClient,
        settings: Settings,
    ) -> None:
        self._links = links
        self._roles = roles
        self._bot = bot
        self._settings = settings

    async def get_audience(self) -> BroadcastAudienceResponse:
        """Роли + счётчики запуска бота (только активные несистемные)."""
        by_role = await self._links.audience_by_role()
        all_started, all_not_started = await self._links.audience_totals()
        return BroadcastAudienceResponse(
            roles=[
                BroadcastAudienceRole(
                    id=row.role_id,
                    name=row.name,
                    started_count=row.started_count,
                    not_started_count=row.not_started_count,
                )
                for row in by_role
            ],
            all_started_count=all_started,
            all_not_started_count=all_not_started,
        )

    async def send(self, payload: BroadcastCreateRequest) -> BroadcastSendResponse:
        """Валидация → адресаты → последовательный fan-out. Пустой токен → 503."""
        if not self._settings.knowledge_bot_enabled:
            raise knowledge_bot_not_configured()

        text = payload.text.strip()
        if not text or len(text) > _TEXT_MAX_LEN:
            raise unprocessable(
                "Текст рассылки должен быть от 1 до 4096 символов",
                details=[{"field": "text", "message": "Недопустимая длина текста"}],
            )

        if payload.all and payload.role_ids:
            raise unprocessable(
                "Укажите либо «всем», либо список ролей",
                details=[{"field": "role_ids", "message": "Нельзя сочетать all и role_ids"}],
            )
        if not payload.all and not payload.role_ids:
            raise unprocessable(
                "Укажите либо «всем», либо список ролей",
                details=[{"field": "role_ids", "message": "Пустой список ролей"}],
            )

        role_ids = set(payload.role_ids)
        if role_ids:
            existing = await self._roles.existing_ids(role_ids)
            if existing != role_ids:
                raise unprocessable(
                    "Роль не найдена",
                    details=[{"field": "role_ids", "message": "Роль не существует"}],
                )

        recipients, skipped = await self._links.recipients_for_roles(
            all_users=payload.all,
            role_ids=role_ids,
        )

        sent = 0
        failed = 0
        for recipient in recipients:
            try:
                await self._bot.send_message(recipient.telegram_user_id, text)
                sent += 1
            except TelegramForbiddenError:
                await self._links.mark_dead(recipient.telegram_user_id)
                failed += 1
            except TelegramApiError:
                failed += 1

        logger.info(
            "broadcast_fanout_done",
            sent=sent,
            failed=failed,
            skipped_not_started=skipped,
        )
        return BroadcastSendResponse(sent=sent, failed=failed, skipped_not_started=skipped)
