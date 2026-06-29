"""Управление file_sd-таргетами Prometheus (09-provisioning.md, ADR-004).

Backend пишет `${FILE_SD_DIR}/<id>.json` атомарно (temp + os.replace), чтобы
Prometheus не прочитал半-записанный JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)


def _targets_dir() -> Path:
    return Path(get_settings().file_sd_dir)


def _target_file(server_id: uuid.UUID) -> Path:
    return _targets_dir() / f"{server_id}.json"


def write_target(*, server_id: uuid.UUID, ip: str, exporter_port: int, name: str) -> None:
    """Атомарно записывает таргет file_sd для сервера."""
    directory = _targets_dir()
    directory.mkdir(parents=True, exist_ok=True)
    content = [
        {
            "targets": [f"{ip}:{exporter_port}"],
            "labels": {"server_id": str(server_id), "name": name},
        }
    ]
    data = json.dumps(content, ensure_ascii=False, indent=2)

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=f".{server_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, _target_file(server_id))
        logger.info("file_sd_target_written", server_id=str(server_id))
    except OSError:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def delete_target(server_id: uuid.UUID) -> None:
    """Удаляет таргет file_sd (idempotent — отсутствие файла не ошибка)."""
    _target_file(server_id).unlink(missing_ok=True)
    logger.info("file_sd_target_deleted", server_id=str(server_id))
