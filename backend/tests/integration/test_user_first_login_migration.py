"""Тест миграции Alembic 0015 (users.first_login_at — метка первого входа, ADR-028).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0014→0015 добавляет в `users`:
  - `first_login_at timestamptz NULL` — момент ПЕРВОГО успешного входа (NULL = ещё не
    входил). Источник производного `UserListItem.status` (pending/active/inactive).
Backfill не требуется (NULL; на проде БД-пользователей 0). Обратный шаг 0015→0014 снимает
колонку. 0015 — голова цепочки миграций.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0014_proxies_alert_grace:0015_user_first_login"
_DOWN_STEP = "0015_user_first_login:0014_proxies_alert_grace"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0015_adds_first_login_column(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "users add column first_login_at" in lower
    assert "timestamp with time zone" in lower
    # Nullable, без DEFAULT и без backfill.
    assert "update users" not in lower


def test_downgrade_0015_drops_first_login_column(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "users drop column first_login_at" in lower


def test_revision_chain_single_head_0015_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0020_backends_domain_canon"]  # одна голова — цепочка линейна (ADR-038)
    rev = script.get_revision("0015_user_first_login")
    assert rev.down_revision == "0014_proxies_alert_grace"
