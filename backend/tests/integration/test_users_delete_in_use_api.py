"""Integration: `DELETE /api/users/{id}` → `409 user_in_use` (TD-077) на реальном Postgres.

Прикладной `409` обязан быть **зеркалом FK `ON DELETE RESTRICT`** на `users.id`
(`document_nodes.owner_id` — ADR-059, `document_attachments.created_by` — ADR-068). Тот же
принцип, что `409 role_in_use` для `users.role_id`: нарушение целостности не должно
всплывать наружу как `500 internal_error`.

**Почему тесты нужны именно интеграционные.** Исход задаёт поведение Postgres, а не код:
перечисленные FK **не `DEFERRABLE`**, поэтому `RESTRICT` срабатывает уже на выполнении
`DELETE`-statement'а, а не на `commit`. Первая редакция реализации оборачивала `try` только
вокруг `commit` — и отдавала `500`. Ни один фейк-репозиторий этого не воспроизводит: в нём
`commit()` — no-op, а FK не существует вовсе. Поэтому здесь проверяется и сам факт
не-отложенности констрейнтов (`pg_constraint.condeferrable`), и оба прикладных пути.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from documents_helpers import (
    add_membership,
    build_app,
    build_principal,
    client,
    documents_db,
    seed_node,
    seed_role,
    seed_team,
    seed_user,
    superadmin_id,
)
from sqlalchemy import text as sa_text

PNG = b"\x89PNG\r\n\x1a\n" + b"payload" * 4

# FK `ON DELETE RESTRICT`, которые обязаны держать пользователя (04-api.md#delete-apiusersid).
_RESTRICT_FKS = {
    "document_nodes": "owner_id",
    "document_attachments": "created_by",
}


@pytest.fixture
def attachments_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Каталог вложений в tmp — загрузка идёт через реальный эндпоинт."""
    from app.config import get_settings

    root = tmp_path / "attachments"
    monkeypatch.setenv("DOCUMENTS_ATTACHMENTS_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def admin_app(sm: Any) -> Any:
    """Приложение под актором admin-уровня (гейт `DELETE /api/users/{id}` — `require_admin`)."""
    return build_app(sm, build_principal())


def user_app(sm: Any, user_id: uuid.UUID, role_id: uuid.UUID) -> Any:
    """Приложение под КОНКРЕТНЫМ пользователем (его `user_id` уйдёт в `created_by`)."""
    return build_app(sm, build_principal(user_id=user_id, role_id=role_id))


async def insert_attachment(sm: Any, *, node_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    """Строка вложения напрямую (когда важен только `created_by`, а не байты на диске)."""
    async with sm() as session:
        attachment_id = (
            await session.execute(
                sa_text(
                    "INSERT INTO document_attachments "
                    "(document_node_id, filename, mime, size_bytes, checksum, created_by) "
                    "VALUES (:n, 'pic.png', 'image/png', 10, :c, :u) RETURNING id"
                ).bindparams(n=node_id, c="a" * 64, u=created_by)
            )
        ).scalar_one()
        await session.commit()
        return attachment_id


def as_char(value: Any) -> str:
    """Значение pg-типа `"char"` → строка (asyncpg отдаёт `bytes`, psycopg — `str`).

    Тот же приём, что в `test_mail_migration_0027.py`: без него сравнение `confdeltype`
    молча провалилось бы на `b'r' != 'r'`.
    """
    return value.decode() if isinstance(value, bytes) else str(value)


async def user_exists(sm: Any, user_id: uuid.UUID) -> bool:
    async with sm() as session:
        return bool(
            (
                await session.execute(
                    sa_text("SELECT 1 FROM users WHERE id = :i").bindparams(i=user_id)
                )
            ).first()
        )


async def leader_of(sm: Any, team_id: uuid.UUID) -> uuid.UUID | None:
    async with sm() as session:
        return (
            await session.execute(
                sa_text("SELECT leader_id FROM teams WHERE id = :i").bindparams(i=team_id)
            )
        ).scalar_one()


# --- Констрейнты действительно НЕ отложенные ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("table", "column"), sorted(_RESTRICT_FKS.items()))
async def test_restrict_fk_on_users_is_not_deferrable(table: str, column: str) -> None:
    """FK на `users.id` — `RESTRICT` и **не `DEFERRABLE`** (основание требования к реализации).

    Именно из-за не-отложенности `IntegrityError` прилетает на `DELETE`-execute, а не на
    `commit`. Если констрейнт когда-нибудь сделают `DEFERRABLE`, момент срабатывания
    сместится на фиксацию — тест обязан это заметить, потому что от этого зависит, где
    должен стоять `try` в `UserService.delete_user`.
    """
    async with documents_db() as sm, sm() as session:
        rows = (
            await session.execute(
                sa_text(
                    "SELECT con.confdeltype, con.condeferrable "
                    "FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(con.conkey) "
                    "WHERE c.relname = :t AND a.attname = :col AND con.contype = 'f'"
                ).bindparams(t=table, col=column)
            )
        ).all()

    assert rows, f"FK {table}.{column} → users.id отсутствует"
    confdeltype, condeferrable = rows[0]
    assert as_char(confdeltype) == "r", "ожидался ON DELETE RESTRICT"
    assert condeferrable is False, "FK не должен быть DEFERRABLE (см. докстринг модуля)"


# --- Путь 1: владелец узла документов ---------------------------------------------------


@pytest.mark.asyncio
async def test_owner_of_document_node_cannot_be_deleted_409_not_500() -> None:
    """Автор узла документов → `409 user_in_use` (а НЕ `500`)."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id = owner.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "user_in_use"
        assert await user_exists(sm, owner_id) is True


@pytest.mark.asyncio
async def test_owner_of_folder_also_blocks_deletion() -> None:
    """Папка — такой же узел с `owner_id`: блокирует наравне с документом."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            await seed_node(session, node_type="folder", owner_id=owner.id)
            await session.commit()
            owner_id = owner.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "user_in_use"


@pytest.mark.asyncio
async def test_soft_deleted_document_still_blocks_deletion() -> None:
    """**Soft-deleted узлы тоже блокируют** — tombstone остаётся физической строкой.

    Нормативно (04-api.md): «удалил все свои документы в UI» блокировку НЕ снимает,
    потому что soft-delete не убирает `owner_id` из таблицы (tombstones живут для RAG).
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            node = await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id, node_id = owner.id, node.id

        # Soft-delete штатным путём (проставляет deleted_at, строку не удаляет).
        async with client(admin_app(sm)) as http:
            deleted = await http.delete(f"/api/documents/nodes/{node_id}")
            response = await http.delete(f"/api/users/{owner_id}")

        assert deleted.status_code == 204
        async with sm() as session:
            still_there = (
                await session.execute(
                    sa_text(
                        "SELECT deleted_at IS NOT NULL FROM document_nodes WHERE id = :i"
                    ).bindparams(i=node_id)
                )
            ).scalar_one()
        assert still_there is True, "soft-delete обязан оставить физическую строку"

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "user_in_use"


# --- Путь 2: загрузивший вложение (узел чужой) -------------------------------------------


@pytest.mark.asyncio
async def test_uploader_of_attachment_on_foreign_node_cannot_be_deleted() -> None:
    """Пользователь, который **только загрузил вложение** (узел чужой) → `409 user_in_use`.

    Второй, независимый путь блокировки: `document_attachments.created_by`. Узел
    принадлежит другому владельцу, поэтому FK `document_nodes.owner_id` здесь не при чём —
    держит именно авторство вложения.
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            uploader = await seed_user(session, role)
            # Узел принадлежит системному якорю, а не загрузившему.
            node = await seed_node(session, node_type="document", owner_id=superadmin_id())
            await session.commit()
            uploader_id, node_id = uploader.id, node.id

        await insert_attachment(sm, node_id=node_id, created_by=uploader_id)

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{uploader_id}")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "user_in_use"
        assert await user_exists(sm, uploader_id) is True


@pytest.mark.asyncio
async def test_uploader_via_real_upload_endpoint_cannot_be_deleted(
    attachments_dir: Path,
) -> None:
    """Тот же путь, но вложение создано РЕАЛЬНЫМ эндпоинтом загрузки (сквозной сценарий).

    `created_by` проставляется из принципала загружающего — так это происходит в проде.
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(
                session, permissions={"documents": ["view", "create", "edit", "delete", "share"]}
            )
            uploader = await seed_user(session, role)
            node = await seed_node(session, node_type="document", owner_id=superadmin_id())
            await session.commit()
            uploader_id, role_id, node_id = uploader.id, role.id, node.id

        async with client(user_app(sm, uploader_id, role_id)) as http:
            uploaded = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files={"file": ("pic.png", PNG, "image/png")},
            )
        assert uploaded.status_code == 201, uploaded.text

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{uploader_id}")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "user_in_use"


# --- Тело ответа: анти-энумерация ----------------------------------------------------------


@pytest.mark.asyncio
async def test_409_body_has_null_details_and_does_not_enumerate_nodes() -> None:
    """`details = null` и никаких id узлов в теле — анти-энумерация ADR-059 не ослабляется.

    Иначе `409` стал бы каналом раскрытия существования документов, невидимых актору
    по роли.
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            secret = await seed_node(
                session, node_type="document", name="Секретный", owner_id=owner.id
            )
            await session.commit()
            owner_id, secret_id = owner.id, secret.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "user_in_use"
    assert error["details"] is None
    assert (
        error["message"] == "Пользователь владеет документами или вложениями — удаление запрещено"
    )
    # Ни id, ни имя удерживающего узла наружу не идут.
    assert str(secret_id) not in response.text
    assert "Секретный" not in response.text


@pytest.mark.asyncio
async def test_409_body_carries_no_sql_or_exception_text() -> None:
    """Текст `IntegrityError`/имя констрейнта наружу не пробрасываются."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id = owner.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

    for leak in ("IntegrityError", "psycopg", "asyncpg", "fk_document", "DETAIL", "violates"):
        assert leak not in response.text


# --- Состояние БД после 409 не изменено (rollback, в т.ч. лидерства) --------------------------


@pytest.mark.asyncio
async def test_409_rolls_back_team_leadership_auto_transfer() -> None:
    """**После `409` состояние БД не изменено вовсе** — включая авто-передачу лидерства.

    Авто-передача (ADR-026) идёт в ТОЙ ЖЕ транзакции ДО удаления, поэтому без `rollback`
    остался бы частичный эффект: пользователь на месте, но команду он уже не ведёт.
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            leader = await seed_user(session, role, username="leader")
            member = await seed_user(session, role, username="member")
            team = await seed_team(session)
            await add_membership(session, leader.id, team.id)
            await add_membership(session, member.id, team.id)
            await session.execute(
                sa_text("UPDATE teams SET leader_id = :u WHERE id = :t").bindparams(
                    u=leader.id, t=team.id
                )
            )
            # Документ, который и заблокирует удаление.
            await seed_node(session, node_type="document", owner_id=leader.id)
            await session.commit()
            leader_id, member_id, team_id = leader.id, member.id, team.id

        assert await leader_of(sm, team_id) == leader_id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{leader_id}")

        assert response.status_code == 409
        # Лидерство НЕ передано участнику — транзакция откачена целиком.
        assert await leader_of(sm, team_id) == leader_id
        assert await leader_of(sm, team_id) != member_id
        assert await user_exists(sm, leader_id) is True


@pytest.mark.asyncio
async def test_409_leaves_memberships_and_channel_extras_intact() -> None:
    """Каскады `user_teams`/`user_channel_teams` тоже не сработали (DELETE откачен)."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            team = await seed_team(session)
            await add_membership(session, owner.id, team.id)
            await session.execute(
                sa_text(
                    "INSERT INTO user_channel_teams (user_id, channel, team_id) "
                    "VALUES (:u, 'mail', :t)"
                ).bindparams(u=owner.id, t=team.id)
            )
            await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id = owner.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

        assert response.status_code == 409
        async with sm() as session:
            memberships = (
                await session.execute(
                    sa_text("SELECT count(*) FROM user_teams WHERE user_id = :u").bindparams(
                        u=owner_id
                    )
                )
            ).scalar_one()
            extras = (
                await session.execute(
                    sa_text(
                        "SELECT count(*) FROM user_channel_teams WHERE user_id = :u"
                    ).bindparams(u=owner_id)
                )
            ).scalar_one()
        assert memberships == 1
        assert extras == 1


@pytest.mark.asyncio
async def test_409_does_not_delete_the_holding_document_or_attachment() -> None:
    """Удерживающие строки на месте (409 ничего не «подчистил» ради удаления)."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            node = await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id, node_id = owner.id, node.id

        attachment_id = await insert_attachment(sm, node_id=node_id, created_by=owner_id)

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{owner_id}")

        assert response.status_code == 409
        async with sm() as session:
            nodes = (
                await session.execute(
                    sa_text("SELECT count(*) FROM document_nodes WHERE id = :i").bindparams(
                        i=node_id
                    )
                )
            ).scalar_one()
            attachments = (
                await session.execute(
                    sa_text("SELECT count(*) FROM document_attachments WHERE id = :i").bindparams(
                        i=attachment_id
                    )
                )
            ).scalar_one()
        assert nodes == 1
        assert attachments == 1


# --- Happy path и соседние коды не сломаны -----------------------------------------------------


@pytest.mark.asyncio
async def test_user_without_documents_is_deleted_204() -> None:
    """Пользователь без документов и вложений удаляется штатно → `204`."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            user = await seed_user(session, role)
            await session.commit()
            user_id = user.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{user_id}")

        assert response.status_code == 204
        assert response.content == b""
        assert await user_exists(sm, user_id) is False


@pytest.mark.asyncio
async def test_team_leader_without_documents_is_deleted_and_leadership_transfers() -> None:
    """Регресс ADR-026: лидер БЕЗ документов удаляется, лидерство авто-передаётся.

    Контрольная точка к кейсу отката: механизм авто-передачи работает, и `409` выше — это
    именно откат, а не «передача никогда не выполнялась».
    """
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            leader = await seed_user(session, role, username="leader2")
            member = await seed_user(session, role, username="member2")
            team = await seed_team(session)
            await add_membership(session, leader.id, team.id)
            await add_membership(session, member.id, team.id)
            await session.execute(
                sa_text("UPDATE teams SET leader_id = :u WHERE id = :t").bindparams(
                    u=leader.id, t=team.id
                )
            )
            await session.commit()
            leader_id, member_id, team_id = leader.id, member.id, team.id

        async with client(admin_app(sm)) as http:
            response = await http.delete(f"/api/users/{leader_id}")

        assert response.status_code == 204
        assert await leader_of(sm, team_id) == member_id
        assert await user_exists(sm, leader_id) is False


@pytest.mark.asyncio
async def test_repeat_delete_after_409_is_still_409_not_404() -> None:
    """Повтор после `409` — снова `409`: пользователь не был удалён (идемпотентность исхода)."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id = owner.id

        async with client(admin_app(sm)) as http:
            first = await http.delete(f"/api/users/{owner_id}")
            second = await http.delete(f"/api/users/{owner_id}")

        assert first.status_code == 409
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "user_in_use"


@pytest.mark.asyncio
async def test_missing_user_is_404_not_409() -> None:
    """Несуществующий пользователь → `404 user_not_found` (прецеденция кодов не сломана)."""
    async with documents_db() as sm, client(admin_app(sm)) as http:
        response = await http.delete(f"/api/users/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


@pytest.mark.asyncio
async def test_deletion_unblocks_after_holding_rows_are_gone() -> None:
    """`409` снимается, когда удерживающих строк не остаётся — блокировка не «залипает»."""
    async with documents_db() as sm:
        async with sm() as session:
            role = await seed_role(session)
            owner = await seed_user(session, role)
            node = await seed_node(session, node_type="document", owner_id=owner.id)
            await session.commit()
            owner_id, node_id = owner.id, node.id

        async with client(admin_app(sm)) as http:
            blocked = await http.delete(f"/api/users/{owner_id}")
        assert blocked.status_code == 409

        # Физическое удаление узла (прикладной операции переназначения авторства пока нет).
        async with sm() as session:
            await session.execute(
                sa_text("DELETE FROM document_nodes WHERE id = :i").bindparams(i=node_id)
            )
            await session.commit()

        async with client(admin_app(sm)) as http:
            unblocked = await http.delete(f"/api/users/{owner_id}")

        assert unblocked.status_code == 204
        assert await user_exists(sm, owner_id) is False
