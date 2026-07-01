"""Тест миграции Alembic ревизии 0003 (колонка `position` в servers и ai_keys).

Проверяется offline-рендер SQL (без подключения к БД, CI-safe, как в
test_ai_keys_migration): шаг 0002→0003 добавляет колонку `position` в обе таблицы,
создаёт индексы сортировки по `position` / `(provider, position)` и снимает старые
`created_at`-индексы; обратный шаг 0003→0002 зеркально откатывает изменения
(восстанавливает `created_at`-индексы, DROP COLUMN). Ревизия обязана иметь рабочий
`downgrade()` (03-data-model.md#миграция-0003_add_position, 07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_UP_STEP = "0002_create_ai_keys:0003_add_position"
_DOWN_STEP = "0003_add_position:0002_create_ai_keys"


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def test_upgrade_0003_adds_position_columns_and_indexes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(_alembic_config(), _UP_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # Колонка position добавляется в обе таблицы.
    assert "alter table servers add column position" in lower
    assert "alter table ai_keys add column position" in lower

    # Новые индексы сортировки создаются.
    assert "ix_servers_position" in sql
    assert "ix_ai_keys_provider_position" in sql
    # Индекс ai_keys — составной (provider, position).
    assert "(provider, position)" in lower

    # Старые created_at-индексы сортировки снимаются.
    assert "drop index" in lower
    assert "ix_servers_created_at" in sql
    assert "ix_ai_keys_created_at" in sql


def test_downgrade_0003_is_mirror_of_upgrade(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.downgrade(_alembic_config(), _DOWN_STEP, sql=True)
    sql = capsys.readouterr().out
    lower = sql.lower()

    # Колонка position снимается с обеих таблиц (DROP COLUMN).
    assert "alter table ai_keys drop column position" in lower
    assert "alter table servers drop column position" in lower

    # Новые индексы снимаются.
    assert "ix_ai_keys_provider_position" in sql
    assert "ix_servers_position" in sql

    # Прежние created_at-индексы восстанавливаются (DESC).
    assert "ix_ai_keys_created_at" in sql
    assert "ix_servers_created_at" in sql
    assert "created_at desc" in lower
