"""Право «Назначение любых ролей» (`users:assign_any`) ролям, уже создающим пользователей.

Revision ID: 0042_roles_users_assign_any
Revises: 0041_roles_users_page_permission
Create Date: 2026-08-20

Прод-инцидент: роль PM с правом `users:create` не могла завести сотрудника — сработала
анти-эскалация «нельзя назначить роль шире собственных прав». По факту PM мог выдать
лишь 3 роли из 8: у остальных есть страницы, которых у него нет (`backend-users`,
`backend-economics`, `broadcast`) либо более широкие действия в `sms`/`mail`.

Лечить это выдачей PM недостающих страниц неверно: чтобы завести тестировщика, менеджер
получил бы доступ к «Продуктам и тарифам» и «Рассылке». Поэтому введено отдельное право
`users:assign_any` — «выдавать любые роли», НЕ расширяющее доступ владельца к страницам.

**Кому выдаём автоматически:** ролям, у которых УЖЕ есть `users:create` или `users:edit`.
Это не расширение доверия, а его признание: администратор, разрешивший роли заводить и
править сотрудников, тем самым уже доверил ей выбор роли для них — а без `assign_any`
это разрешение наполовину нерабочее. Роли без права на реестр не затрагиваются.

`downgrade` снимает только `assign_any`, остальные действия страницы `users` не трогает.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0042_roles_users_assign_any"
down_revision = "0041_roles_users_page_permission"
branch_labels = None
depends_on = None

_PAGE = "users"
_ACTION = "assign_any"
_TRIGGER_ACTIONS = ("create", "edit")


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()

    for role_id, permissions in rows:
        current = json.loads(permissions) if isinstance(permissions, str) else dict(permissions)
        actions = current.get(_PAGE)
        if not isinstance(actions, list):
            continue
        if _ACTION in actions:
            continue
        if not any(trigger in actions for trigger in _TRIGGER_ACTIONS):
            continue
        current[_PAGE] = [*actions, _ACTION]
        connection.execute(
            sa.text("UPDATE roles SET permissions = CAST(:perms AS jsonb) WHERE id = :id"),
            {"perms": json.dumps(current, ensure_ascii=False), "id": role_id},
        )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()

    for role_id, permissions in rows:
        current = json.loads(permissions) if isinstance(permissions, str) else dict(permissions)
        actions = current.get(_PAGE)
        if not isinstance(actions, list) or _ACTION not in actions:
            continue
        current[_PAGE] = [action for action in actions if action != _ACTION]
        connection.execute(
            sa.text("UPDATE roles SET permissions = CAST(:perms AS jsonb) WHERE id = :id"),
            {"perms": json.dumps(current, ensure_ascii=False), "id": role_id},
        )
