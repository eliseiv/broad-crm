r"""mail_accounts += number/app_name + backfill из display_name (ADR-047 §3)

Revision ID: 0024_mail_accounts_number_app_name
Revises: 0023_mail_tags_drop_is_builtin
Create Date: 2026-07-11

«Отображаемое имя» разделяется на два поля: **«Номер»** (`number`) и **«Приложение»**
(`app_name`). Колонка `display_name` СОХРАНЯЕТСЯ и становится ПРОИЗВОДНОЙ
(`"<number> <app_name>"`, пересчитывается сервером при create/update ящика) — это
единственная форма имени во внешнем контракте агрегатора (ADR-047 §3.3, TD-052).

Backfill (нормативно, 03-data-model.md#миграция-0024_mail_accounts_number_app_name):
ведущая числовая часть (включая перечисление через запятую) → `number`, остаток →
`app_name`. Regex `^\s*(\d+(?:\s*,\s*\d+)*)\s*(.*)$`; `number` нормализуется к
разделителю «запятая + пробел»; пустой остаток → NULL; нет ведущих цифр → `number` NULL,
`app_name` = trim(display_name); `display_name IS NULL` → обе колонки NULL.

Нормативные кейсы (примеры владельца):
  `5108 Klyro Forge (Codex)` → (`5108`, `Klyro Forge (Codex)`)
  `173, 57, 104`             → (`173, 57, 104`, NULL)
  `WIU`                      → (NULL, `WIU`)

`downgrade()` — DROP обеих колонок (данные не теряются: `display_name` содержит склейку).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Имя миграции (docs, ADR-047 §3) — `0024_mail_accounts_number_app_name`; оно сохранено в
# имени ФАЙЛА. Идентификатор ревизии укорочен до 31 символа: `alembic_version.version_num`
# — `varchar(32)` (жёсткий предел alembic), полное имя (34 симв.) в него не влезает.
revision: str = "0024_mail_accounts_num_app_name"
down_revision: str | None = "0023_mail_tags_drop_is_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ведущая числовая часть (одно число или перечисление через запятую) + остаток текста.
# DOTALL — на случай многострочного display_name (остаток забирается целиком).
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\s*,\s*\d+)*)\s*(.*)$", re.DOTALL)

_SELECT_ROWS = sa.text("SELECT id, display_name FROM mail_accounts WHERE display_name IS NOT NULL")
_UPDATE_ROW = sa.text(
    "UPDATE mail_accounts SET number = :number, app_name = :app_name WHERE id = :id"
)


def _split_display_name(display_name: str) -> tuple[str | None, str | None]:
    """`display_name` → (`number`, `app_name`) по нормативному правилу разбора (§3.1)."""
    match = _LEADING_NUMBER_RE.match(display_name)
    if match is None:
        rest = display_name.strip()
        return None, rest or None
    number = ", ".join(token.strip() for token in match.group(1).split(","))
    app_name = match.group(2).strip()
    return number, app_name or None


def upgrade() -> None:
    op.add_column("mail_accounts", sa.Column("number", sa.Text(), nullable=True))
    op.add_column("mail_accounts", sa.Column("app_name", sa.Text(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(_SELECT_ROWS).all()
    for account_id, display_name in rows:
        number, app_name = _split_display_name(display_name)
        conn.execute(
            _UPDATE_ROW,
            {"id": account_id, "number": number, "app_name": app_name},
        )


def downgrade() -> None:
    op.drop_column("mail_accounts", "app_name")
    op.drop_column("mail_accounts", "number")
