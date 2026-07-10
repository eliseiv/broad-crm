"""Фоновый Telegram-диспетчер почты (ADR-044 §6, S4). Три прохода + reconcile.

`asyncio`-задача в lifespan (по образцу `SmsDeliveryMonitorService`), без Redis/брокера.
`run()` = `while True: poll_once(); sleep(interval)`; `CancelledError` → лог+re-raise;
ошибка итерации логируется, цикл живёт. `poll_once` выполняет:

- **Проход A** — новые письма (`notified_at IS NULL`): резолв получателей (участники
  команды ящика + admin-уровень с живым линком, минус opt-out) → reserve/send/mark →
  `notified_at=now()`. Push-бот команды (fan-out админам, fire-and-forget, TD-043).
- **Проход B** — recovery транзиентных сбоев (`status IN (pending,failed) AND attempts
  < max`): повтор доставки. Гарантирует доставку — проход A лишь помечает письмо
  обработанным, факт доставки добирает проход B (устраняет регрессию MAJOR-1).
- **Проход C** — mailbox-down алерты (`is_active=false AND down_alert_sent_at IS NULL`):
  guarded-штамп «ровно один на переход» → алерт участникам команды (fire-and-forget).
- **Reconcile** orphan-линков раз в N итераций (safety-net §6).

Стартует только при `MAIL_DISPATCH_ENABLED=true` (cut-over: агрегатор глушится ДО
старта, иначе двойная доставка). Диспетчер синхронно шлёт в цикле (TD-044).
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import MailPushBot, Settings
from app.domain.mail_notify import (
    build_body_preview,
    format_mailbox_down,
    format_notification,
)
from app.domain.permissions import full_catalog_permissions, permissions_subset
from app.infra.mail_telegram import (
    MailBotClient,
    MailTelegramApiError,
    MailTelegramForbiddenError,
    view_message_markup,
)
from app.logging import get_logger
from app.repositories.mail_dispatch_repository import (
    DispatchMessage,
    MailDispatchRepository,
)
from app.repositories.mail_notification_repository import (
    MailNotificationRepository,
    PendingNotification,
)
from app.repositories.mail_telegram_link_repository import (
    MailRecipient,
    MailTelegramLinkRepository,
)

logger = get_logger(__name__)


class MailDispatcherService:
    """Периодическая durable-доставка Telegram-уведомлений почты (проходы A/B/C)."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._interval_sec = settings.mail_dispatch_interval_sec
        self._batch = settings.mail_dispatch_batch
        self._max_attempts = settings.mail_tg_max_attempts
        self._notify_all = settings.mail_tg_notify_all_messages
        self._reconcile_every = max(1, settings.mail_dispatch_reconcile_every)
        self._bot = MailBotClient(settings.mail_bot_token, settings.mail_bot_proxy_url)
        self._push_bots: dict[str, MailPushBot] = {
            str(b.team_id): b for b in settings.mail_push_bots
        }
        self._iteration = 0

    async def run(self) -> None:
        """Бесконечный цикл: poll → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("mail_dispatcher_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("mail_dispatcher_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("mail_dispatcher_stopped")
            raise

    async def poll_once(self) -> None:
        """Одна итерация: проходы A → B → C (+ reconnect orphan'ов раз в N итераций)."""
        if not self._bot.is_configured:
            logger.warning("mail_dispatcher_bot_not_configured")
            return
        self._iteration += 1
        async with self._sessionmaker() as session:
            sees_all = await self._compute_sees_all(session)
            team_cache: dict[str, list[MailRecipient]] = {}
            await self._pass_new_messages(session, sees_all, team_cache)
            await self._pass_recovery(session)
            await self._pass_mailbox_down(session, sees_all, team_cache)
            if self._iteration % self._reconcile_every == 0:
                await self._reconcile_orphans(session)

    # --- Резолв получателей ---------------------------------------------------

    async def _compute_sees_all(self, session: AsyncSession) -> list[MailRecipient]:
        """Получатели admin-уровня (полный каталог прав) с живым линком, минус opt-out."""
        links = MailTelegramLinkRepository(session)
        candidates = await links.sees_all_candidates()
        await session.commit()  # закрыть autobegun read-tx
        full = full_catalog_permissions()
        return [
            MailRecipient(user_id=c.user_id, telegram_user_id=c.telegram_user_id)
            for c in candidates
            if permissions_subset(full, c.permissions)
        ]

    async def _resolve_recipients(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID | None,
        sees_all: list[MailRecipient],
        team_cache: dict[str, list[MailRecipient]],
    ) -> list[MailRecipient]:
        """Получатели = участники команды ящика ∪ admin-уровень; дедуп по chat_id."""
        result: dict[int, MailRecipient] = {}
        if team_id is not None:
            key = str(team_id)
            if key not in team_cache:
                links = MailTelegramLinkRepository(session)
                team_cache[key] = await links.team_recipients(team_id)
            for r in team_cache[key]:
                result[r.telegram_user_id] = r
        for r in sees_all:
            result[r.telegram_user_id] = r
        return list(result.values())

    # --- Проход A: новые письма ----------------------------------------------

    async def _pass_new_messages(
        self,
        session: AsyncSession,
        sees_all: list[MailRecipient],
        team_cache: dict[str, list[MailRecipient]],
    ) -> None:
        dispatch = MailDispatchRepository(session)
        message_ids = await dispatch.unnotified_message_ids(self._batch)
        await session.commit()  # закрыть autobegun read-tx
        for message_id in message_ids:
            await self._process_new_message(session, message_id, sees_all, team_cache)

    async def _process_new_message(
        self,
        session: AsyncSession,
        message_id: int,
        sees_all: list[MailRecipient],
        team_cache: dict[str, list[MailRecipient]],
    ) -> None:
        dispatch = MailDispatchRepository(session)
        message = await dispatch.load_dispatch_message(message_id)
        if message is None:
            return
        tag_names = await dispatch.tag_names_for_message(message_id)
        # Опция «только по тегам»: письмо без тегов не рассылаем, но помечаем обработанным.
        if not self._notify_all and not tag_names:
            await session.commit()
            async with session.begin():
                await dispatch.mark_notified(message_id)
            return
        recipients = await self._resolve_recipients(
            session, team_id=message.team_id, sees_all=sees_all, team_cache=team_cache
        )
        await session.commit()  # закрыть autobegun read-tx перед write-транзакциями

        text = self._notification_text(message, tag_names)
        for recipient in recipients:
            await self._deliver_new(session, message_id, recipient.telegram_user_id, text)

        async with session.begin():
            await dispatch.mark_notified(message_id)

        await self._push_fanout(message, text)

    def _notification_text(self, message: DispatchMessage, tag_names: list[str]) -> str:
        preview = build_body_preview(body_text=message.body_text, body_html=message.body_html)
        return format_notification(
            acc_label=message.acc_label,
            from_label=message.from_name or message.from_addr,
            tag_names=tag_names,
            subject=message.subject,
            body_preview=preview,
        )

    async def _deliver_new(
        self, session: AsyncSession, message_id: int, telegram_user_id: int, text: str
    ) -> None:
        """Reserve → send → mark. Транзиентный сбой → 'failed' (проход B добьёт)."""
        notifications = MailNotificationRepository(session)
        links = MailTelegramLinkRepository(session)
        async with session.begin():
            notification_id = await notifications.try_reserve(
                message_id=message_id, telegram_user_id=telegram_user_id
            )
        if notification_id is None:
            return  # уже заведено/доставлено (идемпотентность)
        try:
            await self._bot.send_message(
                telegram_user_id,
                text,
                parse_mode="HTML",
                reply_markup=view_message_markup(message_id),
            )
        except MailTelegramForbiddenError as exc:
            async with session.begin():
                await notifications.mark_dead(notification_id, str(exc))
                await links.mark_dead(telegram_user_id)
            logger.warning("mail_notify_dead", telegram_user_id=telegram_user_id)
            return
        except MailTelegramApiError as exc:
            async with session.begin():
                await notifications.mark_failed(notification_id, str(exc))
            logger.warning("mail_notify_failed", telegram_user_id=telegram_user_id)
            return
        async with session.begin():
            await notifications.mark_sent(notification_id)

    async def _push_fanout(self, message: DispatchMessage, text: str) -> None:
        """Push-бот команды (§6 шаг 3): fire-and-forget админам (без трекинга, TD-043)."""
        if message.team_id is None:
            return
        bot_config = self._push_bots.get(str(message.team_id))
        if bot_config is None:
            return
        admin_ids = self._settings.mail_admin_telegram_ids_list
        if not admin_ids:
            return
        bot = MailBotClient(bot_config.token, self._settings.mail_bot_proxy_url)
        markup = view_message_markup(message.id) if bot_config.webhook_secret else None
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
            except MailTelegramApiError:
                logger.info("mail_push_fanout_failed", bot=bot_config.name)

    # --- Проход B: recovery ---------------------------------------------------

    async def _pass_recovery(self, session: AsyncSession) -> None:
        notifications = MailNotificationRepository(session)
        pending = await notifications.pending_for_retry(
            max_attempts=self._max_attempts, limit=self._batch
        )
        await session.commit()  # закрыть autobegun read-tx
        for row in pending:
            await self._retry_one(session, row)

    async def _retry_one(self, session: AsyncSession, row: PendingNotification) -> None:
        notifications = MailNotificationRepository(session)
        links = MailTelegramLinkRepository(session)
        dispatch = MailDispatchRepository(session)
        notification_id = row.id
        message_id = row.message_id
        telegram_user_id = row.telegram_user_id
        attempts = row.attempts

        message = await dispatch.load_dispatch_message(message_id)
        if message is None:
            await session.commit()
            async with session.begin():
                await notifications.mark_dead(notification_id, "Письмо не найдено")
            return
        tag_names = await dispatch.tag_names_for_message(message_id)
        await session.commit()  # закрыть autobegun read-tx

        text = self._notification_text(message, tag_names)
        try:
            await self._bot.send_message(
                telegram_user_id,
                text,
                parse_mode="HTML",
                reply_markup=view_message_markup(message_id),
            )
        except MailTelegramForbiddenError as exc:
            async with session.begin():
                await notifications.mark_dead(notification_id, str(exc))
                await links.mark_dead(telegram_user_id)
            return
        except MailTelegramApiError as exc:
            async with session.begin():
                if attempts + 1 >= self._max_attempts:
                    await notifications.mark_dead(notification_id, str(exc))
                else:
                    await notifications.mark_failed(notification_id, str(exc))
            return
        async with session.begin():
            await notifications.mark_sent(notification_id)

    # --- Проход C: mailbox-down алерты ----------------------------------------

    async def _pass_mailbox_down(
        self,
        session: AsyncSession,
        sees_all: list[MailRecipient],
        team_cache: dict[str, list[MailRecipient]],
    ) -> None:
        dispatch = MailDispatchRepository(session)
        downs = await dispatch.down_mailboxes(self._batch)
        await session.commit()  # закрыть autobegun read-tx
        for down in downs:
            recipients = await self._resolve_recipients(
                session, team_id=down.team_id, sees_all=sees_all, team_cache=team_cache
            )
            await session.commit()
            # Guarded-штамп ДО отправки: «ровно один на переход» (fire-and-forget, TD-043).
            async with session.begin():
                won = await dispatch.try_stamp_down_alert(down.id)
            if not won:
                continue
            text = format_mailbox_down(
                acc_label=down.acc_label, last_sync_error=down.last_sync_error
            )
            await self._deliver_alert(session, recipients, text)

    async def _deliver_alert(
        self, session: AsyncSession, recipients: list[MailRecipient], text: str
    ) -> None:
        links = MailTelegramLinkRepository(session)
        for recipient in recipients:
            try:
                await self._bot.send_message(recipient.telegram_user_id, text, parse_mode="HTML")
            except MailTelegramForbiddenError:
                async with session.begin():
                    await links.mark_dead(recipient.telegram_user_id)
            except MailTelegramApiError:
                logger.info("mail_mailbox_alert_dropped")

    # --- Reconcile orphan-линков ----------------------------------------------

    async def _reconcile_orphans(self, session: AsyncSession) -> None:
        links = MailTelegramLinkRepository(session)
        async with session.begin():
            bound = await links.reconcile_orphans()
        if bound:
            logger.info("mail_orphans_reconciled", bound=bound)


__all__ = ["MailDispatcherService"]
