# ADR-060 — Внешний read-only API-ключ модуля «Документы» (env-хранение, статический `X-API-Key`, constant-time; машина видит всё + `visibility_role_ids`)

- **Статус:** accepted
- **Дата:** 2026-07-17
- **Контекст-модули:** [documents](../modules/documents/README.md)
- **Связано:** [ADR-059](ADR-059-documents-module.md); секреты — [05-security.md](../05-security.md#секреты-и-их-хранение), внешний контракт — [04-api.md](../04-api.md#external-documents-read-only-rag)

## Контекст

Будущей RAG-базе ИИ нужен машинный read-only доступ к документам CRM: синхронизировать документы, фиксировать изменения/удаления, проверять эффективный уровень доступа (какие роли видят узел). Требование владельца: **полный read-only СЕЙЧАС** (не read-write, без привязки ключа к роли).

**Факты по репо:** машинные приёмники почты аутентифицируются **HMAC-SHA256 над сырым телом** (`MAIL_PUSH_SECRET`, `X-Mail-Signature`/`X-Mail-Timestamp`), т.к. это POST с телом; порядок «пустой секрет → `503`, невалидно → `401`» ([04-api.md](../04-api.md#машинные-эндпоинты-push-агрегатор--crm), [05-security.md](../05-security.md#push-контракт-агрегатор--crm-hmac-нормативно)). `MAIL_API_KEY` — статический env-ключ (`X-API-Key`), не в БД/логах/ответах/URL.

## Решение

### §1. Статический `X-API-Key` (env `DOCUMENTS_API_KEY`), НЕ HMAC-подпись тела

Внешний read-only использует **статический ключ** в заголовке `X-API-Key`, хранимый **только в env** `DOCUMENTS_API_KEY` (класс секретов `MAIL_API_KEY`/`MAIL_PUSH_SECRET`: ротация через деплой; не в БД/логах/ответах/URL). Сравнение входящего ключа — **constant-time** `hmac.compare_digest`.

**Почему не HMAC над телом (как mail push):** внешние вызовы — **read-only GET без тела** ⇒ подписывать нечего; body-binding HMAC ничего не защищает при отсутствии тела и добавил бы клиенту сложность построения канонической подписи. Статический ключ по HTTPS достаточен и проще (NFR-1). Это **осознанное отличие** от mail-приёмников (там POST с телом ⇒ HMAC оправдан).

**Порядок проверок (нормативно, образец mail_ingest):** пустой `DOCUMENTS_API_KEY` → **`503 documents_external_not_configured`** (приёмник выключен) → неверный/отсутствующий `X-API-Key` → **`401 not_authenticated`**. Эндпоинты — без JWT, CSRF-exempt, `Cache-Control: no-store`.

### §2. Машина видит ВСЕ узлы + отдаёт `visibility_role_ids` в каждом ответе

Ключ **обходит** per-role фильтр видимости (§[ADR-059](ADR-059-documents-module.md) §3): RAG индексирует весь корпус. Но **каждый** ответ несёт **`visibility_role_ids[]`** (эффективный набор ролей узла, вычисленный тем же CTE, что внутренний резолв) + **`content_version`**; публичный узел → `visibility_role_ids = []`. **Фильтрацию по роли конечного пользователя выполняет RAG на своей стороне** — CRM отдаёт факты доступа, не решает за ИИ.

Отдельный эндпоинт `GET /{id}/access` возвращает `{id, is_public, visibility_role_ids[], content_version}` — для точечной проверки уровня доступа.

### §3. Контур синхронизации — keyset по `(updated_at, id)` + tombstones

- `GET /?updated_after=&include_deleted=&cursor=&limit=` — список для синка, keyset-пагинация по `(updated_at, id)` **ASC** (forward-walk от водяного знака; техника — образец mail-курсора, но направление ASC и поле `updated_at`, а не `internal_date DESC`).
- `GET /{id}` — полный узел (+`content_md`); удалённый → **`410 document_node_gone`** с tombstone.
- `GET /changes?since=&cursor=&limit=` — дельта: изменённые узлы + tombstones с водяного знака `since`.
- Tombstone — `{id, deleted_at, content_version}` без `content_md` (soft-delete, [ADR-059](ADR-059-documents-module.md) §5).

Индекс `ix_document_nodes_updated_at_id (updated_at, id)` (не частичный) обслуживает keyset **включая** удалённые ([03-data-model.md](../03-data-model.md#таблицы-модуля-документы-document_nodes-document_node_roles)).

## Последствия

- Новый env-секрет `DOCUMENTS_API_KEY` — регистрируется в [05-security.md](../05-security.md#секреты-и-их-хранение) (класс `MAIL_API_KEY`: env-only, не в БД/логах/ответах/SPA/URL), `.env.example` (зона **devops**). Пустой ⇒ внешний контур выключен (`503`).
- Новые коды ошибок `documents_external_not_configured` (503), `document_node_gone` (410) — в [04-api.md](../04-api.md#глобальные-коды-ошибок).
- **Read-only гарантия:** внешний роутер регистрирует **только GET**; отсутствие write-эндпоинтов — инвариант, проверяемый ревью.
- Ключ обходит видимость ⇒ его утечка = чтение всего корпуса документов; митигация — env-хранение, HTTPS, ротация деплоем, отсутствие в логах/ответах (structlog-фильтр). Угроза внесена в модель угроз [05-security.md](../05-security.md#модель-угроз-этап-1).

## Альтернативы

- **HMAC-подпись тела (как mail push)** — отклонено: read-only GET без тела, подписывать нечего (§1).
- **Привязка ключа к роли/scope** (машина видит только узлы роли X) — **отклонено** (преждевременная сложность, NFR-1; требование владельца «полный read-only сейчас»). RAG получает `visibility_role_ids` и фильтрует сам; при появлении multi-tenant-требования — отдельный ADR (per-key scope/ротация несколькими ключами).
- **Ключ в БД (управление через UI)** — отклонено: класс env-секретов mail единообразнее, ротация через деплой, ключ не попадает в ответы API.
