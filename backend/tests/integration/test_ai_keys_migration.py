"""Тест миграции Alembic ревизии 0002 (создание/DROP таблицы `ai_keys`).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe): `upgrade` для шага
0001→0002 создаёт таблицу `ai_keys` со всеми колонками/CHECK-констрейнтами/индексом;
`downgrade` 0002→0001 удаляет таблицу и индекс. Ревизия обязана иметь рабочий
`downgrade()` (modules/ai-keys DoD, 07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0001_create_servers:0002_create_ai_keys"
_DOWN_STEP = "0002_create_ai_keys:0001_create_servers"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0002_creates_ai_keys_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "CREATE TABLE ai_keys" in sql
    # Ключевые колонки контракта присутствуют.
    for column in (
        "id",
        "name",
        "provider",
        "key_encrypted",
        "key_prefix",
        "key_last4",
        "check_status",
        "error_message",
        "last_checked_at",
        "created_at",
        "updated_at",
    ):
        assert column in sql
    # CHECK-констрейнты статуса/провайдера и индекс сортировки.
    assert "ck_ai_keys_provider" in sql
    assert "ck_ai_keys_check_status" in sql
    assert "ck_ai_keys_name_len" in sql
    assert "ix_ai_keys_created_at" in sql


def test_downgrade_0002_drops_ai_keys_table(capsys: pytest.CaptureFixture[str]) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out

    assert "DROP TABLE ai_keys" in sql
    assert "ix_ai_keys_created_at" in sql  # индекс тоже снимается
