"""Репозиторий каталога ящиков `mail_accounts` (ADR-044 §2/§3).

Каталог — источник истины привязки ящик↔команда. Используется приёмом push'а
(проверка существования ящика: unknown → skip, §3) и status-каналом (зеркалирование
статуса синка + guarded reset `down_alert_sent_at` на re-enable).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.mail import build_display_name, parse_display_name
from app.models.mail_account import MailAccount
from app.models.team import Team


class MailAccountRepository:
    """CRUD/чтение `mail_accounts` для приёма push'а, status-канала и каталога (§4)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, account_id: int) -> MailAccount | None:
        """Ящик по id (= id в агрегаторе) или None."""
        return await self._session.get(MailAccount, account_id)

    async def get_many_with_team(
        self, account_ids: Iterable[int]
    ) -> dict[int, tuple[MailAccount, str | None]]:
        """Ящики + **имя команды-владельца** одним `LEFT JOIN teams` (ADR-056 §1).

        Возвращает `{id: (account, team_name)}`; `team_name is None` — ящик без команды
        (`team_id IS NULL`) ⇒ именно **LEFT** JOIN: INNER молча потерял бы письма
        бесхозных ящиков. **N+1 запрещён:** это ТОТ ЖЕ батч-запрос по странице ленты, что
        и прежний `get_many` (плюс соединение по PK `teams`), а не запрос на письмо.
        Заменил `get_many`: ящик в ленте теперь всегда несёт контекст (`number`/`app_name`/
        `team`), поэтому отдельная выборка «без команды» не нужна.
        """
        ids = list(account_ids)
        if not ids:
            return {}
        stmt = (
            select(MailAccount, Team.name)
            .outerjoin(Team, Team.id == MailAccount.team_id)
            .where(MailAccount.id.in_(ids))
        )
        rows = (await self._session.execute(stmt)).all()
        return {account.id: (account, team_name) for account, team_name in rows}

    async def list_scoped(
        self,
        *,
        team_ids: frozenset[uuid.UUID] | None,
        includes_unassigned: bool,
        is_active: bool | None,
    ) -> list[MailAccount]:
        """Каталог ящиков по **единому предикату scope** (GET /mailboxes, ADR-055 §3).

        `team_ids=None` — без сужения (admin-уровень: все ящики, включая бесхозные).
        Иначе предикат: `team_id IN team_ids` **OR** (`includes_unassigned` **AND**
        `team_id IS NULL`) — прямое `team_id IN team_ids` без ветки флага = дефект
        (иначе носитель «Без команды» не увидел бы ящики, которые вправе править).
        Пустой набор И `includes_unassigned=false` → пустой список без запроса
        (анти-энумерация). `is_active` (опц.) — доп. фильтр активности.
        """
        stmt = select(MailAccount)
        if team_ids is not None:
            predicate = self._scope_predicate(team_ids, includes_unassigned)
            if predicate is None:
                return []
            stmt = stmt.where(predicate)
        if is_active is not None:
            stmt = stmt.where(MailAccount.is_active.is_(is_active))
        stmt = stmt.order_by(MailAccount.email.asc(), MailAccount.id.asc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_by_team(self, team_id: uuid.UUID) -> list[MailAccount]:
        """Ящики одной команды (detail-панель /teams, ADR-044 §4)."""
        stmt = (
            select(MailAccount)
            .where(MailAccount.team_id == team_id)
            .order_by(MailAccount.email.asc(), MailAccount.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_by_teams(self, team_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{team_id: число ящиков}` для набора команд (агрегат `mailbox_count`, ADR-048 §1).

        Батч для `GET /api/teams` (без N+1): один `GROUP BY team_id` по индексу
        `ix_mail_accounts_team_id`. Команды без ящиков в результат не попадают —
        вызывающий подставляет `0`.
        """
        ids = list(team_ids)
        if not ids:
            return {}
        stmt = (
            select(MailAccount.team_id, func.count())
            .where(MailAccount.team_id.in_(ids))
            .group_by(MailAccount.team_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {tid: int(cnt) for tid, cnt in rows if tid is not None}

    async def count_by_team(self, team_id: uuid.UUID) -> int:
        """Число ящиков одной команды (агрегат `mailbox_count` для тел 201/200, ADR-048 §1)."""
        stmt = select(func.count()).select_from(MailAccount).where(MailAccount.team_id == team_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def ids_in_scope(
        self, team_ids: Iterable[uuid.UUID], *, includes_unassigned: bool
    ) -> set[int]:
        """id ящиков, проходящих **единый предикат scope** (фильтр ленты, ADR-055 §3).

        `team_id IN team_ids` **OR** (`includes_unassigned` **AND** `team_id IS NULL`).
        Пустой набор команд И `includes_unassigned=false` → пустое множество (вызывающий
        отдаёт пустую страницу без выборки писем).
        """
        predicate = self._scope_predicate(frozenset(team_ids), includes_unassigned)
        if predicate is None:
            return set()
        stmt = select(MailAccount.id).where(predicate)
        return set((await self._session.execute(stmt)).scalars().all())

    async def ids_by_team(self, team_id: uuid.UUID) -> set[int]:
        """Множество id ящиков одной команды (фильтр ленты по команде, ADR-044 §7)."""
        stmt = select(MailAccount.id).where(MailAccount.team_id == team_id)
        return set((await self._session.execute(stmt)).scalars().all())

    async def ids_unassigned(self) -> set[int]:
        """id ящиков **без команды** (`team_id IS NULL`) — фильтр ленты `no_team=true`
        (ADR-055 §5.3). Пересечение со scope выполняет вызывающий: у не-админа без
        `includes_unassigned` результат пересечения пуст → пустая страница, не 403."""
        stmt = select(MailAccount.id).where(MailAccount.team_id.is_(None))
        return set((await self._session.execute(stmt)).scalars().all())

    @staticmethod
    def _scope_predicate(
        team_ids: frozenset[uuid.UUID], includes_unassigned: bool
    ) -> ColumnElement[bool] | None:
        """SQL-выражение единого предиката scope (ADR-055 §3) или `None`, если scope пуст."""
        clauses: list[ColumnElement[bool]] = []
        if team_ids:
            clauses.append(MailAccount.team_id.in_(team_ids))
        if includes_unassigned:
            clauses.append(MailAccount.team_id.is_(None))
        if not clauses:
            return None
        return or_(*clauses)

    async def create(
        self,
        *,
        account_id: int,
        email: str,
        number: str | None,
        app_name: str | None,
        display_name: str | None,
        team_id: uuid.UUID | None,
        is_active: bool,
    ) -> MailAccount:
        """Вставить строку каталога после создания ящика в агрегаторе (§4).

        `account_id` — id, присвоенный агрегатором (единый ключ связи писем push'а).
        Креды в CRM не хранятся (шифрование в агрегаторе). `display_name` — производное
        от `number`/`app_name`, считает сервис (ADR-047 §3.3).
        """
        account = MailAccount(
            id=account_id,
            email=email,
            number=number,
            app_name=app_name,
            display_name=display_name,
            team_id=team_id,
            is_active=is_active,
        )
        self._session.add(account)
        await self._session.flush()
        return account

    async def upsert_catalog(
        self,
        *,
        account_id: int,
        email: str,
        display_name: str | None,
        team_id: uuid.UUID | None,
        is_active: bool,
    ) -> None:
        """Upsert каталожной записи Outlook-ящика (ADR-045 §3, ADR-047 §3.7). Идемпотентно.

        `id = account_id` (агрегаторский int). Токены/креды в CRM не хранятся.

        **INSERT (новый OAuth-ящик):** `display_name` агрегатора **разбирается** в
        `number`/`app_name` тем же правилом, что и backfill-миграция `0024`
        (`parse_display_name`, ADR-047 §3.1), а `display_name` сохраняется **канонически
        производным** (`build_display_name` от разобранных частей) — инвариант §3.3
        («`display_name` — производное») держится и на этом пути записи каталога.

        **ON CONFLICT (id) DO UPDATE (re-consent):** поля имени — `number`, `app_name`,
        `display_name` — **НЕ перезаписываются** (ADR-047 §3.7 п.2): после создания CRM —
        источник истины имени (админ мог править «Номер»/«Приложение» через `PATCH`), а
        агрегатор лишь эхо-возвращает то, что CRM ему отдала; перезапись затёрла бы правку.
        Обновляются `email`, `is_active`, `team_id` (детерминированно из `crm_state`, §3).
        Поля синка (`last_synced_at`/`last_sync_error`/`consecutive_failures`/
        `down_alert_sent_at`) НЕ трогаются — их ведёт status-канал.
        """
        number, app_name = parse_display_name(display_name)
        stmt = pg_insert(MailAccount).values(
            id=account_id,
            email=email,
            number=number,
            app_name=app_name,
            display_name=build_display_name(number, app_name),
            team_id=team_id,
            is_active=is_active,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MailAccount.id],
            set_={
                "email": email,
                "team_id": team_id,
                "is_active": is_active,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)

    async def delete(self, account_id: int) -> None:
        """Удалить строку каталога (CASCADE удалит письма/reply ящика, §4)."""
        await self._session.execute(sa_delete(MailAccount).where(MailAccount.id == account_id))

    async def existing_ids(self, account_ids: Iterable[int]) -> set[int]:
        """Подмножество `account_ids`, реально существующих в каталоге (одним запросом).

        Приём push'а различает unknown_mailbox (нет в каталоге → skip) от duplicate.
        """
        ids = list(account_ids)
        if not ids:
            return set()
        stmt = select(MailAccount.id).where(MailAccount.id.in_(ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def apply_sync_status(
        self,
        account: MailAccount,
        *,
        is_active: bool,
        last_synced_at: datetime | None,
        last_sync_error: str | None,
        consecutive_failures: int,
    ) -> None:
        """Зеркалирует статус синка из агрегатора (status-канал §3).

        Идемпотентность mailbox-down алерта «ровно один на переход» — через
        `down_alert_sent_at`: при переходе `is_active` true→false штамп НЕ трогается
        (остаётся NULL → проход C §6 разошлёт алерт один раз, guarded set). При
        переходе false→true — сброс `down_alert_sent_at=NULL` (готов к следующему
        падению). Прочие случаи (без перехода) штамп не меняют.
        """
        was_active = account.is_active
        account.is_active = is_active
        account.last_synced_at = last_synced_at
        account.last_sync_error = last_sync_error
        account.consecutive_failures = consecutive_failures
        if not was_active and is_active:
            # re-enable: сброс, чтобы следующее падение отработало штатно.
            account.down_alert_sent_at = None
        await self._session.flush()
