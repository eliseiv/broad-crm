"""Каталог builtin-тегов — порт из mail-агрегатора (ADR-0017 §6 + ADR-0040 §3).

Источник: `mail-agregator/backend/app/tags/builtin.py`. Набор (имена/цвета/правила/
match_mode) перенесён **без изменений**. В CRM теги глобальны (`is_builtin=true`, без
владельца) и сидируются идемпотентно в lifespan (`seed_builtin_tags`, ADR-044 §5) по
`UNIQUE (name)`. На проде они уже придут миграцией данных; seed добирает отсутствующие
на чистой установке. Ленивого per-login `ensure_builtin_tags` в CRM нет.
"""

from __future__ import annotations

from typing import Final, TypedDict


class BuiltinRule(TypedDict):
    """Правило встроенного тега (type ∈ enum mail_tag_rules.type)."""

    type: str
    pattern: str


class BuiltinTag(TypedDict):
    """Встроенный тег: имя, цвет, режим матчинга, правила."""

    name: str
    color: str
    match_mode: str  # 'any' (OR, default) или 'all' (AND)
    rules: list[BuiltinRule]


BUILTIN_TAGS: Final[list[BuiltinTag]] = [
    # --- Pre-existing tags (preserved) -----------------------------------
    {
        "name": "DPLA.PLA",
        "color": "#2563eb",  # c1 blue
        "match_mode": "any",
        "rules": [
            {"type": "subject_contains", "pattern": "DPLA"},
            {"type": "subject_contains", "pattern": "PLA"},
            {"type": "body_contains", "pattern": "DPLA"},
            {"type": "body_contains", "pattern": "PLA"},
        ],
    },
    {
        # Cancel AND subscription must both appear (round-25: was 'any').
        "name": "Отменить подписку",
        "color": "#f59e0b",  # c3 amber
        "match_mode": "all",
        "rules": [
            {"type": "body_contains", "pattern": "cancel"},
            {"type": "body_contains", "pattern": "subscription"},
        ],
    },
    {
        "name": "Продление аккаунта",
        "color": "#16a34a",  # c4 green
        "match_mode": "any",
        "rules": [
            {
                "type": "body_contains",
                "pattern": "Your Distribution Certificate will no longer be valid in 30 days",
            },
        ],
    },
    # --- App Store Connect workflow (round-25) ---------------------------
    {
        # Dispute notices come from a precise address — exact match only.
        "name": "Диспут",
        "color": "#dc2626",  # c2 red
        "match_mode": "any",
        "rules": [
            {"type": "sender_exact", "pattern": "AppStoreNotices@apple.com"},
        ],
    },
    {
        "name": "Бан Аккаунта",
        "color": "#dc2626",  # c2 red
        "match_mode": "all",
        "rules": [
            {"type": "subject_contains", "pattern": "Notice of Termination"},
            {"type": "sender_contains", "pattern": "Apple Developer"},
        ],
    },
    {
        "name": "Релиз",
        "color": "#16a34a",  # c4 green
        "match_mode": "all",
        "rules": [
            {"type": "sender_contains", "pattern": "App Store Connect"},
            {"type": "body_contains", "pattern": "Congratulations!"},
        ],
    },
    {
        "name": "Реджект",
        "color": "#db2777",  # c7 pink
        "match_mode": "all",
        "rules": [
            {"type": "sender_contains", "pattern": "App Store Connect"},
            {
                "type": "body_contains",
                "pattern": (
                    "We noticed an issue with your submission that requires your attention."
                ),
            },
        ],
    },
    {
        "name": "Ревью",
        "color": "#7c3aed",  # c5 purple
        "match_mode": "all",
        "rules": [
            {"type": "sender_contains", "pattern": "App Store Connect"},
            {"type": "body_contains", "pattern": "In Review"},
        ],
    },
    {
        "name": "Ждет Ревью",
        "color": "#0891b2",  # c6 cyan
        "match_mode": "all",
        "rules": [
            {"type": "sender_contains", "pattern": "App Store Connect"},
            {"type": "body_contains", "pattern": "Waiting for Review"},
        ],
    },
    {
        "name": "Нужна замена реквизитов",
        "color": "#475569",  # c8 slate
        "match_mode": "all",
        "rules": [
            {"type": "sender_contains", "pattern": "App Store Connect"},
            {"type": "subject_contains", "pattern": "Payment Returned"},
        ],
    },
]
