"""Тест миграции Alembic 0011 (email→telegram + nullable password_hash, ADR-025).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0010→0011:
  1. переименовать `users.email` → `users.telegram` + swap частичного уникального
     индекса `uq_users_email` → `uq_users_telegram WHERE telegram IS NOT NULL`;
  2. снять `NOT NULL` с `users.password_hash` (NULL = беспарольный пользователь).
Обратный шаг 0011→0010 обратим: NULL-хэши → сентинел `!`, восстановить NOT NULL,
swap индекса обратно, rename telegram→email.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0010_add_user_email:0011_user_passwordless_telegram"
_DOWN_STEP = "0011_user_passwordless_telegram:0010_add_user_email"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0011_renames_email_to_telegram_and_relaxes_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # email → telegram (rename + swap частичного уникального индекса).
    assert "drop index uq_users_email" in lower
    assert "rename email to telegram" in lower
    assert "create unique index uq_users_telegram" in lower
    assert "where telegram is not null" in lower
    # password_hash → nullable (беспарольный пользователь).
    assert "alter column password_hash drop not null" in lower
    # Секрета/пароля в открытом виде миграция не пишет.
    assert "insert into users" not in lower


def test_downgrade_0011_restores_email_and_not_null(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # NULL-хэши заполняются невалидным сентинелом `!` перед восстановлением NOT NULL.
    assert "update users set password_hash = '!' where password_hash is null" in lower
    assert "alter column password_hash set not null" in lower
    # telegram → email обратно (swap индекса).
    assert "drop index uq_users_telegram" in lower
    assert "rename telegram to email" in lower
    assert "create unique index uq_users_email" in lower


def test_revision_0011_sits_on_top_of_0010() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0011_user_passwordless_telegram")
    assert rev.down_revision == "0010_add_user_email"
