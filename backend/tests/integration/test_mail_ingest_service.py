"""Integration-тесты сервиса приёма push'а `MailIngestService` (ADR-044 §3).

Проверяют бизнес-логику приёма поверх реального Postgres (без FastAPI-app): идемпотентность
(`ON CONFLICT` → duplicate++, дубля нет), неизвестный ящик (unknown_mailbox++, батч НЕ
отклоняется), `notified_at IS NULL` у новых писем, best-effort теги (сбой применения в
savepoint не откатывает письмо), границы батча (400 validation_error), а также статус-канал
`apply_mailbox_status`: переходы true→false / false→true / без перехода / неизвестный ящик.

HMAC/HTTP-порядок (503→401→400) — уровень роутера (`test_mail_ingest_api.py`); криптография
подписи — `tests/unit/test_mail_push_security.py`.
"""

from __future__ import annotations

import pytest
from app.errors import AppError
from app.models.mail_message import MailMessage
from app.models.mail_tag import MailMessageTag, MailTag, MailTagRule
from app.repositories.mail_tag_repository import MailTagRepository
from app.schemas.mail_ingest import (
    MailboxStatusRequest,
    MailIngestMessage,
    MailIngestRequest,
)
from app.services.mail_ingest_service import MailIngestService
from mail_helpers import dt, mail_db, seed_account
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _msg(
    uid: int, *, account_id: int = 1, subject: str | None = None, **kw: object
) -> MailIngestMessage:
    return MailIngestMessage(
        mail_account_id=account_id,
        uidvalidity=1,
        uid=uid,
        from_addr="sender@example.com",
        to_addrs="inbox@example.com",
        internal_date=dt(2026, 7, 2, 9, 15),
        body_text="тело",
        subject=subject,
        **kw,  # type: ignore[arg-type]
    )


async def _ingest(
    sm: async_sessionmaker[AsyncSession], request: MailIngestRequest, *, max_batch: int = 100
) -> object:
    async with sm() as session:
        return await MailIngestService(session, max_batch=max_batch).ingest(request)


# ------------------------------------------------------------------ идемпотентность
async def test_new_message_accepted_and_notified_at_null() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        result = await _ingest(sm, MailIngestRequest(messages=[_msg(10)]))
        assert (result.accepted, result.duplicate, result.unknown_mailbox) == (1, 0, 0)  # type: ignore[attr-defined]
        async with sm() as s:
            row = (await s.execute(select(MailMessage))).scalar_one()
            assert row.notified_at is None  # диспетчер (S4) разошлёт; на приёме NULL
            assert row.uid == 10


async def test_repeated_delivery_is_duplicate_no_row_dup() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        first = await _ingest(sm, MailIngestRequest(messages=[_msg(10)]))
        second = await _ingest(sm, MailIngestRequest(messages=[_msg(10)]))
        assert first.accepted == 1 and first.duplicate == 0  # type: ignore[attr-defined]
        assert second.accepted == 0 and second.duplicate == 1  # type: ignore[attr-defined]
        async with sm() as s:
            count = (await s.execute(select(MailMessage))).scalars().all()
            assert len(count) == 1  # дубля нет


async def test_duplicate_within_same_batch() -> None:
    """Идемпотентность держит и внутри одного батча (два одинаковых ключа)."""
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        result = await _ingest(sm, MailIngestRequest(messages=[_msg(10), _msg(10)]))
        assert (result.accepted, result.duplicate) == (1, 1)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- неизвестный ящик
async def test_unknown_mailbox_skipped_batch_not_rejected() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        # ящик 1 известен, ящик 999 — нет: батч НЕ отклоняется, известное принимается.
        result = await _ingest(
            sm, MailIngestRequest(messages=[_msg(10, account_id=1), _msg(11, account_id=999)])
        )
        assert result.accepted == 1  # type: ignore[attr-defined]
        assert result.unknown_mailbox == 1  # type: ignore[attr-defined]
        async with sm() as s:
            rows = (await s.execute(select(MailMessage))).scalars().all()
            assert {r.mail_account_id for r in rows} == {1}


