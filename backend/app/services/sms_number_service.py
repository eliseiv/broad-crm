"""Сервис реестра SMS-номеров (modules/sms, 04-api.md#sms, ADR-030).

Список/правка полей/перенос/удаление номеров. Видимость — по **текущей**
принадлежности номера команде (`sms_phone_numbers.team_id`). Read/list вне scope →
пустой результат; мутации вне scope → 403 forbidden (ADR-030 §6). `label` — системное
поле, через это API не редактируется. Presence-семантика затирания `login`/`app_name`/
`note` — по `model_fields_set` (04-api.md#patch-apismsnumbersid).
"""

from __future__ import annotations

from app.domain.sms import SmsScope
from app.errors import forbidden, sms_number_not_found, sms_team_not_found
from app.logging import get_logger
from app.models.sms_phone_number import SmsPhoneNumber
from app.repositories.sms_number_repository import SmsNumberRepository
from app.repositories.team_repository import TeamRepository
from app.schemas.sms import (
    SmsNumberItem,
    SmsNumbersResponse,
    SmsNumberTransferRequest,
    SmsNumberUpdateRequest,
)
from app.services.sms_serialize import to_number_item

logger = get_logger(__name__)

_EDITABLE_FIELDS = ("login", "app_name", "note")


class SmsNumberService:
    """CRUD над номерами с ролевой видимостью (scope) и presence-семантикой правки."""

    def __init__(self, *, numbers: SmsNumberRepository, teams: TeamRepository) -> None:
        self._numbers = numbers
        self._teams = teams

    async def list_numbers(self, scope: SmsScope) -> SmsNumbersResponse:
        """Список видимых номеров: admin-уровень — все; иначе — номера своих команд."""
        if scope.sees_all_teams:
            rows = await self._numbers.list_all()
        elif not scope.team_ids:
            rows = []
        else:
            rows = await self._numbers.list_by_teams(scope.team_ids)
        return SmsNumbersResponse(numbers=[to_number_item(n) for n in rows])

    async def update_number(
        self,
        scope: SmsScope,
        number_id: int,
        payload: SmsNumberUpdateRequest,
    ) -> SmsNumberItem:
        """Правка `login`/`app_name`/`note` (presence-семантика). Вне scope → 403.

        Ключ присутствует, значение (после `strip`) непустое → установить; пустое/
        пробельное или `null` → затереть (`NULL`); ключ отсутствует → не менять.
        """
        number = await self._numbers.get_by_id(number_id)
        if number is None:
            raise sms_number_not_found()
        self._require_mutation_scope(scope, number)

        fields_set = payload.model_fields_set
        for field in _EDITABLE_FIELDS:
            if field not in fields_set:
                continue
            raw = getattr(payload, field)
            normalized = raw.strip() if isinstance(raw, str) else None
            setattr(number, field, normalized or None)

        await self._numbers.session.commit()
        reloaded = await self._numbers.get_by_id(number_id)
        assert reloaded is not None  # только что обновлён в этой сессии
        logger.info("sms_number_updated", number_id=number_id)
        return to_number_item(reloaded)

    async def transfer_number(
        self,
        scope: SmsScope,
        number_id: int,
        payload: SmsNumberTransferRequest,
    ) -> SmsNumberItem:
        """Назначить/переназначить/снять команду. Вне scope → 403; чужой team → 404.

        `team_id=null` → снять команду (unassigned); иначе — привязать к существующей
        команде (несуществующая → 404 sms_team_not_found).
        """
        number = await self._numbers.get_by_id(number_id)
        if number is None:
            raise sms_number_not_found()
        self._require_mutation_scope(scope, number)

        if payload.team_id is not None and not await self._teams.get_existing_ids(
            {payload.team_id}
        ):
            raise sms_team_not_found()

        await self._numbers.set_team(number_id, payload.team_id)
        await self._numbers.session.commit()
        reloaded = await self._numbers.get_by_id(number_id)
        assert reloaded is not None
        logger.info(
            "sms_number_transferred",
            number_id=number_id,
            team_id=str(payload.team_id) if payload.team_id else None,
        )
        return to_number_item(reloaded)

    async def delete_number(self, scope: SmsScope, number_id: int) -> None:
        """Hard-delete номера (история SMS сохраняется). Вне scope → 403; нет → 404."""
        number = await self._numbers.get_by_id(number_id)
        if number is None:
            raise sms_number_not_found()
        self._require_mutation_scope(scope, number)

        await self._numbers.delete_by_id(number_id)
        await self._numbers.session.commit()
        logger.info("sms_number_deleted", number_id=number_id)

    @staticmethod
    def _require_mutation_scope(scope: SmsScope, number: SmsPhoneNumber) -> None:
        """Мутация вне scope → 403 forbidden (unassigned-номер не-админу недоступен)."""
        if scope.sees_all_teams:
            return
        if number.team_id is None or number.team_id not in scope.team_ids:
            raise forbidden()
