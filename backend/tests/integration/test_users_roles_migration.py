"""Тест миграции Alembic 0008 (создание/DROP таблиц `roles` и `users`, RBAC).

Offline-рендер SQL (без подключения к БД, CI-safe, как в остальных migration-тестах,
03-data-model.md#миграция-0008, ADR-021). Шаг 0007→0008 создаёт `roles` и `users` со
всеми колонками контракта, CHECK кириллицы (btrim + control-символы), FK `users.role_id`
ON DELETE RESTRICT, уникальными индексами и сидом роли `admin` с полными правами.
Обратный шаг 0008→0007 = DROP users; DROP roles. 0008 — единственная голова цепочки.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0007_create_backends:0008_create_users_roles"
_DOWN_STEP = "0008_create_users_roles:0007_create_backends"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0008_creates_roles_and_users(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table roles" in lower
    assert "create table users" in lower

    # Колонки контракта (03-data-model.md).
    for column in ("id", "name", "permissions", "created_at", "updated_at"):
        assert column in lower
    for column in ("username", "password_hash", "role_id", "is_active"):
        assert column in lower

    # Уникальность имени роли и username.
    assert "uq_roles_name" in lower
    assert "uq_users_username" in lower

    # CHECK кириллицы для username: длина + btrim (без ведущих/хвостовых пробелов) +
    # запрет control-символов. Полное правило набора символов — на Pydantic.
    assert "ck_users_username" in sql
    assert "btrim(username)" in lower
    assert "[[:cntrl:]]" in sql

    # FK users.role_id → roles.id ON DELETE RESTRICT (роль с носителями → 409 role_in_use).
    assert "foreign key" in lower
    assert "on delete restrict" in lower
    assert "ix_users_role_id" in lower

    # Сид роли admin с полными правами по каталогу (без сида пользователей — супер-админ
    # из .env). permissions встроены как jsonb-литерал.
    assert "insert into roles" in lower
    assert "admin" in lower
    assert '"servers":["view","create","edit","delete"]' in sql
    assert '"mail":["view"]' in sql
    assert "insert into users" not in lower


def test_downgrade_0008_drops_users_then_roles(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE users" in sql
    assert "DROP TABLE roles" in sql
    # users удаляется раньше roles (FK RESTRICT).
    assert sql.index("DROP TABLE users") < sql.index("DROP TABLE roles")


def test_revision_chain_single_head_with_0008_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0013_backends_alert_grace"]  # одна голова — цепочка линейна
    rev = script.get_revision("0008_create_users_roles")
    assert rev.down_revision == "0007_create_backends"  # 0008 сидит поверх 0007
