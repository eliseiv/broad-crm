r"""teams: ретроактивный backfill лидеров для stale-строк (ADR-026)

Revision ID: 0016_backfill_team_leaders
Revises: 0015_user_first_login
Create Date: 2026-07-08

One-time **data-fix** для stale-состояния, возникшего ДО ввода авто-назначения
лидера (ADR-026 «Амендмент»,
03-data-model.md#миграция-0016_backfill_team_leaders-концепт). На проде существуют
CRM-команды с участниками, но `leader_id IS NULL` («Команда Ивана» — 2 участника,
«Команда Мухамеда» — 1 участник), из-за чего UI показывает «Без лидера» для команд,
где лидер обязан был назначиться.

Правило (нормативно, идемпотентно): для каждой команды с `leader_id IS NULL` И
непустым составом `user_teams` назначить лидером первого участника по
`(user_teams.created_at ASC, user_teams.user_id ASC)` — тот же детерминированный
порядок, что и авто-назначение в рантайме (ADR-026 §2, `get_first_member`). Команды
без участников остаются без лидера (легитимный кейс ADR-026 §3, не трогаются —
EXISTS-гард). Инвариант ADR-026 §5 (лидер ∈ участники) сохраняется. Идемпотентность
обеспечена предикатом `leader_id IS NULL`: повторный прогон не меняет уже
проставленных лидеров. Схема не меняется, рантайм-контракт/поведение эндпоинтов не
затрагиваются.

`downgrade()` — no-op: data-fix необратим по смыслу, прежнее (ошибочное)
`NULL`-состояние лидера не восстанавливается (07-deployment.md, исключение для
one-time data-fix / backfill-миграций).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_backfill_team_leaders"
down_revision: str | None = "0015_user_first_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Коррелированный UPDATE (03-data-model.md#миграция-0016_backfill_team_leaders-концепт).
    # Идемпотентен благодаря предикату `leader_id IS NULL`; EXISTS-гард оставляет
    # команды без участников нетронутыми. `updated_at = now()` бампится, чтобы правка
    # была видима как модификация записи (на сортировку списка команд не влияет).
    op.execute(
        """
        UPDATE teams t
        SET leader_id = (
                SELECT ut.user_id
                FROM user_teams ut
                WHERE ut.team_id = t.id
                ORDER BY ut.created_at ASC, ut.user_id ASC
                LIMIT 1
            ),
            updated_at = now()
        WHERE t.leader_id IS NULL
          AND EXISTS (SELECT 1 FROM user_teams ut2 WHERE ut2.team_id = t.id)
        """
    )


def downgrade() -> None:
    # No-op: one-time data-fix необратим по смыслу (ADR-026 «Амендмент»,
    # 07-deployment.md#откат-миграций-бд). Прежнее ошибочное `leader_id IS NULL`
    # состояние не является значимым и не восстанавливается. `downgrade -1`
    # выполняется успешно (no-op) и не ломает цепочку ревизий.
    pass
