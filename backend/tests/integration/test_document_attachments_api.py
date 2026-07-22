"""Integration: вложения-изображения документов (ADR-068, 04-api.md#вложения) — реальный Postgres.

Каталог хранения — временный (`DOCUMENTS_ATTACHMENTS_DIR` в `tmp_path`), поэтому проверяется
не только HTTP-контракт, но и **состояние диска**: путь строится только из `id`+`mime`,
права `0600`/`0700`, отсутствие `.tmp` после успеха И после отказа.

Порядок кейсов — по 06-testing-strategy.md; каждый обязательный кейс отдельным тестом.
Главный инвариант: **доступ к картинке = доступ к её узлу**, и все негативные исходы отдачи
дают ЕДИНЫЙ `404 document_attachment_not_found` (анти-энумерация).
"""

from __future__ import annotations

import hashlib
import stat
import uuid
from pathlib import Path
from typing import Any

import pytest
from documents_helpers import (
    build_app,
    build_principal,
    client,
    documents_db,
    seed_node,
    seed_role,
    set_node_roles,
)
from sqlalchemy import text as sa_text

# Полный набор действий документов, но НЕ полный каталог прав ⇒ non-admin (sees_all=False).
_DOC_ALL = {"documents": ["view", "create", "edit", "delete", "share"]}

# --- Байты «изображений»: тип определяется ПО СИГНАТУРЕ, декодируемость не требуется ---

PNG = b"\x89PNG\r\n\x1a\n" + b"payload-png" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"payload-jpeg" * 4
GIF = b"GIF89a" + b"payload-gif" * 4
WEBP = b"RIFF" + (64).to_bytes(4, "little") + b"WEBP" + b"payload-webp" * 4

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><html><body><script>alert(1)</script></body></html>"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32

_SIGNATURES: dict[str, tuple[bytes, str]] = {
    "image/png": (PNG, "png"),
    "image/jpeg": (JPEG, "jpg"),
    "image/webp": (WEBP, "webp"),
    "image/gif": (GIF, "gif"),
}


@pytest.fixture
def attachments_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`DOCUMENTS_ATTACHMENTS_DIR` в tmp — состояние диска становится наблюдаемым."""
    from app.config import get_settings

    root = tmp_path / "attachments"
    monkeypatch.setenv("DOCUMENTS_ATTACHMENTS_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def upload_file(
    content: bytes, *, mime: str = "image/png", filename: str = "picture.png"
) -> dict[str, Any]:
    return {"file": (filename, content, mime)}


def expected_path(root: Path, attachment_id: str, ext: str) -> Path:
    """`<root>/<id[0:2]>/<id[2:4]>/<id>.<ext>` — только машинные значения (ADR-068 §1)."""
    return root / attachment_id[0:2] / attachment_id[2:4] / f"{attachment_id}.{ext}"


def tmp_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.tmp")) if root.exists() else []


async def seed_document(sm: Any, **kwargs: Any) -> str:
    async with sm() as session:
        node = await seed_node(session, node_type="document", **kwargs)
        await session.commit()
        return str(node.id)


def admin_app(sm: Any) -> Any:
    return build_app(sm, build_principal())


# --- Загрузка: успешные форматы ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", list(_SIGNATURES), ids=lambda m: m.split("/")[1])
async def test_upload_valid_image_creates_row_and_file(mime: str, attachments_dir: Path) -> None:
    """png/jpeg/webp/gif → `201`; на диске файл по пути из `id`+`mime`, `checksum` = sha256."""
    content, ext = _SIGNATURES[mime]
    async with documents_db() as sm:
        node_id = await seed_document(sm, content_md="текст")
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(content, mime=mime, filename=f"pic.{ext}"),
            )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["mime"] == mime
    assert payload["size_bytes"] == len(content)
    assert payload["checksum"] == hashlib.sha256(content).hexdigest()
    assert payload["document_node_id"] == node_id
    # URL формирует СЕРВЕР — клиент его не конструирует (ADR-068 §2).
    assert payload["url"] == f"/api/documents/attachments/{payload['id']}"

    path = expected_path(attachments_dir, payload["id"], ext)
    assert path.is_file()
    assert path.read_bytes() == content


@pytest.mark.asyncio
async def test_uploaded_file_and_shard_dirs_have_restrictive_modes(attachments_dir: Path) -> None:
    """Файл `0600`, шард-каталоги `0700` — у вложений ровно один читатель (backend)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )

    path = expected_path(attachments_dir, response.json()["id"], "png")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(attachments_dir.stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_user_filename_never_appears_in_disk_path(attachments_dir: Path) -> None:
    """Path traversal невозможен КОНСТРУКТИВНО: `filename` в пути не участвует вовсе."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(PNG, filename="../../../../etc/passwd.png"),
            )

    assert response.status_code == 201
    payload = response.json()
    # Имя сохранено как метаданные (базовое, без каталогов) — но не как путь.
    assert "/" not in payload["filename"]
    assert expected_path(attachments_dir, payload["id"], "png").is_file()
    # Ни одного файла вне корня вложений не появилось.
    assert {p.name for p in attachments_dir.rglob("*") if p.is_file()} == {f"{payload['id']}.png"}


@pytest.mark.asyncio
async def test_no_tmp_files_left_after_successful_upload(attachments_dir: Path) -> None:
    """После успеха временных файлов не остаётся (`os.replace` + `finally`-уборка)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            await http.post(f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG))

    assert tmp_files(attachments_dir) == []


