r"""backends: связи (server_id/ai_key_id) + секреты (api_key/admin_api_key) + git/note

Revision ID: 0019_backends_relations_secrets
Revises: 0018_teams_mail_group_id
Create Date: 2026-07-10

Амендмент ADR-040 (03-data-model.md#миграция-0019_backends_relations_secrets-концепт).
Добавляет в `backends`:
  - `server_id uuid NULL REFERENCES servers(id) ON DELETE SET NULL` — сервер CRM;
  - `ai_key_id uuid NULL REFERENCES ai_keys(id) ON DELETE SET NULL` — ИИ-ключ CRM;
  - `api_key_encrypted bytea NULL` — API KEY бэка (секрет, Fernet at-rest);
  - `admin_api_key_encrypted bytea NULL` — ADMIN API KEY бэка (секрет, Fernet);
  - `git text NULL` — ссылка на репозиторий (НЕ секрет);
  - `note text NULL` — свободные примечания (НЕ секрет);
+ индексы `ix_backends_server_id`, `ix_backends_ai_key_id` (reverse-lookup «бэки
сервера»/«бэки ключа» и обслуживание `ON DELETE SET NULL`). `ON DELETE SET NULL`
обнуляет связь при удалении сервера/ключа, бэк не удаляется.

Backfill не выполняется (новые колонки — `NULL`). `downgrade()` снимает индексы и
колонки (FK-констрейнты снимаются вместе с колонками).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_backends_relations_secrets"
down_revision: str | None = "0018_teams_mail_group_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backends",
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("servers.id", ondelete="SET NULL", name="fk_backends_server_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "backends",
        sa.Column(
            "ai_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_keys.id", ondelete="SET NULL", name="fk_backends_ai_key_id"),
            nullable=True,
        ),
    )
    op.add_column("backends", sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("backends", sa.Column("admin_api_key_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("backends", sa.Column("git", sa.Text(), nullable=True))
    op.add_column("backends", sa.Column("note", sa.Text(), nullable=True))
    op.create_index("ix_backends_server_id", "backends", ["server_id"])
    op.create_index("ix_backends_ai_key_id", "backends", ["ai_key_id"])


def downgrade() -> None:
    op.drop_index("ix_backends_ai_key_id", table_name="backends")
    op.drop_index("ix_backends_server_id", table_name="backends")
    op.drop_column("backends", "note")
    op.drop_column("backends", "git")
    op.drop_column("backends", "admin_api_key_encrypted")
    op.drop_column("backends", "api_key_encrypted")
    op.drop_column("backends", "ai_key_id")
    op.drop_column("backends", "server_id")
