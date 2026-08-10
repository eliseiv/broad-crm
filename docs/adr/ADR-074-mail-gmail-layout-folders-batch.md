# ADR-074: Gmail-like layout `/mail` + папки + личный archive/delete + batch

**Статус:** accepted  
**Дата:** 2026-08-10  
**Амендмент:** [ADR-050](ADR-050-mail-search-team-filter-personal-read-state.md) §2.4 (badge-счётчик), [ADR-044](ADR-044-mail-full-merge-into-crm.md) §2

## Контекст

UI `/mail` переводится на Gmail-like layout (сайдбар + компактный список + деталь). Нужны папки, bulk-действия и серверные фильтры по тегам.

## Решение

### 1. Личное состояние письма (расширение `mail_message_reads`, миграция `0035`)

- `archived_at`, `deleted_at` — nullable timestamptz
- `read_at` → **nullable** (`NULL` = непрочитано при существующей строке)
- `is_unread` = нет строки **ИЛИ** `read_at IS NULL`

Папки (`GET /api/mail/messages?folder=`):

| `folder` | Предикат (для user) |
|----------|---------------------|
| `inbox` (default) | нет `deleted_at` и нет `archived_at` |
| `archived` | `archived_at IS NOT NULL`, `deleted_at IS NULL` |
| `deleted` | `deleted_at IS NOT NULL` |

Доп. query: `has_tags=true`, `tag_id=<uuid>` (AND с остальными).

### 2. Отправленные

`GET /api/mail/sent`, `GET /api/mail/sent/{id}` — лента из `mail_sent_messages` (ответы CRM, не IMAP Sent). Keyset `(sent_at DESC, id DESC)`.

### 3. Счётчик непрочитанных

`GET /api/mail/unread-count` — COUNT непрочитанных в `folder=inbox` с теми же scope-фильтрами. **Разворачивает** ADR-050 §2.4 «badge не вводится».

### 4. Batch (гейт `mail:view`, scope как read)

- `POST /api/mail/messages/batch/read`
- `POST /api/mail/messages/batch/archive` — `archived_at=now()`, `deleted_at=NULL`
- `POST /api/mail/messages/batch/delete` — `deleted_at=now()`
- `POST /api/mail/messages/batch/unarchive`, `batch/restore` — UX корзины

### 5. UI (08-design-system.md)

Трёхколоночный layout: сайдбар | список | деталь. Папки: Входящие, Отправленные, Удалённые, **С тегами** (не Спам). Блок «Команды» — при ≥2 вариантах канала (ADR-055).