# --- Загрузка: подделка типа ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "label"), [(SVG, "svg"), (HTML, "html"), (ELF, "elf")], ids=["svg", "html", "elf"]
)
async def test_spoofed_content_type_is_rejected(
    content: bytes, label: str, attachments_dir: Path
) -> None:
    """SVG/HTML/ELF под видом `image/png` → `422 document_attachment_invalid`.

    Гейт против доверия клиентскому `Content-Type`: он подделывается тривиально, а
    сохранённый `mime` управляет заголовком отдачи (активный документ с нашего origin —
    XSS-вектор).
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(content, mime="image/png", filename=f"evil.{label}.png"),
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_attachment_invalid"
    assert list(attachments_dir.rglob("*.png")) == []


@pytest.mark.asyncio
async def test_honest_svg_content_type_is_rejected(attachments_dir: Path) -> None:
    """Честный `image/svg+xml` → тот же `422`: SVG вне whitelist **нормативно**."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(SVG, mime="image/svg+xml", filename="pic.svg"),
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_attachment_invalid"


@pytest.mark.asyncio
async def test_real_png_declared_as_jpeg_is_rejected(attachments_dir: Path) -> None:
    """Расхождение содержимого и заявленного типа отвергается в обе стороны."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(PNG, mime="image/jpeg", filename="pic.jpg"),
            )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_empty_file_is_rejected(attachments_dir: Path) -> None:
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(b"")
            )

    assert response.status_code == 422
    assert tmp_files(attachments_dir) == []


@pytest.mark.asyncio
async def test_no_tmp_files_left_after_rejected_upload(attachments_dir: Path) -> None:
    """После ОТКАЗА временный файл тоже убран — иначе tmp копил бы мусор на каждой попытке."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(SVG, mime="image/png"),
            )

    assert tmp_files(attachments_dir) == []
    assert [p for p in attachments_dir.rglob("*") if p.is_file()] == []


# --- Загрузка: лимит размера ----------------------------------------------------------


@pytest.mark.asyncio
async def test_over_limit_upload_is_rejected_and_stream_aborted(
    attachments_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Превышение `DOCUMENTS_MAX_IMAGE_BYTES` → `422`, файл на диске **не создан**.

    Проверяется, что чтение оборвано: временный файл убран, финального нет.
    """
    from app.config import get_settings

    monkeypatch.setenv("DOCUMENTS_MAX_IMAGE_BYTES", "1024")
    get_settings.cache_clear()

    oversized = b"\x89PNG\r\n\x1a\n" + b"x" * 5000
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(oversized)
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_attachment_invalid"
    assert tmp_files(attachments_dir) == []
    assert [p for p in attachments_dir.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_upload_at_limit_boundary_is_accepted(
    attachments_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ровно лимит — ещё не превышение (граница `>` , а не `>=`)."""
    from app.config import get_settings

    monkeypatch.setenv("DOCUMENTS_MAX_IMAGE_BYTES", "1024")
    get_settings.cache_clear()

    exact = b"\x89PNG\r\n\x1a\n" + b"x" * (1024 - 8)
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(exact)
            )

    assert response.status_code == 201
    assert response.json()["size_bytes"] == 1024


