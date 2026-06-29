from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-more-than-32-bytes")
    monkeypatch.setenv("JWT_EXPIRES_MIN", "60")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "300")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("FILE_SD_DIR", os.path.join(os.getcwd(), ".pytest-file-sd"))

    from app.config import get_settings
    from app.infra import rate_limit

    get_settings.cache_clear()
    rate_limit._limiter = None
    yield
    get_settings.cache_clear()
    rate_limit._limiter = None
