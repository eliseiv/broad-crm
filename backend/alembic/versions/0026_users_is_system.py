r"""users.is_system — системная строка-якорь супер-админа (ADR-051 §1.1)

Revision ID: 0026_users_is_system
Revises: 0025_mail_message_reads
Create Date: 2026-07-13

Личное состояние с FK на `users` (прочитанность писем `mail_message_reads`, ADR-050)
физически невозможно для принципала без строки в `users`. Владелец работает на проде
под консольным супер-админом (`ADMIN_USER`/`ADMIN_PASSWORD` из `.env`) ⇒ ADR-051 заводит
ему **системную строку-якорь**: идентичность, и только она (не учётка, не источник прав,
не способ входа, не канал доставки).

- **Только схема.** Колонка + частичный уникальный индекс. **Строку-якорь миграция НЕ
  вставляет** — её создаёт идемпотентный bootstrap приложения
  (`UserRepository.ensure_superadmin_anchor`, ADR-051 §1.3): миграции не импортируют код
  приложения (03-data-model.md#3-миграции-не-импортируют-код-приложения), а строке нужны
  `hash_password` и константы якоря.
- **Индекс `uq_users_system_singleton` — зеркало модели:** он объявлен в
  `User.__table_args__` (схема тестов поднимается через `Base.metadata.create_all`);
  миграция повторяет его один-в-один. Расхождение модель↔миграция = дефект.
- **Backfill не нужен:** `DEFAULT false` корректен для всех существующих строк.
- **`downgrade()` УДАЛЯЕТ строку-якорь** (нормативно, ADR-051 «Последствия»): иначе после
  отката схемы якорь остался бы ОБЫЧНЫМ пользователем с ролью `admin` — видимым в
  `/api/users` и редактируемым (можно задать ему пароль), т.е. откат создал бы учётку-
  призрак с admin-ролью. Цена принята: `ON DELETE CASCADE` унесёт отметки прочитанности
  супер-админа (на откатываемой версии они всё равно неработоспособны).

Идентификатор ревизии — `0026_users_is_system` (20 символов), укладывается в предел
`alembic_version.version_num varchar(32)` (ADR-047 §3.5).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_users_is_system"
down_revision: str | None = "0025_mail_message_reads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "uq_users_system_singleton",
        "users",
        ["is_system"],
        unique=True,
        postgresql_where=sa.text("is_system"),
    )


def downgrade() -> None:
    # Порядок нормативен (ADR-051): сначала строка-якорь, затем индекс, затем колонка.
    op.execute("DELETE FROM users WHERE is_system")
    op.drop_index("uq_users_system_singleton", table_name="users")
    op.drop_column("users", "is_system")
