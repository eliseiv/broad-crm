"""Тест миграции Alembic 0012 (optional leader + user_teams.created_at, ADR-026).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0011→0012:
  1. `teams.leader_id` → nullable + FK `fk_teams_leader_id` пересоздаётся
     `ON DELETE SET NULL` (было `RESTRICT`);
  2. `user_teams += created_at timestamptz NOT NULL DEFAULT now()`.
Обратный шаг 0012→0011 обратим: снять `created_at`, FK обратно `RESTRICT`,
`leader_id` снова `NOT NULL`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0011_user_passwordless_telegram:0012_teams_optional_leader"
_DOWN_STEP = "0012_teams_optional_leader:0011_user_passwordless_telegram"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0012_optional_leader_and_created_at(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # leader_id → nullable + FK ON DELETE SET NULL (пересоздать констрейнт).
    assert "alter column leader_id drop not null" in lower
    assert "drop constraint fk_teams_leader_id" in lower
    assert "add constraint fk_teams_leader_id" in lower
    assert "on delete set null" in lower
    # Дата добавления участника (для авто-передачи лидерства).
    assert "user_teams add column created_at" in lower
    assert "timestamp with time zone" in lower
    assert "default now()" in lower
    assert "not null" in lower


def test_downgrade_0012_reverts_to_mandatory_leader(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "user_teams drop column created_at" in lower
    assert "drop constraint fk_teams_leader_id" in lower
    assert "on delete restrict" in lower
    assert "alter column leader_id set not null" in lower


def test_revision_0012_sits_on_top_of_0011() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0012_teams_optional_leader")
    assert rev.down_revision == "0011_user_passwordless_telegram"
