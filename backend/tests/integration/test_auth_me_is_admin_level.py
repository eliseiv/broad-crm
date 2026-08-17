"""GET /api/auth/me += is_admin_level (ADR-078, docs/06-testing-strategy.md).

Поле обязательное, bool. Тот же предикат, что require_admin:
супер-админ / сид admin / роль «Админ»+full_catalog → true;
без documents.share / без broadcast / урезанная роль → false.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.api import deps
from app.config import get_settings
from app.domain.permissions import full_catalog_permissions
from app.schemas.auth import MeResponse
from conftest import RbacFakeDb, make_principal
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError


def _app_with_principal(**kwargs: Any) -> Any:
    from app.main import create_app

    app = create_app(get_settings())
    db = RbacFakeDb()
    principal = make_principal(**kwargs)
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    app.dependency_overrides[deps.get_session] = lambda: db.session
    return app


async def _get_me(**kwargs: Any) -> Any:
    app = _app_with_principal(**kwargs)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get("/api/auth/me")


def _assert_required_bool(body: dict[str, Any], expected: bool) -> None:
    assert "is_admin_level" in body
    assert isinstance(body["is_admin_level"], bool)
    assert body["is_admin_level"] is expected
    MeResponse.model_validate(body)


@pytest.mark.asyncio
async def test_me_is_admin_level_superadmin_true() -> None:
    resp = await _get_me(is_superadmin=True, role="anything", permissions={})
    assert resp.status_code == 200
    _assert_required_bool(resp.json(), True)


@pytest.mark.asyncio
async def test_me_is_admin_level_seed_admin_true() -> None:
    resp = await _get_me(is_superadmin=False, role="admin", permissions={"servers": ["view"]})
    assert resp.status_code == 200
    body = resp.json()
    _assert_required_bool(body, True)
    assert body["role"] == "admin"
    assert body["is_superadmin"] is False


@pytest.mark.asyncio
async def test_me_is_admin_level_cyrillic_admin_full_catalog_true() -> None:
    resp = await _get_me(
        is_superadmin=False,
        role="Админ",
        permissions=full_catalog_permissions(),
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_required_bool(body, True)
    assert body["role"] == "Админ"
    assert body["is_superadmin"] is False


@pytest.mark.asyncio
async def test_me_is_admin_level_cyrillic_admin_missing_documents_share_false() -> None:
    perms = full_catalog_permissions()
    perms["documents"] = ["view", "create", "edit", "delete"]
    resp = await _get_me(is_superadmin=False, role="Админ", permissions=perms)
    assert resp.status_code == 200
    _assert_required_bool(resp.json(), False)


@pytest.mark.asyncio
async def test_me_is_admin_level_cyrillic_admin_missing_broadcast_false() -> None:
    perms = full_catalog_permissions()
    del perms["broadcast"]
    resp = await _get_me(is_superadmin=False, role="Админ", permissions=perms)
    assert resp.status_code == 200
    _assert_required_bool(resp.json(), False)


@pytest.mark.asyncio
async def test_me_is_admin_level_truncated_role_false() -> None:
    resp = await _get_me(
        is_superadmin=False,
        role="Оператор",
        permissions={"servers": ["view"], "documents": ["view", "edit"]},
    )
    assert resp.status_code == 200
    _assert_required_bool(resp.json(), False)


def test_me_response_schema_requires_is_admin_level() -> None:
    payload = {
        "username": "ivan",
        "role": "Админ",
        "is_superadmin": False,
        "permissions": {},
        "sees_all_sms_teams": False,
        "sees_all_mail_teams": False,
        "mail_teams": [],
        "sms_teams": [],
        "mail_includes_unassigned": False,
        "sms_includes_unassigned": False,
    }
    with pytest.raises(ValidationError) as exc:
        MeResponse.model_validate(payload)
    assert "is_admin_level" in str(exc.value)
