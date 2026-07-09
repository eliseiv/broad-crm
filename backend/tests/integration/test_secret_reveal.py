"""Integration-тесты on-demand reveal секретов (ADR-035, 04-api.md, 05-security.md).

Три эндпоинта (`GET /api/servers/{id}/ssh-password`, `/api/proxies/{id}/password`,
`/api/ai-keys/{id}/key`) поверх реального Postgres + реальные сервисы. Проверяют
(security-критично): 200 `SecretRevealResponse{value}` == расшифрованный секрет;
заголовок `Cache-Control: no-store`; 401 без JWT; 403 без `<page>:edit`; 404
`<resource>_not_found`; прокси без пароля → 404 `secret_not_set`; значение секрета
НЕ в схемах list/summary И НЕ в логах; аудит-событие `secret_revealed` без значения
(actor/resource_type/resource_id/at). Секрет в тестах не хардкодится в фикстурах list.
"""

from __future__ import annotations

import uuid

import structlog
from app.api import deps
from app.infra.crypto import encrypt_secret
from app.models.ai_key import AiKey
from app.models.proxy import Proxy
from app.models.server import Server
from sms_helpers import build_app, build_principal, client, sms_db
from sqlalchemy.ext.asyncio import AsyncSession

_SSH_SECRET = "S3cret-ssh-PROBE-777"
_PROXY_SECRET = "pr0xy-PROBE-888"
_AIKEY_SECRET = "sk-aikey-PROBE-999-fulltoken"


async def _seed_server(session: AsyncSession) -> Server:
    server = Server(
        name="srv",
        ip="10.9.9.9",
        ssh_user="root",
        ssh_password_encrypted=encrypt_secret(_SSH_SECRET),
    )
    session.add(server)
    await session.flush()
    return server


async def _seed_proxy(session: AsyncSession, *, with_password: bool = True) -> Proxy:
    proxy = Proxy(
        name="px",
        proxy_type="http",
        host="1.2.3.4",
        port=8080,
        username="u",
        password_encrypted=encrypt_secret(_PROXY_SECRET) if with_password else None,
    )
    session.add(proxy)
    await session.flush()
    return proxy


async def _seed_ai_key(session: AsyncSession) -> AiKey:
    ai_key = AiKey(
        name="key",
        provider="openai",
        key_encrypted=encrypt_secret(_AIKEY_SECRET),
        key_prefix="sk-ai",
        key_last4="t999",
    )
    session.add(ai_key)
    await session.flush()
    return ai_key


# --- happy path: значение + no-store + аудит без значения --------------------


async def test_reveal_server_ssh_password_returns_decrypted_and_no_store() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            server_id = server.id
            await s.commit()
        app = build_app(sm, build_principal())
        with structlog.testing.capture_logs() as logs:
            async with client(app) as c:
                resp = await c.get(f"/api/servers/{server_id}/ssh-password")

    assert resp.status_code == 200
    assert resp.json() == {"value": _SSH_SECRET}
    assert resp.headers["cache-control"] == "no-store"
    # Аудит-событие есть, содержит поля, но НЕ значение секрета.
    audit = [e for e in logs if e.get("event") == "secret_revealed"]
    assert len(audit) == 1
    ev = audit[0]
    assert ev["resource_type"] == "server"
    assert ev["resource_id"] == str(server_id)
    assert ev["actor"] == "tester"
    assert "at" in ev
    serialized = str(logs)
    assert _SSH_SECRET not in serialized  # значение не логируется


async def test_reveal_proxy_password_returns_decrypted() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            proxy = await _seed_proxy(s)
            proxy_id = proxy.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get(f"/api/proxies/{proxy_id}/password")
    assert resp.status_code == 200
    assert resp.json() == {"value": _PROXY_SECRET}
    assert resp.headers["cache-control"] == "no-store"


async def test_reveal_ai_key_returns_full_key() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            ai_key = await _seed_ai_key(s)
            ai_key_id = ai_key.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get(f"/api/ai-keys/{ai_key_id}/key")
    assert resp.status_code == 200
    assert resp.json() == {"value": _AIKEY_SECRET}
    assert resp.headers["cache-control"] == "no-store"


