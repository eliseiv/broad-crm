"""Страница «Пользователи» вошла в каталог прав: доп-права ролям с полным каталогом.

Revision ID: 0041_roles_users_page_permission
Revises: 0040_backend_users_snapshot
Create Date: 2026-08-20

`CATALOG` пополнился страницей `users` (прежде она была вне матрицы и гейтилась
`require_admin`). Предикат `is_admin_level` определён как «супер-админ ИЛИ роль с
именем `admin` ИЛИ **полный каталог** по union прав» — а роли админов в этой
установке названы по-русски («Админ»), то есть их admin-уровень держится ИМЕННО на
полноте каталога. Без этой миграции добавление 14-й страницы мгновенно лишило бы их
`is_admin_level`: пропал бы пункт «Пользователи», отвалился бы фолбэк `documents:share`
и админ-операции. Поэтому ролям, покрывавшим ВЕСЬ прежний каталог, права на новую
страницу выдаются автоматически.

Роли с частичным набором прав не трогаются: им страница `users` не полагалась и
раньше, а тихая выдача доступа к реестру сотрудников была бы расширением привилегий
без решения оператора.

`downgrade` снимает ключ `users` у всех ролей — каталог возвращается к 13 страницам,
и лишний ключ иначе не прошёл бы `validate_permissions` (422 при любой правке роли).
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0041_roles_users_page_permission"
down_revision = "0040_backend_users_snapshot"
branch_labels = None
depends_on = None

# Каталог ДО этой миграции (13 страниц). Зафиксирован здесь копией намеренно:
# миграция обязана быть воспроизводимой и не зависеть от текущего кода домена,
# который продолжит меняться.
_OLD_CATALOG: dict[str, tuple[str, ...]] = {
    "dashboard": ("view",),
    "servers": ("view", "create", "edit", "delete"),
    "ai-keys": ("view", "create", "edit", "delete"),
    "proxies": ("view", "create", "edit", "delete"),
    "backends": ("view", "create", "edit", "delete"),
    "backend-users": ("view", "edit"),
    "backend-economics": ("view", "edit"),
    "mail": ("view", "create", "edit", "delete", "sync", "tags"),
    "sms": ("view", "edit", "transfer", "sync", "delete"),
    "roles": ("view", "create", "edit", "delete"),
    "teams": ("view", "create", "edit", "delete"),
    "documents": ("view", "create", "edit", "delete", "share"),
    "broadcast": ("view", "send"),
}

_USERS_ACTIONS = ["view", "create", "edit", "delete"]


def _covers_old_catalog(permissions: dict[str, list[str]]) -> bool:
    """Роль покрывает ВЕСЬ прежний каталог (та же логика, что `permissions_subset`)."""
    for page, actions in _OLD_CATALOG.items():
        granted = permissions.get(page)
        if not isinstance(granted, list):
            return False
        if not set(actions).issubset(set(granted)):
            return False
    return True


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()

    for role_id, permissions in rows:
        # JSONB приходит уже как dict; str — только на экзотических драйверах.
        current = json.loads(permissions) if isinstance(permissions, str) else dict(permissions)
        if "users" in current or not _covers_old_catalog(current):
            continue
        current["users"] = list(_USERS_ACTIONS)
        connection.execute(
            sa.text("UPDATE roles SET permissions = CAST(:perms AS jsonb) WHERE id = :id"),
            {"perms": json.dumps(current, ensure_ascii=False), "id": role_id},
        )


def downgrade() -> None:
    op.execute("UPDATE roles SET permissions = permissions - 'users' WHERE permissions ? 'users'")
