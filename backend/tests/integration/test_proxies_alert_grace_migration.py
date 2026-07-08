"""Тест миграции Alembic 0014 (proxies grace-порог: error_since + alert_sent, ADR-027).

Offline-рендер SQL (без подключения к БД, CI-safe). Шаг 0013→0014 добавляет в `proxies`:
  - `error_since timestamptz NULL` — начало текущего непрерывного эпизода недоступности
    (ставится при `pending|working → error`, сбрасывается при `working`);
  - `alert_sent boolean NOT NULL DEFAULT false` — отправлен ли 🔴 для текущего эпизода.
Backfill не требуется (DEFAULT/NULL). Обратный шаг 0014→0013 снимает обе колонки.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0013_backends_alert_grace:0014_proxies_alert_grace"
_DOWN_STEP = "0014_proxies_alert_grace:0013_backends_alert_grace"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0014_adds_grace_columns(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "proxies add column error_since" in lower
    assert "timestamp with time zone" in lower
    assert "proxies add column alert_sent" in lower
    assert "boolean" in lower
    assert "default false" in lower
    # Backfill не выполняется — только DEFAULT/NULL.
    assert "update proxies" not in lower


def test_downgrade_0014_drops_grace_columns(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "proxies drop column alert_sent" in lower
    assert "proxies drop column error_since" in lower


def test_revision_0014_links_to_0013() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0014_proxies_alert_grace")
    assert rev.down_revision == "0013_backends_alert_grace"