# --------------------------------------------------- best-effort теги (savepoint)
async def test_tag_apply_failure_does_not_rollback_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)

        async def _boom(self: object, **_kw: object) -> None:
            raise SQLAlchemyError("tag apply exploded")

        monkeypatch.setattr(MailTagRepository, "apply_tags_to_message", _boom)
        result = await _ingest(sm, MailIngestRequest(messages=[_msg(10)]))
        # Письмо принято, несмотря на сбой применения тегов (savepoint откатил только теги).
        assert result.accepted == 1  # type: ignore[attr-defined]
        async with sm() as s:
            row = (await s.execute(select(MailMessage))).scalar_one()
            assert row.uid == 10
            tags = (await s.execute(select(MailMessageTag))).scalars().all()
            assert tags == []  # тег-связей нет — применение откатилось


async def test_tags_applied_on_ingest_when_rule_matches() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
            tag = MailTag(name="DPLA", color="#2563eb", match_mode="any")
            s.add(tag)
            await s.flush()
            s.add(MailTagRule(tag_id=tag.id, type="subject_contains", pattern="DPLA"))
        result = await _ingest(sm, MailIngestRequest(messages=[_msg(10, subject="DPLA build")]))
        assert result.accepted == 1  # type: ignore[attr-defined]
        async with sm() as s:
            links = (await s.execute(select(MailMessageTag))).scalars().all()
            assert len(links) == 1  # тег применён на приёме push'а


# ----------------------------------------------------------- границы батча (400)
async def test_empty_batch_rejected_400() -> None:
    async with mail_db() as sm:
        with pytest.raises(AppError) as exc:
            await _ingest(sm, MailIngestRequest(messages=[]))
        assert exc.value.code == "validation_error"
        assert exc.value.status_code == 400


async def test_batch_over_limit_rejected_400() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        msgs = [_msg(i) for i in range(3)]
        with pytest.raises(AppError) as exc:
            await _ingest(sm, MailIngestRequest(messages=msgs), max_batch=2)
        assert exc.value.code == "validation_error"


# ============================================================ status-канал
async def _apply_status(
    sm: async_sessionmaker[AsyncSession], payload: MailboxStatusRequest
) -> bool:
    async with sm() as session:
        return await MailIngestService(session, max_batch=100).apply_mailbox_status(payload)


async def test_status_true_to_false_keeps_down_alert_null() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1, is_active=True, down_alert_sent_at=None)
        updated = await _apply_status(
            sm,
            MailboxStatusRequest(
                mail_account_id=1,
                is_active=False,
                last_sync_error="connection refused",
                consecutive_failures=3,
            ),
        )
        assert updated is True
        async with sm() as s:
            from app.models.mail_account import MailAccount

            acc = await s.get(MailAccount, 1)
            assert acc is not None
            assert acc.is_active is False
            assert acc.down_alert_sent_at is None  # НЕ трогается → проход C разошлёт один раз
            assert acc.last_sync_error == "connection refused"
            assert acc.consecutive_failures == 3


async def test_status_false_to_true_resets_down_alert() -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1, is_active=False, down_alert_sent_at=dt(2026, 7, 1))
        updated = await _apply_status(sm, MailboxStatusRequest(mail_account_id=1, is_active=True))
        assert updated is True
        async with sm() as s:
            from app.models.mail_account import MailAccount

            acc = await s.get(MailAccount, 1)
            assert acc is not None
            assert acc.is_active is True
            assert acc.down_alert_sent_at is None  # сброс на re-enable


async def test_status_no_transition_keeps_down_alert() -> None:
    """true→true без перехода не трогает `down_alert_sent_at` (guarded)."""
    async with mail_db() as sm:
        stamp = dt(2026, 7, 1)
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1, is_active=True, down_alert_sent_at=stamp)
        updated = await _apply_status(sm, MailboxStatusRequest(mail_account_id=1, is_active=True))
        assert updated is True
        async with sm() as s:
            from app.models.mail_account import MailAccount

            acc = await s.get(MailAccount, 1)
            assert acc is not None
            assert acc.down_alert_sent_at is not None  # не сброшен (перехода не было)


async def test_status_unknown_mailbox_is_noop_false() -> None:
    async with mail_db() as sm:
        updated = await _apply_status(
            sm, MailboxStatusRequest(mail_account_id=12345, is_active=False)
        )
        assert updated is False  # no-op, аномалия TD-041 — запрос не отклоняется
