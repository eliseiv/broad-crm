r"""users: метка первого входа для тристатуса (ADR-028)

Revision ID: 0015_user_first_login
Revises: 0014_proxies_alert_grace
Create Date: 2026-07-08

Колонка `users.first_login_at timestamptz NULL` (ADR-028,
03-data-model.md#миграция-0015_users_first_login-концепт). `NULL` = пользователь
ещё ни разу не входил; непустой timestamptz = момент ПЕРВОГО успешного входа.
Источник производного `UserListItem.status` («pending»/«active»/«inactive»).
Метка проставляется приложением идемпотентно (`if first_login_at is None`) в
парольной ветке `POST /api/auth/login` и в `POST /api/auth/set-password`.
Backfill не требуется (`NULL`; на проде БД-пользователей 0). `downgrade()` снимает колонку.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_user_first_login"
down_revision: str | None = "0014_proxies_alert_grace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("first_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "first_login_at")