# --- Загрузка: цель ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_into_folder_is_422_validation_error(attachments_dir: Path) -> None:
    """Папка контента не хранит → `422 validation_error`, `field='node_id'`."""
    async with documents_db() as sm:
        async with sm() as session:
            folder = await seed_node(session, node_type="folder")
            await session.commit()
            folder_id = str(folder.id)
        async with client(admin_app(sm)) as http:
            response = await http.post(
                f"/api/documents/nodes/{folder_id}/attachments", files=upload_file(PNG)
            )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["details"][0]["field"] == "node_id"


@pytest.mark.asyncio
async def test_upload_into_invisible_node_is_404_document_node_not_found(
    attachments_dir: Path,
) -> None:
    """Невидимый по роли узел → `404 document_node_not_found` (не `403` — анти-энумерация)."""
    async with documents_db() as sm:
        async with sm() as session:
            role_a = await seed_role(session)
            role_b = await seed_role(session)
            root = await seed_node(session, visibility_mode="restricted")
            await set_node_roles(session, root.id, [role_a.id])
            doc = await seed_node(session, node_type="document", parent_id=root.id)
            await session.commit()
            doc_id, outsider_role = str(doc.id), role_b.id

        outsider = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=outsider_role)
        )
        async with client(outsider) as http:
            response = await http.post(
                f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
            )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_node_not_found"


@pytest.mark.asyncio
async def test_upload_into_missing_node_is_404(attachments_dir: Path) -> None:
    async with documents_db() as sm, client(admin_app(sm)) as http:
        response = await http.post(
            f"/api/documents/nodes/{uuid.uuid4()}/attachments", files=upload_file(PNG)
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_node_not_found"


# --- Отдача: заголовки и кэш ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_serving_returns_stored_mime_etag_and_private_cache_control(
    attachments_dir: Path,
) -> None:
    """`Content-Type` = сохранённый `mime`, `ETag` = `checksum`, `Cache-Control` — `private`.

    **`public` ЗАПРЕЩЁН**: ответ зависит от прав запрашивающего (per-node видимость), и
    shared-кэш прокси отдал бы картинку постороннему — регресс-гейт.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments",
                files=upload_file(WEBP, mime="image/webp", filename="pic.webp"),
            )
            attachment_id = created.json()["id"]
            response = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert response.status_code == 200
    assert response.content == WEBP
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["etag"] == f'"{created.json()["checksum"]}"'

    cache_control = response.headers["cache-control"]
    assert "private" in cache_control
    assert "public" not in cache_control


@pytest.mark.asyncio
async def test_if_none_match_returns_304_without_body(attachments_dir: Path) -> None:
    """Повтор с `If-None-Match` → `304`; заголовки кэша сохраняются."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            etag = f'"{created.json()["checksum"]}"'
            response = await http.get(
                f"/api/documents/attachments/{created.json()['id']}",
                headers={"If-None-Match": etag},
            )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert "public" not in response.headers["cache-control"]


