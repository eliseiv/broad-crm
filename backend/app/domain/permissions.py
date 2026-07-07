"""Каталог прав RBAC (канон на сервере) и валидация `permissions` роли.

Единственный источник истины прав — константа `CATALOG` (ADR-021, 05-security.md).
Каталог «страница → допустимые действия». Страница «Пользователи» (`users`) в
каталог НЕ входит — управление пользователями/ролями гейтится `require_admin`.

Функции чистые (без сети/БД), тестируются qa напрямую. Валидация `permissions`
роли выполняется сервисом; нарушение → `422 unprocessable` (04-api.md).
"""

from __future__ import annotations

# Порядок ключей = порядок строк матрицы в UI (GET /api/permissions/catalog).
# Ключи `page` совпадают со слагами маршрутов SPA (`ai-keys` — с дефисом).
CATALOG: dict[str, tuple[str, ...]] = {
    "dashboard": ("view",),
    "servers": ("view", "create", "edit", "delete"),
    "ai-keys": ("view", "create", "edit", "delete"),
    "proxies": ("view", "create", "edit", "delete"),
    "backends": ("view", "create", "edit", "delete"),
    "mail": ("view",),
}

# Страница вне матрицы прав (гейтится require_admin, не через permissions).
_FORBIDDEN_PAGE = "users"


class PermissionsValidationError(ValueError):
    """Права роли не соответствуют каталогу (→ 422 unprocessable)."""


def full_catalog_permissions() -> dict[str, list[str]]:
    """Полный каталог как объект прав `{page: [action, ...]}`.

    Используется для принципала супер-админа (полный доступ) и GET /api/auth/me.
    """
    return {page: list(actions) for page, actions in CATALOG.items()}


def validate_permissions(permissions: dict[str, list[str]]) -> None:
    """Проверяет права роли против каталога (ADR-021, нормативно).

    Валиден ⇔ каждый ключ — известная страница каталога (кроме `users`), каждое
    действие ∈ `CATALOG[page]`, без дублей действий. Нарушение →
    `PermissionsValidationError` (сервис преобразует в 422 unprocessable).
    """
    for page, actions in permissions.items():
        if page == _FORBIDDEN_PAGE:
            raise PermissionsValidationError("Страница «Пользователи» не входит в матрицу прав")
        allowed = CATALOG.get(page)
        if allowed is None:
            raise PermissionsValidationError(f"Неизвестная страница: {page}")
        seen: set[str] = set()
        for action in actions:
            if action not in allowed:
                raise PermissionsValidationError(
                    f"Недопустимое действие «{action}» для страницы «{page}»"
                )
            if action in seen:
                raise PermissionsValidationError(
                    f"Дублирующееся действие «{action}» для страницы «{page}»"
                )
            seen.add(action)


__all__ = [
    "CATALOG",
    "PermissionsValidationError",
    "full_catalog_permissions",
    "validate_permissions",
]
