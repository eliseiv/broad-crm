"""Тест миграции Alembic ревизии 0004 (создание/DROP таблицы `notifier_server_state`).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe, как в
test_ai_keys_migration/test_position_migration): шаг 0003→0004 создаёт таблицу
`notifier_server_state` со всеми колонками, CHECK-констрейнтами зон, PK по `server_id`
и FK `ON DELETE CASCADE` — БЕЗ backfill (таблица пустая, alert-on-first-elevated,
ADR-014); обратный шаг 0004→0003 = `DROP TABLE`. Ревизия обязана иметь рабочий
`downgrade()` (03-data-model.md#миграция-0004_create_notifier_state, ADR-014,
07-deployment.md#откат-миграций-бд). Дополнительно — 0004 сидит поверх 0003
(единственная голова цепочки теперь 0005 — см. test_notifier_alert_log_migration, ADR-018).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0003_add_position:0004_create_notifier_state"
_DOWN_STEP = "0004_create_notifier_state:0003_add_position"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0004_creates_notifier_state_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table notifier_server_state" in lower
    # Все колонки контракта присутствуют.
    for column in ("server_id", "online", "zone_cpu", "zone_ram", "zone_ssd", "updated_at"):
        assert column in lower

    # PK по server_id + FK на servers с ON DELETE CASCADE (1:1, снятие строки при hard-delete).
    assert "pk_notifier_server_state" in sql
    assert "fk_notifier_server_state_server_id" in sql
    assert "on delete cascade" in lower

    # CHECK-констрейнты зон для всех трёх метрик.
    assert "ck_notifier_server_state_zone_cpu" in sql
    assert "ck_notifier_server_state_zone_ram" in sql
    assert "ck_notifier_server_state_zone_ssd" in sql

    # Backfill НЕ выполняется — таблица создаётся пустой (ADR-014).
    assert "insert into notifier_server_state" not in lower


def test_downgrade_0004_drops_notifier_state_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE notifier_server_state" in sql


def test_revision_0004_sits_on_0003() -> None:
    # 0004 сидит поверх 0003. Единственная голова цепочки теперь 0005
    # (проверяется в test_notifier_alert_log_migration после добавления 0005, ADR-018).
    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision("0004_create_notifier_state")
    assert rev.down_revision == "0003_add_position"
