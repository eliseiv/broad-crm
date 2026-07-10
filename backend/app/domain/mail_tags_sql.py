r"""Параметризованный SQL движка тегов почты — порт из mail-агрегатора ПОБУКВЕННО.

Источник: `mail-agregator/backend/app/tags/sql.py` (ADR-0017 §4/§4.1/§4.2/§4.3).
Семантика предиката матчинга перенесена **без изменений**:

* whole-word, case-**SENSITIVE** матчинг (`~`, НЕ `~*`) над whitespace-нормализованным
  текстом; границы слова — **явные граничные классы** `(^|[^[:alnum:]_])`…
  `([^[:alnum:]_]|$)` (НЕ `\y` — round-27 fix: паттерн, начинающийся/кончающийся
  пунктуацией, при `\y` не матчился);
* нормализация: `norm(x) = regexp_replace(translate(x, chr(160), ' '), '\s+', ' ', 'g')`
  (U+00A0 → пробел ДО collapse `\s+`, т.к. в локали PG `\s` не покрывает nbsp);
* экранирование метасимволов паттерна: `regexp_replace(pattern,
  '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g')` (литеральный матч, ReDoS-safe);
* `body_contains` матчит `body_text` **И** tag-stripped `body_html`
  (`strip_tags(x) = regexp_replace(x, '<[^>]+>', ' ', 'g')`, round-29);
* `sender_contains` матчит `from_addr` **И** `from_name` (round-25);
* `sender_exact` = `LOWER(...) = LOWER(...)` (email — один токен, без norm/границ);
* `match_mode` any/all: any = EXISTS(rule), all = EXISTS(rule) AND NOT EXISTS(rule WHERE
  NOT predicate); блок предиката дублируется между ветками (SQL не выносит из двух
  коррелированных подзапросов), экранирование идентично в обеих;
* CAST для `:body_html`/`:sender_name` (nullable) — против asyncpg
  `AmbiguousParameterError`;
* `ON CONFLICT (message_id, tag_id) DO NOTHING` — идемпотентность.

**Отличие от агрегатора (единственное):** теги глобальны — из SQL **выпадают** ветки
visibility (`LEFT JOIN users`, `user_groups`, `super_admin`, `t.user_id IS NULL`,
`JOIN mail_accounts`). Остаётся чистый предикат матчинга правил над одним письмом
(теги видят ВСЕ письма системы). Таблицы переименованы под CRM: `tags`→`mail_tags`,
`tag_rules`→`mail_tag_rules`, `message_tags`→`mail_message_tags`,
`messages`→`mail_messages`. Известное ограничение `strip_tags` (не декодит
HTML-entities) наследуется как TD-024 (порт as-is).
"""

from __future__ import annotations

from typing import Final

# APPLY_TAGS_TO_MESSAGE — применить все матчащие теги к одному свежевставленному письму
# (на приёме push'а). Binds: :message_id, :subject, :body (body_text), :body_html,
# :sender (from_addr), :sender_name (from_name). Визибилити-веток нет (теги глобальны).
APPLY_TAGS_TO_MESSAGE: Final[str] = r"""
INSERT INTO mail_message_tags (message_id, tag_id)
SELECT :message_id, t.id
FROM mail_tags t
WHERE (
        -- match_mode = 'any' (OR, default): at least one rule of the tag matches.
        (t.match_mode = 'any' AND EXISTS (
            SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = t.id AND (
                (r.type = 'subject_contains' AND regexp_replace(translate(:subject, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')) OR
                (r.type = 'body_contains'    AND (
                    regexp_replace(translate(:body, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    OR regexp_replace(translate(regexp_replace(COALESCE(CAST(:body_html AS TEXT), ''), '<[^>]+>', ' ', 'g'), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                )) OR
                (r.type = 'sender_contains'  AND (
                    regexp_replace(translate(:sender, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    OR regexp_replace(translate(COALESCE(CAST(:sender_name AS TEXT), ''), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                )) OR
                (r.type = 'sender_exact'     AND LOWER(:sender) = LOWER(r.pattern))
            )
        ))
        OR
        -- match_mode = 'all' (AND): the tag has >=1 rule AND no rule fails to match.
        (t.match_mode = 'all'
            AND EXISTS (SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = t.id)
            AND NOT EXISTS (
                SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = t.id AND NOT (
                    (r.type = 'subject_contains' AND regexp_replace(translate(:subject, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')) OR
                    (r.type = 'body_contains'    AND (
                        regexp_replace(translate(:body, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                        OR regexp_replace(translate(regexp_replace(COALESCE(CAST(:body_html AS TEXT), ''), '<[^>]+>', ' ', 'g'), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    )) OR
                    (r.type = 'sender_contains'  AND (
                        regexp_replace(translate(:sender, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                        OR regexp_replace(translate(COALESCE(CAST(:sender_name AS TEXT), ''), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    )) OR
                    (r.type = 'sender_exact'     AND LOWER(:sender) = LOWER(r.pattern))
                )
            )
        )
    )
ON CONFLICT (message_id, tag_id) DO NOTHING
"""


