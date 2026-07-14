"""Unit-тесты машинной защиты цепочки бюджетов почты (ADR-053 §1.3 п.7, TD-059).

Поэлементно и РАЗДЕЛЬНО:
- `ge/le` на КАЖДОМ из четырёх полей (обе категории — быстрая и mail-server);
- КРОСС-ПОЛЕВОЙ `model_validator`: случай, который `ge/le` физически пропускают
  (`read=80` + `overall=76` — обе границы соблюдены, но цепочка сломана);
- бюджет ЗАПРОСА (§1.2.1): deadline + компенсация + overhead < `proxy_read_timeout` nginx;
- дефолты стартуют (`85 + 15 + 5 = 105 < 120`).

`ge/le` в тестах НЕ обходятся (и обойти их нельзя: `Settings` — `BaseSettings`, kwargs идут
через тот же валидатор, что и env) — они САМИ являются предметом проверки.
"""

from __future__ import annotations

import pytest
from app.config import (
    CRM_REQUEST_OVERHEAD_SEC,
    MAIL_CLEANUP_DEADLINE_SEC,
    NGINX_PROXY_READ_TIMEOUT_SEC,
    Settings,
)
from pydantic import ValidationError


# --- Дефолты: конфиг прода собирается ----------------------------------------
def test_defaults_are_valid_and_match_adr_chain() -> None:
    settings = Settings()

    assert settings.mail_api_timeout_sec == 10
    assert settings.mail_api_deadline_sec == 30
    assert settings.mail_api_mailserver_timeout_sec == 75
    assert settings.mail_api_mailserver_deadline_sec == 85
    # Цепочка строго возрастает наружу (§1.2): read < overall в каждой категории.
    assert settings.mail_api_timeout_sec < settings.mail_api_deadline_sec
    assert settings.mail_api_mailserver_timeout_sec < settings.mail_api_mailserver_deadline_sec


def test_request_budget_of_defaults_fits_under_nginx_ceiling() -> None:
    settings = Settings()

    budget = (
        settings.mail_api_mailserver_deadline_sec
        + MAIL_CLEANUP_DEADLINE_SEC
        + CRM_REQUEST_OVERHEAD_SEC
    )

    assert budget == 105  # 85 + 15 + 5 (§1.2.1)
    assert budget < NGINX_PROXY_READ_TIMEOUT_SEC  # 105 < 120


# --- (а) `ge/le` на каждом поле ----------------------------------------------
@pytest.mark.parametrize(
    ("field", "value"),
    [
        # Mail-server read обязан превышать потолок агрегатора (60 с его nginx).
        ("mail_api_mailserver_timeout_sec", 60),
        ("mail_api_mailserver_deadline_sec", 86),  # > le=85 → бюджет запроса не удержать
        ("mail_api_deadline_sec", 120),  # > le=60 (быстрая пара)
        ("mail_api_timeout_sec", 31),  # > le=30 (быстрый путь не держит соединение долго)
    ],
)
def test_field_bounds_reject_out_of_range(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_subsecond_budget_rejected_by_type_and_bounds() -> None:
    # Почему бюджеты инъектируются в КЛИЕНТ, а не в Settings (06-testing-strategy.md):
    # поле `int` + `ge=1` — суб-секундное значение не проходит ни по типу, ни по границе.
    with pytest.raises(ValidationError):
        Settings(mail_api_timeout_sec=0.3)  # type: ignore[arg-type]


# --- (б) кросс-полевой валидатор: то, чего `ge/le` выразить НЕ могут ----------
def test_cross_field_rejects_mailserver_read_greater_than_overall() -> None:
    """Контрпример ADR-053 §1.3 п.7б: ОБЕ `ge/le`-границы соблюдены, цепочка сломана."""
    # 80 ∈ [61, 80] ✅ и 76 ∈ [76, 85] ✅ — но read (80) > overall (76).
    with pytest.raises(ValidationError) as exc:
        Settings(mail_api_mailserver_timeout_sec=80, mail_api_mailserver_deadline_sec=76)

    assert "MAIL_API_MAILSERVER_TIMEOUT_SEC" in str(exc.value)


def test_cross_field_rejects_fast_read_greater_than_overall() -> None:
    # 30 ∈ [1, 30] ✅ и 10 ∈ [2, 60] ✅ — но read (30) > overall (10).
    with pytest.raises(ValidationError) as exc:
        Settings(mail_api_timeout_sec=30, mail_api_deadline_sec=10)

    assert "MAIL_API_TIMEOUT_SEC" in str(exc.value)


def test_cross_field_rejects_equal_read_and_overall() -> None:
    # Строгое «меньше»: read == overall не оставляет времени ни на connect/write, ни на ретраи.
    with pytest.raises(ValidationError):
        Settings(mail_api_mailserver_timeout_sec=80, mail_api_mailserver_deadline_sec=80)


# --- (в) бюджет ЗАПРОСА (§1.2.1) ---------------------------------------------
def test_request_budget_violation_blocks_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`deadline + компенсация(15) + overhead(5) ≥ proxy_read_timeout` → приложение не стартует.

    Потолок nginx — зеркало числа из `frontend/nginx/default.conf` (§1.3 п.7б). Понижаем его
    (как если бы прокси перенастроили на 100 с, забыв пересчитать бюджеты) — дефолтный
    mail-server-бюджет запроса (85 + 15 + 5 = 105) выходит за него → ValidationError.
    """
    import app.config as config

    monkeypatch.setattr(config, "NGINX_PROXY_READ_TIMEOUT_SEC", 100.0)

    with pytest.raises(ValidationError) as exc:
        Settings()

    assert "Бюджет ЗАПРОСА" in str(exc.value)


def test_request_budget_respects_cleanup_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Валидатор стережёт именно константу уборки, а не свою копию числа."""
    import app.config as config

    # Уборке выдали столько же, сколько mail-server-вызову → сумма запроса вылетает за 120.
    monkeypatch.setattr(config, "MAIL_CLEANUP_DEADLINE_SEC", 35.0)

    with pytest.raises(ValidationError):
        Settings()


def test_cleanup_constant_is_single_source_shared_with_service() -> None:
    """`mail_service.py` НЕ держит литерал 15 — он импортирует константу из `config.py`."""
    from app.services import mail_service

    assert mail_service.MAIL_CLEANUP_DEADLINE_SEC == MAIL_CLEANUP_DEADLINE_SEC
    # Уборка обязана быть КОРОЧЕ полного бюджета быстрой категории (§1.2.2).
    assert Settings().mail_api_deadline_sec > MAIL_CLEANUP_DEADLINE_SEC