@pytest.mark.asyncio
async def test_if_none_match_with_weak_prefix_and_list_matches(attachments_dir: Path) -> None:
    """`W/"…"` и список значений тоже засчитываются (RFC 7232)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            checksum = created.json()["checksum"]
            attachment_id = created.json()["id"]
            weak = await http.get(
                f"/api/documents/attachments/{attachment_id}",
                headers={"If-None-Match": f'W/"{checksum}"'},
            )
            listed = await http.get(
                f"/api/documents/attachments/{attachment_id}",
                headers={"If-None-Match": f'"other", "{checksum}"'},
            )

    assert weak.status_code == 304
    assert listed.status_code == 304


@pytest.mark.asyncio
async def test_if_none_match_mismatch_returns_200(attachments_dir: Path) -> None:
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            response = await http.get(
                f"/api/documents/attachments/{created.json()['id']}",
                headers={"If-None-Match": '"stale-checksum"'},
            )

    assert response.status_code == 200
    assert response.content == PNG


# --- Отдача: единый 404 (анти-энумерация) --------------------------------------------------


@pytest.mark.asyncio
async def test_serving_to_role_without_node_visibility_is_404_not_403_or_200(
    attachments_dir: Path,
) -> None:
    """Ключевой security-кейс: чужая роль → `404 document_attachment_not_found`.

    Именно `404`, а не `403` (утечка факта существования) и не `200` (утечка байтов).
    """
    async with documents_db() as sm:
        async with sm() as session:
            role_a = await seed_role(session)
            role_b = await seed_role(session)
            root = await seed_node(session, visibility_mode="restricted")
            await set_node_roles(session, root.id, [role_a.id])
            doc = await seed_node(session, node_type="document", parent_id=root.id)
            await session.commit()
            doc_id, insider_role, outsider_role = str(doc.id), role_a.id, role_b.id

        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]

        insider = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=insider_role)
        )
        async with client(insider) as http:
            allowed = await http.get(f"/api/documents/attachments/{attachment_id}")

        outsider = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=outsider_role)
        )
        async with client(outsider) as http:
            denied = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert allowed.status_code == 200
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "document_attachment_not_found"


@pytest.mark.asyncio
async def test_missing_attachment_id_is_the_same_404(attachments_dir: Path) -> None:
    """Несуществующий `id` — тот же код (случай 2 из трёх)."""
    async with documents_db() as sm, client(admin_app(sm)) as http:
        response = await http.get(f"/api/documents/attachments/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_attachment_not_found"


@pytest.mark.asyncio
async def test_soft_deleted_node_attachment_is_the_same_404_but_file_stays(
    attachments_dir: Path,
) -> None:
    """Soft-delete узла: отдача → тот же `404`, а **файлы на диске остаются** (случай 3)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]
            deleted = await http.delete(f"/api/documents/nodes/{node_id}")
            response = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert deleted.status_code == 204
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_attachment_not_found"
    # Soft-delete байты не трогает (восстановление узла должно вернуть и картинки).
    assert expected_path(attachments_dir, attachment_id, "png").is_file()


@pytest.mark.asyncio
async def test_orphan_row_without_file_is_404_not_500(attachments_dir: Path) -> None:
    """Строка есть, байтов нет (рассинхрон восстановления) → `404`, а не `500`."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]
            expected_path(attachments_dir, attachment_id, "png").unlink()
            response = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_attachment_not_found"


@pytest.mark.asyncio
async def test_delete_between_gate_and_serving_is_404_not_500(attachments_dir: Path) -> None:
    """Гонка `GET` с `DELETE`: файл исчез после гейтов → контрактный `404`, не `500`.

    Без явной передачи `stat_result` в `FileResponse` тот сделал бы собственный `stat`
    уже после проверок и упал бы `RuntimeError: File at path … does not exist` ⇒ `500`.
    Гонка моделируется детерминированно: удаление вклинивается в момент `stat` сервиса.
    """
    from app.services.document_attachment_service import DocumentAttachmentService

    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]
            path = expected_path(attachments_dir, attachment_id, "png")
            assert path.is_file()

            original_stat = DocumentAttachmentService._stat_inside_root

            def racing_stat(self: Any, target: Path) -> Any:
                # «Параллельный» DELETE успевает между гейтом видимости и снятием stat.
                if target == path and path.exists():
                    path.unlink()
                return original_stat(self, target)

            DocumentAttachmentService._stat_inside_root = racing_stat  # type: ignore[method-assign]
            try:
                response = await http.get(f"/api/documents/attachments/{attachment_id}")
            finally:
                DocumentAttachmentService._stat_inside_root = original_stat  # type: ignore[method-assign]

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_attachment_not_found"


# --- Удаление вложения ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_attachment_removes_row_and_file_and_repeat_is_404(
    attachments_dir: Path,
) -> None:
    """`DELETE` → `204`, строка и файл удалены; повтор → `404`."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]
            path = expected_path(attachments_dir, attachment_id, "png")
            assert path.is_file()

            deleted = await http.delete(f"/api/documents/attachments/{attachment_id}")
            repeat = await http.delete(f"/api/documents/attachments/{attachment_id}")
            fetched = await http.get(f"/api/documents/attachments/{attachment_id}")

        async with sm() as session:
            rows = (
                await session.execute(
                    sa_text("SELECT count(*) FROM document_attachments WHERE id = :i").bindparams(
                        i=uuid.UUID(attachment_id)
                    )
                )
            ).scalar_one()

    assert deleted.status_code == 204
    assert repeat.status_code == 404
    assert fetched.status_code == 404
    assert rows == 0
    assert not path.exists()