# APPLY_TAG_TO_EXISTING — bulk-INSERT: применить правила тега :tag_id ко ВСЕМ письмам
# (apply-to-existing). Читает поля прямо из колонок m.* (без bind/CAST). Визибилити-
# веток нет (теги глобальны — тег видит все письма системы).
APPLY_TAG_TO_EXISTING: Final[str] = r"""
INSERT INTO mail_message_tags (message_id, tag_id)
SELECT m.id, :tag_id
FROM mail_messages m
WHERE (
        -- match_mode = 'any' (OR, default): at least one rule of the tag matches.
        ((SELECT match_mode FROM mail_tags WHERE id = :tag_id) = 'any' AND EXISTS (
            SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = :tag_id AND (
                (r.type = 'subject_contains' AND regexp_replace(translate(m.subject,   chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')) OR
                (r.type = 'body_contains'    AND (
                    regexp_replace(translate(m.body_text, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    OR regexp_replace(translate(regexp_replace(COALESCE(m.body_html, ''), '<[^>]+>', ' ', 'g'), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                )) OR
                (r.type = 'sender_contains'  AND (
                    regexp_replace(translate(m.from_addr, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    OR regexp_replace(translate(COALESCE(m.from_name, ''), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                )) OR
                (r.type = 'sender_exact'     AND LOWER(m.from_addr) = LOWER(r.pattern))
            )
        ))
        OR
        -- match_mode = 'all' (AND): the tag has >=1 rule AND no rule fails to match.
        ((SELECT match_mode FROM mail_tags WHERE id = :tag_id) = 'all'
            AND EXISTS (SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = :tag_id)
            AND NOT EXISTS (
                SELECT 1 FROM mail_tag_rules r WHERE r.tag_id = :tag_id AND NOT (
                    (r.type = 'subject_contains' AND regexp_replace(translate(m.subject,   chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')) OR
                    (r.type = 'body_contains'    AND (
                        regexp_replace(translate(m.body_text, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                        OR regexp_replace(translate(regexp_replace(COALESCE(m.body_html, ''), '<[^>]+>', ' ', 'g'), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    )) OR
                    (r.type = 'sender_contains'  AND (
                        regexp_replace(translate(m.from_addr, chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                        OR regexp_replace(translate(COALESCE(m.from_name, ''), chr(160), ' '), '\s+', ' ', 'g') ~ ('(^|[^[:alnum:]_])' || regexp_replace(translate(regexp_replace(r.pattern, '([\^$.|?*+()\[\]{}\\])', '\\\1', 'g'), chr(160), ' '), '\s+', ' ', 'g') || '([^[:alnum:]_]|$)')
                    )) OR
                    (r.type = 'sender_exact'     AND LOWER(m.from_addr) = LOWER(r.pattern))
                )
            )
        )
    )
ON CONFLICT (message_id, tag_id) DO NOTHING
"""
