"""Unit-тесты stateless `crm_state` для headless Outlook-OAuth (ADR-045 §3).

Проверяют `app.infra.mail_oauth_state`: round-trip encode→decode валидной подписи;
битая подпись → `CrmStateInvalid`; протухший `exp` → `CrmStateExpired`; битый формат/
base64/UUID/exp → `CrmStateInvalid`; граница `exp == now` (ещё валиден); нормативный
ПОРЯДОК проверок (подпись раньше exp: битая подпись у протухшего токена → Invalid, не
Expired). Модуль импортирует только `mail_oauth_state` (без FastAPI-приложения).
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from app.infra.mail_oauth_state import (
    CrmStateExpired,
    CrmStateInvalid,
    decode_crm_state,
    encode_crm_state,
)

_SECRET = "crm-state-shared-secret-xyz"
_NOW = 1_752_500_000
_TTL = 600


def _team() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


def _user() -> uuid.UUID:
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


# ------------------------------------------------------------------ round-trip
def test_encode_decode_roundtrip_full_payload() -> None:
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW + _TTL
    )
    state = decode_crm_state(secret=_SECRET, token=token, now=_NOW)
    assert state.team_id == _team()
    assert state.initiator_user_id == _user()
    assert state.exp == _NOW + _TTL


def test_encode_decode_roundtrip_null_team_and_initiator() -> None:
    """`team_id=null`/`initiator=null` (admin без команды) сериализуются и восстанавливаются."""
    token = encode_crm_state(secret=_SECRET, team_id=None, initiator_user_id=None, exp=_NOW + _TTL)
    state = decode_crm_state(secret=_SECRET, token=token, now=_NOW)
    assert state.team_id is None
    assert state.initiator_user_id is None
    assert state.exp == _NOW + _TTL


def test_token_is_compact_body_dot_sig() -> None:
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW + _TTL
    )
    body, _, sig = token.partition(".")
    assert body and sig
    assert token.count(".") == 1  # ровно один разделитель (base64url без `.`)


# ------------------------------------------------------- битая подпись → Invalid
def test_tampered_signature_raises_invalid() -> None:
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW + _TTL
    )
    body, _, sig = token.partition(".")
    tampered = f"{body}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}"
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=tampered, now=_NOW)


def test_wrong_secret_raises_invalid() -> None:
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW + _TTL
    )
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret="other-secret", token=token, now=_NOW)


# ---------------------------------------------------------- протухший exp → Expired
def test_expired_exp_raises_expired() -> None:
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW - 1
    )
    with pytest.raises(CrmStateExpired):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_exp_equal_now_still_valid_boundary() -> None:
    """`exp < now` истёк → ровно `exp == now` ещё валиден (граница, ADR-045 §3)."""
    token = encode_crm_state(secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW)
    state = decode_crm_state(secret=_SECRET, token=token, now=_NOW)
    assert state.exp == _NOW


# ------------------------------------ ПОРЯДОК: подпись проверяется раньше exp
def test_signature_checked_before_expiry() -> None:
    """Протухший токен с БИТОЙ подписью → Invalid (401), не Expired (410).

    Подпись — первый гейт (ADR-045 §3): битая подпись не должна раскрывать, что
    полезная нагрузка вдобавок протухла.
    """
    token = encode_crm_state(
        secret=_SECRET, team_id=_team(), initiator_user_id=_user(), exp=_NOW - 100
    )
    body, _, sig = token.partition(".")
    tampered = f"{body}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}"
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=tampered, now=_NOW)


# ------------------------------------------------- битый формат/base64/UUID/exp
@pytest.mark.parametrize(
    "token",
    [
        "no-dot-separator",
        "",
        ".",
        "onlybody.",
        ".onlysig",
        "a.b.c",  # три сегмента
    ],
)
def test_malformed_format_raises_invalid(token: str) -> None:
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_malformed_base64_body_raises_invalid() -> None:
    """Валидная по формату структура `<body>.<sig>`, но body не декодится в JSON."""
    body = "!!!not-base64!!!"
    from app.infra.mail_oauth_state import _sign  # каноническая подпись над body

    sig = _sign(secret=_SECRET, body=body)
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=f"{body}.{sig}", now=_NOW)


def _signed_from_payload(payload: object) -> str:
    """Собрать `<b64url(payload)>.<sig>` c ВАЛИДНОЙ подписью для теста разбора payload."""
    from app.infra.mail_oauth_state import _sign

    raw = json.dumps(payload).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{body}.{_sign(secret=_SECRET, body=body)}"


def test_bad_uuid_in_payload_raises_invalid() -> None:
    token = _signed_from_payload(
        {"team_id": "not-a-uuid", "initiator_user_id": None, "exp": _NOW + _TTL}
    )
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_missing_exp_raises_invalid() -> None:
    token = _signed_from_payload({"team_id": None, "initiator_user_id": None})
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_non_int_exp_raises_invalid() -> None:
    token = _signed_from_payload({"team_id": None, "initiator_user_id": None, "exp": "600"})
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_bool_exp_rejected_as_invalid() -> None:
    """`bool` — подтип `int` в Python; `exp=True` не должен приниматься за timestamp."""
    token = _signed_from_payload({"team_id": None, "initiator_user_id": None, "exp": True})
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)


def test_non_dict_payload_raises_invalid() -> None:
    token = _signed_from_payload([1, 2, 3])
    with pytest.raises(CrmStateInvalid):
        decode_crm_state(secret=_SECRET, token=token, now=_NOW)
