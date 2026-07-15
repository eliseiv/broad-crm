r"""mail_accounts: hard-delete осиротевших зеркальных строк 73/85/140/145 (ADR-058 §2)

Revision ID: 0028_mail_dedup_orphan_cleanup
Revises: 0027_user_channel_teams
Create Date: 2026-07-15

Ремедиация сирот после dedup-merge демонтажа mail-агрегатора (ADR-058). Агрегатор в
фазе C схлопнул 4 легаси-дубля email и удалил проигравшие `mail_accounts` (id
**73, 85, 140, 145**); survivor'ы (18/126/28/71) остались. CRM зеркалит каталог
ящиков агрегатора, используя agg-id как собственный PK (`mail_account.py:38`,
`autoincrement=False`), поэтому осиротевшие зеркальные строки 73/85/140/145 повисли в
CRM: агрегатор их больше не пушит, авто-реконсиляции `mail_accounts` нет — вечные
«ящики-призраки» в каталоге команд и в `mailbox_count` (ADR-058 §1).

**Одна операция — `DELETE FROM mail_accounts WHERE id IN (73, 85, 140, 145)`.** Каждая
нисходящая FK на `mail_accounts.id` — `ON DELETE CASCADE` (ADR-058 §3), поэтому БД
транзитивно и атомарно снесёт всех потомков: `mail_messages`, `mail_sent_messages` и
далее по `message_id` — `mail_telegram_notifications`, `mail_message_tags`,
`mail_message_reads`. Ручной порядок удаления не нужен; ни один `SET NULL`-FK не
указывает внутрь удаляемого поддерева ⇒ висячих ссылок не возникает.

**Удаление строго по id** (НЕ по email): в CRM нет уникальности по email, проигравший и
survivor делят один адрес — `DELETE … WHERE email = …` снёс бы и survivor'а (ADR-058
§4). Survivor'ы 18/126/28/71 имеют другие id ⇒ под условие не попадают.

**Идемпотентность по построению:** свежая/тестовая БД никогда не имела этих id ⇒
`DELETE` там no-op (0 строк), повторный прогон безопасен. DDL нет ⇒ тестовая схема
`create_all` не затрагивается (ADR-058 §2).

Идентификатор ревизии — `0028_mail_dedup_orphan_cleanup` (30 символов), укладывается в
предел `alembic_version.version_num varchar(32)` (03-data-model.md#1-revision-id).

`downgrade()` — **no-op (НЕОБРАТИМО)**: hard-delete исторического архива проигравших
ящиков восстановлению не подлежит (agg-id свободны в агрегаторе, данных для воссоздания
нет). No-op здесь САНКЦИОНИРОВАН карв-аутом 03-data-model.md §2 (необратимые чисто-DML
миграции: DDL нет, восстановление физически невозможно) — прецедент того же класса
`0016_backfill_team_leaders`. Fallback отката — восстановление из pre-deploy снапшота
(ADR-058 §5.1), а не `downgrade -1`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_mail_dedup_orphan_cleanup"
down_revision: str | None = "0027_user_channel_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Единственная операция (ADR-058 §2). Каскад (ADR-058 §3, все нисходящие FK —
    # ON DELETE CASCADE) снесёт mail_messages/mail_sent_messages и транзитивных потомков
    # (mail_telegram_notifications/mail_message_tags/mail_message_reads) на уровне БД.
    # Идемпотентна: на БД без этих id — no-op (0 строк удалено), не падает.
    op.execute("DELETE FROM mail_accounts WHERE id IN (73, 85, 140, 145)")


def downgrade() -> None:
    # No-op: НЕОБРАТИМО (ADR-058 §2). Удалённый исторический архив проигравших ящиков
    # (письма/reply/теги/прочитанность/история Telegram-уведомлений) восстановлению не
    # подлежит — agg-id уже свободны в агрегаторе, данных для воссоздания нет.
    # Санкционировано карв-аутом 03-data-model.md §2 (необратимые чисто-DML миграции:
    # DDL нет, восстановление физически невозможно), прецедент 0016_backfill_team_leaders.
    # Fallback отката — восстановление из pre-deploy снапшота (ADR-058 §5.1), не `downgrade -1`.
    # `downgrade -1` завершается успешно (no-op) и не ломает цепочку ревизий.
    pass
