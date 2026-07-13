"""Репозиторий реестра пользователей (SQLAlchemy 2.0 async, modules/auth, ADR-021/025).

Роль подгружается eager через `User.role` (`lazy="joined"`). CRM-команды (`User.teams`)
грузятся точечно через `selectinload` в `list_all`/`get_with_teams` (в hot-path
принципала `get_by_id` не загружаются). Членство в командах (`user_teams`) пишется
явными statements (`set_membership`) — источник записи под контролем сервиса; при
изменении набора команд `created_at` существующих строк СОХРАНЯЕТСЯ (дата добавления
важна для авто-передачи лидерства, ADR-026), новым строкам ставится `now()`.

**Невидимость системной строки-якоря (ADR-051 §1.4, единое правило — здесь, а не
спец-проверками в сервисах):**

- **Методы-резолверы** (возвращают пользователя как объект/субъект: реестр, резолв
  логина, резолв Telegram-SSO, валидация ссылок) — исключают якорь (`NOT is_system`):
  `list_all`, `get_by_id`, `get_with_teams`, `get_by_username`, `get_by_telegram`,
  `get_existing_ids`, `delete_by_id`.
- **Методы уникальности** (`exists_by_username`, `exists_by_telegram`) — видят ВСЕ
  строки: они зеркалят DB-констрейнты, иначе `409` подменился бы `500`-IntegrityError.
- **`ensure_superadmin_anchor`** — единственный писатель строки-якоря (ADR-051 §1.3).
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.permissions import full_catalog_permissions
from app.domain.superadmin import SUPERADMIN_USER_ID, SUPERADMIN_USERNAME
from app.infra.passwords import hash_password
from app.logging import get_logger
from app.models.role import Role
from app.models.team import user_teams
from app.models.user import User

logger = get_logger(__name__)

# Имя встроенной роли (сид data-миграции 0008) — первый шаг цепочки резолва роли якоря.
_ADMIN_ROLE_NAME = "admin"
# Длина случайного секрета «locked»-пароля якоря (байт до base64url-кодирования).
# `hash_password` усекает до 72 байт (лимит bcrypt) — 86-символьный `token_urlsafe(64)`
# хэшируется корректно. Plaintext нигде не хранится и не логируется.
_ANCHOR_SECRET_BYTES = 64


class UserRepository:
    """CRUD над `users` + уникальность username/telegram + членство в командах."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(
        self,
        *,
        username: str,
        telegram: str | None,
        password_hash: str | None,
        role_id: uuid.UUID,
    ) -> User:
        """Создаёт пользователя (пароль — только bcrypt-хэш ИЛИ None для беспарольного).

        Членство в командах записывается отдельно (`set_membership`).
        """
        user = User(
            username=username,
            telegram=telegram,
            password_hash=password_hash,
            role_id=role_id,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def list_all(self) -> list[User]:
        """Все пользователи (с ролью и командами), сортировка `created_at ASC, id`.

        Якорь супер-админа исключён (`NOT is_system`, ADR-051 §1.4) ⇒ его нет в
        `GET /api/users`.
        """
        stmt = (
            select(User)
            .where(User.is_system.is_(False))
            .options(selectinload(User.teams))
            .order_by(User.created_at.asc(), User.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.unique().scalars().all())

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Возвращает пользователя (с ролью) по id или None. Без загрузки команд.

        **`select(...)`, а НЕ `session.get(...)`** (ADR-051 §1.4): PK-lookup через
        identity-map не позволяет выразить предикат `NOT is_system`, а он критичен
        именно здесь — это hot-path построения принципала (`get_current_principal`) и
        путь Telegram-SSO (`MailTelegramService._resolve_user` резолвит по `link.user_id`).
        Якорь по своему `id` не резолвится ⇒ `PATCH`/`DELETE /api/users/{id}` → 404.
        """
        stmt = select(User).where(User.id == user_id, User.is_system.is_(False)).limit(1)
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_with_teams(self, user_id: uuid.UUID) -> User | None:
        """Пользователь с ролью и командами (для тела ответа users API) или None.

        Якорь невидим (`NOT is_system`, ADR-051 §1.4).
        """
        stmt = (
            select(User)
            .options(selectinload(User.teams))
            .where(User.id == user_id, User.is_system.is_(False))
        )
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Возвращает пользователя (с ролью) по username или None (для логина).

        Якорь невидим (`NOT is_system`) ⇒ вход по БД-ветке под `superadmin@system`
        невозможен (вторая, независимая преграда — locked-хэш, ADR-051 §1.1/§1.6).
        """
        stmt = select(User).where(User.username == username, User.is_system.is_(False)).limit(1)
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_by_telegram(self, telegram: str) -> User | None:
        """Возвращает пользователя (с ролью) по нормализованному telegram или None.

        Используется вторым шагом резолвинга логина (вход по Телеграму, ADR-025) и
        Telegram-SSO. Пустой идентификатор не матчит (telegram хранится непустым
        нормализованным). Якорь невидим (`NOT is_system`; его `telegram` — всегда NULL).
        """
        if not telegram:
            return None
        stmt = select(User).where(User.telegram == telegram, User.is_system.is_(False)).limit(1)
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def exists_by_username(
        self, username: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        """Занят ли username (для 409 username_taken).

        Видит ВСЕ строки, включая якорь (ADR-051 §1.4): метод зеркалит DB-констрейнт
        `uq_users_username`, иначе `409` подменился бы `500`-IntegrityError.
        """
        stmt = select(User.id).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def exists_by_telegram(
        self, telegram: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        """Занят ли telegram среди заданных (для 409 telegram_taken). Уже нормализован.

        Видит ВСЕ строки (зеркало DB-констрейнта `uq_users_telegram`, ADR-051 §1.4).
        """
        stmt = select(User.id).where(User.telegram == telegram)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def get_existing_ids(self, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `ids`, реально существующее в `users` (валидация ссылок).

        Якорь исключён (`NOT is_system`, ADR-051 §1.4) ⇒ его невозможно назначить
        лидером или участником команды (`TeamService` → 422) ⇒ строк в `user_teams` у
        него нет и быть не может (инвариант пустоты связей, §1.4(в)).
        """
        if not ids:
            return set()
        stmt = select(User.id).where(User.id.in_(ids), User.is_system.is_(False))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def team_ids_of_user(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Текущий набор id команд пользователя (для вычисления выбывших при PATCH)."""
        stmt = select(user_teams.c.team_id).where(user_teams.c.user_id == user_id)
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def set_membership(self, user_id: uuid.UUID, team_ids: set[uuid.UUID]) -> None:
        """Приводит членство пользователя к набору `team_ids` (в текущей транзакции).

        Существующие строки СОХРАНЯЮТ `created_at` (дата добавления — база авто-передачи
        лидерства, ADR-026): удаляются только выбывшие, добавляются только новые (с
        `created_at = DEFAULT now()`).
        """
        current = await self.team_ids_of_user(user_id)
        to_remove = current - team_ids
        to_add = team_ids - current
        if to_remove:
            await self._session.execute(
                delete(user_teams).where(
                    user_teams.c.user_id == user_id,
                    user_teams.c.team_id.in_(to_remove),
                )
            )
        if to_add:
            await self._session.execute(
                insert(user_teams),
                [{"user_id": user_id, "team_id": tid} for tid in to_add],
            )

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена.

        Якорь неудаляем через API (`NOT is_system`, ADR-051 §1.4) → `False` → 404
        user_not_found.
        """
        stmt = delete(User).where(User.id == user_id, User.is_system.is_(False))
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    # --- Системная строка-якорь супер-админа (ADR-051 §1.3) ---------------------

    async def ensure_superadmin_anchor(self) -> None:
        """Идемпотентный bootstrap строки-якоря супер-админа (ADR-051 §1.3, нормативно).

        Единственный писатель строки. Порядок строго нормативный:

        1. **Резолв роли** — цепочка `_resolve_anchor_role_id` (самодостаточна: ветки
           «ролей нет ⇒ якоря нет» не существует).
        2. **`INSERT … ON CONFLICT DO NOTHING` без target** — накрывает конфликт и по
           `pk_users`, и по `uq_users_username`, и по `uq_users_system_singleton`.
           Повторный старт / несколько воркеров / рестарт — но-оп; существующая строка
           НЕ перезаписывается (пароль-заглушка не ротируется, отметки прочитанности не
           трогаются).
        3. **Верификация** `SELECT` по `SUPERADMIN_USER_ID`: строки нет (например,
           `username` занят древней записью в обход валидации) → ERROR-лог
           `superadmin_anchor_missing`; исключение НЕ бросается — приложение обязано
           подняться (fallback-инвариант ADR-008).

        Коммитит сам: вызывается из `lifespan` (прод) и из фикстуры БД сразу после
        `Base.metadata.create_all` (тесты) — вне какой-либо транзакции сервиса.

        Пароль якоря — bcrypt-хэш случайного секрета («locked account»); plaintext
        отбрасывается, нигде не хранится и не логируется. `NULL` запрещён: он означал бы
        беспарольного пользователя ⇒ ветка «открытого первого входа» (ADR-025) выдала бы
        setup-token любому, кто назовёт этот `username`.
        """
        role_id = await self._resolve_anchor_role_id()
        stmt = (
            pg_insert(User)
            .values(
                id=SUPERADMIN_USER_ID,
                username=SUPERADMIN_USERNAME,
                password_hash=hash_password(secrets.token_urlsafe(_ANCHOR_SECRET_BYTES)),
                role_id=role_id,
                is_active=True,
                is_system=True,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        await self._session.commit()

        exists = await self._session.execute(
            select(User.id).where(User.id == SUPERADMIN_USER_ID).limit(1)
        )
        if exists.first() is None:
            logger.error("superadmin_anchor_missing", user_id=str(SUPERADMIN_USER_ID))

    async def _resolve_anchor_role_id(self) -> uuid.UUID:
        """Роль-заглушка якоря под `NOT NULL` FK `users.role_id` (ADR-051 §1.1, цепочка).

        (1) роль `admin` → (2) самая ранняя роль (`created_at ASC, id ASC`) → (3) ролей
        нет ВООБЩЕ ⇒ создать `admin` с полным каталогом прав (тот же сид, что в
        data-миграции `0008`). Шаг (3) делает bootstrap самодостаточным (в тестах схема
        поднимается `metadata.create_all` без data-миграций, роли `admin` там нет) и не
        воскрешает удалённые данные (антипаттерн ADR-047 §1): роль якоря неудаляема, пока
        он её держит (409 role_in_use), поэтому пустая таблица ролей = только свежая БД.

        **Шаг (3) идемпотентен и гонко-устойчив:** вставка роли — `ON CONFLICT DO NOTHING`
        по уникальному `roles.name` (+ `RETURNING id`). Два воркера, стартовавшие
        одновременно на ПОЛНОСТЬЮ пустой таблице ролей, не роняют друг друга
        IntegrityError'ом: проигравший получает пустой `RETURNING` и перечитывает роль,
        созданную победителем (READ COMMITTED — `SELECT` после завершения конкурирующей
        вставки видит закоммиченную строку), после чего оба вставляют ОДНУ строку-якорь
        (её собственный `ON CONFLICT DO NOTHING` — §1.3).

        **Роль якоря НЕ является источником его прав:** права супер-админа —
        `full_catalog_permissions()` по claim `superadmin=true` (`app/api/deps.py`),
        БД-роль в них не участвует.
        """
        admin_id = await self._role_id_by_name(_ADMIN_ROLE_NAME)
        if admin_id is not None:
            return admin_id

        earliest = await self._session.execute(
            select(Role.id).order_by(Role.created_at.asc(), Role.id.asc()).limit(1)
        )
        row = earliest.first()
        if row is not None:
            return uuid.UUID(str(row[0]))

        created = await self._session.execute(
            pg_insert(Role)
            .values(name=_ADMIN_ROLE_NAME, permissions=full_catalog_permissions())
            .on_conflict_do_nothing(index_elements=[Role.name])
            .returning(Role.id)
        )
        role_id = created.scalar_one_or_none()
        if role_id is not None:
            return uuid.UUID(str(role_id))

        # Пустой RETURNING = роль `admin` только что создал параллельный воркер.
        admin_id = await self._role_id_by_name(_ADMIN_ROLE_NAME)
        if admin_id is None:
            raise RuntimeError("Роль-заглушка якоря не резолвится после ON CONFLICT DO NOTHING")
        return admin_id

    async def _role_id_by_name(self, name: str) -> uuid.UUID | None:
        """`roles.id` по имени или None (шаг (1) цепочки резолва роли якоря)."""
        result = await self._session.execute(select(Role.id).where(Role.name == name).limit(1))
        row = result.first()
        return uuid.UUID(str(row[0])) if row is not None else None


async def ensure_superadmin_anchor(session: AsyncSession) -> None:
    """Точка входа bootstrap'а якоря (ADR-051 §1.3) — тонкий делегат к репозиторию.

    Прод/staging — `app/main.py::lifespan` (после `startup_recovery`; порядок «миграции →
    приложение» гарантирован entrypoint'ом контейнера). Тесты — фикстура БД сразу после
    `Base.metadata.create_all`.
    """
    await UserRepository(session).ensure_superadmin_anchor()
