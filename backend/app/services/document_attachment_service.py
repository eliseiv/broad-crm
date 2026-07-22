"""Вложения-изображения документов (modules/documents, ADR-068, 04-api.md#вложения).

Строка `document_attachments` — источник истины о вложении, файл на volume — только байты.
Здесь живут: валидация загрузки (размер **по потоку**, тип **по magic bytes**, whitelist),
построение пути **только** из `id` + расширения из `mime`, атомарная запись/удаление файла
и копирование вложений при «Создать копию».

**Главный инвариант доступа:** доступ к картинке = доступ к её узлу — тот же per-node фильтр
видимости (`document_visibility`), что и у самого узла; все негативные исходы отдачи дают
ЕДИНЫЙ `404 document_attachment_not_found` (анти-энумерация).

**Path traversal невозможен конструктивно, а не санитайзингом:** пользовательский `filename`
в пути не участвует вовсе (хранится лишь как метаданные для `Content-Disposition`/alt).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import UploadFile, status

from app.config import Settings
from app.domain.documents import DocumentScope
from app.errors import (
    AppError,
    document_attachment_invalid,
    document_attachment_not_found,
)
from app.logging import get_logger
from app.models.document_attachment import ALLOWED_IMAGE_MIME, DocumentAttachment
from app.repositories.document_attachment_repository import DocumentAttachmentRepository
from app.repositories.document_repository import DocumentRepository
from app.schemas.documents import DocumentAttachmentResponse
from app.services.document_visibility import ensure_visible_node, resolve_visible_node

logger = get_logger(__name__)

# Канонический адрес вложения. ЕДИНСТВЕННОЕ место формирования ссылки: его отдаёт сервер
# в `url` ответа загрузки, по нему же переписываются ссылки в `content_md` копии.
ATTACHMENT_URL_PREFIX: Final = "/api/documents/attachments/"

_CHUNK_BYTES: Final = 64 * 1024
_HEAD_BYTES: Final = 12  # достаточно для всех сигнатур whitelist (RIFF….WEBP — 12 байт)
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700
_FILENAME_MAX: Final = 255
_TMP_PREFIX: Final = ".upload-"
_TMP_SUFFIX: Final = ".tmp"


@dataclass(frozen=True, slots=True)
class AttachmentFile:
    """Готовые к отдаче байты вложения (HTTP-заголовки формирует роутер).

    `stat_result` снят сервисом под гейтами и уходит в `FileResponse`, чтобы тот не
    повторял `stat` (иначе гонка с удалением превращала бы контрактный `404` в `500`).
    """

    path: Path
    mime: str
    filename: str
    checksum: str
    size_bytes: int
    stat_result: os.stat_result


def _invalid_node_type() -> AppError:
    """Загрузка в папку → `422 validation_error` (папка контента не хранит, ADR-059)."""
    message = "Папка не хранит вложения"
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message=message,
        details=[{"field": "node_id", "message": message}],
    )


def _detect_mime(head: bytes) -> str | None:
    """Тип по фактическому содержимому (magic bytes), а не по `Content-Type` клиента.

    Клиентский `Content-Type` подделывается тривиально, а сохранённый `mime` управляет
    заголовком отдачи. Whitelist — ровно четыре растровых формата; SVG сюда не попадает
    (нормативно, ADR-068 §2.3).
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _declared_mime(raw: str | None) -> str:
    """Заявленный клиентом тип без параметров, lowercase (пусто → пустая строка)."""
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


