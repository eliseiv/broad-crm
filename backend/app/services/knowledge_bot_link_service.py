"""Резолв CRM-пользователя по Telegram id + upsert линка ИИ-бота (ADR-076 §2).

Единый порядок шагов для `POST /api/external/knowledge-bot/link` и
`GET /api/external/documents/user-access/{id}`: knowledge → sms → mail → bootstrap
по нику. Системный якорь исключён (`UserRepository` с `NOT is_system`).
"""

from __future__ import annotations

from app.domain.permissions import full_catalog_permissions, permissions_subset
from app.domain.telegram import normalize_telegram
from app.errors import user_not_linked
from app.models.user import User
from app.repositories.knowledge_bot_link_repository import KnowledgeBotLinkRepository
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository
from app.repositories.user_repository import UserRepository
from app.schemas.documents import ExternalUserAccessResponse


class KnowledgeBotLinkService:
    """Резолв + upsert линка ИИ-бота; сборка `ExternalUserAccessResponse`."""

    def __init__(
        self,
        *,
        users: UserRepository,
        knowledge_links: KnowledgeBotLinkRepository,
        sms_links: SmsTelegramLinkRepository,
        mail_links: MailTelegramLinkRepository,
    ) -> None:
        self._users = users
        self._knowledge = knowledge_links
        self._sms = sms_links
        self._mail = mail_links

    async def resolve_user(self, telegram_user_id: int, username: str | None) -> User:
        """Резолв активного несистемного пользователя или 404 `user_not_linked`."""
        user = await self._lookup_user(telegram_user_id, username)
        if user is None or not user.is_active:
            raise user_not_linked()
        return user

    async def link(
        self, *, telegram_user_id: int, username: str | None
    ) -> ExternalUserAccessResponse:
        """Резолв → upsert линка → тот же ответ, что у user-access."""
        user = await self.resolve_user(telegram_user_id, username)
        stored_username = self._normalize_optional_username(username)
        await self._knowledge.upsert(
            telegram_user_id=telegram_user_id,
            user_id=user.id,
            username=stored_username,
        )
        return self.to_access_response(user)

    async def user_access(
        self, telegram_user_id: int, username: str | None
    ) -> ExternalUserAccessResponse:
        """Резолв без записи линка (GET user-access)."""
        user = await self.resolve_user(telegram_user_id, username)
        return self.to_access_response(user)

    def to_access_response(self, user: User) -> ExternalUserAccessResponse:
        """`ExternalUserAccessResponse`: `sees_all_documents` = полный каталог роли."""
        permissions = dict(user.role.permissions or {})
        return ExternalUserAccessResponse(
            user_id=user.id,
            role_id=user.role_id,
            role_name=user.role.name,
            sees_all_documents=permissions_subset(full_catalog_permissions(), permissions),
        )

    async def _lookup_user(self, telegram_user_id: int, username: str | None) -> User | None:
        """Порядок ADR-076 §2. Неактивный/системный на шаге 1–3 → как «нет» (шаг дальше)."""
        knowledge = await self._knowledge.get_active_by_telegram_user_id(telegram_user_id)
        if knowledge is not None:
            return await self._users.get_by_id(knowledge.user_id)

        sms = await self._sms.get_active_by_telegram_user_id(telegram_user_id)
        if sms is not None:
            return await self._users.get_by_id(sms.user_id)

        mail = await self._mail.get_by_telegram_user_id(telegram_user_id)
        if mail is not None and mail.dead_at is None and mail.user_id is not None:
            return await self._users.get_by_id(mail.user_id)

        normalized = self._normalize_optional_username(username)
        if normalized is None:
            return None
        return await self._users.get_by_telegram(normalized)

    @staticmethod
    def _normalize_optional_username(username: str | None) -> str | None:
        """Нормализация ника (без `@`, lower-case) или None, если пусто."""
        if username is None:
            return None
        stripped = username.strip()
        if not stripped:
            return None
        return normalize_telegram(stripped)
