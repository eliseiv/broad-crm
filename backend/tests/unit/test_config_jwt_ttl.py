"""Unit-тесты дефолтного TTL JWT в конфиге (config.py, 05-security.md).

JWT_EXPIRES_MIN по умолчанию = 1440 минут (24 часа). Производное поле
jwt_expires_seconds вычисляется в model_post_init как jwt_expires_min * 60,
то есть 86400 секунд, и именно оно попадает в expires_in выпускаемого токена.
"""

from __future__ import annotations

from app.config import Settings


def test_default_jwt_expires_min_is_1440() -> None:
    settings = Settings()

    assert settings.jwt_expires_min == 1440


def test_default_jwt_expires_seconds_is_86400() -> None:
    settings = Settings()

    assert settings.jwt_expires_seconds == 86400
    assert settings.jwt_expires_seconds == settings.jwt_expires_min * 60


def test_custom_jwt_expires_min_propagates_to_seconds() -> None:
    settings = Settings(jwt_expires_min=30)

    assert settings.jwt_expires_seconds == 1800
