"""Integration-тесты приёма/fan-out и retry-монитора SMS (modules/sms, ADR-030).

`SmsIngestService` напрямую поверх реального Postgres + `FakeBot`. Проверяют
crash-recoverable fan-out (дедуп по SID идемпотентен, `try_reserve` идемпотентен,
403→mark_dead+link.mark_dead, прочая ошибка Bot API→mark_failed, неизвестный номер→
без доставок, бот не настроен→mark_failed) и `retry_pending_deliveries`
(переотправка pending, мёртвый линк→dead, отсутствие исходного SMS→failed).
"""

from __future__ import annotations

from typing import Any

import pytest
from app.models.sms_delivery import SmsDelivery
from app.models.sms_inbound import SmsInbound
from app.models.sms_telegram_link import SmsTelegramLink
from app.services.sms_ingest_service import SmsIngestService
from sms_helpers import (
    FakeBot,
    add_membership,
    seed_delivery,
    seed_inbound,
    seed_link,
    seed_number,
    seed_role,
    seed_team,
    seed_user,
    sms_db,
)
from sqlalchemy import select


async def _deliveries(sm: Any) -> list[SmsDelivery]:
    async with sm() as s:
        rows = (await s.execute(select(SmsDelivery).order_by(SmsDelivery.id))).scalars().all()
    return list(rows)


async def test_fanout_delivers_to_all_team_recipients() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            u2 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await add_membership(s, u2.id, team.id)
            await seed_link(s, telegram_user_id=1001, user_id=u1.id)
            await seed_link(s, telegram_user_id=1002, user_id=u2.id)
            await seed_number(s, phone_number="+13105551111", team_id=team.id)
            await s.commit()

        bot = FakeBot()
        async with sm() as s:
            await SmsIngestService(s, bot).handle_incoming_sms(
                twilio_message_sid="SMfan1",
                from_number="+79161234567",
                to_number="+13105551111",
                body="hi",
                raw_payload={"MessageSid": "SMfan1"},
            )

    assert {chat for chat, _ in bot.sent} == {1001, 1002}
    deliveries = await _deliveries(sm)
    assert len(deliveries) == 2
    assert all(d.status == "sent" for d in deliveries)


async def test_duplicate_sid_is_idempotent() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await seed_link(s, telegram_user_id=2001, user_id=u1.id)
            await seed_number(s, phone_number="+13105552222", team_id=team.id)
            await s.commit()

        bot = FakeBot()
        for _ in range(2):  # webhook-retry с тем же MessageSid
            async with sm() as s:
                await SmsIngestService(s, bot).handle_incoming_sms(
                    twilio_message_sid="SMdup",
                    from_number="+79161234567",
                    to_number="+13105552222",
                    body="hi",
                    raw_payload={"MessageSid": "SMdup"},
                )

        async with sm() as s:
            sms_rows = (await s.execute(select(SmsInbound))).scalars().all()

    assert len(sms_rows) == 1  # дедуп по SID — одна запись
    assert len(bot.sent) == 1  # try_reserve идемпотентен — без повторной доставки
    deliveries = await _deliveries(sm)
    assert len(deliveries) == 1


async def test_forbidden_marks_delivery_and_link_dead() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await seed_link(s, telegram_user_id=3001, user_id=u1.id)
            await seed_number(s, phone_number="+13105553333", team_id=team.id)
            await s.commit()

        bot = FakeBot()
        bot.forbidden_for(3001)
        async with sm() as s:
            await SmsIngestService(s, bot).handle_incoming_sms(
                twilio_message_sid="SMdead",
                from_number="+79161234567",
                to_number="+13105553333",
                body="hi",
                raw_payload={},
            )
        async with sm() as s:
            link = (
                await s.execute(
                    select(SmsTelegramLink).where(SmsTelegramLink.telegram_user_id == 3001)
                )
            ).scalar_one()

    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["dead"]
    assert link.dead_at is not None  # линк помечен мёртвым


