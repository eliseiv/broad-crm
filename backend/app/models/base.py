"""Базовый класс для декларативных моделей."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий declarative base всех моделей."""
