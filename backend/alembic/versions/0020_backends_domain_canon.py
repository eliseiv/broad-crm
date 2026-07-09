r"""backends: канонизация домена к форме https://<host>/ (ADR-042)

Revision ID: 0020_backends_domain_canon
Revises: 0019_backends_relations_secrets
Create Date: 2026-07-10

Амендмент ADR-042 (03-data-model.md#миграция-0020_backends_domain_canon-концепт).
Переканонизирует `backends.domain` к форме `https://<host>/` и меняет DB-инвариант
CHECK `ck_backends_domain`:
  было  `char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^[^\s/]+$'`  (голый host)
  стало `char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^https://[^\s/]+/$'` (канон).

`upgrade()`: снять старый CHECK → backfill голых доменов
(`'https://' || lower(domain) || '/'`, в проде 0 бэков → no-op) → добавить новый CHECK.
`downgrade()`: снять новый CHECK → обратный backfill к голому host → вернуть старый CHECK.
Полная валидация формата хоста — на уровне приложения (Pydantic, 422); DB-CHECK —
«свободный» инвариант каноничной формы.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_backends_domain_canon"
down_revision: str | None = "0019_backends_relations_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_CHECK = r"char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^https://[^\s/]+/$'"
_OLD_CHECK = r"char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^[^\s/]+$'"


def upgrade() -> None:
    op.drop_constraint("ck_backends_domain", "backends", type_="check")
    # Backfill уже-голых доменов к канону (в проде 0 бэков → фактически no-op).
    op.execute(
        "UPDATE backends SET domain = 'https://' || lower(domain) || '/' "
        "WHERE domain !~ '^https://'"
    )
    op.create_check_constraint("ck_backends_domain", "backends", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_backends_domain", "backends", type_="check")
    op.execute(
        "UPDATE backends SET domain = "
        "regexp_replace(regexp_replace(domain, '^https://', ''), '/$', '')"
    )
    op.create_check_constraint("ck_backends_domain", "backends", _OLD_CHECK)
