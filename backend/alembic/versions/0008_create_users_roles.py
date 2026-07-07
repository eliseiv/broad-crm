r"""create users and roles tables (RBAC)

Revision ID: 0008_create_users_roles
Revises: 0007_create_backends
Create Date: 2026-07-07

Реестр ролей и пользователей для многопользовательского режима с правами на все
страницы (ADR-021, 03-data-model.md#таблицы-roles-и-users-rbac). Супер-админ (`.env`)
в таблицу `users` НЕ пишется. `roles.permissions` — jsonb, валидируется приложением
против каталога. `users.username` — «свободный» DB-CHECK (длина 1–64, без ведущих/
хвостовых пробелов, без control-символов); полное правило (кириллица) — на Pydantic.
FK `users.role_id → roles.id` ON DELETE RESTRICT (роль с носителями → 409 role_in_use).
Сид: одна роль `admin` с полными правами по каталогу. `downgrade()` = DROP users, roles.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_create_users_roles"
down_revision: str | None = "0007_create_backends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Полные права роли `admin` по каталогу (app/domain/permissions.py::CATALOG).
_ADMIN_PERMISSIONS = (
    '{"dashboard":["view"],'
    '"servers":["view","create","edit","delete"],'
    '"ai-keys":["view","create","edit","delete"],'
    '"proxies":["view","create","edit","delete"],'
    '"backends":["view","create","edit","delete"],'
    '"mail":["view"]}'
)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64",
            name="ck_roles_name_len",
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_users_role_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(username) BETWEEN 1 AND 64 "
            "AND username = btrim(username) "
            "AND username !~ '[[:cntrl:]]'",
            name="ck_users_username",
        ),
    )
    op.create_index("ix_users_role_id", "users", ["role_id"])

    # Сид роли `admin` с полными правами по каталогу (id/created_at/updated_at —
    # server_default). Пользователи не сидятся (супер-админ — из .env). JSON содержит
    # только двойные кавычки — безопасно встраивается в одинарно-кавычный SQL-литерал
    # (offline-совместимо, без bind-параметров).
    op.execute(
        "INSERT INTO roles (name, permissions) " f"VALUES ('admin', '{_ADMIN_PERMISSIONS}'::jsonb)"
    )


def downgrade() -> None:
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_table("users")
    op.drop_table("roles")
