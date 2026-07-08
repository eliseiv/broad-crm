"""Тест миграции Alembic ревизии 0006 (создание/DROP таблицы `proxies`).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe, как в
test_ai_keys_migration/test_notifier_alert_log_migration): шаг 0005→0006 создаёт
таблицу `proxies` со всеми колонками контракта, CHECK-констрейнтами
(proxy_type/port/check_status/длины name/host), колонкой `position` и индексом
`ix_proxies_position`; обратный шаг 0006→0005 = `DROP TABLE` (индекс снимается
вместе с таблицей). Ревизия обязана иметь рабочий `downgrade()`
(modules/proxies DoD, 07-deployment.md#откат-миграций-бд). Дополнительно — 0006
единственная голова цепочки, сидит поверх 0005.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0005_create_notifier_alert_log:0006_create_proxies"
_DOWN_STEP = "0006_create_proxies:0005_create_notifier_alert_log"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0006_creates_proxies_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table proxies" in lower
    # Все колонки контракта присутствуют (04-api.md#proxies, 03-data-model.md).
    for column in (
        "id",
        "name",
        "proxy_type",
        "host",
        "port",
        "username",
        "password_encrypted",
        "check_status",
        "error_message",
        "position",
        "last_checked_at",
        "created_at",
        "updated_at",
    ):
        assert column in lower

    # CHECK-констрейнты типа/порта/статуса/длин.
    assert "ck_proxies_proxy_type" in sql
    assert "ck_proxies_port" in sql
    assert "ck_proxies_check_status" in sql
    assert "ck_proxies_name_len" in sql
    assert "ck_proxies_host_len" in sql
    # Все три типа прокси и статуса в CHECK.
    for value in ("http", "https", "socks5", "pending", "working", "error"):
        assert value in lower

    # Индекс сортировки единого списка (drag-and-drop).
    assert "ix_proxies_position" in lower

    # Таблица создаётся пустой — backfill не выполняется.
    assert "insert into proxies" not in lower


def test_downgrade_0006_drops_proxies_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE proxies" in sql


def test_revision_chain_single_head_with_0006_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    # Голова цепочки — 0008_create_users_roles (добавлена поверх 0007 фичей RBAC, ADR-021);
    # цепочка остаётся линейной (одна голова). 0006 по-прежнему сидит поверх 0005.
    assert heads == ["0015_user_first_login"]  # одна голова — цепочка линейна
    rev = script.get_revision("0006_create_proxies")
    assert rev.down_revision == "0005_create_notifier_alert_log"  # 0006 сидит поверх 0005
