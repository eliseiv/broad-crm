"""Integration-тесты движка матчинга тегов почты (ADR-044 §5, порт ПОБУКВЕННО).

Фиксируют семантику `mail_tags_sql` против реального Postgres, чтобы будущий рефактор
не изменил тихо, какие письма получают теги: whole-word, case-**sensitive**,
экранирование метасимволов, нормализация U+00A0, `body_contains` по `body_text` И
strip-tags(`body_html`), `sender_contains` по `from_addr` И `from_name`, `sender_exact`
регистронезависимо, `match_mode` any/all, идемпотентность `ON CONFLICT DO NOTHING`.

Работают через `MailTagRepository.apply_tags_to_message` (путь приёма push'а) и
`apply_tag_to_existing` (bulk apply-to-existing) поверх тест-сессии — без FastAPI-app.
"""

from __future__ import annotations

import uuid

from app.models.mail_message import MailMessage
from app.models.mail_tag import MailTag, MailTagRule
from app.repositories.mail_tag_repository import MailTagRepository
from mail_helpers import dt, mail_db, seed_account
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_ACCOUNT_ID = 1


async def _make_tag(
    session: AsyncSession,
    *,
    name: str,
    match_mode: str,
    rules: list[tuple[str, str]],
) -> uuid.UUID:
    tag = MailTag(name=name, color="#2563eb", match_mode=match_mode)
    session.add(tag)
    await session.flush()
    for rtype, pattern in rules:
        session.add(MailTagRule(tag_id=tag.id, type=rtype, pattern=pattern))
    await session.flush()
    return tag.id


async def _make_message(
    session: AsyncSession,
    *,
    uid: int,
    subject: str | None = None,
    body_text: str = "",
    body_html: str | None = None,
    from_addr: str = "sender@example.com",
    from_name: str | None = None,
) -> MailMessage:
    msg = MailMessage(
        mail_account_id=_ACCOUNT_ID,
        uidvalidity=1,
        uid=uid,
        from_addr=from_addr,
        from_name=from_name,
        to_addrs="",
        internal_date=dt(),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    session.add(msg)
    await session.flush()
    return msg


async def _matched_ids(session: AsyncSession, message_id: int) -> set[uuid.UUID]:
    from app.models.mail_tag import MailMessageTag

    result = await session.execute(
        select(MailMessageTag.tag_id).where(MailMessageTag.message_id == message_id)
    )
    return set(result.scalars().all())


async def _apply(session: AsyncSession, *messages: MailMessage) -> None:
    repo = MailTagRepository(session)
    for m in messages:
        await repo.apply_tags_to_message(
            message_id=m.id,
            subject=m.subject,
            body_text=m.body_text,
            body_html=m.body_html,
            from_addr=m.from_addr,
            from_name=m.from_name,
        )


# ------------------------------------------------------------------ whole-word
async def test_whole_word_matches_token_and_bracketed_not_superstring() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="DPLA", match_mode="any", rules=[("subject_contains", "DPLA")]
        )
        m_word = await _make_message(session, uid=1, subject="DPLA build")
        m_bracket = await _make_message(session, uid=2, subject="[DPLA]")
        m_super = await _make_message(session, uid=3, subject="DPLAX")
        for m in (m_word, m_bracket, m_super):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, m_word.id)
        assert tag in await _matched_ids(session, m_bracket.id)
        assert tag not in await _matched_ids(session, m_super.id)


# ----------------------------------------------------------------- case-sensitive
async def test_subject_match_is_case_sensitive() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="DPLA", match_mode="any", rules=[("subject_contains", "DPLA")]
        )
        upper = await _make_message(session, uid=1, subject="DPLA build")
        lower = await _make_message(session, uid=2, subject="dpla build")
        for m in (upper, lower):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, upper.id)
        assert tag not in await _matched_ids(session, lower.id)


# --------------------------------------------------------- экранирование метасимволов
async def test_metacharacters_escaped_literal_match() -> None:
    """Паттерн `a.b`: точка экранируется → литерал; `axb` НЕ матчит, `a.b` матчит."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="dotlit", match_mode="any", rules=[("subject_contains", "a.b")]
        )
        literal = await _make_message(session, uid=1, subject="value a.b here")
        wildcard = await _make_message(session, uid=2, subject="value axb here")
        for m in (literal, wildcard):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, literal.id)
        assert tag not in await _matched_ids(session, wildcard.id)


# ------------------------------------------------- нормализация U+00A0 (nbsp)
async def test_nbsp_normalized_before_whole_word_match() -> None:
    """Неразрывный пробел в тексте нормализуется в обычный → паттерн со space матчит."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="phrase", match_mode="any", rules=[("subject_contains", "hello world")]
        )
        m = await _make_message(session, uid=1, subject="say hello world now")
        await MailTagRepository(session).apply_tags_to_message(
            message_id=m.id,
            subject=m.subject,
            body_text=m.body_text,
            body_html=m.body_html,
            from_addr=m.from_addr,
            from_name=m.from_name,
        )
        assert tag in await _matched_ids(session, m.id)


