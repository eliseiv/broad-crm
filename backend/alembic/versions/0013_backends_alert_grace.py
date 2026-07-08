r"""backends: grace-порог алерта недоступности (ADR-024)

Revision ID: 0013_backends_alert_grace
Revises: 0012_teams_optional_leader
Create Date: 2026-07-08

Поля grace-порога алерта недоступности бэка (ADR-024,
03-data-model.md#миграция-0013_backends_alert_grace-концепт):
  - `error_since timestamptz NULL` — начало текущего непрерывного эпизода
    недоступности (ставится при `pending|working → error`, сбрасывается при `working`);
  - `alert_sent boolean NOT NULL DEFAULT false` — отправлен ли 🔴 для текущего эпизода.
`check_status→error` остаётся немедленным (реальность в UI сразу), но Telegram-🔴
шлётся только после непрерывной недоступности ≥ `BACKEND_ALERT_AFTER_SEC` (30 мин).
Backfill не требуется (DEFAULT/NULL). `downgrade()` снимает обе колонки.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_backends_alert_grace"
down_revision: str | None = "0012_teams_optional_leader"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backends",
        sa.Column("error_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backends",
        sa.Column(
            "alert_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("backends", "alert_sent")
    op.drop_column("backends", "error_since")
