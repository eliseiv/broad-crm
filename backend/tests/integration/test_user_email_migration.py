"""Тест миграции Alembic 0010 (users.email + частичный uq-индекс, seed admin, ADR-022).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0009→0010 добавляет
`users.email` (`text NULL`) + частичный уникальный индекс `uq_users_email WHERE email
IS NOT NULL` (UNIQUE-when-present) и обновляет права seed-роли `admin` до нового каталога
(с `roles`/`teams`). Обратный шаг 0010→0009 возвращает права `admin` к прежнему каталогу
и снимает индекс/колонку. 0010 — голова цепочки.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0009_create_teams:0010_add_user_email"
_DOWN_STEP = "0010_add_user_email:0009_create_teams"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0010_adds_email_and_updates_admin(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # Колонка email + частичный уникальный индекс (UNIQUE-when-present).
    assert "add column email" in lower
    assert "uq_users_email" in lower
    assert "where email is not null" in lower

    # Обновление seed-роли admin до нового каталога (roles/teams присутствуют).
    assert "update roles" in lower
    assert "where name = 'admin'" in lower
    assert '"roles":["view","create","edit","delete"]' in sql
    assert '"teams":["view","create","edit","delete"]' in sql


def test_downgrade_0010_reverts_admin_and_drops_email(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # Откат прав admin к прежнему каталогу (без roles/teams).
    assert "update roles" in lower
    assert '"teams":[' not in sql  # прежний каталог не содержит teams
    assert '"mail":["view"]' in sql  # прежний каталог сохраняет mail
    # Снятие индекса и колонки email.
    assert "drop index uq_users_email" in lower
    assert "drop column email" in lower


def test_revision_chain_single_head_with_0010_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0017_create_sms_module"]  # одна голова — цепочка линейна (ADR-022)
    rev = script.get_revision("0010_add_user_email")
    assert rev.down_revision == "0009_create_teams"  # 0010 сидит поверх 0009