# ---------------------------------------- body_contains: body_text И strip(body_html)
async def test_body_contains_matches_plain_and_html_stripped() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="congrats", match_mode="any", rules=[("body_contains", "Congratulations")]
        )
        plain = await _make_message(session, uid=1, body_text="Congratulations! You shipped.")
        html_only = await _make_message(
            session, uid=2, body_text="", body_html="<p><b>Congratulations</b>!</p>"
        )
        miss = await _make_message(session, uid=3, body_text="nothing here", body_html=None)
        for m in (plain, html_only, miss):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, plain.id)
        assert tag in await _matched_ids(session, html_only.id)
        assert tag not in await _matched_ids(session, miss.id)


# ------------------------------------ sender_contains: from_addr И from_name
async def test_sender_contains_matches_addr_and_name() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="dev", match_mode="any", rules=[("sender_contains", "developer")]
        )
        via_addr = await _make_message(
            session, uid=1, from_addr="developer@apple.com", from_name=None
        )
        via_name = await _make_message(
            session, uid=2, from_addr="noreply@apple.com", from_name="Apple developer team"
        )
        miss = await _make_message(
            session, uid=3, from_addr="noreply@apple.com", from_name="Apple Team"
        )
        for m in (via_addr, via_name, miss):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, via_addr.id)
        assert tag in await _matched_ids(session, via_name.id)
        assert tag not in await _matched_ids(session, miss.id)


# ---------------------------------------------- sender_exact: регистронезависимо
async def test_sender_exact_case_insensitive_equality() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="dispute",
            match_mode="any",
            rules=[("sender_exact", "AppStoreNotices@apple.com")],
        )
        exact = await _make_message(session, uid=1, from_addr="appstorenotices@APPLE.com")
        substr = await _make_message(session, uid=2, from_addr="x-AppStoreNotices@apple.com")
        for m in (exact, substr):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag in await _matched_ids(session, exact.id)
        # sender_exact — полное равенство, не подстрока.
        assert tag not in await _matched_ids(session, substr.id)


# ------------------------------------------------------ match_mode any vs all
async def test_match_mode_any_needs_one_rule() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="any",
            match_mode="any",
            rules=[("subject_contains", "cancel"), ("subject_contains", "subscription")],
        )
        m = await _make_message(session, uid=1, subject="please cancel now")
        await MailTagRepository(session).apply_tags_to_message(
            message_id=m.id,
            subject=m.subject,
            body_text=m.body_text,
            body_html=m.body_html,
            from_addr=m.from_addr,
            from_name=m.from_name,
        )
        assert tag in await _matched_ids(session, m.id)


async def test_match_mode_all_needs_every_rule() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="all",
            match_mode="all",
            rules=[("body_contains", "cancel"), ("body_contains", "subscription")],
        )
        partial = await _make_message(session, uid=1, body_text="please cancel it")
        full = await _make_message(session, uid=2, body_text="cancel your subscription please")
        for m in (partial, full):
            await MailTagRepository(session).apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert tag not in await _matched_ids(session, partial.id)
        assert tag in await _matched_ids(session, full.id)


# ----------------------------------------- идемпотентность ON CONFLICT DO NOTHING
async def test_apply_tags_idempotent_on_conflict() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="DPLA", match_mode="any", rules=[("subject_contains", "DPLA")]
        )
        m = await _make_message(session, uid=1, subject="DPLA build")
        repo = MailTagRepository(session)
        for _ in range(3):
            await repo.apply_tags_to_message(
                message_id=m.id,
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                from_addr=m.from_addr,
                from_name=m.from_name,
            )
        assert await _matched_ids(session, m.id) == {tag}


# ------------------------------- apply_tag_to_existing (bulk) — тот же предикат
async def test_apply_tag_to_existing_bulk_matches_only_qualifying() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session, name="DPLA", match_mode="any", rules=[("subject_contains", "DPLA")]
        )
        hit = await _make_message(session, uid=1, subject="DPLA release")
        miss = await _make_message(session, uid=2, subject="unrelated")
        applied = await MailTagRepository(session).apply_tag_to_existing(tag)
        assert applied == 1
        assert tag in await _matched_ids(session, hit.id)
        assert tag not in await _matched_ids(session, miss.id)


