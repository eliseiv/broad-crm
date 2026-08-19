"""Unit-тесты `is_admin_level` (ADR-076 §4 в редакции ADR-079 §2, docs/06-testing-strategy.md).

Чистая функция: супер-админ / сид `admin` / полный каталог → true;
кириллическое «Админ» без extra-действий или без `broadcast` → false.
Ключевой кейс M2M-ролей: ДВЕ частичные роли, union которых == полный каталог → true,
хотя каждая по отдельности → false (предикат считает по union, а не поролево).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.permissions import (
    CATALOG,
    full_catalog_permissions,
    is_admin_level,
    union_permissions,
)


def _principal(
    *,
    is_superadmin: bool = False,
    role: str = "Оператор",
    permissions: dict[str, list[str]] | None = None,
) -> SimpleNamespace:
    # ADR-079 §2: предикат читает НАБОР ролей (`"admin" ∈ roles`), а не одну роль.
    return SimpleNamespace(
        is_superadmin=is_superadmin,
        roles=(role,),
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


# --- ADR-079 §2: union ДВУХ частичных ролей ---------------------------------


def _split_catalog() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Разбивает каталог на две ЧАСТИЧНЫЕ роли, union которых == полный каталог.

    Страницы раздаются через одну (чётные — первой роли, нечётные — второй), а
    «documents» дополнительно расщепляется по ДЕЙСТВИЯМ: `share` попадает только в
    первую роль, остальные действия — только во вторую. Так ни одна роль не является
    полным каталогом ни по страницам, ни по действиям.
    """
    left: dict[str, list[str]] = {}
    right: dict[str, list[str]] = {}
    for index, (page, actions) in enumerate(CATALOG.items()):
        if page == "documents":
            left[page] = ["share"]
            right[page] = [a for a in actions if a != "share"]
            continue
        (left if index % 2 == 0 else right)[page] = list(actions)
    return left, right


def _multi_role_principal(
    *, permissions: dict[str, list[str]], roles: tuple[str, ...] = ("Кадры", "Техподдержка")
) -> SimpleNamespace:
    """Принципал с НЕСКОЛЬКИМИ ролями (ни одна не `admin`) и уже посчитанным union прав."""
    return SimpleNamespace(is_superadmin=False, roles=roles, permissions=permissions)


def test_is_admin_level_two_partial_roles_union_full_catalog_true() -> None:
    """Две частичные роли, union которых == каталог → true (по отдельности — false)."""
    left, right = _split_catalog()
    union = union_permissions([left, right])
    assert union == full_catalog_permissions()  # разбиение действительно полное

    assert is_admin_level(_multi_role_principal(permissions=union)) is True
    # Каждая роль в одиночку — не admin-уровень.
    only_left = _multi_role_principal(permissions=left, roles=("Кадры",))
    only_right = _multi_role_principal(permissions=right, roles=("Техподдержка",))
    assert is_admin_level(only_left) is False
    assert is_admin_level(only_right) is False


def test_is_admin_level_two_partial_roles_without_documents_share_false() -> None:
    """Тот же союз без `documents.share` → false: union ≠ полный каталог, ролей это не меняет."""
    left, right = _split_catalog()
    left = {page: actions for page, actions in left.items() if page != "documents"}
    union = union_permissions([left, right])

    assert "share" not in union["documents"]
    assert is_admin_level(_multi_role_principal(permissions=union)) is False
