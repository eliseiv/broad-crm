"""Unit-тесты backfill jsonb миграции 0037 (ADR-076 §4).

`_apply_backfill` — чистая функция: сид `admin` → полный каталог; extra-действия
страницам с полным CRUD; `broadcast` ролям, покрывающим прежний каталог.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REV = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0037_knowledge_bot_links.py"
_spec = importlib.util.spec_from_file_location("rev_0037_knowledge_bot_links", _REV)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_FULL_CATALOG = _mod._FULL_CATALOG
_apply_backfill = _mod._apply_backfill


def test_apply_backfill_admin_seed_gets_full_catalog() -> None:
    updated = _apply_backfill("admin", {"servers": ["view"], "mail": ["view"]})
    assert updated == _FULL_CATALOG
    assert updated["broadcast"] == ["view", "send"]
    assert list(updated)[-1] == "broadcast"


def test_apply_backfill_adds_documents_share_when_crud_complete() -> None:
    perms = {"documents": ["view", "create", "edit", "delete"]}
    updated = _apply_backfill("Оператор", perms)
    assert "share" in updated["documents"]
    assert "broadcast" not in updated


def test_apply_backfill_adds_mail_sms_extras_when_crud_complete() -> None:
    perms = {
        "mail": ["view", "create", "edit", "delete"],
        "sms": ["view", "edit", "delete"],
    }
    updated = _apply_backfill("Оператор", perms)
    assert set(updated["mail"]) >= {"sync", "tags"}
    assert set(updated["sms"]) >= {"transfer", "sync"}


def test_apply_backfill_skips_extras_when_crud_incomplete() -> None:
    perms = {"documents": ["view", "edit"]}
    updated = _apply_backfill("Оператор", perms)
    assert updated["documents"] == ["view", "edit"]
    assert "share" not in updated["documents"]


def test_apply_backfill_adds_broadcast_when_old_catalog_covered() -> None:
    old = {page: list(acts) for page, acts in _FULL_CATALOG.items() if page != "broadcast"}
    updated = _apply_backfill("Админ", old)
    assert updated["broadcast"] == ["view", "send"]
    for page, acts in old.items():
        assert set(acts) <= set(updated[page])


def test_apply_backfill_noop_when_already_complete() -> None:
    full = {page: list(acts) for page, acts in _FULL_CATALOG.items()}
    updated = _apply_backfill("Кастом", full)
    assert updated == full