@pytest.mark.asyncio
async def test_delete_attachment_of_invisible_node_is_404(attachments_dir: Path) -> None:
    """Удаление чужого вложения — тот же единый `404` (не `403`)."""
    async with documents_db() as sm:
        async with sm() as session:
            role_a = await seed_role(session)
            role_b = await seed_role(session)
            root = await seed_node(session, visibility_mode="restricted")
            await set_node_roles(session, root.id, [role_a.id])
            doc = await seed_node(session, node_type="document", parent_id=root.id)
            await session.commit()
            doc_id, outsider_role = str(doc.id), role_b.id

        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]

        outsider = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=outsider_role)
        )
        async with client(outsider) as http:
            response = await http.delete(f"/api/documents/attachments/{attachment_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_attachment_not_found"
    assert expected_path(attachments_dir, attachment_id, "png").is_file()


# --- Регресс-гейт класса «серверное значение читается после flush UPDATE» ------------
#
# Класс бага (а не единичный кейс): после `flush` UPDATE-а SQLAlchemy гасит атрибуты,
# которые вычисляет СЕРВЕР (`document_nodes.updated_at`, `onupdate=func.now()`), потому что
# на UPDATE значение инлайн не забирается. Чтение такого атрибута в async-контексте вне
# greenlet уходит в синхронную ленивую догрузку → `MissingGreenlet` → `500`.
# `expire_on_commit=False` не спасает: гасит именно flush UPDATE, а не commit.
#
# Ниже — все ветки `copy_node`, где UPDATE вообще возникает, включая те, где корень
# поддерева и переписываемый узел — РАЗНЫЕ строки (копия папки).


@pytest.mark.asyncio
async def test_copy_of_folder_with_nested_attachments_returns_201_not_500(
    attachments_dir: Path,
) -> None:
    """Копия ПАПКИ, вложения которой лежат во вложенных документах → `201`, а не `500`.

    Ключевое отличие от копии документа: UPDATE (перезапись ссылок) применяется к
    ПОТОМКУ, а сериализуется КОРЕНЬ — то есть погашённый и читаемый атрибуты принадлежат
    разным строкам. Реализация, которая «чинит» баг рефрешем не того узла (или полагается
    на то, что корень и есть переписанный узел), пройдёт тест копии документа и упадёт
    здесь.
    """
    async with documents_db() as sm:
        async with sm() as session:
            folder = await seed_node(session, node_type="folder", name="Папка")
            doc = await seed_node(
                session, node_type="document", parent_id=folder.id, name="Вложенный"
            )
            await session.commit()
            folder_id, doc_id = str(folder.id), str(doc.id)

        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{doc_id}",
                json={"content_md": f"текст\n\n![a]({created['url']})\n"},
            )
            copied = await http.post(f"/api/documents/nodes/{folder_id}/copy", json={})

    assert copied.status_code == 201, copied.text
    assert copied.json()["node_type"] == "folder"


@pytest.mark.asyncio
async def test_copy_of_deep_subtree_with_attachments_returns_201(attachments_dir: Path) -> None:
    """Та же ветка на глубине 3 (папка → папка → документ с картинкой)."""
    async with documents_db() as sm:
        async with sm() as session:
            outer = await seed_node(session, node_type="folder", name="Внешняя")
            inner = await seed_node(
                session, node_type="folder", parent_id=outer.id, name="Внутренняя"
            )
            doc = await seed_node(session, node_type="document", parent_id=inner.id)
            await session.commit()
            outer_id, doc_id = str(outer.id), str(doc.id)

        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{doc_id}", json={"content_md": f"![a]({created['url']})"}
            )
            copied = await http.post(f"/api/documents/nodes/{outer_id}/copy", json={})

    assert copied.status_code == 201, copied.text


@pytest.mark.asyncio
async def test_nested_copy_rewrites_links_in_child_and_serializes_root(
    attachments_dir: Path,
) -> None:
    """Копия папки: ссылки переписаны в ПОТОМКЕ, а тело ответа — корректный корень.

    Проверяет, что «не упало» не куплено ценой пропущенной перезаписи: обе половины
    инварианта ADR-068 §5 на месте одновременно.
    """
    async with documents_db() as sm:
        async with sm() as session:
            folder = await seed_node(session, node_type="folder", name="Папка")
            doc = await seed_node(session, node_type="document", parent_id=folder.id, name="Док")
            await session.commit()
            folder_id, doc_id = str(folder.id), str(doc.id)

        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{doc_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{doc_id}", json={"content_md": f"![a]({created['url']})"}
            )
            copied = await http.post(f"/api/documents/nodes/{folder_id}/copy", json={})
            copy_root_id = copied.json()["id"]

            children = (await http.get(f"/api/documents/nodes?parent_id={copy_root_id}")).json()
            child_id = children[0]["id"]
            child_body = (await http.get(f"/api/documents/nodes/{child_id}")).json()

        # Корень ответа — папка-копия с валидными серверными полями (они и гасли раньше).
        root_body = copied.json()
        assert root_body["node_type"] == "folder"
        assert root_body["updated_at"]
        assert root_body["created_at"]

        # Ссылка в потомке переписана на НОВЫЙ id вложения.
        assert created["id"] not in child_body["content_md"]
        assert "/api/documents/attachments/" in child_body["content_md"]


