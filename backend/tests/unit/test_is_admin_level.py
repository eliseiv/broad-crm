"""Unit-тесты `is_admin_level` (ADR-076 §4, docs/06-testing-strategy.md).

Чистая функция: супер-админ / сид `admin` / полный каталог → true;
кириллическое «Админ» без extra-действий или без `broadcast` → false.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.permissions import full_catalog_permissions, is_admin_level


def _principal(
    *,
    is_superadmin: bool = False,
    role: str = "Оператор",
    permissions: dict[str, list[str]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        is_superadmin=is_superadmin,
        role=role,
        permissions={} if permissions is None else permissions,
    )


def test_is_admin_level_superadmin_true() -> None:
    assert is_admin_level(_principal(is_superadmin=True, role="anything", permissions={})) is True


def test_is_admin_level_seed_admin_role_true() -> None:
    assert is_admin_level(_principal(role="admin", permissions={})) is True


def test_is_admin_level_full_catalog_true() -> None:
    assert is_admin_level(_principal(role="Админ", permissions=full_catalog_permissions())) is True


def test_is_admin_level_cyrillic_admin_missing_documents_share_false() -> None:
    perms = full_catalog_permissions()
    perms["documents"] = ["view", "create", "edit", "delete"]
    assert is_admin_level(_principal(role="Админ", permissions=perms)) is False


def test_is_admin_level_cyrillic_admin_missing_broadcast_false() -> None:
    perms = full_catalog_permissions()
    del perms["broadcast"]
    assert is_admin_level(_principal(role="Админ", permissions=perms)) is False


def test_is_admin_level_truncated_role_false() -> None:
    assert is_admin_level(_principal(role="Оператор", permissions={"servers": ["view"]})) is False
