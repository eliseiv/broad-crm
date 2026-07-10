r"""Одноразовый ETL: перенос данных модуля «Почты» из mail-агрегатора в CRM.

Источник истины плана — `docs/adr/ADR-044-mail-full-merge-into-crm.md` §10
(«Миграция данных (агрегатор → CRM)»). Скрипт НЕ является production-кодом:
это cut-over-инструмент, запускаемый вручную под контролем оператора. Однако
ошибка необратима (перенос в живую систему), поэтому дизайн — fail-fast +
идемпотентность + dry-run-репетиция.

Что переносится (ADR-044 §10 / задача S6):
  1. Ящики (`mail_accounts` → `mail_accounts`): `id` preserve; `team_id` через
     `teams.mail_group_id = aggregator.groups.id`; `down_alert_sent_at = now()`
     ВСЕМ ящикам с `is_active = false`.
  2. Письма (`messages` → `mail_messages`): `id` preserve + `setval` sequence;
     `notified_at = now()` ВСЕМ (иначе диспетчер разошлёт всю историю).
     Вложения НЕ переносятся.
  3. Теги — 16 глобальных: 10 builtin (канон `app.domain.mail_builtin_tags`) +
     6 кастомных (из персональных агрегатора, вместе с правилами). `id` тегов в
     CRM — UUID; маппинг агрегатор↔CRM строится по имени.
  4. `message_tags` НЕ переносятся — разметка воспроизводится ПЕРЕ-ПРИМЕНЕНИЕМ
     16 тегов по корпусу через побуквенный движок `app.domain.mail_tags_sql`.
  5. Telegram-привязки (`telegram_links` → `mail_telegram_links`): `user_id` по
     нормативной таблице резолва (chat_id → username → `users.telegram`, ci).
  6. История уведомлений (`telegram_notifications` →
     `mail_telegram_notifications`): `status = 'sent'` ВСЕМ.
  7. Отправленные reply (`sent_messages` → `mail_sent_messages`, ADR-044 §10):
     `id` НЕ preserve (источник BIGSERIAL, цель UUID) — генерируется
     ДЕТЕРМИНИРОВАННЫЙ uuid5 от исходного id (для идемпотентности повторного
     запуска); `from_account_id` → `mail_account_id` (int сохраняется); автор
     `user_id` резолвится нормативным мостом chat_id → username →
     `users.telegram` (ci), при 0 или >1 агрегаторских Telegram-привязках → NULL.
     Осознанно отбрасываются `bcc_addrs`/`appended_to_sent`/`appended_error`.

Что НЕ переносится (задача S6, п.7): вложения, `admin_audit`, `webhooks`,
`group_forwarding` (TD-040 — целевой таблицы `mail_forwarding` пока нет),
`users_settings` (0 строк), персональные теги и их привязки.

Связь серверов. Агрегатор (`postapp.store`) и CRM (`broadappsdev.shop`) — разные
хосты. Скрипт поддерживает ДВА режима (выбор — оператора, см. --mode):
  * `direct` — один процесс держит оба подключения (нужна сетевая достижимость
    обеих БД одновременно). Читает агрегатор, пишет CRM в одной транзакции.
  * `extract` + `load` — двухфазный дамп для air-gapped-переноса: `extract`
    (на стороне агрегатора, read-only) выгружает данные в каталог; каталог
    переносится; `load` (на стороне CRM) читает дамп и пишет в CRM.

Формат дампа — JSON Lines, НЕ CSV: тела писем содержат произвольный текст
(переводы строк, кавычки, Unicode, запятые) → CSV в необратимой миграции
рискует «тихой» порчей на экранировании; JSONL round-trip'ит без потерь.
Обоснованное отклонение от буквы задачи («дамп CSV») в пользу безопасности.

Транзакционность. Вся загрузка в CRM идёт в ОДНОЙ транзакции: либо переносится
всё, либо ничего (единственная точка отката — вся транзакция). `--dry-run`
выполняет полную репетицию (включая все INSERT и проверки FK/constraint) и в
конце делает ROLLBACK — ничего не записывая. Любое fail-fast-нарушение →
исключение → ROLLBACK.

ИСКЛЮЧЕНИЕ — `setval` последовательности `mail_messages_id_seq`. Операции над
sequence в PostgreSQL НЕ транзакционны: `setval` переживает `ROLLBACK`. Поэтому
`setval` вынесен ИЗ загрузочной транзакции и выполняется ТОЛЬКО на боевом пути,
ОТДЕЛЬНЫМ подключением ПОСЛЕ успешного `commit` — иначе `--dry-run` против
боевой БД реально сдвигал бы последовательность (несмотря на печатаемое
«транзакция откачена»). На dry-run `setval` не выполняется вовсе.

Идемпотентность. Повторный запуск не дублирует: `ON CONFLICT DO NOTHING` по
натуральным ключам (`mail_accounts.id`, `uq_mail_messages_account_uidv_uid`,
`mail_tags.name`, `mail_telegram_links.telegram_user_id`,
`uq_mail_tg_notif_msg_chat`, `mail_sent_messages.id` — детерминированный uuid5 от
исходного id); правила тегов — `INSERT ... WHERE NOT EXISTS`; пере-применение
тегов — `ON CONFLICT (message_id, tag_id) DO NOTHING`.

Переменные окружения (креды НЕ хардкодятся):
  * `AGG_DATABASE_URL` — БД агрегатора (`postgresql[+asyncpg]://...`), режимы
    `direct`/`extract`.
  * `CRM_DATABASE_URL` (fallback `DATABASE_URL`) — БД CRM, режимы
    `direct`/`load`.

Запуск (из каталога `backend/`):
  Dry-run (репетиция, ничего не пишет):
    uv run python scripts/migrate_mail_data.py --mode direct --dry-run
  Боевой (в одной транзакции, коммит в конце):
    uv run python scripts/migrate_mail_data.py --mode direct
  Двухфазный:
    (на агрегаторе)  uv run python scripts/migrate_mail_data.py \
                         --mode extract --work-dir ./_mail_dump
    (на CRM, dry)    uv run python scripts/migrate_mail_data.py \
                         --mode load --work-dir ./_mail_dump --dry-run
    (на CRM, боевой) uv run python scripts/migrate_mail_data.py \
                         --mode load --work-dir ./_mail_dump
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

logger = logging.getLogger("mail_migration")

# --- Ожидаемые объёмы прода (ADR-044 §10; растут — синк идёт; трактуются как
#     нижняя граница для сверки, точное равенство не требуется). ------------------
EXPECTED_ACCOUNTS = 121
EXPECTED_MESSAGES = 2871
EXPECTED_TAGS = 16
EXPECTED_TELEGRAM_LINKS = 8
EXPECTED_NOTIFICATIONS = 12982

# --- Пространство имён для ДЕТЕРМИНИРОВАННОГО uuid5 строк mail_sent_messages ---------
#     Источник (`sent_messages.id`) — BIGSERIAL; цель — UUID (ADR-044 §10: «id НЕ
#     переносится, генерируется новый UUID»). Но шаг обязан быть идемпотентным, а у
#     mail_sent_messages нет натурального уникального ключа для ON CONFLICT. Решение:
#     фиксированный uuid5(namespace, str(src_id)) — «новый UUID» (не исходный int) и
#     СТАБИЛЬНЫЙ между запусками → повтор конфликтует по id, а не дублирует строку.
_SENT_MESSAGE_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "adr-044:mail_sent_messages")

# --- 6 кастомных тегов (ADR-044 §10 / §5): повышаются до глобальных, is_builtin=false.
#     Берутся из ПЕРСОНАЛЬНЫХ тегов агрегатора вместе с правилами (по одной копии). ---
CUSTOM_TAG_NAMES: tuple[str, ...] = (
    "Поддержка",
    "Билд в коннекте",
    "Small Business",
    "Билд не дошёл",
    "Ждет Ревью 2",
    "Билд не приняли",
)

# --- Нормативная таблица резолва Telegram-привязок (ADR-044 §10; подтверждена Bot
#     API getChat). chat_id → Telegram-username (как в Bot API, до нормализации).
#     `username` кладётся в CRM в нижнем регистре; `user_id` резолвится по
#     `users.telegram` (case-insensitive, ведущий `@` снят). username в источнике
#     `telegram_links` НЕ хранится — единственный источник соответствия здесь. -------
NORMATIVE_TG_USERNAMES: dict[int, str] = {
    1604863121: "not_ryan_reynolds",
    1028365903: "Katetown",
    164692303: "novikov_iwan",
    399743086: "m_niyazov",
    453350292: "michtl",
    63356836: "Loveink",
    1039984194: "Anellie_sss",
    290151018: "yuliya_2704",
}


class MigrationError(RuntimeError):
    """Fail-fast: несоответствие данных, при котором миграцию продолжать нельзя."""


# --- Датасет источника (результат extract; сериализуется в JSONL для load) --------


@dataclass(slots=True)
class MigrationDataset:
    """Снимок данных агрегатора, необходимых для загрузки в CRM.

    Поля — «сырые» строки агрегатора (без CRM-резолва team_id/user_id: он делается
    на стороне CRM при load). datetime сериализуются в ISO 8601.
    """

    accounts: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    custom_tags: list[dict[str, Any]] = field(default_factory=list)
    telegram_links: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    sent_messages: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------------------
# Утилиты
# ------------------------------------------------------------------------------


def _normalize_db_url(url: str) -> str:
    """Приводит URL к async-драйверу asyncpg (SQLAlchemy требует `+asyncpg`)."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _require_env(name: str, *, fallback: str | None = None) -> str:
    """Читает обязательную переменную окружения (или fallback-имя). Иначе — ошибка."""
    value = os.environ.get(name)
    if not value and fallback:
        value = os.environ.get(fallback)
    if not value:
        alt = f" (или {fallback})" if fallback else ""
        raise MigrationError(f"Не задана переменная окружения {name}{alt}")
    return value


