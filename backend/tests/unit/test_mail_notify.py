"""Unit-тесты форматирования Telegram-уведомлений почты `app/domain/mail_notify.py` (ADR-044 §6).

Чистые функции без I/O: `format_notification` (структура/экранирование/плейсхолдеры),
`format_message_body` (полное тело callback), `format_mailbox_down` (алерт), preview-
хелперы (`html_to_plain`/`normalize_preview`/`build_body_preview`) и `split_for_telegram`
(разбиение с потолком частей).
"""

from __future__ import annotations

from app.domain.mail_notify import (
    MAX_CHUNKS,
    MAX_TELEGRAM_TEXT_LEN,
    PREVIEW_LEN,
    build_body_preview,
    format_mailbox_down,
    format_message_body,
    format_notification,
    html_to_plain,
    normalize_preview,
    split_for_telegram,
)


# --- format_notification -----------------------------------------------------
def test_notification_has_account_and_client_lines() -> None:
    text = format_notification(
        acc_label="box@x.com",
        from_label="Иван",
        tag_names=["Поддержка"],
        subject="Привет",
        body_preview="тело",
    )
    assert "🆔" in text and "box@x.com" in text
    assert "Клиент" in text and "Иван" in text
    assert "Поддержка" in text
    assert "Тема" in text and "Привет" in text


def test_notification_placeholders_when_empty() -> None:
    text = format_notification(
        acc_label="b@x", from_label="f", tag_names=[], subject=None, body_preview=""
    )
    assert "Не сортировано" in text  # плейсхолдер тегов
    assert "(без темы)" in text  # плейсхолдер темы


def test_notification_escapes_html() -> None:
    text = format_notification(
        acc_label="<b>x</b>",
        from_label="a & b",
        tag_names=["<i>t</i>"],
        subject="<script>",
        body_preview="<img>",
    )
    # Пользовательские значения экранированы (нет сырых угловых скобок от инъекции).
    assert "&lt;b&gt;x&lt;/b&gt;" in text
    assert "&amp;" in text
    assert "&lt;script&gt;" in text


def test_notification_body_preview_omitted_when_empty() -> None:
    with_preview = format_notification(
        acc_label="b", from_label="f", tag_names=[], subject="s", body_preview="hello"
    )
    without = format_notification(
        acc_label="b", from_label="f", tag_names=[], subject="s", body_preview=""
    )
    assert "hello" in with_preview
    assert with_preview.count("\n") > without.count("\n")


# --- preview -----------------------------------------------------------------
def test_html_to_plain_strips_tags_and_scripts() -> None:
    html = "<style>.x{}</style><p>Привет</p><script>bad()</script><b>мир</b>"
    plain = html_to_plain(html)
    assert "Привет" in plain and "мир" in plain
    assert "bad()" not in plain
    assert "<" not in plain


def test_normalize_preview_truncates() -> None:
    long = "a" * (PREVIEW_LEN + 50)
    out = normalize_preview(long)
    assert len(out) <= PREVIEW_LEN + 1  # +1 на «…»
    assert out.endswith("…")


def test_build_body_preview_prefers_text() -> None:
    assert build_body_preview(body_text="plain", body_html="<p>html</p>") == "plain"


def test_build_body_preview_falls_back_to_html() -> None:
    out = build_body_preview(body_text="   ", body_html="<p>из HTML</p>")
    assert "из HTML" in out


# --- format_message_body -----------------------------------------------------
def test_message_body_empty_subject_and_body_placeholders() -> None:
    out = format_message_body(subject=None, from_label="f", body_text="", body_html=None)
    assert "(без темы)" in out
    assert "(пустое тело)" in out


def test_message_body_escapes() -> None:
    out = format_message_body(subject="s", from_label="<x>", body_text="a & b < c", body_html=None)
    assert "&lt;x&gt;" in out
    assert "&amp;" in out


# --- format_mailbox_down -----------------------------------------------------
def test_mailbox_down_includes_reason() -> None:
    out = format_mailbox_down(acc_label="box@x", last_sync_error="auth failed")
    assert "box@x" in out and "auth failed" in out
    assert out.startswith("⚠️")


def test_mailbox_down_without_reason() -> None:
    out = format_mailbox_down(acc_label="box@x", last_sync_error=None)
    assert "box@x" in out
    assert "не работает." in out  # без хвоста-причины


def test_mailbox_down_escapes_reason() -> None:
    out = format_mailbox_down(acc_label="b", last_sync_error="<script>x</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# --- split_for_telegram ------------------------------------------------------
def test_short_text_single_chunk() -> None:
    assert split_for_telegram("short") == ["short"]


def test_long_text_split_capped() -> None:
    text = "строка\n" * 5000
    chunks = split_for_telegram(text)
    assert 1 < len(chunks) <= MAX_CHUNKS
    assert all(len(c) <= MAX_TELEGRAM_TEXT_LEN + 2 for c in chunks)
    assert chunks[-1].endswith("…")  # маркер продолжения при усечении
