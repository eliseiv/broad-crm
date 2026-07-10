"""Integration S4 (ADR-044 §7): Mini App SSO `/api/mail/telegram/auth` — граница безопасности.

Проверка HMAC-подписи `initData` (граница безопасности, приоритет 1): валидная подпись →
CRM access-JWT; подделанная → 401 invalid_init_data; протухшая (TTL) → 401
init_data_expired; пустой initData → 400; пустой bot_token → отказ; username не сопоставлен
→ 403 mail_operator_not_provisioned. Критично: секретный ключ = HMAC(key=b"WebAppData",
msg=bot_token) — тест ломается при перестановке аргументов. Коды по `error.code`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from mail_s34_helpers import build_app, build_principal, client, mail_db, seed_role, seed_user

_BOT_TOKEN = "123456:MAIL-BOT-TEST-TOKEN"


def _secret_key_correct(bot_token: str) -> bytes:
    """Корректный порядок (ADR-044/Telegram): HMAC(key=b'WebAppData', msg=bot_token)."""
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _secret_key_swapped(bot_token: str) -> bytes:
    """НЕВЕРНЫЙ (переставленные аргументы): HMAC(key=bot_token, msg=b'WebAppData')."""
    return hmac.new(bot_token.encode("utf-8"), b"WebAppData", hashlib.sha256).digest()


def _build_init_data(
    *,
    bot_token: str,
    telegram_user_id: int,
    username: str | None,
    auth_date: int | None = None,
    secret_key_fn=_secret_key_correct,
) -> str:
    """Собрать валидный по структуре initData с подписью по заданной схеме ключа."""
    auth_date = int(time.time()) if auth_date is None else auth_date
    user: dict[str, object] = {"id": telegram_user_id, "first_name": "T"}
    if username is not None:
        user["username"] = username
    pairs = {"auth_date": str(auth_date), "user": json.dumps(user, ensure_ascii=False)}
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    digest = hmac.new(secret_key_fn(bot_token), data_check.encode("utf-8"), hashlib.sha256)
    pairs["hash"] = digest.hexdigest()
    return urlencode(pairs)


async def _enable_bot(monkeypatch: pytest.MonkeyPatch, *, token: str = _BOT_TOKEN) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_BOT_TOKEN", token)
    monkeypatch.setenv("MAIL_TG_INITDATA_TTL_SEC", "300")
    get_settings.cache_clear()


# --- Валидная подпись → JWT --------------------------------------------------
async def test_valid_signature_issues_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, username="katya", telegram="katetown")
            await s.commit()
        init = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=555, username="Katetown")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["telegram_user_id"] == 555
    assert body["linked"] is True


# --- КРИТИЧНО: перестановка аргументов ключа ломает подпись -------------------
async def test_key_arg_order_matters_swap_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, username="katya", telegram="katetown")
            await s.commit()
        # initData подписан ПЕРЕСТАВЛЕННЫМ ключом. Корректная реализация (key=WebAppData)
        # обязана его ОТВЕРГНУТЬ (иначе перестановка прошла бы незамеченной).
        bad = _build_init_data(
            bot_token=_BOT_TOKEN,
            telegram_user_id=555,
            username="Katetown",
            secret_key_fn=_secret_key_swapped,
        )
        # Контроль: тот же payload, правильный ключ → проходит.
        good = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=555, username="Katetown")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            bad_resp = await c.post("/api/mail/telegram/auth", json={"init_data": bad})
            good_resp = await c.post("/api/mail/telegram/auth", json={"init_data": good})
    assert bad_resp.status_code == 401
    assert bad_resp.json()["error"]["code"] == "invalid_init_data"
    assert good_resp.status_code == 200  # правильный ключ — единственный принимаемый


# --- Подделанная подпись → 401 ----------------------------------------------
async def test_forged_signature_401(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        init = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=1, username="x")
        tampered = init.replace("hash=", "hash=deadbeef")  # ломаем hash
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": tampered})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_init_data"


async def test_wrong_bot_token_signature_401(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        # Подписано ДРУГИМ токеном → HMAC не сойдётся с настроенным.
        init = _build_init_data(bot_token="other:token", telegram_user_id=1, username="x")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_init_data"


# --- Протухшая (TTL) → 401 init_data_expired --------------------------------
async def test_expired_init_data_401(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, telegram="katetown")
            await s.commit()
        old = int(time.time()) - 3600  # час назад, TTL=300
        init = _build_init_data(
            bot_token=_BOT_TOKEN, telegram_user_id=555, username="Katetown", auth_date=old
        )
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "init_data_expired"


# --- Пустой initData → 400 ---------------------------------------------------
async def test_empty_init_data_400(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": "   "})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


# --- Пустой bot_token → отказ (не пропуск) -----------------------------------
async def test_empty_bot_token_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch, token="")  # бот выключен
    async with mail_db() as sm:
        # Даже структурно-валидный initData (подписан пустым токеном) не должен пройти.
        init = _build_init_data(bot_token="", telegram_user_id=1, username="x")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    # Пустой bot_token → verify_init_data возвращает "malformed" → 401 (не 200).
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_init_data"


# --- Username не сопоставлен → 403 mail_operator_not_provisioned --------------
async def test_unprovisioned_username_403(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        # В БД нет пользователя с telegram = "ghost".
        init = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=999, username="ghost")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mail_operator_not_provisioned"


# --- Регистронезависимый резолв username (реальные данные прода) --------------
@pytest.mark.parametrize(
    ("tg_username", "crm_telegram"),
    [("Katetown", "katetown"), ("Anellie_sss", "anellie_sss"), ("Loveink", "loveink")],
)
async def test_case_insensitive_username_resolve(
    monkeypatch: pytest.MonkeyPatch, tg_username: str, crm_telegram: str
) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, telegram=crm_telegram)
            await s.commit()
        init = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=42, username=tg_username)
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    # `@Katetown` (Telegram) ↔ `katetown` (CRM) — совпадают только регистронезависимо.
    assert resp.status_code == 200, resp.text


# --- Ведущий `@` в username нормализуется ------------------------------------
async def test_leading_at_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, telegram="loveink")
            await s.commit()
        init = _build_init_data(bot_token=_BOT_TOKEN, telegram_user_id=42, username="@Loveink")
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/auth", json={"init_data": init})
    assert resp.status_code == 200, resp.text