def _make_engine(url: str) -> AsyncEngine:
    """Создаёт async-движок SQLAlchemy к БД по URL."""
    return create_async_engine(_normalize_db_url(url), pool_pre_ping=True, future=True)


def _jsonify(value: Any) -> Any:
    """Готовит значение к JSON-сериализации (datetime → ISO 8601).

    Обработка ТОЛЬКО `datetime` достаточна и безопасна для двухфазного режима
    (`extract` → JSONL → `load`): каждый SELECT источника (см. `_SQL_READ_*`)
    выбирает исключительно скаляры, JSON-native по типам агрегатора — `BigInteger`/
    `Integer` (→ int), `Text` (→ str), `Boolean` (→ bool) — плюс `DateTime` (→ здесь
    в ISO 8601). Ни один SELECT не читает `Decimal`/`UUID`/`bytes`/`LargeBinary`
    (зашифрованные креды `encrypted_password`/`oauth_*` и т.п. НЕ выбираются вовсе),
    поэтому расширять сериализацию/разбор не требуется. `_parse_dt` симметрично
    восстанавливает datetime на стороне `load`. (Проверено по моделям агрегатора:
    `message.py`, `mail_account.py`, `telegram_link.py`, `sent_message.py`.)
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value: str | None) -> datetime | None:
    """Разбирает ISO 8601 обратно в datetime (или None)."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


# ------------------------------------------------------------------------------
# EXTRACT — чтение из агрегатора (read-only)
# ------------------------------------------------------------------------------

_SQL_READ_ACCOUNTS = text(
    """
    SELECT id, group_id, email, display_name, is_active,
           last_synced_at, last_sync_error, consecutive_failures,
           created_at, updated_at
    FROM mail_accounts
    ORDER BY id
    """
)
# Примечание: `disabled_alert_sent_at` НАМЕРЕННО не выбирается. По ADR-044 §10
# (MINOR-1) `down_alert_sent_at` в CRM выставляется `now()` ВСЕМ неактивным ящикам
# (а не переносится «как есть» лишь для 2 оталерченных) — исходный штамп не нужен.

_SQL_READ_MESSAGES = text(
    """
    SELECT id, mail_account_id, uidvalidity, uid, message_id_header, subject,
           from_addr, from_name, to_addrs, cc_addrs, internal_date,
           body_text, body_html, body_truncated, body_present,
           in_reply_to, refs_header
    FROM messages
    ORDER BY id
    """
)

_SQL_READ_CUSTOM_TAGS = text(
    """
    SELECT id, name, color, match_mode
    FROM tags
    WHERE name IN :names AND is_builtin = false
    ORDER BY name, id
    """
).bindparams(bindparam("names", expanding=True))

_SQL_READ_TAG_RULES = text(
    """
    SELECT tag_id, type, pattern
    FROM tag_rules
    WHERE tag_id IN :tag_ids
    ORDER BY tag_id, id
    """
).bindparams(bindparam("tag_ids", expanding=True))

_SQL_READ_TG_LINKS = text(
    """
    SELECT telegram_user_id, user_id, created_at, dead_at
    FROM telegram_links
    ORDER BY telegram_user_id
    """
)
# `user_id` (агрегаторский автор-id) читается для МОСТА резолва автора
# `sent_messages` (ADR-044 §10): агрегатор `user_id` → chat_id(-ы) → username →
# CRM `users.telegram`. Для самой `mail_telegram_links` он НЕ используется (там
# резолв идёт по нормативной таблице chat_id→username + CRM `users`).