async def test_api_error_marks_delivery_failed() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await seed_link(s, telegram_user_id=4001, user_id=u1.id)
            await seed_number(s, phone_number="+13105554444", team_id=team.id)
            await s.commit()

        bot = FakeBot()
        bot.api_error_for(4001)
        async with sm() as s:
            await SmsIngestService(s, bot).handle_incoming_sms(
                twilio_message_sid="SMfail",
                from_number="+79161234567",
                to_number="+13105554444",
                body="hi",
                raw_payload={},
            )

    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["failed"]


async def test_unknown_number_saves_without_deliveries() -> None:
    async with sms_db() as sm:
        bot = FakeBot()
        async with sm() as s:
            sms = await SmsIngestService(s, bot).handle_incoming_sms(
                twilio_message_sid="SMunknown",
                from_number="+79161234567",
                to_number="+13105550000",  # номера нет в реестре
                body="hi",
                raw_payload={},
            )
        assert sms.team_id is None
    assert bot.sent == []
    assert await _deliveries(sm) == []


async def test_bot_not_configured_marks_failed() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await seed_link(s, telegram_user_id=5001, user_id=u1.id)
            await seed_number(s, phone_number="+13105555555", team_id=team.id)
            await s.commit()

        bot = FakeBot(is_configured=False)
        async with sm() as s:
            await SmsIngestService(s, bot).handle_incoming_sms(
                twilio_message_sid="SMnobot",
                from_number="+79161234567",
                to_number="+13105555555",
                body="hi",
                raw_payload={},
            )

    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["failed"]
    assert bot.sent == []


# --- retry_pending_deliveries -----------------------------------------------


async def test_retry_resends_pending() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, u1.id, team.id)
            await seed_link(s, telegram_user_id=6001, user_id=u1.id)
            await seed_number(s, phone_number="+13105556666", team_id=team.id)
            sms = await seed_inbound(
                s, from_number="+79161234567", to_number="+13105556666", team_id=team.id
            )
            await seed_delivery(
                s, inbound_sms_id=sms.id, user_id=u1.id, telegram_user_id=6001, status="failed"
            )
            await s.commit()

        bot = FakeBot()
        async with sm() as s:
            retried = await SmsIngestService(s, bot).retry_pending_deliveries(max_attempts=5)

    assert retried == 1
    assert bot.sent == [(6001, bot.sent[0][1])]
    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["sent"]


async def test_retry_dead_link_marks_dead() -> None:
    from datetime import UTC, datetime

    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await seed_link(
                s, telegram_user_id=7001, user_id=u1.id, dead_at=datetime.now(UTC)
            )  # мёртвая привязка
            sms = await seed_inbound(
                s, from_number="+79161234567", to_number="+13105557777", team_id=team.id
            )
            await seed_delivery(
                s, inbound_sms_id=sms.id, user_id=u1.id, telegram_user_id=7001, status="pending"
            )
            await s.commit()

        bot = FakeBot()
        async with sm() as s:
            await SmsIngestService(s, bot).retry_pending_deliveries(max_attempts=5)

    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["dead"]
    assert bot.sent == []


async def test_retry_missing_source_sms_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ветка «исходное SMS не найдено» защитная (FK CASCADE её не создаёт) — эмулируем
    # через monkeypatch `SmsInboundRepository.get` → None.
    import app.services.sms_ingest_service as ingest_module

    async def _get_none(_self: Any, _sms_id: int) -> None:
        return None

    monkeypatch.setattr(ingest_module.SmsInboundRepository, "get", _get_none)

    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role)
            team = await seed_team(s)
            await seed_link(s, telegram_user_id=8001, user_id=u1.id)
            sms = await seed_inbound(
                s, from_number="+79161234567", to_number="+13105558888", team_id=team.id
            )
            await seed_delivery(
                s, inbound_sms_id=sms.id, user_id=u1.id, telegram_user_id=8001, status="pending"
            )
            await s.commit()

        bot = FakeBot()
        async with sm() as s:
            await SmsIngestService(s, bot).retry_pending_deliveries(max_attempts=5)

    deliveries = await _deliveries(sm)
    assert [d.status for d in deliveries] == ["failed"]
    assert bot.sent == []
