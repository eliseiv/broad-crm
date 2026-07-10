"""Чистые функции форматирования Telegram-уведомлений почты (ADR-044 §6).

Порт агрегаторского `notify_format`/`preview` на CRM (без Jinja/внешних sanitize-
зависимостей): текст push-уведомления (`format_notification`), полный текст письма
для callback «Посмотреть сообщение» (`format_message_body`), алерт о падении ящика
(`format_mailbox_down`), плюс preview-хелперы (`html_to_plain`/`normalize_preview`) и
разбиение длинного сообщения на части (`split_for_telegram`). Все пользовательские
строки экранируются `html.escape` (Telegram `parse_mode=HTML` принимает узкий сабсет
разметки). Без I/O/БД — тестируется qa напрямую. Callback-контракт — `mail:{id}`
(ADR-044 §6). Известное ограничение `html_to_plain` (не декодит часть HTML-entities
до strip) наследуется как TD-024.
"""

from __future__ import annotations

import html
import re
from typing import Final

# Максимум символов темы до усечения многоточием.
SUBJECT_MAX: Final[int] = 150
# Максимум символов preview-строки тела.
PREVIEW_LEN: Final[int] = 100
# Рабочий бюджет символов на одну часть sendMessage (Telegram-лимит 4096; запас на
# маркер продолжения и HTML-теги).
MAX_TELEGRAM_TEXT_LEN: Final[int] = 3800
# Потолок числа частей на одно длинное тело (антифлуд): 1 МБ тело ≠ 250 сообщений.
MAX_CHUNKS: Final[int] = 4

_ELLIPSIS: Final[str] = "…"
_CONTINUATION_MARKER: Final[str] = "\n…"

# Плейсхолдеры (строка тегов/темы присутствует всегда, ADR-044 §6 паритет агрегатора).
_NO_TAG: Final[str] = "Не сортировано"
_NO_SUBJECT: Final[str] = "(без темы)"