_SQL_READ_NOTIFICATIONS = text(
    """
    SELECT message_id, telegram_user_id, sent_at
    FROM telegram_notifications
    ORDER BY id
    """
)

_SQL_READ_SENT_MESSAGES = text(
    """
    SELECT id, user_id, from_account_id, to_addrs, cc_addrs, subject,
           body_text, in_reply_to, refs_header, smtp_message_id, sent_at
    FROM sent_messages
    ORDER BY id
    """
)


async def extract(conn: AsyncConnection) -> MigrationDataset:
    """Читает все нужные сущности из БД агрегатора в датасет (без записи)."""
    dataset = MigrationDataset()

    logger.info("extract: чтение mail_accounts ...")
    dataset.accounts = [dict(r) for r in (await conn.execute(_SQL_READ_ACCOUNTS)).mappings()]
    logger.info("extract: ящиков прочитано: %d", len(dataset.accounts))

    logger.info("extract: чтение messages ...")
    dataset.messages = [dict(r) for r in (await conn.execute(_SQL_READ_MESSAGES)).mappings()]
    logger.info("extract: писем прочитано: %d", len(dataset.messages))

    logger.info("extract: чтение кастомных тегов (6) и их правил ...")
    dataset.custom_tags = await _extract_custom_tags(conn)
    logger.info("extract: кастомных тегов прочитано: %d", len(dataset.custom_tags))

    logger.info("extract: чтение telegram_links ...")
    dataset.telegram_links = [dict(r) for r in (await conn.execute(_SQL_READ_TG_LINKS)).mappings()]
    logger.info("extract: привязок прочитано: %d", len(dataset.telegram_links))

    logger.info("extract: чтение telegram_notifications ...")
    dataset.notifications = [
        dict(r) for r in (await conn.execute(_SQL_READ_NOTIFICATIONS)).mappings()
    ]
    logger.info("extract: уведомлений прочитано: %d", len(dataset.notifications))

    logger.info("extract: чтение sent_messages ...")
    dataset.sent_messages = [
        dict(r) for r in (await conn.execute(_SQL_READ_SENT_MESSAGES)).mappings()
    ]
    logger.info("extract: отправленных reply прочитано: %d", len(dataset.sent_messages))

    return dataset


