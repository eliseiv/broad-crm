"""Каналы per-channel scope команд (ADR-055 §1/§2.1, 05-security.md#per-channel-scope).

Канал — измерение доп-команд пользователя (`user_channel_teams.channel`). Каналов
ровно два и третий не планируется (потому `text` + CHECK в БД, а не PG-enum —
03-data-model.md). Литерал здесь — единственный источник имён каналов для кода
(сервисы/репозитории/миграция обязаны совпадать с CHECK `ck_user_channel_teams_channel`).
"""

from __future__ import annotations

from typing import Literal, get_args

Channel = Literal["mail", "sms"]

CHANNEL_MAIL: Channel = "mail"
CHANNEL_SMS: Channel = "sms"

# Оба канала (для нормализации инварианта §2.3 — она затрагивает КАЖДЫЙ канал).
CHANNELS: tuple[Channel, ...] = get_args(Channel)

__all__ = ["CHANNELS", "CHANNEL_MAIL", "CHANNEL_SMS", "Channel"]
