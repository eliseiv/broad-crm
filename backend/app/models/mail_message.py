"""Модель таблицы `mail_messages` — система-запись писем (ADR-044 §2).

`id BIGSERIAL` — ключ пагинации (при миграции preserve id из агрегатора). Идемпотентность
push — `UNIQUE (mail_account_id, uidvalidity, uid)` = `uq_mail_messages_account_uidv_uid`.
**Порядок ленты — по `(internal_date DESC, id DESC)`, НЕ по `id`** (MAJOR-8): `id`
отражает порядок прихода push'а, а не дату письма; keyset-курсор компаундный по паре
`(internal_date, id)` (MINOR-2). `notified_at` — high-water Telegram-диспетчера (S4):
NULL = уведомление ещё не разослано; новые письма приходят с `notified_at IS NULL`.
Вложений НЕТ (решение владельца).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailMessage(Base):
    """Письмо (durable system of record, ADR-044 §2)."""

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint(
            "mail_account_id",
            "uidvalidity",
            "uid",
            name="uq_mail_messages_account_uidv_uid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mail_account_id: Mapped[int] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="CASCADE", name="fk_mail_messages_account_id"),
        nullable=False,
    )
    uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_addrs: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    cc_addrs: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    body_present: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    refs_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    # high-water Telegram-диспетчера (S4): NULL = уведомление ещё не разослано.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Лента по ящику: (mail_account_id, internal_date DESC, id DESC).
Index(
    "ix_mail_messages_account_feed",
    MailMessage.mail_account_id,
    MailMessage.internal_date.desc(),
    MailMessage.id.desc(),
)
# Глобальная лента admin-scope: (internal_date DESC, id DESC).
Index(
    "ix_mail_messages_feed",
    MailMessage.internal_date.desc(),
    MailMessage.id.desc(),
)
# Очередь диспетчера (S4): id-порядок ок — это очередь обработки, не лента.
Index(
    "ix_mail_messages_notify",
    MailMessage.id,
    postgresql_where=text("notified_at IS NULL"),
)
