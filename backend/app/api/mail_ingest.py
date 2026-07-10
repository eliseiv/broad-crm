"""Push-приёмник агрегатор→CRM (ADR-044 §3). Машинные эндпоинты: HMAC, без JWT, CSRF-exempt.

`POST /api/mail/ingest` — приём батча новых писем; `POST /api/mail/mailbox-status` —
зеркало статуса синка ящика. Аутентификация — HMAC-SHA256 (тело-связанная, §3):
подпись считается над **сырыми байтами** тела ДО JSON-парсинга. Порядок проверок:
пустой секрет → 503; невалидная подпись/skew → 401; битое тело/батч → 400. Секреты и
сырое тело не логируются.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.api.deps import MailIngestServiceDep, SettingsDep
from app.errors import (
    mail_ingest_not_configured,
    not_authenticated,
    oauth_state_expired,
    validation_error,
)
from app.infra.mail_oauth_state import (
    CrmStateExpired,
    CrmStateInvalid,
    decode_crm_state,
)
from app.infra.mail_push_security import verify_mail_push_signature
from app.logging import get_logger
from app.schemas.mail_ingest import (
    MailboxStatusRequest,
    MailboxStatusResponse,
    MailIngestRequest,
    MailIngestResponse,
    MailOauthIngestRequest,
    MailOauthIngestResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/mail", tags=["mail-ingest"])

_SIGNATURE_HEADER = "X-Mail-Signature"
_TIMESTAMP_HEADER = "X-Mail-Timestamp"


async def _authenticated_raw_body(request: Request, settings: SettingsDep) -> bytes:
    """Читает сырое тело и проверяет HMAC push-контракта (ADR-044 §3).

    Пустой `MAIL_PUSH_SECRET` → 503 mail_ingest_not_configured; невалидная подпись или
    протухший timestamp → 401 not_authenticated. Возвращает сырые байты тела (подпись
    считается над ними, не над ре-сериализованным JSON).
    """
    secret = settings.mail_push_secret
    if not secret:
        raise mail_ingest_not_configured()

    raw_body = await request.body()
    valid = verify_mail_push_signature(
        secret=secret,
        signature_header=request.headers.get(_SIGNATURE_HEADER),
        timestamp_header=request.headers.get(_TIMESTAMP_HEADER),
        raw_body=raw_body,
        max_skew_sec=settings.mail_push_max_skew_sec,
    )
    if not valid:
        raise not_authenticated()
    return raw_body


@router.post("/ingest", response_model=MailIngestResponse)
async def ingest(
    request: Request,
    service: MailIngestServiceDep,
    settings: SettingsDep,
) -> MailIngestResponse:
    """Приём батча новых писем от агрегатора (HMAC, идемпотентно; ADR-044 §3).

    Порядок: 503 (пустой секрет) → 401 (подпись/skew) → 400 (битое тело/батч вне
    лимита). Ответ `200 {accepted, duplicate, unknown_mailbox}`.
    """
    raw_body = await _authenticated_raw_body(request, settings)
    try:
        payload = MailIngestRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise validation_error("Невалидное тело запроса") from exc
    return await service.ingest(payload)


@router.post("/mailbox-status", response_model=MailboxStatusResponse)
async def mailbox_status(
    request: Request,
    service: MailIngestServiceDep,
    settings: SettingsDep,
) -> MailboxStatusResponse:
    """Зеркало статуса синка ящика (HMAC; ADR-044 §3).

    Порядок проверок как у `/ingest`. Неизвестный `mail_account_id` → `200 {updated:false}`
    (no-op, аномалия TD-041). Идемпотентность down-алерта — через `down_alert_sent_at`.
    """
    raw_body = await _authenticated_raw_body(request, settings)
    try:
        payload = MailboxStatusRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise validation_error("Невалидное тело запроса") from exc
    updated = await service.apply_mailbox_status(payload)
    return MailboxStatusResponse(updated=updated)


@router.post("/oauth/ingest", response_model=MailOauthIngestResponse)
async def oauth_ingest(
    request: Request,
    service: MailIngestServiceDep,
    settings: SettingsDep,
) -> MailOauthIngestResponse:
    """Уведомление агрегатора о созданном/переподключённом Outlook-ящике (ADR-045 §3).

    Аутентификация — HMAC (`MAIL_PUSH_SECRET`) над сырым телом ДО парсинга, как `/ingest`:
    пустой секрет → 503; подпись/skew → 401. Битое тело → 400. Затем `crm_state`: битый
    HMAC/формат → 401 not_authenticated; протух (`exp` в прошлом) → 410 oauth_state_expired.
    Upsert каталожной записи (id, email, display_name, team_id из `crm_state`, is_active),
    `ON CONFLICT (id) DO UPDATE` детерминированно перезаписывает `team_id`. Идемпотентно.
    """
    raw_body = await _authenticated_raw_body(request, settings)
    try:
        payload = MailOauthIngestRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise validation_error("Невалидное тело запроса") from exc
    try:
        state = decode_crm_state(secret=settings.mail_push_secret, token=payload.crm_state)
    except CrmStateExpired as exc:
        raise oauth_state_expired() from exc
    except CrmStateInvalid as exc:
        raise not_authenticated() from exc
    await service.link_oauth_account(
        payload, team_id=state.team_id, initiator_user_id=state.initiator_user_id
    )
    return MailOauthIngestResponse(ok=True)
