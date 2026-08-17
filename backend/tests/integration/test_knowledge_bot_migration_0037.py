"""Offline-SQL миграции 0037: CREATE TABLE knowledge_bot_links + индекс (ADR-076)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0036_ai_keys_credit_probe:0037_knowledge_bot_links"
_DOWN_STEP = "0037_knowledge_bot_links:0036_ai_keys_credit_probe"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0037_creates_knowledge_bot_links(capsys: pytest.CaptureFixture[str]) -> None:
    # Backfill jsonb делает SELECT по roles — в offline SQL (`sql=True`) bind=None,
    # поэтому upgrade падает после печати DDL. DDL всё равно попадает в stdout.
    try:
        command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    except AttributeError:
        pass
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table knowledge_bot_links" in lower
    for column in ("telegram_user_id", "user_id", "username", "started_at", "dead_at"):
        assert column in lower
    assert "ix_knowledge_bot_links_user_id" in lower
    assert "primary key" in lower
    assert "foreign key" in lower
    assert "on delete cascade" in lower


def test_downgrade_0037_drops_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    assert "DROP TABLE knowledge_bot_links" in sql
    assert "ix_knowledge_bot_links_user_id" in sql


def test_revision_0037_is_head_after_0036() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0037_knowledge_bot_links")
    assert rev.down_revision == "0036_ai_keys_credit_probe"
