"""Проверка статического `X-API-Key` внешнего read-only контура документов (ADR-060 §1).

Порядок проверок (нормативно, образец `mail_ingest`, 04-api.md#external-documents):
пустой `DOCUMENTS_API_KEY` → **503 documents_external_not_configured** (приёмник выключен)
→ неверный/отсутствующий `X-API-Key` → **401 not_authenticated**. Сравнение входящего
ключа — constant-time `hmac.compare_digest`.

Read-only GET **без тела** ⇒ HMAC-подпись тела (как у mail push) не нужна — статического
ключа по HTTPS достаточно (ADR-060 §1, осознанное отличие от mail-приёмников). Ключ
читается только из env (`app.config.Settings`), в логи/ответы не попадает. Модуль
намеренно НЕ зависит от `app.api.deps` (нижний слой, как `mail_push_security`).
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import documents_external_not_configured, not_authenticated

_API_KEY_HEADER = "X-API-Key"


async def require_documents_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Гейт внешнего контура документов: 503 (пустой env-ключ) → 401 (неверный `X-API-Key`).

    Не возвращает принципала: ключ безролевой, машина видит всё (ADR-060 §2). Присутствие
    зависимости на роутере — единственный эффект (raise до тела эндпоинта).
    """
    configured = settings.documents_api_key
    if not configured:
        raise documents_external_not_configured()
    provided = request.headers.get(_API_KEY_HEADER) or ""
    # Сравниваем БАЙТЫ, а не str: Starlette декодирует заголовки как latin-1, поэтому
    # `provided` может нести не-ASCII символы (байты 128..255), а `hmac.compare_digest`
    # на не-ASCII `str` бросает TypeError (→ 500 вместо 401). На bytes он принимает любые
    # байты и остаётся constant-time; равенство utf-8-байт эквивалентно равенству строк.
    if not hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8")):
        raise not_authenticated()


DocumentsApiKeyDep = Annotated[None, Depends(require_documents_api_key)]
