"""Роутер каталога прав (04-api.md#permissions, ADR-021). Гейт require_admin."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RequireAdmin
from app.domain.permissions import CATALOG
from app.schemas.permissions import PermissionCatalogPage, PermissionsCatalogResponse

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("/catalog", response_model=PermissionsCatalogResponse)
async def get_catalog(_admin: RequireAdmin) -> PermissionsCatalogResponse:
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
