"""Схемы каталога прав (04-api.md#permissions, ADR-021).

Каталог «страница × действия» отдаётся UI для построения матрицы прав. Порядок
страниц = порядок строк матрицы (совпадает с `app/domain/permissions.py::CATALOG`).
Страница `users` в каталог не входит (гейтится require_admin).
"""

from __future__ import annotations

from pydantic import BaseModel


class PermissionCatalogPage(BaseModel):
    """Одна страница каталога: слаг + допустимые действия."""

    page: str
    actions: list[str]


class PermissionsCatalogResponse(BaseModel):
    """Ответ 200 GET /api/permissions/catalog."""

    pages: list[PermissionCatalogPage]
