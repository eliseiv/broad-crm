r"""user_roles — роли пользователя стали M2M (ADR-079 §1)

Revision ID: 0038_user_roles_m2m
Revises: 0037_knowledge_bot_links
Create Date: 2026-08-19

Создаёт `user_roles(user_id, role_id, created_at)` (03-data-model.md#таблица-user_roles-m2m-adr-079),
переносит в неё текущие связи из `users.role_id` — **включая системную строку-якорь**
(ADR-051: иначе якорь остался бы без роли, а гард `is_in_use` перестал бы держать роль
`admin`) — и дропает колонку `users.role_id` в той же миграции (два дома одной связи
немедленно разошлись бы).

- FK `user_id → users.id` — `ON DELETE CASCADE`; FK `role_id → roles.id` — **`RESTRICT`**
  (зеркало прежнего `409 role_in_use`; `CASCADE` молча снял бы роль со всех носителей).
- `ix_user_roles_role_id` обязателен: под гард `DELETE /api/roles/{id}` и обратную
  выборку «кто в роли».
- **`downgrade()` lossy:** восстанавливается только ПЕРВАЯ роль (`MIN(created_at)`).
  **Пользователь БЕЗ ролей** (заведён прямым SQL в обход API — «минимум одна роль»
  держит сервис, а не БД) откатить нельзя: колонка `users.role_id` возвращается
  `NOT NULL`, и подставлять такому пользователю произвольную роль запрещено — это
  молча выдало бы ему чужие права. Поэтому `downgrade()` СНАЧАЛА проверяет наличие
  таких строк и падает с внятным сообщением (перечисляя `username`), а не опаковым
  нарушением `NOT NULL` в середине отката.

`revision = "0038_user_roles_m2m"` — 19 символов ≤ 32.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_user_roles_m2m"
down_revision: str | None = "0037_knowledge_bot_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_roles_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_user_roles_role_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    # Перенос БЕЗ фильтра `is_system`: якорь супер-админа обязан получить свою строку.
    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT id, role_id FROM users "
            "ON CONFLICT DO NOTHING"
        )
    )

    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_column("users", "role_id")


def downgrade() -> None:
    # Пользователь без единой строки `user_roles` не имеет чем заполнить NOT NULL
    # `users.role_id`. Падаем ЯВНО и с именами — иначе оператор получил бы опаковое
    # нарушение констрейнта посреди отката и не знал бы, какие строки чинить.
    orphans = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT u.username FROM users u "
                "WHERE NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id) "
                "ORDER BY u.username LIMIT 20"
            )
        )
        .scalars()
        .all()
    )
    if orphans:
        raise RuntimeError(
            "Откат 0038 невозможен: у пользователей нет ни одной роли "
            f"({', '.join(orphans)}). Назначьте роль вручную и повторите откат."
        )

    op.add_column(
        "users",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Lossy: возвращается только ПЕРВАЯ роль (created_at, role_id).
    op.execute(
        sa.text(
            "UPDATE users u SET role_id = ("
            "  SELECT ur.role_id FROM user_roles ur WHERE ur.user_id = u.id"
            "  ORDER BY ur.created_at, ur.role_id LIMIT 1"
            ")"
        )
    )
    op.alter_column(
        "users",
        "role_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_users_role_id",
        "users",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_users_role_id", "users", ["role_id"])
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