# --- proxy без пароля → 404 secret_not_set ----------------------------------


async def test_reveal_proxy_without_password_is_404_secret_not_set() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            proxy = await _seed_proxy(s, with_password=False)
            proxy_id = proxy.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get(f"/api/proxies/{proxy_id}/password")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "secret_not_set"


# --- 404 <resource>_not_found -----------------------------------------------


async def test_reveal_missing_resources_are_404() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        rid = uuid.uuid4()
        async with client(app) as c:
            srv = await c.get(f"/api/servers/{rid}/ssh-password")
            px = await c.get(f"/api/proxies/{rid}/password")
            ak = await c.get(f"/api/ai-keys/{rid}/key")
    assert srv.status_code == 404 and srv.json()["error"]["code"] == "server_not_found"
    assert px.status_code == 404 and px.json()["error"]["code"] == "proxy_not_found"
    assert ak.status_code == 404 and ak.json()["error"]["code"] == "ai_key_not_found"


# --- 401 без JWT ------------------------------------------------------------


async def test_reveal_without_jwt_is_401() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            server_id = server.id
            await s.commit()
        app = build_app(sm, build_principal())
        # Снимаем override принципала → работает реальный get_current_principal (нет Bearer).
        del app.dependency_overrides[deps.get_current_principal]
        async with client(app) as c:
            resp = await c.get(f"/api/servers/{server_id}/ssh-password")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


# --- 403 без <page>:edit ----------------------------------------------------


async def test_reveal_without_edit_permission_is_403() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            proxy = await _seed_proxy(s)
            ai_key = await _seed_ai_key(s)
            ids = (server.id, proxy.id, ai_key.id)
            await s.commit()
        server_id, proxy_id, ai_key_id = ids
        # Только view-права на всех страницах — edit нет → reveal запрещён.
        viewer = build_principal(
            is_superadmin=False,
            permissions={"servers": ["view"], "proxies": ["view"], "ai-keys": ["view"]},
        )
        app = build_app(sm, viewer)
        async with client(app) as c:
            srv = await c.get(f"/api/servers/{server_id}/ssh-password")
            px = await c.get(f"/api/proxies/{proxy_id}/password")
            ak = await c.get(f"/api/ai-keys/{ai_key_id}/key")
    for resp in (srv, px, ak):
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"


async def test_reveal_allowed_with_edit_permission() -> None:
    # Не-супер-админ с <page>:edit → reveal разрешён (гейт — edit, не только супер-админ).
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            server_id = server.id
            await s.commit()
        editor = build_principal(is_superadmin=False, permissions={"servers": ["view", "edit"]})
        app = build_app(sm, editor)
        async with client(app) as c:
            resp = await c.get(f"/api/servers/{server_id}/ssh-password")
    assert resp.status_code == 200
    assert resp.json()["value"] == _SSH_SECRET


# --- секрет не в схемах обычных ответов --------------------------------------


def test_plaintext_secret_not_exposed_in_list_schemas() -> None:
    # Структурная гарантия: обычные схемы list/summary/created не имеют plaintext-поля
    # секрета (значение доступно ТОЛЬКО через reveal-эндпоинт, ADR-035).
    from app.schemas.ai_key import AiKeyListItem
    from app.schemas.proxy import ProxyListItem
    from app.schemas.server import ServerCreatedResponse, ServerListItem, ServerSummaryResponse

    for schema, forbidden_fields in (
        (ServerListItem, ("ssh_password", "ssh_password_encrypted", "password")),
        (ServerSummaryResponse, ("ssh_password", "ssh_password_encrypted", "password")),
        (ServerCreatedResponse, ("ssh_password", "ssh_password_encrypted", "password")),
        (ProxyListItem, ("password", "password_encrypted")),
        (AiKeyListItem, ("key", "key_encrypted")),
    ):
        fields = set(schema.model_fields)
        for f in forbidden_fields:
            assert f not in fields, f"{schema.__name__} не должна раскрывать {f}"
