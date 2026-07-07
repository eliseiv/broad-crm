"""Тест миграции Alembic ревизии 0007 (создание/DROP таблицы `backends`).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe, как в
test_proxies_migration): шаг 0006→0007 создаёт таблицу `backends` со всеми колонками
контракта, CHECK-констрейнтами (длины code/name, домен-инвариант, check_status),
уникальным индексом `uq_backends_code` и индексом `ix_backends_position`; обратный шаг
0007→0006 = `DROP TABLE` (индексы снимаются вместе с таблицей). Ревизия обязана иметь
рабочий `downgrade()` (modules/backends DoD, 07-deployment.md#откат-миграций-бд).
Дополнительно — 0007 единственная голова цепочки, сидит поверх 0006.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0006_create_proxies:0007_create_backends"
_DOWN_STEP = "0007_create_backends:0006_create_proxies"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0007_creates_backends_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    assert "create table backends" in lower
    # Все колонки контракта присутствуют (04-api.md#backends, 03-data-model.md).
    for column in (
        "id",
        "code",
        "name",
        "domain",
        "check_status",
        "error_message",
        "position",
        "last_checked_at",
        "created_at",
        "updated_at",
    ):
        assert column in lower

    # CHECK-констрейнты длин/домена/статуса.
    assert "ck_backends_code_len" in sql
    assert "ck_backends_name_len" in sql
    assert "ck_backends_domain" in sql
    assert "ck_backends_check_status" in sql
    for value in ("pending", "working", "error"):
        assert value in lower

    # Уникальный индекс по code (дубль → 409 backend_code_taken).
    assert "uq_backends_code" in lower
    assert "unique" in lower
    # Индекс сортировки единого списка (drag-and-drop).
    assert "ix_backends_position" in lower

    # Секрета/Fernet нет — колонок пароля быть не должно.
    assert "password" not in lower
    # Таблица создаётся пустой — backfill не выполняется.
    assert "insert into backends" not in lower


def test_downgrade_0007_drops_backends_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE backends" in sql


def test_revision_chain_single_head_with_0007_on_top() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()

    assert heads == ["0008_create_users_roles"]  # одна голова — цепочка линейна (ADR-021)
    rev = script.get_revision("0007_create_backends")
    assert rev.down_revision == "0006_create_proxies"  # 0007 сидит поверх 0006
