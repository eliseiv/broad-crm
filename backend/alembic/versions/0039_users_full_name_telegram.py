r"""users += last_name/first_name/middle_name (ФИО, ADR-079 §7)

Revision ID: 0039_users_full_name_telegram
Revises: 0038_user_roles_m2m
Create Date: 2026-08-19

Добавляет три **nullable**-колонки ФИО (03-data-model.md#миграция-0039_users_full_name_telegram-концепт-adr-079)
и переносит прежний логин в «Имя» у несистемных строк.

- Колонки nullable: у существующих пользователей фамилии нет, у якоря ФИО нет навсегда.
  Обязательность `last_name`/`first_name` — **на уровне API** (`422`), не в схеме.
- Backfill только `is_system = false`: `username` якоря (`superadmin@system`) — служебное
  значение, показывать его как имя нельзя.
- **Колонка `telegram` и `uq_users_telegram` НЕ меняются**: ужесточение (обязателен при
  создании, очистка запрещена) — прикладное, иначе миграция упала бы на строках с
  `telegram IS NULL`.

`revision = "0039_users_full_name_telegram"` — 29 символов ≤ 32.
`downgrade()` — DROP трёх колонок (lossy по введённым ФИО).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_users_full_name_telegram"
down_revision: str | None = "0038_user_roles_m2m"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("first_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("middle_name", sa.Text(), nullable=True))
    op.execute(sa.text("UPDATE users SET first_name = username WHERE is_system = false"))


def downgrade() -> None:
    op.drop_column("users", "middle_name")
    op.drop_column("users", "first_name")
    op.drop_column("users", "last_name")