@pytest.mark.asyncio
async def test_copy_with_attachment_but_no_link_in_text_returns_201(
    attachments_dir: Path,
) -> None:
    """Вложение есть, ссылки на него в тексте НЕТ → `201` (ветка без перезаписи).

    Здесь `_rewrite_attachment_links` не меняет ни одной строки ⇒ ветка `flush` + `refresh`
    не выполняется вовсе. Кейс закрывает третий путь `copy_node` (первые два — «перезапись
    была» и «вложений нет»), чтобы отказ не прятался в необойдённой ветке.

    ⚠️ Честная граница этого теста: он **не** отличает возврат `bool` из
    `_rewrite_attachment_links` от безусловного присваивания `content_md` — проверено
    мутацией, обе редакции дают `201`, потому что при безусловном присваивании
    погашенный `updated_at` всё равно восстановит `refresh` в той же ветке. Возврат `bool`
    экономит холостой UPDATE, но снаружи API этот эффект не наблюдаем; ассерта на него
    здесь нет намеренно — вместо ложного «гейта», который на самом деле ничего не стережёт.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm, content_md="текст без картинок")
        async with client(admin_app(sm)) as http:
            await http.post(f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG))
            copied = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})

    assert copied.status_code == 201, copied.text
    assert copied.json()["updated_at"]


@pytest.mark.asyncio
async def test_copy_response_carries_server_computed_timestamps(attachments_dir: Path) -> None:
    """Ответ копии несёт серверные `created_at`/`updated_at` — их чтение и падало.

    Прямой ассерт на симптом: раньше обращение к `updated_at` после flush UPDATE давало
    `MissingGreenlet`. Наличие непустых значений в теле — доказательство, что атрибут
    загружен, а не просто «исключение подавили».
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm, content_md="текст")
        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{node_id}", json={"content_md": f"![a]({created['url']})"}
            )
            copied = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})

    assert copied.status_code == 201
    body = copied.json()
    assert body["created_at"]
    assert body["updated_at"]
    assert body["content_version"] == 1


# --- Копирование документа (ADR-068 §5) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_of_document_with_attachment_returns_201_not_500(
    attachments_dir: Path,
) -> None:
    """Минимальный регресс: копия документа С вложением обязана отдавать `201`, а не `500`.

    Изолирует ровно одно условие — наличие вложения у копируемого документа с непустым
    `content_md`. Копия БЕЗ вложений в том же тесте проходит, поэтому падение здесь
    однозначно указывает на путь `copy_node` → `_rewrite_attachment_links` → `flush`,
    а не на копирование вообще.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm, content_md="без картинок")
        async with client(admin_app(sm)) as http:
            before = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})

            created = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{node_id}",
                json={"content_md": f"текст\n\n![a]({created['url']})\n"},
            )
            after = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})

    assert before.status_code == 201, "копия без вложений — контрольная точка"
    assert after.status_code == 201, after.text


@pytest.mark.asyncio
async def test_copy_document_with_two_images_creates_new_ids_files_and_rewrites_links(
    attachments_dir: Path,
) -> None:
    """Копия получает **новые** `id`, **новые** файлы, и ссылки в `content_md` переписаны.

    Старых `uuid` в тексте копии не остаётся — иначе копия зависела бы от оригинала, и
    удаление оригинала обрывало бы её картинки.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm, name="Исходный")
        async with client(admin_app(sm)) as http:
            first = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            second = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments",
                    files=upload_file(GIF, mime="image/gif", filename="pic.gif"),
                )
            ).json()

            content = f"Текст\n\n![a]({first['url']})\n\n![b]({second['url']})\n"
            await http.patch(f"/api/documents/nodes/{node_id}", json={"content_md": content})

            copied = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})
            copy_id = copied.json()["id"]
            copy_body = (await http.get(f"/api/documents/nodes/{copy_id}")).json()

        async with sm() as session:
            new_ids = [
                str(row[0])
                for row in (
                    await session.execute(
                        sa_text(
                            "SELECT id FROM document_attachments WHERE document_node_id = :n "
                            "ORDER BY created_at"
                        ).bindparams(n=uuid.UUID(copy_id))
                    )
                ).all()
            ]

    assert copied.status_code == 201
    assert len(new_ids) == 2
    assert set(new_ids).isdisjoint({first["id"], second["id"]})

    copy_md = copy_body["content_md"]
    # Старых id в тексте копии не осталось; новые — на месте.
    assert first["id"] not in copy_md
    assert second["id"] not in copy_md
    for new_id in new_ids:
        assert f"/api/documents/attachments/{new_id}" in copy_md

    # Файлы копии — отдельные, но побайтово равны исходным.
    originals = {first["id"]: PNG, second["id"]: GIF}
    for original_id, payload in originals.items():
        ext = "png" if payload is PNG else "gif"
        assert expected_path(attachments_dir, original_id, ext).read_bytes() == payload
    copied_bytes = sorted(
        path.read_bytes() for new_id in new_ids for path in attachments_dir.rglob(f"{new_id}.*")
    )
    assert copied_bytes == sorted([PNG, GIF])


