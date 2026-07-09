"""Сервис ленты входящих SMS (modules/sms, 04-api.md#get-apismsmessages, ADR-030).

Порт донорского `messages_service` + keyset-курсор. Видимость — по **текущей**
принадлежности номера команде (`sms_phone_numbers.team_id`), не по снимку
`sms_inbound.team_id` (ADR-030 §6). Фильтры `number_id`/`team_id` комбинируемы
(AND-пересечение множеств видимых `to_number`); вне scope → пустая страница
(анти-энумерация). Курсор битый → 400 invalid_cursor; `limit` вне [1,100] → 400
invalid_limit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sms import SmsCursorError, SmsScope, decode_cursor, encode_cursor
from app.errors import invalid_cursor, invalid_limit
from app.repositories.sms_inbound_repository import SmsInboundRepository
from app.repositories.sms_number_repository import SmsNumberRepository
from app.schemas.sms import SmsMessagesResponse
from app.services.sms_serialize import to_message_item

_MIN_LIMIT = 1
_MAX_LIMIT = 100


class SmsMessageService:
    """Read-only лента SMS с ролевой видимостью и keyset-пагинацией."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_messages(
        self,
        *,
        scope: SmsScope,
        number_id: int | None,
        team_id: uuid.UUID | None,
        cursor: str | None,
        limit: int,
    ) -> SmsMessagesResponse:
        """Отдаёт страницу SMS по правилам видимости и keyset-пагинации.

        :raises AppError: 400 invalid_limit (limit вне [1,100]) / 400 invalid_cursor
            (битый курсор).
        """
        if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
            raise invalid_limit()
        decoded = None
        if cursor:
            try:
                decoded = decode_cursor(cursor)
            except SmsCursorError as exc:
                raise invalid_cursor() from exc

        to_numbers = await self._resolve_visible_phones(
            scope=scope, number_id=number_id, team_id=team_id
        )

        inbound = SmsInboundRepository(self._session)
        rows = await inbound.list_inbound(to_numbers=to_numbers, cursor=decoded, limit=limit + 1)

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor: str | None = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(last.received_at, last.id)

        # Резолв ТЕКУЩЕГО номера по to_number (для бейджа/пилюль карточки, ADR-030 §6).
        numbers_repo = SmsNumberRepository(self._session)
        distinct_phones = {row.to_number for row in page}
        found = await numbers_repo.find_many_by_phones(distinct_phones)
        by_phone = {number.phone_number: number for number in found}

        messages = [to_message_item(row, by_phone.get(row.to_number)) for row in page]
        return SmsMessagesResponse(messages=messages, next_cursor=next_cursor)

    async def _resolve_visible_phones(
        self,
        *,
        scope: SmsScope,
        number_id: int | None,
        team_id: uuid.UUID | None,
    ) -> list[str] | None:
        """Множество видимых `to_number` (AND-пересечение scope + фильтров).

        `None` — без ограничения (супер-админ без фильтров → все SMS). `[]` — пустой
        результат (вне scope / несуществующий фильтр — анти-энумерация).
        """
        numbers = SmsNumberRepository(self._session)
        constraints: list[set[str]] = []

        # Базовый scope не-админа: номера его команд (по текущей принадлежности).
        if not scope.is_super_admin:
            if not scope.team_ids:
                return []
            scoped = await numbers.list_by_teams(scope.team_ids)
            constraints.append({n.phone_number for n in scoped})

        # Фильтр по номеру: несуществующий → пустой результат.
        if number_id is not None:
            number = await numbers.get_by_id(number_id)
            if number is None:
                return []
            constraints.append({number.phone_number})

        # Фильтр по команде: номера этой команды (несуществующая/пустая → пусто).
        if team_id is not None:
            team_numbers = await numbers.list_by_team(team_id)
            constraints.append({n.phone_number for n in team_numbers})

        if not constraints:
            return None
        visible = constraints[0]
        for extra in constraints[1:]:
            visible &= extra
        return list(visible)
