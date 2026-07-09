"""Схема reveal-эндпоинтов секретов (04-api.md#reveal-секретов-по-требованию-adr-035).

Единая схема ответа on-demand reveal секретов сущностей (SSH-пароль сервера,
пароль прокси, полный ключ ИИ-ключа). Секрет присутствует ТОЛЬКО в этой схеме —
никогда в общих list/detail-ответах (ADR-035).
"""

from __future__ import annotations

from pydantic import BaseModel


class SecretRevealResponse(BaseModel):
    """Ответ 200 reveal-эндпоинтов секретов (ADR-035).

    `value` — расшифрованный секрет (plaintext). Расшифровка — `decrypt_secret`
    в памяти обработчика; значение не логируется и не сохраняется. Ответ обязан
    нести заголовок `Cache-Control: no-store` (выставляется в роутере).
    """

    value: str