@pytest.mark.asyncio
async def test_copy_of_attachment_is_independent_of_original_deletion(
    attachments_dir: Path,
) -> None:
    """Удаление ОРИГИНАЛА не обрывает картинку копии (общий файл с refcount отклонён)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            original = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{node_id}",
                json={"content_md": f"![a]({original['url']})"},
            )
            copy_id = (await http.post(f"/api/documents/nodes/{node_id}/copy", json={})).json()[
                "id"
            ]
            copy_md = (await http.get(f"/api/documents/nodes/{copy_id}")).json()["content_md"]
            new_id = copy_md.rsplit("/", 1)[-1].rstrip(")\n")

            await http.delete(f"/api/documents/attachments/{original['id']}")
            response = await http.get(f"/api/documents/attachments/{new_id}")

    assert response.status_code == 200
    assert response.content == PNG


@pytest.mark.asyncio
async def test_copy_skips_attachment_whose_source_file_is_missing(attachments_dir: Path) -> None:
    """Осиротевший исходный файл → копирование **без `500`**: вложение пропущено.

    Сценарий ADR-068 §8 (рассинхрон восстановления `pgdata` и volume): строка-копия не
    создаётся, ссылка в `content_md` копии остаётся прежней и деградирует до плашки
    «Изображение недоступно» — ровно как у оригинала.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            alive = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            orphan = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments",
                    files=upload_file(GIF, mime="image/gif", filename="pic.gif"),
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{node_id}",
                json={"content_md": f"![a]({alive['url']})\n\n![b]({orphan['url']})\n"},
            )
            # Байты второго вложения «потеряны» — строка в БД осталась.
            expected_path(attachments_dir, orphan["id"], "gif").unlink()

            copied = await http.post(f"/api/documents/nodes/{node_id}/copy", json={})
            copy_id = copied.json()["id"]
            copy_md = (await http.get(f"/api/documents/nodes/{copy_id}")).json()["content_md"]

        async with sm() as session:
            copies = (
                await session.execute(
                    sa_text(
                        "SELECT count(*) FROM document_attachments WHERE document_node_id = :n"
                    ).bindparams(n=uuid.UUID(copy_id))
                )
            ).scalar_one()

    assert copied.status_code == 201
    assert copies == 1  # скопировано только живое вложение
    assert alive["id"] not in copy_md  # его ссылка переписана на новый id
    assert orphan["id"] in copy_md  # ссылка осиротевшего осталась прежней


@pytest.mark.asyncio
async def test_copy_leaves_no_tmp_files(attachments_dir: Path) -> None:
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            await http.patch(
                f"/api/documents/nodes/{node_id}", json={"content_md": f"![a]({created['url']})"}
            )
            await http.post(f"/api/documents/nodes/{node_id}/copy", json={})

    assert tmp_files(attachments_dir) == []


# --- Внешний контур байты НЕ отдаёт -----------------------------------------------------------


