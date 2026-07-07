"""Тест миграции Alembic ревизии 0005 (создание/DROP таблицы `notifier_alert_log`).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe, как в
test_notifier_state_migration/test_ai_keys_migration): шаг 0004→0005 создаёт
append-only таблицу `notifier_alert_log` с `bigint` IDENTITY PK, `server_id` FK
`ON DELETE SET NULL` (лог переживает удаление сервера), CHECK на `kind`
(`offline`/`recovered`/`warning`/`critical`) и индексом по `created_at DESC` —
БЕЗ backfill (таблица пустая); обратный шаг 0005→0004 = `DROP TABLE`. Ревизия
обязана иметь рабочий `downgrade()` (03-data-model.md#миграция-0005_create_notifier_alert_log,
ADR-018, 07-deployment.md#откат-миграций-бд). Дополнительно — 0005 единственная
голова цепочки ревизий, сидит поверх 0004.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0004_create_notifier_state:0005_create_notifier_alert_log"
_DOWN_STEP = "0005_create_notifier_alert_log:0004_create_notifier_state"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0005_creates_notifier_alert_log_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table notifier_alert_log" in lower
    # Все колонки контракта присутствуют.
    for column in ("id", "server_id", "kind", "message", "delivered", "created_at"):
        assert column in lower

    # bigint IDENTITY PK (осознанное отклонение от uuid-конвенции, ADR-018).
    assert "pk_notifier_alert_log" in sql
    assert "bigint" in lower
    assert "identity" in lower

    # FK на servers с ON DELETE SET NULL (лог переживает удаление сервера).
    assert "fk_notifier_alert_log_server_id" in sql
    assert "on delete set null" in lower

    # CHECK на kind со всеми четырьмя типами алертов (ADR-018).
    assert "ck_notifier_alert_log_kind" in sql
    for kind in ("offline", "recovered", "warning", "critical"):
        assert kind in lower

    # Индекс по created_at (DESC) под ретенцию/просмотр.
    assert "ix_notifier_alert_log_created_at" in lower
    assert "created_at desc" in lower

    # Backfill НЕ выполняется — таблица создаётся пустой (ADR-018).
    assert "insert into notifier_alert_log" not in lower


def test_downgrade_0005_drops_notifier_alert_log_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE notifier_alert_log" in sql


def test_revision_chain_single_head_with_0005_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0005_create_notifier_alert_log"]  # одна голова — цепочка линейна
    rev = script.get_revision("0005_create_notifier_alert_log")
    assert rev.down_revision == "0004_create_notifier_state"  # 0005 сидит поверх 0004
