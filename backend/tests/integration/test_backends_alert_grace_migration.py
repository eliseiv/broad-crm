"""Тест миграции Alembic 0013 (backends grace-порог: error_since + alert_sent, ADR-024).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0012→0013 добавляет в `backends`:
  - `error_since timestamptz NULL` — начало текущего непрерывного эпизода недоступности;
  - `alert_sent boolean NOT NULL DEFAULT false` — отправлен ли 🔴 для текущего эпизода.
Backfill не требуется (DEFAULT/NULL). Обратный шаг 0013→0012 снимает обе колонки.
0013 — голова цепочки миграций.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0012_teams_optional_leader:0013_backends_alert_grace"
_DOWN_STEP = "0013_backends_alert_grace:0012_teams_optional_leader"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0013_adds_grace_columns(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "backends add column error_since" in lower
    assert "timestamp with time zone" in lower
    assert "backends add column alert_sent" in lower
    assert "boolean" in lower
    assert "default false" in lower
    # Backfill не выполняется — только DEFAULT/NULL.
    assert "update backends" not in lower


def test_downgrade_0013_drops_grace_columns(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "backends drop column alert_sent" in lower
    assert "backends drop column error_since" in lower


def test_revision_chain_single_head_with_0013_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0013_backends_alert_grace"]  # одна голова — цепочка линейна
    rev = script.get_revision("0013_backends_alert_grace")
    assert rev.down_revision == "0012_teams_optional_leader"
