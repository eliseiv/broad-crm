r"""servers — вход по приватному SSH-ключу рядом с паролем (ADR-067 §2)

Revision ID: 0031_servers_ssh_key_auth
Revises: 0030_document_node_roles
Create Date: 2026-07-22

`upgrade()` — строго в этом порядке (03-data-model.md#миграция-0031_servers_ssh_key_auth):

1. `ADD COLUMN auth_method text NOT NULL` **со `server_default='password'`**;
2. `ADD COLUMN ssh_private_key_encrypted bytea` и `ssh_key_passphrase_encrypted bytea`;
3. `ALTER COLUMN ssh_password_encrypted DROP NOT NULL`;
4. оба CHECK (`ck_servers_auth_method`, `ck_servers_auth_material`) — **последним шагом**.

**Backfill НЕ требуется — это следствие порядка, а не совпадение:** `server_default`
проставляет `'password'` всем существующим строкам В МОМЕНТ `ADD COLUMN`, а
`ssh_password_encrypted` у них уже заполнен ⇒ к шагу 4 каждая существующая строка
удовлетворяет `ck_servers_auth_material`. Совместимость со старыми записями полная.

`downgrade()` — **рабочий** (карв-аут «чисто DML» неприменим: есть DDL), но **ЛОССИ ПО
СТРОКАМ**: key-серверы в старой схеме непредставимы (`ssh_password_encrypted NOT NULL`, а
пароля у них нет и взять неоткуда), поэтому они удаляются. Подстановка пароля-заглушки
ЗАПРЕЩЕНА: после отката провижининг пошёл бы на хост с заведомо неверным паролем, а
оператор видел бы «пароль задан». Каскады штатные (`notifier_server_state` — `CASCADE`;
`notifier_alert_log`/`backends.server_id` — `SET NULL`). Перед `DELETE` — `RAISE NOTICE`
с числом удаляемых строк (без секретов): потеря не должна быть молчаливой.

⚠️ Откат этой миграции требует pre-deploy `pg_dump` (07-deployment.md#откат-миграций-бд);
осиротевшие `targets/<id>.json` убираются регенерацией file_sd из БД.

`revision = "0031_servers_ssh_key_auth"` (25 символов ≤ 32, 03-data-model.md#1-revision-id).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_servers_ssh_key_auth"
down_revision: str | None = "0030_document_node_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Дубль правила из app/models/server.py осознан: миграции НЕ импортируют код приложения
# (03-data-model.md §3) — удаление/переименование модуля сломало бы миграцию задним числом.
_CK_AUTH_MATERIAL = """(
    (auth_method = 'password'
        AND ssh_password_encrypted       IS NOT NULL
        AND ssh_private_key_encrypted    IS NULL
        AND ssh_key_passphrase_encrypted IS NULL)
    OR
    (auth_method = 'key'
        AND ssh_private_key_encrypted    IS NOT NULL
        AND ssh_password_encrypted       IS NULL)
)"""

# Не молчаливая потеря: печатает число удаляемых key-серверов (без секретов) до DELETE.
_NOTICE_LOSSY_DELETE = """
DO $$
DECLARE
    doomed integer;
BEGIN
    SELECT count(*) INTO doomed FROM servers WHERE auth_method = 'key';
    RAISE NOTICE 'downgrade 0031_servers_ssh_key_auth: удаляется key-серверов: %', doomed;
END
$$;
"""


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("auth_method", sa.Text(), nullable=False, server_default=sa.text("'password'")),
    )
    op.add_column(
        "servers", sa.Column("ssh_private_key_encrypted", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "servers", sa.Column("ssh_key_passphrase_encrypted", sa.LargeBinary(), nullable=True)
    )
    op.alter_column(
        "servers", "ssh_password_encrypted", existing_type=sa.LargeBinary(), nullable=True
    )
    op.create_check_constraint(
        "ck_servers_auth_method", "servers", "auth_method IN ('password','key')"
    )
    op.create_check_constraint("ck_servers_auth_material", "servers", _CK_AUTH_MATERIAL)


def downgrade() -> None:
    op.drop_constraint("ck_servers_auth_material", "servers", type_="check")
    op.drop_constraint("ck_servers_auth_method", "servers", type_="check")
    op.execute(_NOTICE_LOSSY_DELETE)
    op.execute("DELETE FROM servers WHERE auth_method = 'key'")
    op.alter_column(
        "servers", "ssh_password_encrypted", existing_type=sa.LargeBinary(), nullable=False
    )
    op.drop_column("servers", "ssh_key_passphrase_encrypted")
    op.drop_column("servers", "ssh_private_key_encrypted")
    op.drop_column("servers", "auth_method")
