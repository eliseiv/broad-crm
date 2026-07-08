r"""add users.email + partial unique index; update seed admin role catalog

Revision ID: 0010_add_user_email
Revises: 0009_create_teams
Create Date: 2026-07-08

Опциональный `users.email` (ADR-022, 03-data-model.md#миграция-0010_add_user_email):
колонка `text NULL` + частичный уникальный индекс `uq_users_email WHERE email IS NOT NULL`
(UNIQUE-when-present; дубликат → 409 email_taken, формат — на Pydantic → 422). Плюс
обновление seed-роли `admin` до полного нового каталога (права `roles`/`teams`), иначе
БД-администратор не получит доступ к новым страницам (супер-админ бэкапится
`full_catalog_permissions()` в рантайме). `downgrade()` возвращает права `admin` к
прежнему каталогу и снимает индекс/колонку.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_add_user_email"
down_revision: str | None = "0009_create_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Полный новый каталог прав роли `admin` (app/domain/permissions.py::CATALOG, ADR-022).
_ADMIN_PERMISSIONS_NEW = (
    '{"dashboard":["view"],'
    '"servers":["view","create","edit","delete"],'
    '"ai-keys":["view","create","edit","delete"],'
    '"proxies":["view","create","edit","delete"],'
    '"backends":["view","create","edit","delete"],'
    '"mail":["view"],'
    '"roles":["view","create","edit","delete"],'
    '"teams":["view","create","edit","delete"]}'
)

# Прежний каталог (до ADR-022, без roles/teams) — для downgrade.
_ADMIN_PERMISSIONS_OLD = (
    '{"dashboard":["view"],'
    '"servers":["view","create","edit","delete"],'
    '"ai-keys":["view","create","edit","delete"],'
    '"proxies":["view","create","edit","delete"],'
    '"backends":["view","create","edit","delete"],'
    '"mail":["view"]}'
)


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    # Обновить seed-роль `admin` до полного нового каталога (idempotent по имени).
    op.execute(
        "UPDATE roles "
        f"SET permissions = '{_ADMIN_PERMISSIONS_NEW}'::jsonb, updated_at = now() "
        "WHERE name = 'admin'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE roles "
        f"SET permissions = '{_ADMIN_PERMISSIONS_OLD}'::jsonb, updated_at = now() "
        "WHERE name = 'admin'"
    )
    op.drop_index("uq_users_email", table_name="users")
    op.drop_column("users", "email")
