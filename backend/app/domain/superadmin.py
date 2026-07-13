"""Константы системной строки-якоря супер-админа (ADR-051 §1.1, 03-data-model.md).

Якорь — **идентичность и только она**: FK-цель личного состояния консольного
супер-админа (`ADMIN_USER`/`ADMIN_PASSWORD` из `.env`), у которого нет обычной строки
в `users`. Он НЕ учётка, НЕ источник прав, НЕ способ входа и НЕ канал доставки.

- `SUPERADMIN_USER_ID` фиксирован ⇒ `Principal.user_id` супер-админа подставляется
  **без запроса в БД** (fallback-инвариант ADR-008 сохранён), а смена
  `ADMIN_USER`/`ADMIN_PASSWORD` не теряет его личное состояние (ADR-051 §1.7).
- `SUPERADMIN_USERNAME` зарезервирован **by construction**: символ `@` отвергается
  правилом `username` (`app/domain/identity.py`) ⇒ такое имя невозможно создать или
  занять через API (422). DB-CHECK `ck_users_username` его пропускает. Наружу имя не
  отдаётся: `Principal.username` супер-админа — `ADMIN_USER` из claim `sub`.
"""

from __future__ import annotations

import uuid

SUPERADMIN_USER_ID: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SUPERADMIN_USERNAME: str = "superadmin@system"

__all__ = ["SUPERADMIN_USERNAME", "SUPERADMIN_USER_ID"]
