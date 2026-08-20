"""Тесты каталога прав и валидации `permissions` роли (ADR-021, app/domain/permissions.py).

Чистые функции без сети/БД. Нормативное правило (ADR-021 §1): валиден ⇔ каждый ключ —
известная страница каталога (кроме `users`), каждое действие ∈ CATALOG[page], без дублей.
Нарушение → PermissionsValidationError (сервис преобразует в 422 unprocessable).
"""

from __future__ import annotations

import pytest
from app.domain.permissions import (
    CATALOG,
    PermissionsValidationError,
    full_catalog_permissions,
    validate_permissions,
)


def test_full_catalog_permissions_matches_catalog_and_is_independent_copy() -> None:
    full = full_catalog_permissions()

    assert full == {page: list(actions) for page, actions in CATALOG.items()}
    # dashboard — только view; mail расширен (ADR-038 §4); ресурсные — полный набор.
    assert full["dashboard"] == ["view"]
    assert full["mail"] == ["view", "create", "edit", "delete", "sync", "tags"]
    assert full["servers"] == ["view", "create", "edit", "delete"]
    # Мутация результата не затрагивает исходный CATALOG (возвращается копия).
    full["servers"].append("hack")
    assert "hack" not in CATALOG["servers"]
    # Страница «Пользователи» вошла в каталог (Спринт B): без неё выдать доступ к
    # реестру не-админской роли было невозможно.
    assert full["users"] == ["view", "create", "edit", "delete", "assign_any"]
    assert list(CATALOG)[-1] == "users"
    assert CATALOG["broadcast"] == ("view", "send")


def test_validate_permissions_accepts_valid_subset_and_empty() -> None:
    validate_permissions({"dashboard": ["view"], "servers": ["view", "edit"]})
    validate_permissions({"servers": []})  # пустой массив — валиден (страница без доступа)
    validate_permissions({})  # пустой объект прав — валиден
    validate_permissions(full_catalog_permissions())  # полный каталог валиден


def test_validate_permissions_accepts_users_page() -> None:
    """Страница `users` — обычная строка матрицы (прежний запрет снят, Спринт B)."""
    validate_permissions({"users": ["view", "edit"]})

    with pytest.raises(PermissionsValidationError):
        validate_permissions({"users": ["send"]})  # действия — только из каталога


def test_validate_permissions_rejects_unknown_page() -> None:
    with pytest.raises(PermissionsValidationError):
        validate_permissions({"nope": ["view"]})


def test_validate_permissions_rejects_unknown_action_for_page() -> None:
    with pytest.raises(PermissionsValidationError):
        validate_permissions({"dashboard": ["create"]})  # dashboard допускает только view
    with pytest.raises(PermissionsValidationError):
        validate_permissions({"servers": ["view", "explode"]})


def test_validate_permissions_rejects_duplicate_action() -> None:
    with pytest.raises(PermissionsValidationError):
        validate_permissions({"servers": ["view", "view"]})
