r"""drop mail_tags.is_builtin + однократный сев канонических тегов (ADR-047 §1)

Revision ID: 0023_mail_tags_drop_is_builtin
Revises: 0022_create_mail_sent_messages
Create Date: 2026-07-11

Признак «встроенный тег» упразднён: удалить можно ЛЮБОЙ тег (ветка 409 снята), сев в
lifespan убран (он воскрешал удалённый тег при рестарте — это и был корень фикса).

Порядок шагов обязателен (03-data-model.md#миграция-0023_mail_tags_drop_is_builtin):
  1. Data-seed 10 канонических тегов + их правил — идемпотентно
     (`INSERT ... ON CONFLICT (name) DO NOTHING` по `uq_mail_tags_name`; правила
     вставляются ТОЛЬКО впервые созданным тегам: у `mail_tag_rules` натурального
     уникального ключа нет, повторный прогон иначе задублировал бы правила).
     На проде теги уже есть → но-оп; на чистой инсталляции создаются один раз.
  2. `DROP COLUMN is_builtin`.

Данные **вшиты в тело миграции** и НЕ импортируются из кода приложения
(`app.domain.mail_builtin_tags` удалён этим же ADR — импорт сломал бы миграцию задним
числом).

`downgrade()` возвращает только ФОРМУ схемы (колонку с default false) — значения
признака не восстанавливаются: он упразднён.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0023_mail_tags_drop_is_builtin"
down_revision: str | None = "0022_create_mail_sent_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Канонический каталог тегов (порт из mail-агрегатора; набор/цвета/правила/match_mode —
# ровно те, что сеялись в lifespan до ADR-047 §1). Вшит в миграцию намеренно.
_CANONICAL_TAGS: list[dict[str, Any]] = [
    {
        "name": "DPLA.PLA",
        "color": "#2563eb",
        "match_mode": "any",
        "rules": [
            ("subject_contains", "DPLA"),
            ("subject_contains", "PLA"),
            ("body_contains", "DPLA"),
            ("body_contains", "PLA"),
        ],
    },
    {
        "name": "Отменить подписку",
        "color": "#f59e0b",
        "match_mode": "all",
        "rules": [
            ("body_contains", "cancel"),
            ("body_contains", "subscription"),
        ],
    },
    {
        "name": "Продление аккаунта",
        "color": "#16a34a",
        "match_mode": "any",
        "rules": [
            (
                "body_contains",
                "Your Distribution Certificate will no longer be valid in 30 days",
            ),
        ],
    },
    {
        "name": "Диспут",
        "color": "#dc2626",
        "match_mode": "any",
        "rules": [
            ("sender_exact", "AppStoreNotices@apple.com"),
        ],
    },
    {
        "name": "Бан Аккаунта",
        "color": "#dc2626",
        "match_mode": "all",
        "rules": [
            ("subject_contains", "Notice of Termination"),
            ("sender_contains", "Apple Developer"),
        ],
    },
    {
        "name": "Релиз",
        "color": "#16a34a",
        "match_mode": "all",
        "rules": [
            ("sender_contains", "App Store Connect"),
            ("body_contains", "Congratulations!"),
        ],
    },
    {
        "name": "Реджект",
        "color": "#db2777",
        "match_mode": "all",
        "rules": [
            ("sender_contains", "App Store Connect"),
            (
                "body_contains",
                "We noticed an issue with your submission that requires your attention.",
            ),
        ],
    },
    {
        "name": "Ревью",
        "color": "#7c3aed",
        "match_mode": "all",
        "rules": [
            ("sender_contains", "App Store Connect"),
            ("body_contains", "In Review"),
        ],
    },
    {
        "name": "Ждет Ревью",
        "color": "#0891b2",
        "match_mode": "all",
        "rules": [
            ("sender_contains", "App Store Connect"),
            ("body_contains", "Waiting for Review"),
        ],
    },
    {
        "name": "Нужна замена реквизитов",
        "color": "#475569",
        "match_mode": "all",
        "rules": [
            ("sender_contains", "App Store Connect"),
            ("subject_contains", "Payment Returned"),
        ],
    },
]

_INSERT_TAG = sa.text(
    """
    INSERT INTO mail_tags (name, color, match_mode)
    VALUES (:name, :color, :match_mode)
    ON CONFLICT (name) DO NOTHING
    RETURNING id
    """
)

_INSERT_RULE = sa.text(
    """
    INSERT INTO mail_tag_rules (tag_id, type, pattern)
    VALUES (:tag_id, :type, :pattern)
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    for tag in _CANONICAL_TAGS:
        tag_id = conn.execute(
            _INSERT_TAG,
            {"name": tag["name"], "color": tag["color"], "match_mode": tag["match_mode"]},
        ).scalar_one_or_none()
        if tag_id is None:
            continue  # тег с таким именем уже есть — не трогаем (и правила не дублируем)
        for rule_type, pattern in tag["rules"]:
            conn.execute(_INSERT_RULE, {"tag_id": tag_id, "type": rule_type, "pattern": pattern})

    op.drop_column("mail_tags", "is_builtin")


def downgrade() -> None:
    op.add_column(
        "mail_tags",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