async def _extract_custom_tags(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Читает 6 кастомных тегов + их правила. Fail-fast: отсутствие/дубль имени."""
    rows = [
        dict(r)
        for r in (
            await conn.execute(_SQL_READ_CUSTOM_TAGS, {"names": list(CUSTOM_TAG_NAMES)})
        ).mappings()
    ]
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["name"]
        if name in by_name:
            raise MigrationError(
                f"Кастомный тег '{name}' встречается в агрегаторе более одного раза "
                f"(персональные копии) — маппинг по имени неоднозначен; ожидалась одна копия"
            )
        by_name[name] = row

    missing = [n for n in CUSTOM_TAG_NAMES if n not in by_name]
    if missing:
        raise MigrationError(
            f"В агрегаторе не найдены кастомные теги: {missing}. "
            f"Ожидались все 6 (ADR-044 §10). Миграция остановлена."
        )

    tag_ids = [row["id"] for row in by_name.values()]
    rules_rows = [
        dict(r) for r in (await conn.execute(_SQL_READ_TAG_RULES, {"tag_ids": tag_ids})).mappings()
    ]
    rules_by_tag: dict[Any, list[dict[str, str]]] = {}
    for r in rules_rows:
        rules_by_tag.setdefault(r["tag_id"], []).append(
            {"type": r["type"], "pattern": r["pattern"]}
        )

    result: list[dict[str, Any]] = []
    for name in CUSTOM_TAG_NAMES:
        tag = by_name[name]
        rules = rules_by_tag.get(tag["id"], [])
        if not rules:
            raise MigrationError(
                f"У кастомного тега '{name}' нет правил в агрегаторе — переносить нечего "
                f"(правила обязательны для пере-применения разметки). Миграция остановлена."
            )
        result.append(
            {
                "name": tag["name"],
                "color": tag["color"],
                "match_mode": tag["match_mode"],
                "rules": rules,
            }
        )
    return result


# ------------------------------------------------------------------------------
# DUMP / RESTORE — двухфазный JSONL-перенос
# ------------------------------------------------------------------------------

_JSONL_FILES = ("accounts", "messages", "telegram_links", "notifications", "sent_messages")


def dump_dataset(dataset: MigrationDataset, work_dir: Path) -> None:
    """Пишет датасет в каталог: *.jsonl для крупных таблиц + custom_tags.json + manifest."""
    work_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, list[dict[str, Any]]] = {
        "accounts": dataset.accounts,
        "messages": dataset.messages,
        "telegram_links": dataset.telegram_links,
        "notifications": dataset.notifications,
        "sent_messages": dataset.sent_messages,
    }
    for name, rows in mapping.items():
        path = work_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({k: _jsonify(v) for k, v in row.items()}, ensure_ascii=False))
                fh.write("\n")
        logger.info("dump: %s → %d строк", path.name, len(rows))

    tags_path = work_dir / "custom_tags.json"
    tags_path.write_text(
        json.dumps(dataset.custom_tags, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("dump: %s → %d тегов", tags_path.name, len(dataset.custom_tags))

    manifest = {
        "extracted_at": datetime.now(tz=UTC).isoformat(),
        "counts": {
            "accounts": len(dataset.accounts),
            "messages": len(dataset.messages),
            "custom_tags": len(dataset.custom_tags),
            "telegram_links": len(dataset.telegram_links),
            "notifications": len(dataset.notifications),
            "sent_messages": len(dataset.sent_messages),
        },
    }
    (work_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("dump: manifest.json записан")


def load_dataset(work_dir: Path) -> MigrationDataset:
    """Читает датасет из каталога дампа (обратная dump_dataset)."""
    if not work_dir.is_dir():
        raise MigrationError(f"Каталог дампа не найден: {work_dir}")
    dataset = MigrationDataset()
    for name in _JSONL_FILES:
        path = work_dir / f"{name}.jsonl"
        if not path.is_file():
            raise MigrationError(f"В дампе отсутствует файл {path.name}")
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
        setattr(dataset, name, rows)
        logger.info("restore: %s ← %d строк", path.name, len(rows))

    tags_path = work_dir / "custom_tags.json"
    if not tags_path.is_file():
        raise MigrationError(f"В дампе отсутствует файл {tags_path.name}")
    dataset.custom_tags = json.loads(tags_path.read_text(encoding="utf-8"))
    logger.info("restore: custom_tags.json ← %d тегов", len(dataset.custom_tags))
    return dataset


# ------------------------------------------------------------------------------
# LOAD — запись в CRM (в одной транзакции; fail-fast; идемпотентно)
# ------------------------------------------------------------------------------


@dataclass(slots=True)
class LoadReport:
    """Итоги загрузки по каждой сущности: прочитано / вставлено / пропущено (конфликт)."""

    accounts: tuple[int, int, int] = (0, 0, 0)
    messages: tuple[int, int, int] = (0, 0, 0)
    tags: tuple[int, int, int] = (0, 0, 0)
    tag_rules_inserted: int = 0
    reapplied_message_tags: int = 0
    telegram_links: tuple[int, int, int] = (0, 0, 0)
    notifications: tuple[int, int, int] = (0, 0, 0)
    sent_messages: tuple[int, int, int] = (0, 0, 0)


async def _table_count(conn: AsyncConnection, table: str) -> int:
    """COUNT(*) целевой таблицы (для расчёта дельты вставок)."""
    # table — из фиксированного белого списка вызывающих (не пользовательский ввод).
    result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
    return int(result.scalar_one())


async def _load_team_map(conn: AsyncConnection) -> dict[int, uuid.UUID]:
    """Строит {teams.mail_group_id → teams.id} для резолва ящик→команда (ADR-044 §10)."""
    rows = (
        await conn.execute(
            text("SELECT mail_group_id, id FROM teams WHERE mail_group_id IS NOT NULL")
        )
    ).all()
    return {int(mgid): tid for mgid, tid in rows}


async def _load_user_by_telegram(conn: AsyncConnection) -> dict[str, uuid.UUID]:
    """Строит {lower(users.telegram) → users.id} для резолва Telegram-привязок."""
    rows = (
        await conn.execute(
            text("SELECT lower(telegram) AS tg, id FROM users WHERE telegram IS NOT NULL")
        )
    ).all()
    return {str(tg): uid for tg, uid in rows}


def _normalize_username(raw: str) -> str:
    """Снимает ведущий `@` и приводит к нижнему регистру (канон CRM `users.telegram`)."""
    value = raw.strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower()


def _resolve_accounts(
    accounts: list[dict[str, Any]], team_map: dict[int, uuid.UUID], now: datetime
) -> list[dict[str, Any]]:
    """Готовит строки mail_accounts CRM: резолвит team_id, выставляет down_alert_sent_at.

    Fail-fast: ящик, чей group_id не маппится в команду (включая NULL group_id), — стоп.
    down_alert_sent_at = now() ВСЕМ ящикам с is_active=false (ADR-044 §10, MINOR-1).
    """
    resolved: list[dict[str, Any]] = []
    for acc in accounts:
        group_id = acc["group_id"]
        team_id = team_map.get(group_id) if group_id is not None else None
        if team_id is None:
            raise MigrationError(
                f"Ящик id={acc['id']} ({acc['email']}): group_id={group_id!r} не маппится "
                f"ни в одну teams.mail_group_id. Молчаливый team_id=NULL запрещён — стоп."
            )
        is_active = bool(acc["is_active"])
        resolved.append(
            {
                "id": acc["id"],
                "email": acc["email"],
                "display_name": acc["display_name"],
                "team_id": team_id,
                "is_active": is_active,
                "last_synced_at": _parse_dt(acc["last_synced_at"])
                if isinstance(acc["last_synced_at"], str)
                else acc["last_synced_at"],
                "last_sync_error": acc["last_sync_error"],
                "consecutive_failures": acc["consecutive_failures"],
                # ВСЕМ отключённым ящикам — штамп now(), чтобы проход C §6 не разослал
                # алерты о давних падениях (импорт известного состояния, не транзиция).
                "down_alert_sent_at": now if not is_active else None,
                "created_at": _parse_dt(acc["created_at"])
                if isinstance(acc["created_at"], str)
                else acc["created_at"],
                "updated_at": _parse_dt(acc["updated_at"])
                if isinstance(acc["updated_at"], str)
                else acc["updated_at"],
            }
        )
    return resolved


def _resolve_telegram_links(
    links: list[dict[str, Any]],
    user_map: dict[str, uuid.UUID],
    now: datetime,
) -> list[dict[str, Any]]:
    """Готовит строки mail_telegram_links: username (lower) + user_id (резолв).

    Fail-fast: chat_id вне нормативной таблицы, либо username не резолвится в CRM-пользователя.
    """
    resolved: list[dict[str, Any]] = []
    for link in links:
        chat_id = int(link["telegram_user_id"])
        raw_username = NORMATIVE_TG_USERNAMES.get(chat_id)
        if raw_username is None:
            expected = len(NORMATIVE_TG_USERNAMES)
            raise MigrationError(
                f"Telegram-привязка chat_id={chat_id} отсутствует в нормативной таблице "
                f"резолва (ADR-044 §10). Ожидались ровно {expected} привязок — стоп."
            )
        username = _normalize_username(raw_username)
        user_id = user_map.get(username)
        if user_id is None:
            raise MigrationError(
                f"Telegram-привязка chat_id={chat_id} (username='{username}') не резолвится "
                f"в users.telegram (ci). На проде все 8 обязаны резолвиться — стоп."
            )
        created = link["created_at"]
        dead = link["dead_at"]
        resolved.append(
            {
                "telegram_user_id": chat_id,
                "user_id": user_id,
                "username": username,
                "created_at": _parse_dt(created) if isinstance(created, str) else (created or now),
                "dead_at": _parse_dt(dead) if isinstance(dead, str) else dead,
            }
        )
    return resolved


def _build_author_bridge(telegram_links: list[dict[str, Any]]) -> dict[int, list[int]]:
    """Строит мост {агрегаторский users.id → [chat_id, ...]} из telegram_links.

    Основа резолва автора `sent_messages` (ADR-044 §10): агрегаторский `user_id`
    отправителя → его Telegram-привязки → chat_id(-ы). Неоднозначность (0 или >1
    привязок у автора) выявляется по длине списка на стороне `_resolve_sent_author`.
    """
    chat_ids_by_user: dict[int, list[int]] = {}
    for link in telegram_links:
        agg_user_id = link.get("user_id")
        if agg_user_id is None:
            continue
        chat_ids_by_user.setdefault(int(agg_user_id), []).append(int(link["telegram_user_id"]))
    return chat_ids_by_user


def _resolve_sent_author(
    agg_user_id: Any,
    chat_ids_by_user: dict[int, list[int]],
    user_map: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    """Резолвит автора отправленного письма в CRM `users.id` (ADR-044 §10).

    Мост: агрегаторский `user_id` → РОВНО ОДНА Telegram-привязка (chat_id) →
    `NORMATIVE_TG_USERNAMES[chat_id]` → CRM `users.telegram` (ci). При 0 или >1
    привязках (напр. агрегаторский admin с двумя чатами) → `NULL` (не подмена
    участником команды). Best-effort: любой несрезолвившийся шаг → `NULL` (колонка
    `mail_sent_messages.user_id` nullable, FK `ON DELETE SET NULL`) — НЕ fail-fast,
    в отличие от миграции самих `mail_telegram_links` (там все 8 обязаны резолвиться).
    """
    if agg_user_id is None:
        return None
    chat_ids = chat_ids_by_user.get(int(agg_user_id), [])
    if len(chat_ids) != 1:
        return None  # 0 или >1 — неоднозначно, автор не резолвится
    raw_username = NORMATIVE_TG_USERNAMES.get(chat_ids[0])
    if raw_username is None:
        return None
    return user_map.get(_normalize_username(raw_username))


def _resolve_sent_messages(
    sent_messages: list[dict[str, Any]],
    chat_ids_by_user: dict[int, list[int]],
    user_map: dict[str, uuid.UUID],
) -> list[dict[str, Any]]:
    """Готовит строки mail_sent_messages CRM (ADR-044 §10).

    `id` — детерминированный uuid5 от исходного `sent_messages.id` (идемпотентность,
    НЕ preserve int); `from_account_id` → `mail_account_id`; автор через мост-резолв;
    `to_addrs`/`body_text` (NOT NULL в CRM) — защита от None. Отбрасываются
    `bcc_addrs`/`appended_to_sent`/`appended_error`.
    """
    resolved: list[dict[str, Any]] = []
    for s in sent_messages:
        sent_at = s["sent_at"]
        resolved.append(
            {
                "id": uuid.uuid5(_SENT_MESSAGE_ID_NAMESPACE, str(s["id"])),
                "mail_account_id": s["from_account_id"],
                "user_id": _resolve_sent_author(s["user_id"], chat_ids_by_user, user_map),
                "to_addrs": s["to_addrs"] if s["to_addrs"] is not None else "",
                "cc_addrs": s["cc_addrs"],
                "subject": s["subject"],
                "body_text": s["body_text"] if s["body_text"] is not None else "",
                "in_reply_to": s["in_reply_to"],
                "refs_header": s["refs_header"],
                "smtp_message_id": s["smtp_message_id"],
                "sent_at": _parse_dt(sent_at) if isinstance(sent_at, str) else sent_at,
            }
        )
    return resolved


def _validate_referential(dataset: MigrationDataset) -> None:
    """Проверяет ссылочную целостность источника ДО записи (fail-fast, ADR-044 §10).

    * каждое письмо ссылается на существующий ящик;
    * каждое уведомление ссылается на существующее письмо.
    """
    account_ids = {acc["id"] for acc in dataset.accounts}
    orphan_msgs = [m["id"] for m in dataset.messages if m["mail_account_id"] not in account_ids]
    if orphan_msgs:
        raise MigrationError(
            f"Письма без ящика (mail_account_id вне набора): {orphan_msgs[:10]} "
            f"(всего {len(orphan_msgs)}). На проде сирот 0 — стоп."
        )

    message_ids = {m["id"] for m in dataset.messages}
    orphan_notifs = [
        (n["message_id"], n["telegram_user_id"])
        for n in dataset.notifications
        if n["message_id"] not in message_ids
    ]
    if orphan_notifs:
        raise MigrationError(
            f"Уведомления без письма (message_id вне набора): {orphan_notifs[:10]} "
            f"(всего {len(orphan_notifs)}). На проде сирот 0 — стоп."
        )

    orphan_sent = [
        s["id"] for s in dataset.sent_messages if s["from_account_id"] not in account_ids
    ]
    if orphan_sent:
        raise MigrationError(
            f"Отправленные reply без ящика (from_account_id вне набора): {orphan_sent[:10]} "
            f"(всего {len(orphan_sent)}). FK mail_sent_messages.mail_account_id — стоп."
        )


_INSERT_ACCOUNT = text(
    """
    INSERT INTO mail_accounts (
        id, email, display_name, team_id, is_active,
        last_synced_at, last_sync_error, consecutive_failures,
        down_alert_sent_at, created_at, updated_at
    ) VALUES (
        :id, :email, :display_name, :team_id, :is_active,
        :last_synced_at, :last_sync_error, :consecutive_failures,
        :down_alert_sent_at, :created_at, :updated_at
    )
    ON CONFLICT (id) DO NOTHING
    """
)

_INSERT_MESSAGE = text(
    """
    INSERT INTO mail_messages (
        id, mail_account_id, uidvalidity, uid, message_id_header, subject,
        from_addr, from_name, to_addrs, cc_addrs, internal_date,
        body_text, body_html, body_truncated, body_present,
        in_reply_to, refs_header, notified_at, created_at
    ) VALUES (
        :id, :mail_account_id, :uidvalidity, :uid, :message_id_header, :subject,
        :from_addr, :from_name, :to_addrs, :cc_addrs, :internal_date,
        :body_text, :body_html, :body_truncated, :body_present,
        :in_reply_to, :refs_header, :notified_at, :created_at
    )
    ON CONFLICT (mail_account_id, uidvalidity, uid) DO NOTHING
    """
)

_INSERT_TAG = text(
    """
    INSERT INTO mail_tags (name, color, match_mode, is_builtin)
    VALUES (:name, :color, :match_mode, :is_builtin)
    ON CONFLICT (name) DO NOTHING
    """
)

_INSERT_TAG_RULE = text(
    """
    INSERT INTO mail_tag_rules (tag_id, type, pattern)
    SELECT :tag_id, :type, :pattern
    WHERE NOT EXISTS (
        SELECT 1 FROM mail_tag_rules
        WHERE tag_id = :tag_id AND type = :type AND pattern = :pattern
    )
    """
)

_INSERT_TG_LINK = text(
    """
    INSERT INTO mail_telegram_links (telegram_user_id, user_id, username, created_at, dead_at)
    VALUES (:telegram_user_id, :user_id, :username, :created_at, :dead_at)
    ON CONFLICT (telegram_user_id) DO NOTHING
    """
)

_INSERT_NOTIFICATION = text(
    """
    INSERT INTO mail_telegram_notifications (
        message_id, telegram_user_id, status, attempts, sent_at, created_at, updated_at
    ) VALUES (
        :message_id, :telegram_user_id, 'sent', 1, :sent_at, :created_at, :updated_at
    )
    ON CONFLICT (message_id, telegram_user_id) DO NOTHING
    """
)

_INSERT_SENT_MESSAGE = text(
    """
    INSERT INTO mail_sent_messages (
        id, mail_account_id, user_id, to_addrs, cc_addrs, subject,
        body_text, in_reply_to, refs_header, smtp_message_id, sent_at
    ) VALUES (
        :id, :mail_account_id, :user_id, :to_addrs, :cc_addrs, :subject,
        :body_text, :in_reply_to, :refs_header, :smtp_message_id, :sent_at
    )
    ON CONFLICT (id) DO NOTHING
    """
)

_CHUNK = 500


async def _insert_chunked(
    conn: AsyncConnection, table: str, stmt: Any, rows: list[dict[str, Any]], label: str
) -> tuple[int, int, int]:
    """Батч-вставка с дельтой (before/after count). Возвращает (read, inserted, skipped)."""
    before = await _table_count(conn, table)
    total = len(rows)
    for start in range(0, total, _CHUNK):
        chunk = rows[start : start + _CHUNK]
        await conn.execute(stmt, chunk)
        logger.info("load %s: отправлено %d/%d", label, min(start + _CHUNK, total), total)
    after = await _table_count(conn, table)
    inserted = after - before
    return total, inserted, total - inserted


async def _load_tags(
    conn: AsyncConnection, custom_tags: list[dict[str, Any]]
) -> tuple[tuple[int, int, int], int, list[uuid.UUID]]:
    """Заводит 16 глобальных тегов (10 builtin канон + 6 кастомных) + правила.

    Идемпотентно: теги — ON CONFLICT (name); правила — INSERT WHERE NOT EXISTS.
    Возвращает ((read, inserted, skipped) по тегам, число вставленных правил, id всех 16 тегов).
    """
    builtin_tags, _ = _import_app_symbols()
    desired: list[dict[str, Any]] = []
    for bt in builtin_tags:
        desired.append(
            {
                "name": bt["name"],
                "color": bt["color"],
                "match_mode": bt["match_mode"],
                "is_builtin": True,
                "rules": [dict(r) for r in bt["rules"]],
            }
        )
    for ct in custom_tags:
        desired.append(
            {
                "name": ct["name"],
                "color": ct["color"],
                "match_mode": ct["match_mode"],
                "is_builtin": False,
                "rules": [dict(r) for r in ct["rules"]],
            }
        )

    before = await _table_count(conn, "mail_tags")
    for tag in desired:
        await conn.execute(
            _INSERT_TAG,
            {
                "name": tag["name"],
                "color": tag["color"],
                "match_mode": tag["match_mode"],
                "is_builtin": tag["is_builtin"],
            },
        )
    after = await _table_count(conn, "mail_tags")
    tags_read = len(desired)
    tags_inserted = after - before

    # Резолв id тегов по имени (в т.ч. уже существовавших до запуска — например,
    # 10 builtin, засиженных lifespan seed_builtin_tags).
    names = [t["name"] for t in desired]
    id_rows = (
        await conn.execute(
            text("SELECT name, id FROM mail_tags WHERE name IN :names").bindparams(
                bindparam("names", expanding=True)
            ),
            {"names": names},
        )
    ).all()
    id_by_name: dict[str, uuid.UUID] = {str(n): i for n, i in id_rows}

    rules_before = await _table_count(conn, "mail_tag_rules")
    for tag in desired:
        tag_id = id_by_name[tag["name"]]
        for rule in tag["rules"]:
            await conn.execute(
                _INSERT_TAG_RULE,
                {"tag_id": tag_id, "type": rule["type"], "pattern": rule["pattern"]},
            )
    rules_after = await _table_count(conn, "mail_tag_rules")

    tag_ids = [id_by_name[name] for name in names]
    return (
        (tags_read, tags_inserted, tags_read - tags_inserted),
        rules_after - rules_before,
        tag_ids,
    )


async def _reapply_tags(conn: AsyncConnection, tag_ids: list[uuid.UUID]) -> int:
    """Пере-применяет 16 тегов ко ВСЕМ письмам через побуквенный движок (ADR-044 §5/§10).

    `message_tags` НЕ переносятся — разметка воспроизводится детерминированными
    правилами. Возвращает число вставленных строк mail_message_tags.
    """
    _, apply_sql = _import_app_symbols()
    before = await _table_count(conn, "mail_message_tags")
    stmt = text(apply_sql)
    for tag_id in tag_ids:
        await conn.execute(stmt, {"tag_id": tag_id})
    after = await _table_count(conn, "mail_message_tags")
    return after - before


def _import_app_symbols() -> tuple[list[dict[str, Any]], str]:
    """Лениво импортирует канон builtin-тегов и SQL пере-применения из пакета CRM `app`.

    Импорт ленивый (внутри функции) — избегает жёсткой зависимости extract-фазы от
    пакета `app` (extract может выполняться на хосте агрегатора, где `app` нет).
    """
    _ensure_app_importable()
    from app.domain.mail_builtin_tags import BUILTIN_TAGS
    from app.domain.mail_tags_sql import APPLY_TAG_TO_EXISTING

    builtin = [dict(t) for t in BUILTIN_TAGS]
    return builtin, APPLY_TAG_TO_EXISTING


def _ensure_app_importable() -> None:
    """Добавляет каталог backend/ в sys.path, чтобы `import app...` работал при запуске скрипта."""
    backend_dir = str(Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


async def _advance_message_sequence(conn: AsyncConnection) -> None:
    """Выставляет mail_messages_id_seq на MAX(id) (ADR-044 §10: setval = max(id)+1).

    ⚠️ Вызывать ТОЛЬКО на боевом пути и ТОЛЬКО ПОСЛЕ успешного commit загрузочной
    транзакции (см. `_run_load`): операции над sequence в PostgreSQL НЕ
    транзакционны — `setval` пережил бы ROLLBACK dry-run и молча сдвинул бы боевую
    последовательность. `setval(seq, MAX(id))` (is_called=true) → следующий
    nextval вернёт MAX(id)+1, как требует §10.

    Guard: `pg_get_serial_sequence` возвращает NULL для `GENERATED ... AS IDENTITY`.
    Текущая схема — `BigInteger + autoincrement` (SERIAL) → последовательность есть.
    Если схема сменится на IDENTITY, NULL приведёт к понятной ошибке, а не к
    `setval(NULL, ...)`.
    """
    seq_name = (
        await conn.execute(text("SELECT pg_get_serial_sequence('mail_messages', 'id')"))
    ).scalar_one()
    if seq_name is None:
        raise MigrationError(
            "pg_get_serial_sequence('mail_messages','id') вернул NULL: у столбца нет "
            "связанной SERIAL-последовательности (вероятно, схема перешла на "
            "GENERATED ... AS IDENTITY). setval невозможен — обновите этот шаг под "
            "новую схему (ALTER TABLE ... ALTER COLUMN id RESTART WITH ...)."
        )
    await conn.execute(
        text("SELECT setval(:seq, (SELECT COALESCE(MAX(id), 1) FROM mail_messages))").bindparams(
            seq=seq_name
        )
    )
    logger.info("load: mail_messages_id_seq выставлен на MAX(id) (seq=%s)", seq_name)


async def load(conn: AsyncConnection, dataset: MigrationDataset) -> LoadReport:
    """Загружает датасет в CRM в порядке cut-over (ADR-044 §10 шаг 2). Fail-fast внутри."""
    now = datetime.now(tz=UTC)
    report = LoadReport()

    # --- Резолв справочников CRM + fail-fast-проверки ДО записи ---
    team_map = await _load_team_map(conn)
    user_map = await _load_user_by_telegram(conn)
    _validate_referential(dataset)
    accounts = _resolve_accounts(dataset.accounts, team_map, now)
    links = _resolve_telegram_links(dataset.telegram_links, user_map, now)
    author_bridge = _build_author_bridge(dataset.telegram_links)
    sent_rows = _resolve_sent_messages(dataset.sent_messages, author_bridge, user_map)

    # --- 1. Ящики ---
    report.accounts = await _insert_chunked(
        conn, "mail_accounts", _INSERT_ACCOUNT, accounts, "mail_accounts"
    )

    # --- 2. Письма (preserve id, notified_at=now всем; setval — post-commit, §10) ---
    msg_rows = [
        {
            "id": m["id"],
            "mail_account_id": m["mail_account_id"],
            "uidvalidity": m["uidvalidity"],
            "uid": m["uid"],
            "message_id_header": m["message_id_header"],
            "subject": m["subject"],
            "from_addr": m["from_addr"],
            "from_name": m["from_name"],
            # to_addrs/body_text — NOT NULL в CRM (server_default '' НЕ применяется
            # при явном INSERT со значением None → NOT NULL violation → откат всей
            # транзакции). Защита: None → '' (ADR-044 §2; 0021:81,84).
            "to_addrs": m["to_addrs"] if m["to_addrs"] is not None else "",
            "cc_addrs": m["cc_addrs"],
            "internal_date": _parse_dt(m["internal_date"])
            if isinstance(m["internal_date"], str)
            else m["internal_date"],
            "body_text": m["body_text"] if m["body_text"] is not None else "",
            "body_html": m["body_html"],
            "body_truncated": m["body_truncated"],
            "body_present": m["body_present"],
            "in_reply_to": m["in_reply_to"],
            "refs_header": m["refs_header"],
            "notified_at": now,
            "created_at": now,
        }
        for m in dataset.messages
    ]
    report.messages = await _insert_chunked(
        conn, "mail_messages", _INSERT_MESSAGE, msg_rows, "mail_messages"
    )
    # NB: `setval` mail_messages_id_seq вынесен ИЗ этой транзакции —
    # выполняется на боевом пути ПОСЛЕ commit (`_run_load` → `_advance_message_sequence`),
    # т.к. sequence-операции не транзакционны и пережили бы ROLLBACK dry-run.

    # --- 3. Теги (16 глобальных) + правила ---
    report.tags, report.tag_rules_inserted, tag_ids = await _load_tags(conn, dataset.custom_tags)
    logger.info("load: тегов %s, правил вставлено %d", report.tags, report.tag_rules_inserted)

    # --- 4. Пере-применение тегов по корпусу (вместо переноса message_tags) ---
    report.reapplied_message_tags = await _reapply_tags(conn, tag_ids)
    logger.info("load: пере-применено привязок тегов: %d", report.reapplied_message_tags)

    # --- 5. Telegram-привязки ---
    report.telegram_links = await _insert_chunked(
        conn, "mail_telegram_links", _INSERT_TG_LINK, links, "mail_telegram_links"
    )

    # --- 6. История уведомлений (status='sent' всем) ---
    notif_rows = [
        {
            "message_id": n["message_id"],
            "telegram_user_id": n["telegram_user_id"],
            "sent_at": _parse_dt(n["sent_at"]) if isinstance(n["sent_at"], str) else n["sent_at"],
            "created_at": now,
            "updated_at": now,
        }
        for n in dataset.notifications
    ]
    report.notifications = await _insert_chunked(
        conn,
        "mail_telegram_notifications",
        _INSERT_NOTIFICATION,
        notif_rows,
        "mail_telegram_notifications",
    )

    # --- 7. Отправленные reply (sent_messages → mail_sent_messages) ---
    report.sent_messages = await _insert_chunked(
        conn, "mail_sent_messages", _INSERT_SENT_MESSAGE, sent_rows, "mail_sent_messages"
    )

    return report


# ------------------------------------------------------------------------------
# Отчёт
# ------------------------------------------------------------------------------


def _print_report(report: LoadReport, *, dry_run: bool) -> None:
    """Печатает итоговый отчёт и сверку с ожидаемыми объёмами прода."""
    mode = "DRY-RUN (ROLLBACK — ничего не записано)" if dry_run else "БОЕВОЙ (COMMIT)"

    def line(name: str, triple: tuple[int, int, int], expected: int) -> str:
        read, inserted, skipped = triple
        mark = "ok" if read >= expected else "ВНИМАНИЕ: меньше ожидаемого"
        return (
            f"  {name:<26} прочитано={read:<6} вставлено={inserted:<6} "
            f"пропущено(дубль)={skipped:<6} ожидалось>={expected:<6} [{mark}]"
        )

    lines = [
        "",
        "=" * 88,
        f"ОТЧЁТ МИГРАЦИИ — {mode}",
        "=" * 88,
        line("mail_accounts", report.accounts, EXPECTED_ACCOUNTS),
        line("mail_messages", report.messages, EXPECTED_MESSAGES),
        line("mail_tags", report.tags, EXPECTED_TAGS),
        f"  mail_tag_rules             вставлено={report.tag_rules_inserted}",
        f"  mail_message_tags (reapply) вставлено={report.reapplied_message_tags} "
        f"(воспроизведение разметки, message_tags НЕ переносились)",
        line("mail_telegram_links", report.telegram_links, EXPECTED_TELEGRAM_LINKS),
        line("mail_telegram_notifications", report.notifications, EXPECTED_NOTIFICATIONS),
        # sent_messages — сверочной константы в §10 нет (растёт, нижней границы не задано);
        # печатаем факт без ожидания. id — детерминированный uuid5, автор best-effort NULL.
        f"  {'mail_sent_messages':<26} прочитано={report.sent_messages[0]:<6} "
        f"вставлено={report.sent_messages[1]:<6} пропущено(дубль)={report.sent_messages[2]:<6}",
        "=" * 88,
    ]
    logger.info("\n".join(lines))


# ------------------------------------------------------------------------------
# Оркестрация режимов
# ------------------------------------------------------------------------------


async def _run_extract(work_dir: Path) -> None:
    """Режим extract: читает агрегатор, пишет дамп в каталог (read-only на источнике)."""
    agg_url = _require_env("AGG_DATABASE_URL")
    engine = _make_engine(agg_url)
    try:
        async with engine.connect() as conn:
            dataset = await extract(conn)
        dump_dataset(dataset, work_dir)
        logger.info("extract завершён: дамп в %s", work_dir)
    finally:
        await engine.dispose()


async def _run_load(dataset: MigrationDataset, *, dry_run: bool) -> None:
    """Режим load: пишет датасет в CRM в ОДНОЙ транзакции (dry-run → ROLLBACK)."""
    crm_url = _require_env("CRM_DATABASE_URL", fallback="DATABASE_URL")
    engine = _make_engine(crm_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                report = await load(conn, dataset)
                if dry_run:
                    await trans.rollback()
                    logger.info("DRY-RUN: транзакция откачена — БД CRM не изменена")
                else:
                    await trans.commit()
                    logger.info("COMMIT: миграция зафиксирована в БД CRM")
            except Exception:
                await trans.rollback()
                logger.error("Ошибка загрузки — транзакция откачена (изменений нет)")
                raise

        # setval mail_messages_id_seq — ТОЛЬКО на боевом пути и ТОЛЬКО ПОСЛЕ commit,
        # ОТДЕЛЬНЫМ подключением. Sequence-операции в PostgreSQL НЕ транзакционны:
        # выполни мы setval в загрузочной транзакции, он пережил бы ROLLBACK dry-run
        # и молча сдвинул бы боевую последовательность (подрывая саму гарантию
        # dry-run как безопасной репетиции). На dry-run setval не выполняется вовсе.
        if not dry_run:
            try:
                async with engine.connect() as seq_conn, seq_conn.begin():
                    await _advance_message_sequence(seq_conn)
            except Exception as exc:
                # Данные УЖЕ закоммичены (COMMIT выше прошёл) — не выполнился ТОЛЬКО
                # финальный setval. Без явного объяснения оператор увидит лишь
                # стектрейс и не поймёт, что данные целы и как чинить. Логируем
                # развёрнутую инструкцию, ВСЁ РАВНО печатаем отчёт (данные залиты —
                # оператор должен видеть, что именно загружено) и пробрасываем
                # исключение дальше, чтобы код возврата остался ненулевым.
                logger.error(
                    "\n".join(
                        [
                            "",
                            "!" * 88,
                            "СБОЙ СДВИГА ПОСЛЕДОВАТЕЛЬНОСТИ mail_messages_id_seq — "
                            "ДАННЫЕ УЖЕ ЗАЛИТЫ И ЦЕЛЫ.",
                            "!" * 88,
                            "Миграция закоммичена успешно (COMMIT прошёл). Не выполнился "
                            "ТОЛЬКО финальный сдвиг последовательности mail_messages_id_seq.",
                            f"Причина: {exc!r}",
                            "",
                            "ЧЕМ ОПАСНО ОСТАВИТЬ КАК ЕСТЬ: nextval последовательности отстаёт "
                            "от MAX(id). Первая же вставка нового письма через push-ingest "
                            "упадёт с 'duplicate key' по первичному ключу (идемпотентность "
                            "ON CONFLICT навешана на uq_account_uidv_uid, НЕ на PK), и приём "
                            "почты будет сбоить, пока nextval не переползёт MAX(id).",
                            "",
                            "НЕ ВКЛЮЧАЙТЕ push-ingest до устранения — иначе гарантированы "
                            "ошибки 'duplicate key' при приёме новых писем.",
                            "",
                            "КАК ПОЧИНИТЬ (достаточно одного из двух):",
                            "  1) Выполнить в БД CRM вручную SQL:",
                            "     SELECT setval(pg_get_serial_sequence('mail_messages','id'), "
                            "(SELECT MAX(id) FROM mail_messages));",
                            "  2) ЛИБО перезапустить эту миграцию: повторный прогон идемпотентен "
                            "(данные НЕ задвоятся) и повторит только setval.",
                            "!" * 88,
                        ]
                    )
                )
                _print_report(report, dry_run=dry_run)
                raise

        _print_report(report, dry_run=dry_run)
    finally:
        await engine.dispose()


async def _run_direct(*, dry_run: bool) -> None:
    """Режим direct: extract из агрегатора + load в CRM в одном процессе."""
    agg_url = _require_env("AGG_DATABASE_URL")
    agg_engine = _make_engine(agg_url)
    try:
        async with agg_engine.connect() as conn:
            dataset = await extract(conn)
    finally:
        await agg_engine.dispose()
    await _run_load(dataset, dry_run=dry_run)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Разбор аргументов CLI."""
    parser = argparse.ArgumentParser(
        description="ETL миграция данных почты: mail-агрегатор → CRM (ADR-044 §10)."
    )
    parser.add_argument(
        "--mode",
        choices=("direct", "extract", "load"),
        default="direct",
        help="direct: оба подключения в процессе; extract: дамп источника; load: заливка дампа.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Полная репетиция с ROLLBACK: ничего не записывается (direct/load).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("./_mail_dump"),
        help="Каталог дампа для режимов extract/load (default: ./_mail_dump).",
    )
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    """Асинхронная точка входа: диспетчеризация по режиму."""
    if args.mode == "extract":
        if args.dry_run:
            logger.warning("extract — read-only на источнике; --dry-run не применяется")
        await _run_extract(args.work_dir)
    elif args.mode == "load":
        dataset = load_dataset(args.work_dir)
        await _run_load(dataset, dry_run=args.dry_run)
    else:  # direct
        await _run_direct(dry_run=args.dry_run)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Синхронная обёртка: логирование + запуск asyncio + маппинг ошибок в exit-код."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args(argv)
    try:
        return asyncio.run(_async_main(args))
    except MigrationError as exc:
        logger.error("FAIL-FAST: %s", exc)
        return 2
    except Exception as exc:
        # Верхнеуровневый барьер CLI: любую иную ошибку логируем и отдаём код возврата.
        logger.exception("Непредвиденная ошибка миграции: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
