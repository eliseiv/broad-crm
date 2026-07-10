"""HMAC-подписанный stateless `crm_state` для headless Outlook-OAuth (ADR-045 §3).

`crm_state` — непрозрачный CRM-токен, переносимый агрегатором **без интерпретации**
(ADR-045 §1). Кодирует `{team_id, initiator_user_id, exp}` и подписан HMAC-SHA256 общим
секретом `MAIL_PUSH_SECRET`. Канон подписи — как `mail_push_security` (граница ADR-044
§3): HMAC-SHA256 hex, ключ `secret.encode("utf-8")`, сравнение `hmac.compare_digest`
(constant-time), `mac_input` строится **побайтно** (f-string над `bytes` запрещён).

Формат токена: ``<b64url(payload_json)>.<hex_sig>``, где подпись считается над ASCII-
байтами сегмента ``b64url(payload_json)`` (тем же, что передаётся) — без ре-сериализации
JSON. Stateless: CRM не заводит таблицу для `state`; анти-replay/идемпотентность на
`/oauth/ingest` обеспечивает upsert по id ящика (ADR-045 §3, «Отдельная CRM-таблица…
отклонена»). `team_id` защищён подписью — оператору не виден и не подделываем. Секрет
не логируется.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CrmOauthState:
    """Декодированный `crm_state`: CRM-контекст OAuth-flow (ADR-045 §3)."""

    team_id: uuid.UUID | None
    initiator_user_id: uuid.UUID | None
    exp: int


class CrmStateInvalid(Exception):
    """Битый `crm_state`: неверный формат/HMAC/payload (роутер → 401 not_authenticated)."""


class CrmStateExpired(Exception):
    """`crm_state.exp` в прошлом: консент завершён после TTL (роутер → 410 oauth_state_expired)."""


def _sign(*, secret: str, body: str) -> str:
    """Каноническая HMAC-SHA256-подпись (hex) над ASCII-байтами сегмента `body`.

    Ключ — `secret.encode("utf-8")`; `mac_input` = `body.encode("ascii")` (побайтно,
    без f-string над `bytes`) — идентично канону `mail_push_security`.
    """
    return hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()


def _b64url_encode(raw: bytes) -> str:
    """base64url без паддинга (URL-safe алфавит, без `.` → пригоден как сегмент токена)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    """Обратное к `_b64url_encode`: восстанавливает паддинг и декодит (может бросить)."""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode_crm_state(
    *,
    secret: str,
    team_id: uuid.UUID | None,
    initiator_user_id: uuid.UUID | None,
    exp: int,
) -> str:
    """Собрать подписанный `crm_state` (ADR-045 §3).

    `exp` — абсолютный unix-timestamp истечения (сек). `team_id`/`initiator_user_id`
    сериализуются как строки UUID (или `null`). Возвращает компактный токен
    ``<b64url>.<sig>`` (≤512 симв. по контракту агрегатора, ADR-045 §2).
    """
    payload = {
        "team_id": str(team_id) if team_id is not None else None,
        "initiator_user_id": str(initiator_user_id) if initiator_user_id is not None else None,
        "exp": exp,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(secret=secret, body=body)}"


def _parse_optional_uuid(value: object) -> uuid.UUID | None:
    """`None` → None; строка UUID → UUID; иначе → CrmStateInvalid."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise CrmStateInvalid("Некорректный UUID в crm_state")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise CrmStateInvalid("Некорректный UUID в crm_state") from exc


def decode_crm_state(*, secret: str, token: str, now: int | None = None) -> CrmOauthState:
    """Верифицировать подпись и вернуть CRM-контекст (ADR-045 §3).

    Порядок (нормативный): сначала HMAC-подпись (битая → CrmStateInvalid → 401), затем
    разбор payload, в последнюю очередь `exp` (в прошлом → CrmStateExpired → 410). `exp`
    сравнивается как `exp < now` (ровно `now` — ещё валиден). Подпись — constant-time.
    """
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CrmStateInvalid("Некорректный формат crm_state")
    body, provided_sig = parts

    expected_sig = _sign(secret=secret, body=body)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise CrmStateInvalid("Неверная подпись crm_state")

    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, TypeError) as exc:
        raise CrmStateInvalid("Некорректный payload crm_state") from exc
    if not isinstance(payload, dict):
        raise CrmStateInvalid("Некорректный payload crm_state")

    exp = payload.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise CrmStateInvalid("Некорректный exp в crm_state")

    team_id = _parse_optional_uuid(payload.get("team_id"))
    initiator_user_id = _parse_optional_uuid(payload.get("initiator_user_id"))

    now_ts = int(time.time()) if now is None else now
    if exp < now_ts:
        raise CrmStateExpired("crm_state истёк")

    return CrmOauthState(team_id=team_id, initiator_user_id=initiator_user_id, exp=exp)


__all__ = [
    "CrmOauthState",
    "CrmStateExpired",
    "CrmStateInvalid",
    "decode_crm_state",
    "encode_crm_state",
]
