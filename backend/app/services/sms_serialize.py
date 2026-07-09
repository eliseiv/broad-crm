"""Сериализация ORM-моделей SMS в схемы контракта (04-api.md#sms, ADR-030).

Единая точка сборки `SmsTeamRef`/`SmsNumberRef`/`SmsNumberItem`/`SmsMessageItem`,
переиспользуемая сервисами ленты/номеров и teams-слоем (`GET /api/teams/{id}/numbers`).
Бейдж команды и пилюли `Логин/Приложение/Примечание` берутся из **текущего** номера
(`SmsPhoneNumber.team`), не из снимка `sms_inbound.team_id` (ADR-030 §6). Требует
загруженной relationship `SmsPhoneNumber.team` (selectinload на уровне репозитория).
"""

from __future__ import annotations

from app.models.sms_inbound import SmsInbound
from app.models.sms_phone_number import SmsPhoneNumber
from app.schemas.sms import (
    SmsMessageItem,
    SmsNumberItem,
    SmsNumberRef,
    SmsTeamRef,
    TeamNumberItem,
)


def to_team_ref(number: SmsPhoneNumber) -> SmsTeamRef | None:
    """Текущая команда номера или `None` (unassigned). Требует загруженного `team`."""
    if number.team is None:
        return None
    return SmsTeamRef(id=number.team.id, name=number.team.name)


def to_number_item(number: SmsPhoneNumber) -> SmsNumberItem:
    """Полный элемент номера (включая системный `label` и метки)."""
    return SmsNumberItem(
        id=number.id,
        phone_number=number.phone_number,
        label=number.label,
        team=to_team_ref(number),
        login=number.login,
        app_name=number.app_name,
        note=number.note,
        is_active=number.is_active,
        created_at=number.created_at,
        updated_at=number.updated_at,
    )


def to_team_number_item(number: SmsPhoneNumber) -> TeamNumberItem:
    """Элемент номера для `GET /api/teams/{id}/numbers` (ADR-030 §8 + ADR-034).

    `id`/`phone_number`/`team` + слабо-чувствительные `login`/`app_name` (ADR-034);
    БЕЗ `note`/`label` (остаются сужёнными под матрицу `sms:*`). Требует загруженного
    `team` (номера отфильтрованы по `team_id`, потому `team` всегда присутствует).
    """
    return TeamNumberItem(
        id=number.id,
        phone_number=number.phone_number,
        # `team` всегда присутствует: номера отфильтрованы по `team_id = {id}`.
        team=to_team_ref(number),
        login=number.login,
        app_name=number.app_name,
    )


def to_number_ref(number: SmsPhoneNumber) -> SmsNumberRef:
    """Ссылка на текущий номер для карточки сообщения (без `label`/меток активности)."""
    return SmsNumberRef(
        id=number.id,
        phone_number=number.phone_number,
        team=to_team_ref(number),
        login=number.login,
        app_name=number.app_name,
        note=number.note,
    )


def to_message_item(sms: SmsInbound, number: SmsPhoneNumber | None) -> SmsMessageItem:
    """Элемент ленты: SMS + текущий номер по `to_number` (`None`, если номер удалён)."""
    return SmsMessageItem(
        id=sms.id,
        from_number=sms.from_number,
        to_number=sms.to_number,
        body=sms.body,
        received_at=sms.received_at,
        number=to_number_ref(number) if number is not None else None,
    )


__all__ = [
    "to_message_item",
    "to_number_item",
    "to_number_ref",
    "to_team_number_item",
    "to_team_ref",
]
