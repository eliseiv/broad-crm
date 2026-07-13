r"""create mail_message_reads — ЛИЧНАЯ прочитанность писем (ADR-050 §2.1)

Revision ID: 0025_mail_message_reads
Revises: 0024_mail_accounts_num_app_name
Create Date: 2026-07-13

Таблица связи «пользователь × письмо» (03-data-model.md#таблица-mail_message_reads):
**существование строки = «прочитано» ЭТИМ пользователем**, отсутствие = «не прочитано»
(отдельного булева поля нет — оно было бы избыточным). Прочитанность ЛИЧНАЯ: признак
принадлежит ПАРЕ (пользователь, письмо), а не письму — колонка `mail_messages.is_read`
физически не могла бы этого выразить (прочтение коллегой гасило бы индикатор всем).

- **PK `(user_id, message_id)`** (`pk_mail_message_reads`) обслуживает оба горячих пути:
  батч-лукап `WHERE user_id = :uid AND message_id = ANY(:page_ids)` (поле `is_unread` для
  страницы ленты) и анти-джойн `NOT EXISTS (…)` (фильтр `unread=true`). Отдельный индекс
  по `(user_id)` не нужен — PK ведёт с `user_id`.
- **`ix_mail_message_reads_message_id` ОБЯЗАТЕЛЕН** (ADR-050 §2.1): PK ведёт с `user_id`,
  поэтому поиск по `message_id` его не использует, а `ON DELETE CASCADE` со стороны
  `mail_messages` (каскад `mail_accounts` → `mail_messages` → `mail_message_reads` при
  удалении ящика) без него выполнял бы seq scan на каждое удаляемое письмо.
- **Backfill не требуется:** пустая таблица = «все письма непрочитаны для всех» —
  корректное начальное состояние.

Идентификатор ревизии — `0025_mail_message_reads` (23 символа), укладывается в предел
`alembic_version.version_num varchar(32)` (укорачивать не требуется, ADR-047 §3.5).
`downgrade()` — рабочий: DROP таблицы (миграция чисто аддитивная).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_mail_message_reads"
down_revision: str | None = "0024_mail_accounts_num_app_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_message_reads",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "message_id", name="pk_mail_message_reads"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mail_message_reads_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["mail_messages.id"],
            name="fk_mail_message_reads_message_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mail_message_reads_message_id",
        "mail_message_reads",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mail_message_reads_message_id", table_name="mail_message_reads")
    op.drop_table("mail_message_reads")
