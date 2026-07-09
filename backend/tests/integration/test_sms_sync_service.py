"""Integration-тесты синхронизации номеров из Twilio (04-api.md#post-apismsnumberssync).

`SmsSyncService` поверх реального Postgres + подменённый `TwilioNumbersClient`:
идемпотентный upsert как unassigned (ON CONFLICT DO NOTHING) + обновление `label`
из friendly_name + корректные счётчики; `twilio_not_configured`→503;
`TwilioNumbersApiError`→502 twilio_error.
"""

from __future__ import annotations

from typing import Any

import app.services.sms_sync_service as sync_module
import pytest
from app.errors import AppError
from app.infra.twilio_numbers import TwilioNumber, TwilioNumbersApiError
from app.repositories.sms_number_repository import SmsNumberRepository
from app.services.sms_sync_service import SmsSyncService
from sms_helpers import seed_number, sms_db


def _settings(monkeypatch: pytest.MonkeyPatch, *, configured: bool = True) -> Any:
    from app.config import get_settings

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123" if configured else "")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token" if configured else "")
    get_settings.cache_clear()
    return get_settings()


def _patch_twilio(monkeypatch: pytest.MonkeyPatch, numbers: list[TwilioNumber]) -> None:
    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_incoming_numbers(self) -> list[TwilioNumber]:
            return numbers

    monkeypatch.setattr(sync_module, "TwilioNumbersClient", _FakeClient)


def _patch_twilio_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_incoming_numbers(self) -> list[TwilioNumber]:
            raise TwilioNumbersApiError("boom")

    monkeypatch.setattr(sync_module, "TwilioNumbersClient", _FailingClient)


async def test_sync_adds_new_and_updates_label(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    _patch_twilio(
        monkeypatch,
        [
            TwilioNumber(phone_number="+13105551000", friendly_name="Sales US"),
            TwilioNumber(phone_number="+13105551001", friendly_name="Support"),
        ],
    )
    async with sms_db() as sm:
        async with sm() as s:
            result = await SmsSyncService(numbers=SmsNumberRepository(s), settings=settings).sync()
        async with sm() as s:
            rows = await SmsNumberRepository(s).list_all()

    assert result.synced_total == 2
    assert result.added == 2
    assert result.skipped_existing == 0
    labels = {n.phone_number: n.label for n in rows}
    assert labels == {"+13105551000": "Sales US", "+13105551001": "Support"}
    assert all(n.team_id is None for n in rows)  # unassigned


async def test_sync_is_idempotent_and_skips_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            await seed_number(s, phone_number="+13105551000", label="old")
            await s.commit()
        _patch_twilio(
            monkeypatch,
            [
                TwilioNumber(phone_number="+13105551000", friendly_name="Sales US"),
                TwilioNumber(phone_number="+13105551002", friendly_name="New"),
            ],
        )
        async with sm() as s:
            result = await SmsSyncService(numbers=SmsNumberRepository(s), settings=settings).sync()
        async with sm() as s:
            rows = await SmsNumberRepository(s).list_all()

    assert result.synced_total == 2
    assert result.added == 1  # только новый вставлен
    assert result.skipped_existing == 1
    labels = {n.phone_number: n.label for n in rows}
    assert labels["+13105551000"] == "Sales US"  # label обновлён у существующего


async def test_sync_not_configured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, configured=False)
    async with sms_db() as sm, sm() as s:
        with pytest.raises(AppError) as exc:
            await SmsSyncService(numbers=SmsNumberRepository(s), settings=settings).sync()
    assert exc.value.status_code == 503
    assert exc.value.code == "twilio_not_configured"


async def test_sync_twilio_api_error_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    _patch_twilio_error(monkeypatch)
    async with sms_db() as sm, sm() as s:
        with pytest.raises(AppError) as exc:
            await SmsSyncService(numbers=SmsNumberRepository(s), settings=settings).sync()
    assert exc.value.status_code == 502
    assert exc.value.code == "twilio_error"