# Любой оставшийся HTML-тег (после грубой очистки) — вырезается для plain-preview.
_ANY_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
# Блоки <style>/<script> вместе с содержимым (CSS/JS не должны течь в preview).
_STYLE_SCRIPT_RE: Final[re.Pattern[str]] = re.compile(
    r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# <br> и закрытия блочных тегов → перевод строки (читабельность).
_BLOCK_BREAK_RE: Final[re.Pattern[str]] = re.compile(
    r"<br\s*/?>|</(p|div|tr|li|h[1-6])\s*>", re.IGNORECASE
)
# Любой прогон пробельных символов (вкл. переносы, табы, U+00A0) → один пробел.
_WHITESPACE_RUN_RE: Final[re.Pattern[str]] = re.compile(r"[\s\xa0]+")


def html_to_plain(body_html: str | None) -> str:
    """Свести HTML-тело письма к читабельному plain-тексту для preview.

    Убирает <style>/<script> с содержимым, преобразует <br>/закрытия блоков в
    переводы строк, вырезает остальные теги и декодит HTML-entities. Пустой/None → "".
    """
    if not body_html:
        return ""
    without_blocks = _STYLE_SCRIPT_RE.sub(" ", body_html)
    with_breaks = _BLOCK_BREAK_RE.sub("\n", without_blocks)
    without_tags = _ANY_TAG_RE.sub(" ", with_breaks)
    return html.unescape(without_tags)


def normalize_preview(text: str) -> str:
    """Схлопнуть пробелы, обрезать и ограничить `text` до `PREVIEW_LEN` (суффикс `…`).

    Возвращает "" если после нормализации ничего значимого не осталось.
    """
    if not text:
        return ""
    cleaned = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    if not cleaned:
        return ""
    if len(cleaned) > PREVIEW_LEN:
        return cleaned[:PREVIEW_LEN].rstrip() + _ELLIPSIS
    return cleaned


def build_body_preview(*, body_text: str, body_html: str | None) -> str:
    """Собрать preview из тела письма: plain-часть, иначе HTML сведённый к plain."""
    raw_preview = body_text if body_text.strip() else html_to_plain(body_html)
    return normalize_preview(raw_preview)


def format_notification(
    *,
    acc_label: str,
    from_label: str,
    tag_names: list[str],
    subject: str | None,
    body_preview: str,
) -> str:
    """HTML-текст push-уведомления (`parse_mode=HTML`, ADR-044 §6).

    Структура (6 строк, 2 пустых разделителя): 🆔 ящик (всегда), #️⃣ теги (всегда,
    пусто → плейсхолдер), пустая строка, Клиент, Тема (всегда, пусто → плейсхолдер),
    пустая строка + preview (только если preview непуст). Все значения экранируются.
    """
    acc_safe = html.escape(acc_label)
    from_safe = html.escape(from_label)
    tags_safe = ", ".join(html.escape(t) for t in tag_names) if tag_names else html.escape(_NO_TAG)
    subj = _WHITESPACE_RUN_RE.sub(" ", subject or "").strip()
    if not subj:
        subj = _NO_SUBJECT
    elif len(subj) > SUBJECT_MAX:
        subj = subj[:SUBJECT_MAX].rstrip() + _ELLIPSIS
    subj_safe = html.escape(subj)

    lines = [
        f"🆔: <b>{acc_safe}</b>",
        f"#️⃣: <b>{tags_safe}</b>",
        "",
        f"Клиент: <b>{from_safe}</b>",
        f"Тема: <b>{subj_safe}</b>",
    ]
    if body_preview:
        lines.append("")
        lines.append(html.escape(body_preview))
    return "\n".join(lines)


def format_message_body(
    *,
    subject: str | None,
    from_label: str,
    body_text: str,
    body_html: str | None,
) -> str:
    """Полный текст письма для callback «Посмотреть сообщение» (`parse_mode=HTML`).

    Заголовки Тема/От — жирным, экранированы. Тело — plain (plain-часть, иначе HTML
    сведённый к plain), экранировано целиком (безопасно для Telegram HTML-сабсета).
    """
    subject_safe = html.escape(subject) if subject and subject.strip() else "<i>(без темы)</i>"
    from_safe = html.escape(from_label)

    body_plain = body_text if body_text.strip() else html_to_plain(body_html)
    body_safe = html.escape(body_plain.strip()) if body_plain.strip() else "<i>(пустое тело)</i>"

    return f"<b>Тема:</b> {subject_safe}\n<b>От:</b> {from_safe}\n\n{body_safe}"


def format_mailbox_down(*, acc_label: str, last_sync_error: str | None) -> str:
    """HTML-текст алерта о падении ящика (`parse_mode=HTML`, ADR-044 §6, проход C).

    `acc_label` (display_name/email) экранируется. `last_sync_error` — при наличии
    добавляется (экранированным); иначе — обобщённая формулировка.
    """
    safe_label = html.escape(acc_label)
    if last_sync_error and last_sync_error.strip():
        reason = html.escape(last_sync_error.strip())
        detail = f": {reason}"
    else:
        detail = ""
    return (
        f"⚠️ Почта <b>{safe_label}</b> не работает{detail}. "
        "Синхронизация приостановлена — проверьте пароль/настройки."
    )


def split_for_telegram(text: str) -> list[str]:
    """Разбить `text` на части в пределах лимита Telegram (по границам строк).

    Предпочитает перенос строки; одиночная слишком длинная строка режется жёстко.
    Ограничено `MAX_CHUNKS` частями (последняя несёт маркер продолжения при усечении).
    """
    if len(text) <= MAX_TELEGRAM_TEXT_LEN:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining and len(chunks) < MAX_CHUNKS:
        if len(remaining) <= MAX_TELEGRAM_TEXT_LEN:
            chunks.append(remaining)
            remaining = ""
            break
        cut = remaining.rfind("\n", 0, MAX_TELEGRAM_TEXT_LEN)
        if cut <= 0:
            cut = MAX_TELEGRAM_TEXT_LEN
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks[-1] = chunks[-1].rstrip() + _CONTINUATION_MARKER
    return chunks


__all__ = [
    "MAX_CHUNKS",
    "MAX_TELEGRAM_TEXT_LEN",
    "PREVIEW_LEN",
    "SUBJECT_MAX",
    "build_body_preview",
    "format_mailbox_down",
    "format_message_body",
    "format_notification",
    "html_to_plain",
    "normalize_preview",
    "split_for_telegram",
]
