"""Тест миграции Alembic 0009 (создание/DROP таблиц `teams` и `user_teams`, ADR-022).

Offline-рендер SQL (без подключения к БД, CI-safe, как в остальных migration-тестах,
03-data-model.md#миграция-0009_create_teams). Шаг 0008→0009 создаёт `teams` (UNIQUE name,
CHECK кириллицы, FK leader_id → users ON DELETE RESTRICT, `ix_teams_leader_id`) и
ассоциативную `user_teams` (составной PK, обе FK ON DELETE CASCADE, `ix_user_teams_team_id`).
Обратный шаг 0009→0008 = DROP user_teams; DROP teams (порядок из-за FK).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0008_create_users_roles:0009_create_teams"
_DOWN_STEP = "0009_create_teams:0008_create_users_roles"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0009_creates_teams_and_user_teams(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table teams" in lower
    assert "create table user_teams" in lower

    # Колонки контракта.
    for column in ("id", "name", "leader_id", "created_at", "updated_at"):
        assert column in lower

    # Уникальность имени и CHECK кириллицы (длина + btrim + control-символы).
    assert "uq_teams_name" in lower
    assert "ck_teams_name" in sql
    assert "btrim(name)" in lower
    assert "[[:cntrl:]]" in sql

    # FK teams.leader_id → users ON DELETE RESTRICT (лидера нельзя удалить → 409).
    assert "fk_teams_leader_id" in lower
    assert "on delete restrict" in lower
    assert "ix_teams_leader_id" in lower

    # user_teams: составной PK, обе FK ON DELETE CASCADE, индекс по team_id.
    assert "pk_user_teams" in lower
    assert "fk_user_teams_user_id" in lower
    assert "fk_user_teams_team_id" in lower
    assert "on delete cascade" in lower
    assert "ix_user_teams_team_id" in lower


def test_downgrade_0009_drops_user_teams_then_teams(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE user_teams" in sql
    assert "DROP TABLE teams" in sql
    # user_teams удаляется раньше teams (FK CASCADE ссылается на teams).
    assert sql.index("DROP TABLE user_teams") < sql.index("DROP TABLE teams")


def test_0009_sits_on_top_of_0008() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0009_create_teams")
    assert rev.down_revision == "0008_create_users_roles"
