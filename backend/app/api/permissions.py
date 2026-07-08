"""Роутер каталога прав (04-api.md#permissions, ADR-021/022). Гейт require("roles","view").

Со Спринта A гейт `require_admin` заменён на `require("roles","view")` (ADR-022): каталог
нужен редактору роли (носитель `roles:view`) для построения матрицы.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Principal, require
from app.domain.permissions import CATALOG
from app.schemas.permissions import PermissionCatalogPage, PermissionsCatalogResponse

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("/catalog", response_model=PermissionsCatalogResponse)
async def get_catalog(
    _principal: Annotated[Principal, Depends(require("roles", "view"))],
) -> PermissionsCatalogResponse:
    """Канонический каталог «страница × действия» для построения UI-матрицы.

    Порядок страниц = порядок `CATALOG` (совпадает со строками матрицы). Страница
    `users` в каталог не входит.
    """
    return PermissionsCatalogResponse(
        pages=[
            PermissionCatalogPage(page=page, actions=list(actions))
            for page, actions in CATALOG.items()
        ]
    )
