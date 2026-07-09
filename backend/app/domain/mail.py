"""Чистые доменные типы модуля «Почты» (modules/mail, ADR-038).

Без I/O, БД и сайд-эффектов. `MailScope` вынесен сюда (рядом с `SmsScope` в
`domain/sms.py`), чтобы разорвать цикл импорта `api/deps.py` ↔ сервис почты и
тестироваться qa напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MailScope:
    """Ролевая видимость почты по CRM-командам (ADR-038 §3, образец `SmsScope`).

    `sees_all_teams` — «видит все команды» ⇔ `is_superadmin` ИЛИ роль владеет полным
    каталогом прав (ADR-032/038). При True → доступ ко всем группам (`group_ids` не
    используется). Иначе — видимость по группам агрегатора (`teams.mail_group_id`)
    команд пользователя из `user_teams`; вне scope: чтение → пусто (анти-энумерация),
    мутация → 403. Пустой `group_ids` у не-админа → пустая страница без вызова
    внешнего API. Вычисляется фабрикой `get_mail_scope` в `api/deps.py`.
    """

    sees_all_teams: bool
    group_ids: frozenset[int]


__all__ = ["MailScope"]
