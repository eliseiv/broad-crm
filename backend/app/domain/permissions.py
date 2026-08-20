"""Каталог прав RBAC (канон на сервере) и валидация `permissions` роли.

Единственный источник истины прав — константа `CATALOG` (ADR-021, 05-security.md).
Каталог «страница → допустимые действия». Страница «Пользователи» (`users`) входит
в каталог со Спринта B: без этого выдать доступ к реестру не-админской роли было
невозможно. Эскалацию сдерживает не отсутствие страницы в матрице, а инвариант
`UserService` (роль шире собственной не назначается, admin-level не редактируется).

Функции чистые (без сети/БД), тестируются qa напрямую. Валидация `permissions`
роли выполняется сервисом; нарушение → `422 unprocessable` (04-api.md).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

# Порядок ключей = порядок строк матрицы в UI (GET /api/permissions/catalog).
# Ключи `page` совпадают со слагами маршрутов SPA (`ai-keys` — с дефисом).
CATALOG: dict[str, tuple[str, ...]] = {
    "dashboard": ("view",),
    "servers": ("view", "create", "edit", "delete"),
    "ai-keys": ("view", "create", "edit", "delete"),
    "proxies": ("view", "create", "edit", "delete"),
    "backends": ("view", "create", "edit", "delete"),
    # «Пользователи бэков»: view — просмотр списка/карточки; edit — admin-операции
    # (начисление токенов / выдача подписки) через CRM Admin API бэка.
    "backend-users": ("view", "edit"),
    # «Продукты и тарифы» (ADR-072 §2): view — каталог продуктов/тарифов бэка;
    # edit — правка количества токенов. НЕ алиас `backend-users:edit`: то — операция
    # над одним пользователем, это — глобальное изменение для всех будущих покупок.
    "backend-economics": ("view", "edit"),
    "mail": ("view", "create", "edit", "delete", "sync", "tags"),
    "sms": ("view", "edit", "transfer", "sync", "delete"),
    "roles": ("view", "create", "edit", "delete"),
    "teams": ("view", "create", "edit", "delete"),
    "documents": ("view", "create", "edit", "delete", "share"),
    "broadcast": ("view", "send"),
    # «Пользователи» (реестр сотрудников CRM). Со Спринта B страница управляется
    # матрицей, а не только `require_admin`: без этого роль-не-админ невозможно
    # пустить на страницу вообще. Security-инвариант эскалации сохранён в
    # `UserService`: непривилегированный актор не может назначить роль с правами
    # шире собственного union и не может трогать admin-level пользователя.
    "users": ("view", "create", "edit", "delete"),
}


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


def union_permissions(sources: Iterable[dict[str, list[str]]]) -> dict[str, list[str]]:
    """Объединение прав нескольких ролей (ADR-079 §2, нормативно).

    Права пользователя с несколькими ролями = **union по страницам** с дедупом действий.
    Порядок страниц — порядок каталога `CATALOG` (устойчивый вывод для UI и сравнений),
    страницы вне каталога (исторический jsonb) идут после в порядке первого появления;
    порядок действий внутри страницы — порядок `CATALOG[page]`, для неизвестных действий
    — порядок появления. Пустой вход → `{}`.
    """
    collected: dict[str, list[str]] = {}
    for permissions in sources:
        for page, actions in (permissions or {}).items():
            bucket = collected.setdefault(page, [])
            for action in actions:
                if action not in bucket:
                    bucket.append(action)

    ordered: dict[str, list[str]] = {}
    for page in CATALOG:
        if page in collected:
            ordered[page] = _order_actions(page, collected[page])
    for page, actions in collected.items():
        if page not in ordered:
            ordered[page] = _order_actions(page, actions)
    return ordered


def _order_actions(page: str, actions: list[str]) -> list[str]:
    """Действия страницы в порядке каталога; неизвестные — следом, в порядке появления."""
    catalog = CATALOG.get(page, ())
    known = [action for action in catalog if action in actions]
    unknown = [action for action in actions if action not in catalog]
    return known + unknown


# Значение информационного JWT-claim `role` у пользователя БЕЗ ролей (заведён прямым SQL
# в обход API — «минимум одна роль» держит сервис). Claim обязан быть непустым
# (`decode_access_token` отвергает пустой как легаси-токен), а гейтить им нельзя ничем:
# права грузятся из БД по `uid` на каждый запрос (ADR-079 §3).
NO_ROLE_CLAIM = "none"


def primary_role_name(roles: Sequence[Any]) -> str:
    """Имя **первой** роли пользователя — информационный JWT-claim `role` (ADR-079 §3).

    «Первая» = первая в уже упорядоченном наборе `User.roles` (`user_roles.created_at
    ASC, role_id ASC`). Тот же порядок несут `role_id`/`role_name` внешнего контура бота
    (§6). Ролей нет → `NO_ROLE_CLAIM`: claim обязан быть непустым, но ничего не гейтит.

    Тип элемента — `Any` намеренно: домен не импортирует ORM-модели, а структурный
    протокол здесь неприменим (`Role.name` объявлен как `Mapped[str]`, и mypy без
    SQLAlchemy-плагина сравнил бы его с `str` буквально). `str(...)` гарантирует, что
    `Any` наружу не утекает.
    """
    return str(roles[0].name) if roles else NO_ROLE_CLAIM


class AdminLevelPrincipal(Protocol):
    """Минимальный контракт принципала для `is_admin_level` (без импорта deps).

    Поля — `@property`: совместимо с frozen `Principal` (read-only атрибуты).
    """

    @property
    def is_superadmin(self) -> bool: ...

    @property
    def roles(self) -> Sequence[str]: ...

    @property
    def permissions(self) -> dict[str, list[str]]: ...


def is_admin_level(principal: AdminLevelPrincipal) -> bool:
    """Admin-уровень: супер-админ, сид `admin` среди ролей или полный каталог по union.

    ADR-076 §4 в редакции ADR-079 §2: предикат **монотонен по добавлению ролей** —
    пользователь с ролью `admin` и второй узкой ролью остаётся админом при любом порядке
    `created_at` (`"admin" ∈ roles`, а не «главная роль == admin»); `permissions` актора
    уже несут union прав всех его ролей.
    """
    return (
        principal.is_superadmin
        or "admin" in principal.roles
        or permissions_subset(full_catalog_permissions(), principal.permissions)
    )


def permissions_subset(child: dict[str, list[str]], parent: dict[str, list[str]]) -> bool:
    """True ⇔ `child` — подмножество `parent` (subset-инвариант эскалации, ADR-022 §4а).

    Для каждой страницы набор действий `child` должен быть подмножеством действий
    `parent` по той же странице. Пустой `child` — тривиально подмножество. Используется
    сервисом ролей: не-супер-админ/не-`admin` не может выдать роли права сверх своих.
    """
    return all(set(actions) <= set(parent.get(page, [])) for page, actions in child.items())


__all__ = [
    "CATALOG",
    "NO_ROLE_CLAIM",
    "PermissionsValidationError",
    "full_catalog_permissions",
    "is_admin_level",
    "primary_role_name",
    "permissions_subset",
    "union_permissions",
    "validate_permissions",
]