def _safe_filename(raw: str | None, ext: str) -> str:
    """Имя для метаданных: базовое имя, 1..255 символов (CHECK БД), fallback по типу.

    В пути на диске НЕ участвует, поэтому санитайзинг здесь — не защита от traversal, а
    приведение к ограничениям колонки. В `Content-Disposition` имя уходит percent-encoded,
    поэтому инъекция заголовка невозможна.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        name = f"image.{ext}"
    return name[:_FILENAME_MAX]


class DocumentAttachmentService:
    """Загрузка, отдача, удаление и копирование вложений-изображений."""

    def __init__(
        self,
        attachments: DocumentAttachmentRepository,
        documents: DocumentRepository,
        settings: Settings,
    ) -> None:
        self._repo = attachments
        self._documents = documents
        self._settings = settings

    # --- Загрузка ---------------------------------------------------------

    async def upload(
        self,
        node_id: uuid.UUID,
        *,
        file: UploadFile,
        scope: DocumentScope,
        created_by: uuid.UUID,
    ) -> DocumentAttachmentResponse:
        """Загрузка изображения в документ (ADR-068 §2).

        Порядок записи **нормативен**: стриминг во временный файл в КОРНЕ
        `DOCUMENTS_ATTACHMENTS_DIR` (та же ФС, что финальный шард-путь ⇒ `os.replace`
        останется атомарным) с подсчётом sha256 и размера → проверки → `INSERT` с
        готовыми `checksum`/`size_bytes` → `flush` (получен `id` ⇒ известен шард-путь) →
        `mkdir -p` шардов → `os.replace` → `commit`. Сбой до `commit` → `rollback` +
        `unlink` temp. **Строки без файла не бывает.**
        """
        node = await ensure_visible_node(self._documents, scope, node_id)
        if node.node_type != "document":
            raise _invalid_node_type()

        root = self._root()
        _ensure_directory(root)
        tmp_path = await asyncio.to_thread(self._make_temp_file, root)
        try:
            checksum, size_bytes, head = await self._stream_to_temp(file, tmp_path)
            mime = self._resolve_mime(head, file.content_type)
            attachment = await self._repo.create(
                document_node_id=node_id,
                filename=_safe_filename(file.filename, ALLOWED_IMAGE_MIME[mime]),
                mime=mime,
                size_bytes=size_bytes,
                checksum=checksum,
                created_by=created_by,
            )
            final_path = self._file_path(attachment.id, mime)
            await asyncio.to_thread(_place_file, tmp_path, final_path)
            await self._repo.session.commit()
        except BaseException:
            await self._repo.session.rollback()
            raise
        finally:
            await asyncio.to_thread(_unlink_quietly, tmp_path)

        logger.info(
            "document_attachment_uploaded",
            attachment_id=str(attachment.id),
            node_id=str(node_id),
            size_bytes=size_bytes,
        )
        return self.serialize(attachment)

    async def _stream_to_temp(self, file: UploadFile, tmp_path: Path) -> tuple[str, int, bytes]:
        """Пишет поток во временный файл, считая sha256 и размер; лимит обрывает чтение.

        Файл целиком в память НЕ читается: превышение `DOCUMENTS_MAX_IMAGE_BYTES`
        прекращает чтение сразу (temp удаляется вызывающим в `finally`).
        """
        limit = self._settings.documents_max_image_bytes
        digest = hashlib.sha256()
        size = 0
        head = b""
        # Запись — синхронная, как и открытие файла: это буферизованный `write` в локальный
        # temp (порядок микросекунд), тогда как вынос каждого 64-КБ куска в `to_thread` дал
        # бы ~80 переключений контекста на 5-МБ файл — дороже самой записи.
        with tmp_path.open("wb") as tmp_file:
            while chunk := await file.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise document_attachment_invalid()
                digest.update(chunk)
                if len(head) < _HEAD_BYTES:
                    head += chunk[: _HEAD_BYTES - len(head)]
                tmp_file.write(chunk)
        if size == 0:
            raise document_attachment_invalid()
        return digest.hexdigest(), size, head

    @staticmethod
    def _resolve_mime(head: bytes, content_type: str | None) -> str:
        """Тип по содержимому + сверка с заявленным; расхождение/не whitelist → 422."""
        actual = _detect_mime(head)
        declared = _declared_mime(content_type)
        if actual is None or declared not in ALLOWED_IMAGE_MIME or declared != actual:
            raise document_attachment_invalid()
        return actual

    # --- Отдача -----------------------------------------------------------

    async def get_file(self, attachment_id: uuid.UUID, *, scope: DocumentScope) -> AttachmentFile:
        """Метаданные + путь и `stat` байтов для отдачи (гейт `documents:view` — на роутере).

        Нет строки / узел-владелец невидим по роли / узел soft-deleted / файла нет на
        диске → **единый** `404 document_attachment_not_found`.

        `stat_result` снимается ЗДЕСЬ и передаётся в `FileResponse`, чтобы тот не делал
        собственный `stat` уже после гейтов: гонка с `DELETE /attachments/{id}` дала бы у
        него `RuntimeError: File at path … does not exist` ⇒ `500` вместо контрактного
        `404`. Остаточное окно (файл исчез между `stat` и стримингом тела) неустранимо
        для любой потоковой отдачи и наблюдаемо только как оборванное тело — статус к
        этому моменту уже отправлен.
        """
        attachment = await self._resolve_visible(attachment_id, scope)
        path = self._file_path(attachment.id, attachment.mime)
        stat_result = await asyncio.to_thread(self._stat_inside_root, path)
        if stat_result is None:
            raise document_attachment_not_found()
        return AttachmentFile(
            path=path,
            mime=attachment.mime,
            filename=attachment.filename,
            checksum=attachment.checksum,
            size_bytes=attachment.size_bytes,
            stat_result=stat_result,
        )

    # --- Удаление ---------------------------------------------------------

    async def delete(self, attachment_id: uuid.UUID, *, scope: DocumentScope) -> None:
        """Удаляет строку в транзакции, файл — ПОСЛЕ успешного `commit` (ADR-068 §2).

        Обратный порядок дал бы строку, указывающую в пустоту. Сбой удаления файла —
        best-effort: лог + GC (TD-076). Ссылка в `content_md` не переписывается
        автоматически: висячая ссылка деградирует до alt-текста, документ не ломается.
        """
        attachment = await self._resolve_visible(attachment_id, scope)
        path = self._file_path(attachment.id, attachment.mime)
        await self._repo.delete_by_id(attachment.id)
        await self._repo.session.commit()
        await asyncio.to_thread(_unlink_quietly, path)
        logger.info("document_attachment_deleted", attachment_id=str(attachment_id))

    # --- Копирование поддерева (ADR-068 §5) -------------------------------

    async def copy_for_nodes(
        self, node_id_map: dict[uuid.UUID, uuid.UUID], *, created_by: uuid.UUID
    ) -> dict[uuid.UUID, uuid.UUID]:
        """Физически копирует вложения узлов поддерева; возвращает карту `старый id → новый`.

        Новые `id`, новые файлы (побайтовая копия) — копия независима от оригинала
        (общий файл с refcount отклонён: удаление оригинала обрывало бы картинки копии).

        Порядок тот же, что при загрузке: сначала байты во временный файл, затем строки
        (одна bulk-вставка + один `flush`), затем `os.replace` — ДО `commit` вызывающего
        сервиса ⇒ инвариант «строки без файла не бывает» соблюдён.

        **Осиротевший исходный файл** (рассинхрон восстановления `pgdata` и volume —
        сценарий ADR-068 §8) — не `500`: вложение пропускается с `warning`, строка-копия
        не создаётся, ссылка в `content_md` копии остаётся прежней и деградирует до
        плашки «Изображение недоступно», как и у оригинала.
        """
        sources = await self._repo.list_for_nodes(list(node_id_map))
        if not sources:
            return {}
        root = self._root()
        _ensure_directory(root)

        staged: list[tuple[DocumentAttachment, Path]] = []
        try:
            for source in sources:
                tmp_path = await asyncio.to_thread(
                    self._stage_copy, self._file_path(source.id, source.mime), root
                )
                if tmp_path is None:
                    logger.warning(
                        "document_attachment_source_file_missing",
                        attachment_id=str(source.id),
                    )
                    continue
                staged.append((source, tmp_path))
            if not staged:
                return {}

            copies = await self._repo.create_many(
                [
                    {
                        "document_node_id": node_id_map[source.document_node_id],
                        "filename": source.filename,
                        "mime": source.mime,
                        "size_bytes": source.size_bytes,
                        "checksum": source.checksum,
                        "created_by": created_by,
                    }
                    for source, _ in staged
                ]
            )
            id_map: dict[uuid.UUID, uuid.UUID] = {}
            for (source, tmp_path), copy in zip(staged, copies, strict=True):
                await asyncio.to_thread(_place_file, tmp_path, self._file_path(copy.id, copy.mime))
                id_map[source.id] = copy.id
        finally:
            for _source, tmp_path in staged:
                await asyncio.to_thread(_unlink_quietly, tmp_path)

        logger.info("document_attachments_copied", count=len(id_map))
        return id_map

    # --- Сериализация -----------------------------------------------------

    @staticmethod
    def serialize(attachment: DocumentAttachment) -> DocumentAttachmentResponse:
        """Форма `DocumentAttachment` контракта; `url` формирует СЕРВЕР (не клиент)."""
        return DocumentAttachmentResponse(
            id=attachment.id,
            document_node_id=attachment.document_node_id,
            filename=attachment.filename,
            mime=attachment.mime,
            size_bytes=attachment.size_bytes,
            checksum=attachment.checksum,
            url=f"{ATTACHMENT_URL_PREFIX}{attachment.id}",
            created_at=attachment.created_at,
        )

    # --- Внутренние помощники --------------------------------------------

    async def _resolve_visible(
        self, attachment_id: uuid.UUID, scope: DocumentScope
    ) -> DocumentAttachment:
        """Строка вложения при видимом узле-владельце; иначе единый 404 (анти-энумерация)."""
        attachment = await self._repo.get_by_id(attachment_id)
        if attachment is None:
            raise document_attachment_not_found()
        # `resolve_visible_node` отсекает и soft-deleted узел (он исключён из всех
        # внутренних выборок) — тем же кодом, что «нет вложения».
        node = await resolve_visible_node(self._documents, scope, attachment.document_node_id)
        if node is None:
            raise document_attachment_not_found()
        return attachment

    def _root(self) -> Path:
        return Path(self._settings.documents_attachments_dir)

    def _file_path(self, attachment_id: uuid.UUID, mime: str) -> Path:
        """`<root>/<id[0:2]>/<id[2:4]>/<id>.<ext>` — только машинные значения (ADR-068 §1)."""
        raw_id = str(attachment_id)
        extension = ALLOWED_IMAGE_MIME[mime]
        return self._root() / raw_id[0:2] / raw_id[2:4] / f"{raw_id}.{extension}"

    def _stat_inside_root(self, path: Path) -> os.stat_result | None:
        """`stat` файла, если он существует и лежит внутри корня; иначе `None` (→ 404).

        Defensive-проверка `realpath`-containment — страховка от симлинка/битой
        конфигурации, а не основная защита (та конструктивная: пользовательские строки в
        построении пути не участвуют). Отсутствие файла (осиротевшая строка) тоже даёт
        `None` ⇒ 404, а не 500 на отдаче.
        """
        try:
            stat_result = path.stat()
        except OSError:
            return None
        if not stat.S_ISREG(stat_result.st_mode):
            return None
        root = os.path.realpath(self._root())
        resolved = os.path.realpath(path)
        if os.path.commonpath([root, resolved]) != root:
            return None
        return stat_result

    @staticmethod
    def _make_temp_file(root: Path) -> Path:
        """Временный файл в КОРНЕ каталога вложений (та же ФС ⇒ `os.replace` атомарен)."""
        fd, tmp_name = tempfile.mkstemp(dir=root, prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX)
        os.close(fd)  # mkstemp уже создал файл с 0600
        return Path(tmp_name)

    @staticmethod
    def _stage_copy(source: Path, root: Path) -> Path | None:
        """Побайтовая копия исходного файла во временный файл в корне (или `None`).

        `None` ⇒ исходных байтов нет (осиротевшая строка после несогласованного
        восстановления volume, ADR-068 §8): вызывающий пропускает вложение вместо `500`.
        """
        fd, tmp_name = tempfile.mkstemp(dir=root, prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX)
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            shutil.copyfile(source, tmp_path)
        except OSError:
            _unlink_quietly(tmp_path)
            return None
        return tmp_path


def _ensure_directory(path: Path) -> None:
    """Создаёт каталог с правами `0700` (у вложений ровно один читатель — backend).

    `mkdir` не применяет режим при существующем каталоге и урезается umask, поэтому режим
    выставляется отдельным `chmod` (best-effort: на смонтированном томе он может быть
    запрещён — права тома задаёт devops).
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _DIR_MODE)
    except OSError:
        logger.warning("document_attachments_chmod_failed", path=str(path))


def _place_file(tmp_path: Path, final_path: Path) -> None:
    """`mkdir -p` шард-каталогов (`0700`) + атомарный `os.replace` на финальное имя."""
    _ensure_directory(final_path.parent.parent)
    _ensure_directory(final_path.parent)
    os.chmod(tmp_path, _FILE_MODE)
    os.replace(tmp_path, final_path)


def _unlink_quietly(path: Path) -> None:
    """Удаляет файл best-effort (отсутствие — не ошибка; сбой — лог + GC, TD-076)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("document_attachment_unlink_failed")