# =================================================== реальные прод-паттерны (метасимволы)
# Источник: боевые правила разметки архива. Экранирование метасимволов обязано держать —
# при пере-применении к корпусу (2849×16) ошибка порта разметит тысячи писем вместо десятков.


async def test_bracket_pattern_escaped_not_char_class() -> None:
    """`[Build SUCCEEDED]` — квадратные скобки литеральны, НЕ символьный класс."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="Билд в коннекте sub",
            match_mode="any",
            rules=[("subject_contains", "[Build SUCCEEDED]")],
        )
        hit = await _make_message(session, uid=1, subject="[Build SUCCEEDED] MyApp 1.2")
        no_brackets = await _make_message(session, uid=2, subject="Build SUCCEEDED")
        one_char = await _make_message(session, uid=3, subject="SUCCEEDED")
        await _apply(session, hit, no_brackets, one_char)
        assert tag in await _matched_ids(session, hit.id)
        # Без экранирования char-class матчил бы «Build SUCCEEDED» и любую букву из набора.
        assert tag not in await _matched_ids(session, no_brackets.id)
        assert tag not in await _matched_ids(session, one_char.id)


async def test_parens_and_dot_pattern_escaped_literal() -> None:
    """`Hi! I need help with the app. (ukassa)` — круглые скобки и точка литеральны."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        pattern = "Hi! I need help with the app. (ukassa)"
        tag = await _make_tag(
            session, name="Поддержка", match_mode="any", rules=[("body_contains", pattern)]
        )
        hit = await _make_message(session, uid=1, body_text=f"{pattern} — please assist")
        # Точка и скобки как литералы: строка без них НЕ матчит.
        miss = await _make_message(session, uid=2, body_text="Hi! I need help with the app  ukassa")
        await _apply(session, hit, miss)
        assert tag in await _matched_ids(session, hit.id)
        assert tag not in await _matched_ids(session, miss.id)


async def test_angle_brackets_at_dots_in_sender_pattern() -> None:
    """`Apple Developer <developer@email.apple.com>` — <>, @, точки литеральны."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        pattern = "Apple Developer <developer@email.apple.com>"
        tag = await _make_tag(
            session, name="Small Business", match_mode="any", rules=[("sender_contains", pattern)]
        )
        hit = await _make_message(
            session, uid=1, from_name="Apple Developer <developer@email.apple.com>"
        )
        miss = await _make_message(session, uid=2, from_name="Apple Developer team")
        await _apply(session, hit, miss)
        assert tag in await _matched_ids(session, hit.id)
        assert tag not in await _matched_ids(session, miss.id)


async def test_typographic_apostrophe_not_a_metachar() -> None:
    """Типографский апостроф U+2019 в `We’re` не ломает матчинг (не метасимвол)."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        pattern = "We’re pleased to welcome you to the App Store Small Business Program"
        tag = await _make_tag(
            session, name="SBP body", match_mode="any", rules=[("body_contains", pattern)]
        )
        hit = await _make_message(session, uid=1, body_text=f"Hello. {pattern}. Regards.")
        await _apply(session, hit)
        assert tag in await _matched_ids(session, hit.id)


async def test_cyrillic_whole_word_pattern() -> None:
    """Whole-word работает на кириллице: `Запрос в поддержку`."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="Поддержка ру",
            match_mode="any",
            rules=[("subject_contains", "Запрос в поддержку")],
        )
        hit = await _make_message(session, uid=1, subject="Запрос в поддержку по приложению")
        miss = await _make_message(session, uid=2, subject="Другая тема")
        await _apply(session, hit, miss)
        assert tag in await _matched_ids(session, hit.id)
        assert tag not in await _matched_ids(session, miss.id)


async def test_all_mode_sender_and_subject_both_required() -> None:
    """`match_mode='all'`: нужны ОБА правила — sender Codemagic И subject `[Build SUCCEEDED]`."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=_ACCOUNT_ID)
        tag = await _make_tag(
            session,
            name="Билд в коннекте",
            match_mode="all",
            rules=[
                ("sender_contains", "Codemagic"),
                ("subject_contains", "[Build SUCCEEDED]"),
            ],
        )
        both = await _make_message(
            session,
            uid=1,
            subject="[Build SUCCEEDED] MyApp",
            from_name="Codemagic CI",
        )
        only_subject = await _make_message(
            session, uid=2, subject="[Build SUCCEEDED] MyApp", from_name="Someone Else"
        )
        only_sender = await _make_message(
            session, uid=3, subject="nightly report", from_name="Codemagic CI"
        )
        await _apply(session, both, only_subject, only_sender)
        assert tag in await _matched_ids(session, both.id)
        assert tag not in await _matched_ids(session, only_subject.id)
        assert tag not in await _matched_ids(session, only_sender.id)
