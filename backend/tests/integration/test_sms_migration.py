"""Тест миграции Alembic 0017 (создание 4 таблиц модуля «СМС», ADR-030).

Offline-рендер SQL (без БД, CI-safe, как остальные migration-тесты). Шаг
0016→0017 создаёт sms_phone_numbers, sms_inbound (+3 индекса), sms_deliveries
(+CHECK/UNIQUE/retry-индекс), sms_telegram_links (+индекс) с FK/ON DELETE по
03-data-model.md. Обратный шаг — DROP в обратном порядке (FK sms_deliveries →
sms_inbound). Сверка `down_revision`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0016_backfill_team_leaders:0017_create_sms_module"
_DOWN_STEP = "0017_create_sms_module:0016_backfill_team_leaders"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0017_creates_four_sms_tables(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    for table in (
        "sms_phone_numbers",
        "sms_inbound",
        "sms_deliveries",
        "sms_telegram_links",
    ):
        assert f"create table {table}" in lower

    # Уникальность номера + FK team/added_by с ON DELETE SET NULL.
    assert "uq_sms_phone_numbers_phone_number" in lower
    assert "fk_sms_phone_numbers_team_id" in lower
    assert "on delete set null" in lower
    assert "ix_sms_phone_numbers_team_id" in lower

    # sms_inbound: partial-UNIQUE дедупа по SID + keyset-индекс + partial team-индекс.
    assert "sms_inbound_sid_uq" in lower
    assert "twilio_message_sid is not null" in lower
    assert "ix_sms_inbound_to_number_received" in lower
    assert "received_at desc" in lower

    # sms_deliveries: CHECK статусов, UNIQUE (inbound_sms_id, telegram_user_id), retry-индекс,
    # FK на sms_inbound/users с CASCADE.
    assert "ck_sms_deliveries_status" in lower
    assert "uq_sms_deliveries_sms_chat" in lower
    assert "ix_sms_deliveries_retry" in lower
    assert "fk_sms_deliveries_inbound_sms_id" in lower
    assert "on delete cascade" in lower

    # sms_telegram_links: PK telegram_user_id, FK user_id CASCADE, индекс user_id.
    assert "pk_sms_telegram_links" in lower
    assert "ix_sms_telegram_links_user_id" in lower


def test_downgrade_0017_drops_in_reverse_fk_order(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    for table in (
        "sms_telegram_links",
        "sms_deliveries",
        "sms_inbound",
        "sms_phone_numbers",
    ):
        assert f"DROP TABLE {table}" in sql

    # sms_deliveries (FK → sms_inbound) удаляется раньше sms_inbound.
    assert sql.index("DROP TABLE sms_deliveries") < sql.index("DROP TABLE sms_inbound")


def test_0017_sits_on_top_of_0016() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0017_create_sms_module")
    assert rev.down_revision == "0016_backfill_team_leaders"
