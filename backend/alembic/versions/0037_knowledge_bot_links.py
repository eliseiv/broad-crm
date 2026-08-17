r"""knowledge_bot_links + backfill extra-действий и broadcast (ADR-076)

Revision ID: 0037_knowledge_bot_links
Revises: 0036_ai_keys_credit_probe
Create Date: 2026-08-17

Таблица `knowledge_bot_links` (03-data-model.md#таблица-knowledge_bot_links-adr-076)
+ backfill jsonb ролей (ADR-076 §4):

1. Страницам с полным CRUD (`view`/`create`/`edit`/`delete` — какие есть у страницы)
   дописать extra-действия (`documents.share`, `mail.sync`/`tags`, `sms.transfer`/`sync`).
2. Сид `admin` получает полный текущий каталог (включая `broadcast: [view, send]`).
3. Роли, которые после шага 1 покрывают полный каталог **без** `broadcast`, получают
   `broadcast: [view, send]`.

`revision = "0037_knowledge_bot_links"` — 24 символа ≤ 32.
`downgrade()` — `DROP TABLE`. Backfill jsonb **не откатывается** (как `0010`/`0016`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_knowledge_bot_links"
down_revision: str | None = "0036_ai_keys_credit_probe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Снимок CATALOG на момент 0037 (не импортировать app.domain — миграция самодостаточна).
_FULL_CATALOG: dict[str, list[str]] = {
    "dashboard": ["view"],
    "servers": ["view", "create", "edit", "delete"],
    "ai-keys": ["view", "create", "edit", "delete"],
    "proxies": ["view", "create", "edit", "delete"],
    "backends": ["view", "create", "edit", "delete"],
    "backend-users": ["view", "edit"],
    "backend-economics": ["view", "edit"],
    "mail": ["view", "create", "edit", "delete", "sync", "tags"],
    "sms": ["view", "edit", "transfer", "sync", "delete"],
    "roles": ["view", "create", "edit", "delete"],
    "teams": ["view", "create", "edit", "delete"],
    "documents": ["view", "create", "edit", "delete", "share"],
    "broadcast": ["view", "send"],
}

_CRUD = frozenset({"view", "create", "edit", "delete"})
_ADMIN_ROLE_NAME = "admin"


def _apply_backfill(name: str, perms: dict[str, list[str]]) -> dict[str, list[str]]:
    """Extra-действия + broadcast для admin / полного прежнего каталога."""
    if name == _ADMIN_ROLE_NAME:
        return {page: list(actions) for page, actions in _FULL_CATALOG.items()}

    result = {page: list(actions) for page, actions in perms.items()}
    for page, actions in _FULL_CATALOG.items():
        if page == "broadcast":
            continue
        page_crud = [action for action in actions if action in _CRUD]
        page_extras = [action for action in actions if action not in _CRUD]
        if not page_extras:
            continue
        current = list(result.get(page, []))
        if page_crud and set(page_crud) <= set(current):
            merged = list(current)
            for extra in page_extras:
                if extra not in merged:
                    merged.append(extra)
            result[page] = merged

    old_catalog = {page: acts for page, acts in _FULL_CATALOG.items() if page != "broadcast"}
    if all(set(acts) <= set(result.get(page, [])) for page, acts in old_catalog.items()):
        result["broadcast"] = ["view", "send"]
    return result


def _backfill_role_permissions() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, permissions FROM roles")).mappings().all()
    for row in rows:
        perms = dict(row["permissions"] or {})
        updated = _apply_backfill(str(row["name"]), perms)
        if updated == perms:
            continue
        conn.execute(
            sa.text(
                "UPDATE roles SET permissions = CAST(:p AS jsonb), updated_at = now() "
                "WHERE id = :id"
            ),
            {"p": json.dumps(updated, ensure_ascii=False), "id": row["id"]},
        )


def upgrade() -> None:
    op.create_table(
        "knowledge_bot_links",
        sa.Column("telegram_user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("telegram_user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_knowledge_bot_links_user_id",
        "knowledge_bot_links",
        ["user_id"],
    )
    _backfill_role_permissions()


def downgrade() -> None:
    # Backfill jsonb не откатывается (как 0010/0016): extra-действия и broadcast
    # в permissions ролей остаются. Откат схемы — только DROP TABLE.
    op.drop_index("ix_knowledge_bot_links_user_id", table_name="knowledge_bot_links")
    op.drop_table("knowledge_bot_links")
