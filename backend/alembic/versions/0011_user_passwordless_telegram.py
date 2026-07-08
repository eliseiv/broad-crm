r"""passwordless users + email→telegram (ADR-025)

Revision ID: 0011_user_passwordless_telegram
Revises: 0010_add_user_email
Create Date: 2026-07-08

Беспарольные пользователи + вход по Логину/Телеграму (ADR-025,
03-data-model.md#миграция-0011_user_passwordless_telegram-концепт). Два независимых
действия над `users`:
  1. Заменить контакт `email` → `telegram` (rename колонки + swap частичного
     уникального индекса `uq_users_email` → `uq_users_telegram WHERE telegram IS NOT NULL`).
  2. Снять `NOT NULL` с `password_hash` (`NULL` = беспарольный пользователь).
На проде пользователей 0 → замена колонки безопасна (данных нет). `downgrade()`
обратимо: `NULL`-хэши заполняются невалидным сентинелом `!` перед восстановлением
`NOT NULL` (вход по нему невозможен).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_passwordless_telegram"
down_revision: str | None = "0010_add_user_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) email → telegram (rename + swap частичного уникального индекса).
    op.drop_index("uq_users_email", table_name="users")
    op.alter_column("users", "email", new_column_name="telegram")
    op.create_index(
        "uq_users_telegram",
        "users",
        ["telegram"],
        unique=True,
        postgresql_where=sa.text("telegram IS NOT NULL"),
    )

    # 2) Снять NOT NULL с password_hash (беспарольные пользователи).
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # 2') Восстановить NOT NULL: заполнить NULL-хэши невалидным сентинелом `!`
    # (bcrypt-хэшем не является → вход по нему невозможен).
    op.execute("UPDATE users SET password_hash = '!' WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)

    # 1') telegram → email (swap индекса + rename обратно).
    op.drop_index("uq_users_telegram", table_name="users")
    op.alter_column("users", "telegram", new_column_name="email")
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