@pytest.mark.asyncio
async def test_external_contour_has_no_attachment_binary_route(attachments_dir: Path) -> None:
    """`GET /api/external/documents/attachments/{id}` → `404`: роут не зарегистрирован.

    RAG получает `content_md` со ссылками, но байты картинок ему не отдаются — иначе
    статический `X-API-Key` открыл бы доступ к изображениям в обход per-node видимости.
    """
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]
            response = await http.get(f"/api/external/documents/attachments/{attachment_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_external_attachment_route_absent_from_openapi(attachments_dir: Path) -> None:
    """Роута нет и в схеме — он не «закрыт правами», а отсутствует."""
    async with documents_db() as sm:
        app = admin_app(sm)
        paths = app.openapi()["paths"]

    assert "/api/documents/attachments/{attachment_id}" in paths
    assert not any(p.startswith("/api/external/documents/attachments") for p in paths)


@pytest.mark.asyncio
async def test_external_document_returns_links_as_is_and_no_attachments_field(
    attachments_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GET /api/external/documents/{id}` отдаёт `content_md` **как есть**, без `attachments`."""
    from app.config import get_settings

    monkeypatch.setenv("DOCUMENTS_API_KEY", "external-test-key")
    get_settings.cache_clear()

    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = (
                await http.post(
                    f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
                )
            ).json()
            content = f"Текст\n\n![a]({created['url']})\n"
            await http.patch(f"/api/documents/nodes/{node_id}", json={"content_md": content})
            response = await http.get(
                f"/api/external/documents/{node_id}",
                headers={"X-API-Key": "external-test-key"},
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content_md"] == content
    assert created["url"] in payload["content_md"]
    assert "attachments" not in payload


# --- Гейты прав ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_and_delete_require_edit_not_view(attachments_dir: Path) -> None:
    """Загрузка/удаление — гейт `documents:edit`; чтения (`view`) недостаточно."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]

        viewer = build_app(
            sm,
            build_principal(
                is_superadmin=False, permissions={"documents": ["view"]}, role_id=uuid.uuid4()
            ),
        )
        async with client(viewer) as http:
            upload = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            delete = await http.delete(f"/api/documents/attachments/{attachment_id}")
            fetch = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert upload.status_code == 403
    assert delete.status_code == 403
    # Чтение под `view` разрешено (узел публичен) — гейт отдачи именно `view`.
    assert fetch.status_code == 200


@pytest.mark.asyncio
async def test_serving_requires_view_permission(attachments_dir: Path) -> None:
    """Без `documents:view` байты не отдаются вовсе (`403` — прав на страницу нет)."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            created = await http.post(
                f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG)
            )
            attachment_id = created.json()["id"]

        no_rights = build_app(
            sm,
            build_principal(
                is_superadmin=False, permissions={"mail": ["view"]}, role_id=uuid.uuid4()
            ),
        )
        async with client(no_rights) as http:
            response = await http.get(f"/api/documents/attachments/{attachment_id}")

    assert response.status_code == 403


# --- Границы БД ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_rejects_mime_outside_whitelist(attachments_dir: Path) -> None:
    """CHECK `mime` в БД отвергает SVG — граница живёт не только в сервисе."""
    from sqlalchemy.exc import IntegrityError

    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with sm() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa_text(
                        "INSERT INTO document_attachments "
                        "(document_node_id, filename, mime, size_bytes, checksum, created_by) "
                        "VALUES (:n, 'evil.svg', 'image/svg+xml', 10, :c, "
                        "(SELECT id FROM users LIMIT 1))"
                    ).bindparams(n=uuid.UUID(node_id), c="a" * 64)
                )
                await session.commit()
            await session.rollback()


@pytest.mark.asyncio
async def test_hard_delete_of_node_cascades_attachment_rows(attachments_dir: Path) -> None:
    """FK `ON DELETE CASCADE`: физическое удаление узла снимает строки вложений."""
    async with documents_db() as sm:
        node_id = await seed_document(sm)
        async with client(admin_app(sm)) as http:
            await http.post(f"/api/documents/nodes/{node_id}/attachments", files=upload_file(PNG))

        async with sm() as session:
            await session.execute(
                sa_text("DELETE FROM document_nodes WHERE id = :i").bindparams(i=uuid.UUID(node_id))
            )
            await session.commit()
            remaining = (
                await session.execute(sa_text("SELECT count(*) FROM document_attachments"))
            ).scalar_one()

    assert remaining == 0
