# Модуль `documents` — Документы (Notion/Kaiten-подобный менеджер знаний в БД CRM)

Статус: `implemented` (Спринт 1–2) · **изображения ([ADR-068](../../adr/ADR-068-documents-image-attachments.md)) реализованы и покрыты тестами (2026-07-22/23)** — `qa`: backend **1590 passed / 0 failed** на реальном Postgres (coverage 88 %), frontend **941/941** (90 %). ⚠️ Вне покрытия остаётся **runtime-smoke стека** (права `DOCUMENTS_ATTACHMENTS_DIR` под `-u app`, пин uid/gid `999`) — он контейнерный и юнит-/интеграционными тестами не заменяется ([06-testing-strategy.md §Runtime-smoke](../../06-testing-strategy.md#runtime-smoke-стека-перед-релизом)). · Исполнители: backend, frontend, devops

> Действующая архитектура — [ADR-059](../../adr/ADR-059-documents-module.md) (гринфилд-модуль: единая таблица `document_nodes`, permission-based enforcement, вычисляемое наследование видимости по ролям, soft-delete для RAG), [ADR-060](../../adr/ADR-060-documents-external-readonly-api-key.md) (внешний read-only API-ключ), [ADR-061](../../adr/ADR-061-documents-sidebar-two-panel-nav.md) (двухпанельный сайдбар `/documents` — частичный разворот плоской навигации [ADR-033](../../adr/ADR-033-flat-nav-theme-toggle-numbers-table.md)), [ADR-062](../../adr/ADR-062-documents-wysiwyg-tiptap.md) (WYSIWYG-редактор TipTap), [ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md) (контракт кэша узла после `PATCH`, жизненный цикл редактора и ресинк версии, фокус по клику, локализация каталога прав — **отменяет** норму «keyed по `id:content_version`»), [ADR-068](../../adr/ADR-068-documents-image-attachments.md) (**изображения в документах**: файлы на volume + таблица `document_attachments`, авторизованная отдача через `blob:`).
>
> **Ключевые инварианты:** видимость — по **РОЛЯМ** (`role_id`), НЕ по командам; редактор — WYSIWYG (хранение — markdown в `content_md`); внешний API — **полный read-only и ТЕКСТОВЫЙ** (RAG; байты изображений наружу не отдаются); загрузка **документа** — только `.md`; загрузка **изображения** — только `png/jpeg/webp/gif` (SVG исключён); `data:`-картинки в `content_md` запрещены.

## Scope

Страница **«Документы»** (`/documents`) — двухпанельный менеджер документов (левый сайдбар с деревом папок + правая панель контента). Возможности:

- Дерево папок и Markdown-документов (единая self-referencing таблица `document_nodes`).
- Создание папки / документа; открытие документа; **WYSIWYG-редактирование** (хранение — markdown); **загрузка `.md`-файла** как документа.
- **Изображения в документе** ([ADR-068](../../adr/ADR-068-documents-image-attachments.md)): вставка **кнопкой тулбара** и **из буфера обмена (Ctrl+V)**; байты — файлом на volume, метаданные — в `document_attachments`; в markdown — `![alt](/api/documents/attachments/{id})`. **Drag-and-drop не поддерживается** (решение владельца).
- **Контекстное меню (kebab, 3 точки) на строке узла:** «Удалить» / «Создать копию» / «Переименовать» / «Сменить видимость».
- **Видимость по ролям** (`visibility_mode='restricted'` + набор `role_id`); вычисляемое наследование вниз по дереву.
- **Внешний read-only API по статическому API-ключу** (`X-API-Key`) для будущей RAG-базы ИИ: синхронизация документов, фиксация изменений/удалений (tombstones), проверка эффективного уровня доступа.

**CRM — единственная система-запись:** дерево, контент, видимость, авторство, версия контента и tombstones хранятся в БД CRM. Внешних зависимостей у модуля нет.

## Out of scope

- **Вложения-НЕизображения** (pdf/docx/архивы и т.п.) — [TD-065](../../100-known-tech-debt.md) (сужен: **изображения реализованы** — [ADR-068](../../adr/ADR-068-documents-image-attachments.md), 2026-07-22; произвольные файлы — нет ни в спеке, ни в коде). **SVG — вне scope нормативно** (активный документ = XSS-вектор с нашего origin), а не «пока не сделали».
- **Замена/редактирование загруженного изображения** — вложения **иммутабельны**: «заменить картинку» = загрузить новую (новый `id`) и заменить ссылку. Именно на этом стоит отказ расширять внешний RAG-контракт ([ADR-068](../../adr/ADR-068-documents-image-attachments.md) §6).
- **Выдача байтов изображений во внешний RAG-контур** — [TD-074](../../100-known-tech-debt.md) (контур остаётся текстовым).
- **GC осиротевших файлов вложений** — [TD-076](../../100-known-tech-debt.md).
- Полнотекстовый поиск по документам — [TD-066](../../100-known-tech-debt.md).
- Обязательный optimistic-lock (сейчас `expected_version` **опционален**) — [TD-064](../../100-known-tech-debt.md).
- Ретенция/GC tombstones — [TD-067](../../100-known-tech-debt.md).
- Внешний **read-write** API, привязка API-ключа к роли/scope (машина видит всё, фильтрует RAG на своей стороне — [ADR-060](../../adr/ADR-060-documents-external-readonly-api-key.md)).
- Видимость по командам (владелец решил: строго по ролям).
- Совместное real-time редактирование (CRDT/OT).

## Архитектура

```mermaid
flowchart LR
    subgraph SPA["SPA /documents ([ADR-061])"]
        TREE["TreeView (сайдбар)"]
        ED["DocumentEditor (WYSIWYG, TipTap)"]
        KEBAB["kebab-меню узла"]
        VMOD["Модалка видимости (Modal + MultiSelect)"]
    end
    subgraph CRM["CRM backend (JWT)"]
        DOCAPI["/api/documents/* (require documents:*)"]
        SVC["DocumentService (резолв видимости, copy, soft-delete)"]
        DB[("document_nodes\ndocument_node_roles")]
    end
    subgraph RAG["Внешний ИИ (RAG)"]
        EXT["/api/external/documents/* (X-API-Key, read-only)"]
    end
    SPA -->|JWT| DOCAPI --> SVC --> DB
    RAG -->|X-API-Key| EXT --> DB
```

**Слои backend (по образцу mail/sms):**

| Слой | Назначение |
|------|-----------|
| `api/documents.py` | Внутренние JWT-эндпоинты `/api/documents/*`, гейты `require("documents", <action>)` + per-node фильтр видимости. |
| `api/external_documents.py` | Внешние read-only эндпоинты `/api/external/documents/*`, проверка `X-API-Key` (без JWT, CSRF-exempt). |
| `services/document_service.py` | Резолюция эффективной видимости (рекурсивный CTE), copy поддерева, soft-delete каскад, инкремент `content_version`. |
| `repositories/document_repository.py` | Доступ к `document_nodes`/`document_node_roles`, keyset-пагинация для внешнего sync. |
| `schemas/document.py` | Pydantic-схемы request/response (внутренние и внешние). |
| `models/document_node.py`, `models/document_node_role.py`, `models/document_attachment.py` | ORM-модели. |
| `services/document_attachment_service.py` | Валидация загрузки (размер по потоку, magic-bytes, whitelist), путь из `id`+`mime`, атомарная запись/удаление файла, копирование вложений при copy ([ADR-068](../../adr/ADR-068-documents-image-attachments.md)). |

**Данные:** таблицы `document_nodes` + `document_node_roles` — [03-data-model.md §Модуль «Документы»](../../03-data-model.md#таблицы-модуля-документы-document_nodes-document_node_roles); **`document_attachments`** — [03-data-model.md](../../03-data-model.md#таблица-document_attachments-adr-068). Миграции (концепт): **`0029_document_nodes`** (19 симв. ≤32), **`0030_document_node_roles`** (24 симв. ≤32), **`0032_document_attachments`** (25 симв. ≤32); `down_revision` первой = фактическая голова цепочки на момент реализации (backend сверяет по `alembic_version`). Backfill не нужен (таблицы стартуют пустыми).

**Второй носитель состояния:** байты изображений живут **файлами на volume `documents-attachments`** ([07-deployment.md](../../07-deployment.md#volume-documents-attachments-нормативно-adr-068)), а не в БД. Следствия, которые нельзя забыть: (1) бэкап приложения — **два согласованных объекта** (`pgdata` + volume), одна лишь БД даёт документы с битыми картинками; (2) `downgrade` миграции `0032` файлы **не удаляет** (Alembic диском не управляет).

## Модель данных и инварианты

- **`document_nodes`** — единая таблица папок и документов (`node_type ∈ {folder, document}`), self-referencing дерево через `parent_id` (`NULL` = корень, `ON DELETE CASCADE`).
- **Папка не хранит контент:** CHECK `ck_document_nodes_folder_no_content` — `content_md` у папки всегда `NULL`.
- **`owner_id`** — автор узла (FK `users(id) ON DELETE RESTRICT`), **только для отображения**, НЕ гейт (enforcement — permission-based, см. [RBAC](#rbac)). Действия консольного супер-админа записывают `owner_id = SUPERADMIN_USER_ID` (системная строка-якорь, [ADR-051](../../adr/ADR-051-superadmin-db-anchor-personal-state.md)) — у него нет иного `user_id`.
- **`content_version bigint DEFAULT 1`** — инкрементируется **только** при изменении `content_md` ИЛИ `name` (переименование/правка контента). Смена видимости/перемещение/soft-delete `content_version` **не** меняют. Точка фиксации для RAG (нужно ли переэмбеддить).
- **`updated_at`** — обновляется при **ЛЮБОЙ** мутации строки (rename, content, visibility, move/position, delete). Именно `updated_at` — водяной знак внешнего sync (любое изменение возвращает узел в дельту; `content_version` уточняет, менялся ли контент).
- **`visibility_mode ∈ {inherit, restricted}`** (`DEFAULT 'inherit'`). Строки `document_node_roles` существуют **только** для узлов `restricted`.
- **`deleted_at timestamptz NULL`** — **soft-delete** (обязателен для RAG-tombstone). Это **осознанный отход** от репо-конвенции hard-delete (везде hard-delete + [TD-001](../../100-known-tech-debt.md)); обоснование — RAG обязан узнавать об удалениях ([ADR-059](../../adr/ADR-059-documents-module.md)). Удалённый узел исключён из всех внутренних выборок (`WHERE deleted_at IS NULL`).
- **Дубликаты имён в папке РАЗРЕШЕНЫ** (как в Notion; уникален только `id`) — UNIQUE по `(parent_id, name)` НЕ вводится. Зафиксировано как решение ([ADR-059](../../adr/ADR-059-documents-module.md)).
- **Лимит размера markdown** — `DOCUMENTS_MAX_MD_BYTES` (env, default `1_048_576` = 1 МБ) проверяется и при upload, и при inline-правке контента; превышение → `422` (upload → `document_upload_invalid`, inline → `validation_error`, поле `content_md`).

## Видимость по ролям и её резолюция (нормативно)

Два **независимых** уровня доступа (оба обязаны выполниться):

1. **`documents:view`** — право на страницу/API вообще. Механизм — `require("documents","view")` (см. [05-security.md](../../05-security.md#каталог-прав-канон-на-сервере), `deps.py`). Нет права → `403 forbidden` (страница-заглушка на фронте — [08-design-system.md](../../08-design-system.md#гейтинг-навигации-и-действий-по-правам-rbac-нормативно)).
2. **Видимость по ролям (per-node)** — фильтр **внутри** модуля. У пользователя ровно одна роль (`users.role_id`, [ADR-021](../../adr/ADR-021-rbac-users-roles.md)). Узел виден ⇔ он **публичен внутри модуля** ИЛИ его **эффективный набор ролей** содержит `role_id` пользователя.

**Эффективная видимость узла (вычисляемая, НЕ материализованная):**

- Рекурсивный CTE вверх по `parent_id` до **ближайшего `restricted`-предка** (включая сам узел).
- Найден ближайший `restricted`-узел `R` → **эффективный набор ролей** = `document_node_roles` узла `R`. Узел виден пользователю ⇔ `role_id ∈` этот набор.
- Ветка полностью `inherit` до корня (ни одного `restricted`-предка) → узел **публичен внутри модуля** (виден всем, у кого есть `documents:view`).

```sql
WITH RECURSIVE chain AS (
    SELECT id, parent_id, visibility_mode, 0 AS depth
    FROM document_nodes WHERE id = :node_id AND deleted_at IS NULL
  UNION ALL
    SELECT n.id, n.parent_id, n.visibility_mode, c.depth + 1
    FROM document_nodes n JOIN chain c ON n.id = c.parent_id
    WHERE n.deleted_at IS NULL
)
SELECT id FROM chain WHERE visibility_mode = 'restricted'
ORDER BY depth ASC LIMIT 1;   -- ближайший restricted-предок; нет строки ⇒ узел публичен
```

**Admin-уровень видит всё.** Предикат «видит все узлы» — тот же admin-level, что в mail/sms ([ADR-032](../../adr/ADR-032-sms-visibility-admin-full-catalog.md)):

```
sees_all_documents = principal.is_superadmin OR permissions_subset(full_catalog_permissions(), principal.permissions)
```

Такой актор (консольный супер-админ; роль с полным каталогом) видит и правит **любой** узел, per-role фильтр к нему не применяется.

**Анти-энумерация.** Невидимую пользователю ноду **нельзя** и читать по `id`, и править/удалять → **`404 document_node_not_found`** (не `403`) — неотличимо от несуществующей. Список/дерево (`GET /tree`, `GET /nodes`) — **фильтруются** (невидимый узел просто отсутствует). Это симметрично «пустому scope» в mail/sms.

**Enforcement — permission-based, НЕ owner-based** (нормативно, [ADR-059](../../adr/ADR-059-documents-module.md)): `owner_id` — только автор для отображения; право читать/править/удалять узел определяется `documents:<action>` + видимостью по роли, а НЕ совпадением `owner_id == user_id`. Согласованность с RBAC-каноном репо (везде enforcement на правах, не на владении).

## RBAC

Страница **`documents`** в каталоге прав ([05-security.md](../../05-security.md#каталог-прав-канон-на-сервере), `app/domain/permissions.py::CATALOG`): действия **`view, create, edit, delete, share`**.

- **Подпись раздела в UI — «Документы»** ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md) §D): сервер отдаёт только технический ключ `documents`, локализация каталога живёт на фронтенде ([08-design-system.md §Словарь](../../08-design-system.md#страница-роли-1)); отсутствие подписи — дефект локализации, а не безобидный фолбэк.
- ⚠️ **`documents:share` сейчас нельзя выдать через UI** — матрица прав `/roles` рендерит только `view/create/edit/delete` ([TD-068](../../100-known-tech-debt.md)). Серверный каталог и контракт `PATCH /api/roles/{id}` действие принимают; пробел — на стороне UI.
- `share` — **отдельное чувствительное действие смены видимости** узла (по образцу того, как `mail` имеет `sync`/`tags` сверх CRUD): управлять тем, кто видит узел, — привилегия сильнее обычного `edit`.
- Маппинг метод→действие ([04-api.md](../../04-api.md#rbac-и-enforcement-прав)): `GET → documents:view` (+ per-node фильтр); `POST /folders`/`/documents`/`/upload`/`/nodes/{id}/copy → documents:create`; `PATCH /nodes/{id}` (rename/content) → `documents:edit`; `PATCH /nodes/{id}/visibility → documents:share`; `PATCH /order → documents:edit`; `DELETE /nodes/{id} → documents:delete`. **Исключение из `GET → documents:view`:** `GET /nodes/{id}/visibility` и `GET /role-refs` гейтятся **`documents:share`** (read-сторона модалки видимости — чувствительна как и write, `share`-контур).
- **Вложения** ([ADR-068](../../adr/ADR-068-documents-image-attachments.md)): `POST /nodes/{id}/attachments → documents:edit`, `DELETE /attachments/{id} → documents:edit`, `GET /attachments/{id} → documents:view` **+ тот же per-node фильтр видимости узла-владельца**. **Загрузка под `edit`, а не `create`** — вложение есть часть контента **существующего** узла, тогда как `create` в этом модуле означает «создать узел». **Нового действия в каталоге не вводится.**
- **Список ролей для модалки видимости** не-админу с `documents:share` — лёгкий `GET /api/documents/role-refs` (`{id, name}[]`) под гейтом `documents:share`. **НЕ** переиспользуется admin-gated `GET /api/roles` (он под `require("roles","view")` — не-админ его не получит, ровно тот дефект, что был у mail в [TD-050](../../100-known-tech-debt.md)).

## Внутренний API (JWT, префикс `/api/documents`)

Полные контракты — [04-api.md §Documents](../../04-api.md#documents). Кратко:

| Метод / путь | Действие | Назначение |
|--------------|----------|-----------|
| `GET /tree` | `view` (+фильтр) | Всё видимое дерево (папки+документы). |
| `GET /nodes?parent_id=` | `view` (+фильтр) | Дети узла (`parent_id` пуст/`null` = корень). |
| `GET /nodes/{id}` | `view` (+фильтр) | Узел (+`content_md` для документа). Невидим → `404`. |
| `POST /folders` | `create` | Создать папку (`{parent_id, name}`). |
| `POST /documents` | `create` | Создать документ (`{parent_id, name, content_md?}`). |
| `POST /upload` | `create` | multipart, **только `.md`**; не-`.md`/размер/битый UTF-8 → `422 document_upload_invalid`. |
| `PATCH /nodes/{id}` | `edit` | Rename и/или content; `content_version += 1`; опц. `expected_version` (mismatch → `409 document_node_conflict`). |
| `POST /nodes/{id}/copy` | `create` | Рекурсивная копия поддерева (новые `id`); цикл → `422 document_copy_cycle`. |
| `GET /nodes/{id}/visibility` | `share` | Собственные настройки видимости узла для **предзаполнения** модалки: `{visibility_mode, role_ids[]}` (собственные роли узла; `inherit` → `[]`). Read↔write симметрия с `PATCH …/visibility`. |
| `PATCH /nodes/{id}/visibility` | `share` | `{visibility_mode, role_ids[]}`. |
| `PATCH /order` | `edit` | Полная перестановка уровня (`{parent_id, ids[]}`), проверка полноты как у reorder серверов. |
| `DELETE /nodes/{id}` | `delete` | Soft-delete; папка — каскад поддерева (tombstone на каждый узел). |
| `GET /role-refs` | `share` | `{id,name}[]` ролей для модалки видимости. |
| `POST /nodes/{id}/attachments` | `edit` | Загрузка изображения (multipart). Whitelist `png/jpeg/webp/gif` **по magic bytes**, ≤ `DOCUMENTS_MAX_IMAGE_BYTES`; отказ → `422 document_attachment_invalid`. |
| `GET /attachments/{id}` | `view` (+фильтр видимости узла) | Байты. `ETag`=`checksum`, `Cache-Control: private…` (**не `public`**). Нет/невидим/удалён → **единый** `404 document_attachment_not_found`. |
| `DELETE /attachments/{id}` | `edit` | Удаление строки + файла (файл — после `commit`). |

**Сортировка уровня** — по канону `position` ([03-data-model.md](../../03-data-model.md#колонка-position-порядок-карточек)): `ORDER BY position ASC, created_at DESC, id`.

## Внешний read-only API (RAG, `X-API-Key`, префикс `/api/external/documents`)

Полные контракты — [04-api.md §External Documents](../../04-api.md#external-documents-read-only-rag). Кратко:

- **Аутентификация** — статический ключ `X-API-Key`, хранение — **только env** `DOCUMENTS_API_KEY` (класс секретов mail: ротация через деплой; не в БД/логах/ответах/URL). Сравнение — constant-time `hmac.compare_digest`. **Порядок проверок** (образец mail_ingest): пустой `DOCUMENTS_API_KEY` → **`503 documents_external_not_configured`** → неверный/отсутствующий `X-API-Key` → **`401 not_authenticated`**.
- **Read-only GET без тела** ⇒ подпись тела (HMAC, как у mail push) **не нужна** — статического ключа достаточно ([ADR-060](../../adr/ADR-060-documents-external-readonly-api-key.md)).
- **Машина видит ВСЕ узлы** (обходит per-role фильтр), но каждый ответ несёт **`visibility_role_ids[]`** + **`content_version`**. RAG сам фильтрует по роли конечного пользователя; публичный узел → `visibility_role_ids = []`.

| Метод / путь | Назначение |
|--------------|-----------|
| `GET /?updated_after=&include_deleted=&cursor=&limit=` | Список для синка; keyset-пагинация по `(updated_at, id)` ASC (образец mail-курсора). Каждый элемент — метаданные + `visibility_role_ids[]` + `content_version` (+ `deleted_at` при tombstone). |
| `GET /{id}` | Полный узел (+`content_md`); удалённый → **`410 document_node_gone`** (tombstone). |
| `GET /{id}/access` | Эффективный доступ: `{id, is_public, visibility_role_ids[], content_version}`. |
| `GET /changes?since=&cursor=&limit=` | Дельта с водяного знака: изменённые узлы + tombstones (`include_deleted` подразумевается), keyset по `(updated_at, id)` ASC. |

## Edge-cases (нормативно)

- **Удаление непустой папки** — каскадный **soft-delete** всего поддерева в одной транзакции: `deleted_at` проставляется папке и каждому потомку (`ON DELETE CASCADE` служит только для физического каскада при hard-delete `owner`/`role`; здесь удаление логическое, поддерево обходится рекурсивным CTE вниз). RAG получит tombstone на **каждый** узел поддерева. **Вложения soft-delete не трогает** (ни строки, ни файлы), но их отдача → `404 document_attachment_not_found`; физуборка — вместе с GC tombstones ([TD-067](../../100-known-tech-debt.md), расширен на файлы).
- **Копия папки с вложенными** — рекурсия по поддереву: новые `id`, **сохранение** структуры/`position`/`visibility_mode` и строк `document_node_roles`; одна транзакция. `owner_id` копий = актор копирования. `content_version` копий = 1. **Вложения копируются физически** (новые `id` + новые файлы), и **в `content_md` копии ссылки переписываются** литеральной заменой `/api/documents/attachments/<old_uuid>` → `…/<new_uuid>` по карте копирования (**regex по markdown запрещён** — ложные срабатывания в коде/цитатах). Общий файл с refcount отвергнут: копия перестала бы быть независимой от оригинала ([ADR-068](../../adr/ADR-068-documents-image-attachments.md) §5).
- **Циклы при copy** — цель копирования (`parent_id`) не должна быть самим узлом или его потомком (проверка рекурсивным CTE вниз от копируемого узла); нарушение → **`422 document_copy_cycle`**.
- **Наследование/override видимости** — потомок с `visibility_mode='inherit'` наследует ближайшего `restricted`-предка; собственный `restricted` на потомке **переопределяет** (его набор ролей действует ниже). Публичность = отсутствие `restricted`-предков до корня.
- **Конфликт имён в папке** — **разрешён** (дубликаты допустимы, `id` уникален) — решение зафиксировано.
- **Ручной перенос ссылки на вложение в другой узел** ([ADR-068](../../adr/ADR-068-documents-image-attachments.md) §5) — **допустимое недокументированное состояние**, не дефект. Скопированная строка `![alt](/api/documents/attachments/{id})` не меняет `document_attachments.document_node_id` (сервер markdown не парсит) ⇒ (а) видимость картинки продолжает следовать **узлу-владельцу**: у части читателей нового узла она молча не отрисуется (`404` → плашка «Изображение недоступно»); (б) `DELETE` вложения или удаление узла-владельца **рвёт ссылку везде**. Авто-починка отвергнута (парсинг markdown на каждом `PATCH` + неразрешимый конфликт при ссылке из двух узлов). Штатный путь — загрузить картинку в нужный узел либо копировать узел целиком («Создать копию» клонирует вложения и переписывает ссылки).
- **Лимит размера markdown** — превышение `DOCUMENTS_MAX_MD_BYTES` → `422` (upload → `document_upload_invalid`; inline → `validation_error`, поле `content_md`).
- **Одновременное редактирование** — `content_version` инкрементируется при каждой правке контента/имени; optimistic-lock через `expected_version` — **опционален** (рекомендуемое усиление, [TD-064](../../100-known-tech-debt.md)); при передаче `expected_version` ≠ текущему → `409 document_node_conflict`.
- **`documents:view` без доступа к узлу** — `404 document_node_not_found` (анти-энумерация), НЕ `403`.
- **Удаление УЧЁТКИ автора документа/вложения** — `document_nodes.owner_id` и `document_attachments.created_by` — FK `ON DELETE RESTRICT` ⇒ `DELETE /api/users/{id}` обязан отдавать **`409 user_in_use`** ([04-api.md](../../04-api.md#delete-apiusersid)), **не `500`** и не каскад. Состав узлов в ответе не раскрывается (анти-энумерация не ослабляется); **soft-deleted узлы блокируют так же, как живые** (tombstone — физическая строка). Реализовано и покрыто тестами (2026-07-23); остаток долга — отсутствие операции передачи владения: [TD-077](../../100-known-tech-debt.md).
- **Внешний API + удалённые** — tombstone `{id, deleted_at, content_version}` без `content_md` (в списке/`/changes` — как элемент при `include_deleted`; в `GET /{id}` — `410`).

## Frontend — ТЗ

- Маршрут **`/documents`** — **двухпанельный сайдбар-shell** ([ADR-061](../../adr/ADR-061-documents-sidebar-two-panel-nav.md), [08-design-system.md §Страница «Документы»](../../08-design-system.md#страница-документы-нормативно-adr-061)): левый сайдбар — `TreeView` дерева; правая панель — просмотр/`DocumentEditor`. Это **частичный разворот** плоской навигации ([ADR-033](../../adr/ADR-033-flat-nav-theme-toggle-numbers-table.md)): `/documents` идёт по **full-bleed** ветке (второй маршрут после `/mail`).
- **kebab-меню** (3 точки) на строке узла — обёртка над `@radix-ui/react-dropdown-menu` (**уже** в `package.json`, новой зависимости нет): «Удалить» (`delete`) / «Создать копию» (`create`) / «Переименовать» (`edit`) / «Сменить видимость» (`share`). Пункт рендерится ⇔ есть соответствующее право (UX-гейт; безопасность — сервер).
- **`DocumentEditor`** — WYSIWYG на **TipTap** ([ADR-062](../../adr/ADR-062-documents-wysiwyg-tiptap.md), [02-tech-stack.md](../../02-tech-stack.md#frontend)); сериализует ProseMirror ↔ markdown (`tiptap-markdown`), хранение остаётся markdown в `content_md`. Тулбар включает пункт **«ссылка»** ([поправка ADR-062 §2](../../adr/ADR-062-documents-wysiwyg-tiptap.md#поправка-2026-07-18--граница-расширена-tiptapextension-link)): реализуется через `@tiptap/extension-link`, markdown-ссылки сохраняются при round-trip (URL не теряется).
- **Изображения в редакторе** ([ADR-068](../../adr/ADR-068-documents-image-attachments.md), нормы — [08-design-system.md](../../08-design-system.md#изображения-в-documenteditor-нормативно-adr-068)): расширение **`@tiptap/extension-image`** (официальное расширение линии TipTap 2.x — новая зависимость, [02-tech-stack.md](../../02-tech-stack.md#frontend)) с **`inline: false`** и **`allowBase64: false`**.
  - Два жеста: **кнопка тулбара** (скрытый `<input type="file" accept="image/png,image/jpeg,image/webp,image/gif">`) и **`Ctrl+V`** (`editorProps.handlePaste` — перехват при наличии `image/*` в `clipboardData`). **`handleDrop` НЕ переопределяется** — drag-and-drop не требуется (решение владельца).
  - Оба жеста ведут в **один** путь: `POST /nodes/{id}/attachments` → вставка ноды с `src` из **`url` ответа сервера** (клиент URL не конструирует) и `alt` = `filename`. Пока идёт загрузка — плейсхолдер; ошибка → toast.
  - **`allowBase64: false` — не косметика:** иначе `Ctrl+V` вставлял бы `data:`-URI прямо в `content_md`, съедая `DOCUMENTS_MAX_MD_BYTES` base64-мусором в обход хранилища и лимитов вложений.
  - **Отрисовка — только через авторизованный `fetch` + `blob:`** (NodeView-компонент): `<img src="/api/…">` уходит **без** `Authorization` (JWT в `localStorage`) и получит `401`. Обязателен `URL.revokeObjectURL` на размонтировании; ошибка/`404` → плашка «Изображение недоступно» + alt (документ не ломается).
  - Узел к моменту загрузки **всегда существует** (созданный документ открывается сразу — [ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md)) ⇒ ситуации «вложение без узла-владельца» не возникает.
- **Кэш узла после мутации** ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md) §A, нормы — [08-design-system.md](../../08-design-system.md#кэш-узла-после-мутации-нормативно-adr-063-a), [04-api.md](../../04-api.md#patch-apidocumentsnodesid)): ответ `PATCH /nodes/{id}` несёт **`content_md: null`** (контент отдаёт только `GET /nodes/{id}`) ⇒ класть ответ в кэш узла **целиком запрещено** — он **сливается** поверх предыдущего значения (`content_md` — из тела запроса при `!== undefined`, иначе из прежнего кэша). Правило покрывает и сохранение контента, и **переименование** (общий мутационный хук). Инвалидация узла — только в ветке `409`.
- **Жизненный цикл редактора** ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md) §B): ключ ремоунта — **только `id`** узла (⚠️ прежняя норма «keyed по `id:content_version`» **отменена**); контент ресинкается при расхождении `content_version` с базовой версией текущего маунта (внешняя правка / рефетч после `409`), собственное сохранение ресинк **не** вызывает ⇒ курсор и скролл не сбрасываются. Optimistic-lock ([TD-064](../../100-known-tech-debt.md)) не меняется.
- **Фокус и колонка** ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md) §C): клик по **любому** месту области документа при наличии `documents:edit` ставит курсор (координаты зажимаются в прямоугольник области ⇒ клик правее колонки → конец **той же** строки); в режиме просмотра фокус не ставится; колонка контента центрирована, выравнивание текста — по левому краю и **как функция редактора не вводится** (невыразимо в markdown, формат хранения фиксирован RAG-контрактом).
- **Созданный узел открывается сразу** ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md), [08-design-system.md §Страница «Документы»](../../08-design-system.md#страница-документы-нормативно-adr-061)): создание документа/папки и загрузка `.md` немедленно показывают узел в дереве (раскрытие предков — **от `parent_id` вверх**, без ожидания рефетча) и открывают его в правой панели через самоочищающийся локальный фолбэк — без мигания заглушкой.
- **Модалка видимости** — существующие Radix-Dialog-модалка + `MultiSelect` ([08-design-system.md](../../08-design-system.md#компонент-multiselect)); опции ролей — из `GET /api/documents/role-refs`. **Предзаполнение текущего выбора** — из `GET /api/documents/nodes/{id}/visibility` (`{visibility_mode, role_ids[]}`, собственные роли узла; `inherit` → `[]`), симметричного write-контракту. `visibility_mode='restricted'` + выбранные роли; переключение на `inherit` очищает набор.
- Гейтинг пунктов/кнопок — по правам из `GET /api/auth/me`; невидимые по роли узлы не приходят с сервера (фильтр backend).

## DoD

- [x] Таблицы `document_nodes`/`document_node_roles` + миграции `0029`/`0030`, CHECK/FK/индексы по [03-data-model.md](../../03-data-model.md#таблицы-модуля-документы-document_nodes-document_node_roles).
- [x] Каталог прав += `documents:view/create/edit/delete/share`; `require(...)`-гейты на всех эндпоинтах.
- [x] Резолюция видимости (рекурсивный CTE) + admin-уровень + анти-энумерация `404`.
- [x] Внутренний API `/api/documents/*` (tree/nodes/CRUD/upload/copy/visibility/order/role-refs).
- [x] Внешний read-only `/api/external/documents/*` (`X-API-Key`, keyset, tombstones, `/access`, `/changes`).
- [x] Soft-delete каскад поддерева; copy рекурсией; цикл → `422`.
- [x] Frontend `/documents`: двухпанельный shell, `TreeView`, kebab-меню, `DocumentEditor` (TipTap), модалка видимости.
- [x] Тесты (qa): резолюция видимости, анти-энумерация, copy/soft-delete, keyset внешнего sync, порядок проверки `X-API-Key`.

### DoD изображений ([ADR-068](../../adr/ADR-068-documents-image-attachments.md), реализовано 2026-07-22 — кроме тестов)

- [x] Таблица `document_attachments` + миграция `0032` (4 CHECK, 2 индекса, рабочий `downgrade`) — [03-data-model.md](../../03-data-model.md#таблица-document_attachments-adr-068).
- [x] Три эндпоинта с гейтами `edit`/`view`/`edit` **и per-node фильтром видимости** узла-владельца на отдаче; **единый** `404 document_attachment_not_found` на «нет / невидим / soft-deleted».
- [x] Валидация: размер **по потоку** (не после полной загрузки в память), тип — **по magic bytes** (а не по `Content-Type` клиента), whitelist ровно `png/jpeg/webp/gif`, **SVG отвергается**; отказ → `422 document_attachment_invalid`.
- [x] Путь строится **только** из `id` + расширения из `mime`; `filename` в пути не участвует; `realpath`-containment; запись атомарная; каталоги `0700`, файлы `0600`.
- [x] Порядок: стриминг в temp **в корне** `DOCUMENTS_ATTACHMENTS_DIR` (та же ФС ⇒ атомарный `os.replace`) с подсчётом sha256/размера → `INSERT` с готовыми `checksum`/`size_bytes` → `flush` → `mkdir -p` шардов → `os.replace` → `commit`; сбой → `rollback` + `unlink` temp. Строки без файла не бывает; удаление файла при `DELETE` — **после** `commit`.
- [x] Copy: вложения копируются физически + **литеральная** перезапись ссылок в `content_md` копии; soft-delete: файлы не трогаются, отдача → `404`.
- [x] Кэш: `ETag`=`checksum`, `304` на `If-None-Match`, `Cache-Control: private…` (**`public` отсутствует**).
- [x] Frontend: `@tiptap/extension-image` (`inline:false`, `allowBase64:false`), кнопка тулбара + `Ctrl+V` (без `handleDrop`), отрисовка через авторизованный `fetch` + `blob:` с `revokeObjectURL`.
- [x] CSP `img-src` содержит `blob:` — **побайтово одинаково** в [05-security.md](../../05-security.md#csp) и в **обоих** SPA-`location` nginx.
- [x] **`DOCUMENTS_ATTACHMENTS_DIR` создан в backend-образе** (`ENV` + `mkdir -p` + `chown -R app:app` + `chmod 0700` — по образцу `FILE_SD_DIR`): образ работает под `USER app`, пустой named-volume Docker создаёт `root:root` ⇒ без этого **первая же загрузка падает** на создании шард-каталога. Проверяется прогоном загрузки в контейнере под non-root, а не только юнит-тестом ([07-deployment.md](../../07-deployment.md#каталоги-создаваемые-в-образе-нормативно)).
- [x] Volume `documents-attachments` смонтирован **только** в `backend`; бэкап согласован с `pgdata` ([07-deployment.md](../../07-deployment.md#бэкапы-и-данные)).
- [x] Тесты (qa): отдача картинки узла, невидимого по роли → `404` (не `403`, не `200`); подделанный `Content-Type` (SVG/HTML под видом png) → `422`; превышение лимита обрывается по потоку; copy переносит вложения и **переписывает ссылки**; `304` по `ETag`; отсутствие `public` в `Cache-Control`.

## Changelog

- 2026-07-23 (`qa`): **тесты вложений зелёные.** `backend/tests/integration/test_document_attachments_api.py` — **42** тестовые функции: отдача картинки узла, невидимого по роли → `404` (не `403`/`200`); подделанный `Content-Type` (SVG/HTML под видом png) → `422`; обрыв по лимиту **по потоку**; copy переносит вложения и **переписывает ссылки**; `304` по `ETag`; отсутствие `public` в `Cache-Control`. Плюс **5 регресс-гейтов класса `MissingGreenlet`** (copy папки с вложениями во вложенных документах, поддерево глубины 3, ветка «вложение есть, ссылки в `content_md` нет»). Frontend — `DocumentEditorImages.test.tsx` (**16** кейсов). Прогон: backend 1590 passed / 0 failed (реальный Postgres), frontend 941/941. **Не прогонялись** (остаются открытыми): runtime-smoke стека, контейнерная проверка состава образа, E2E Playwright ([TD-015](../../100-known-tech-debt.md)).
- 2026-07-22 ([ADR-068](../../adr/ADR-068-documents-image-attachments.md)): **изображения РЕАЛИЗОВАНЫ** (backend + frontend + devops, прошли backend-reviewer/frontend-reviewer). Backend: миграция **`0032_document_attachments`**, `models/document_attachment.py`, `repositories/document_attachment_repository.py`, `services/document_attachment_service.py` (magic-bytes, лимит по потоку, `realpath`-containment, атомарный `os.replace`, `ETag`/`304`, `Cache-Control: private…`), три эндпоинта в `api/documents.py`, копирование вложений + литеральная перезапись ссылок в `services/document_service.py`; конфиг `documents_attachments_dir`, `documents_max_image_bytes`=5 МБ. Frontend: `features/documents/imageExtension.ts`/`imageUploadPlaceholder.ts` (прямые импорты `@tiptap/pm/*` ⇒ пакет вынесен в манифест), `components/DocumentImageNodeView.tsx` (blob-резолвер + `revokeObjectURL`), вставка кнопкой тулбара и `Ctrl+V` (`handlePaste`; `handleDrop` не переопределён). DevOps: каталог в `backend/Dockerfile`, volume `documents-attachments` только в `backend`, `img-src … blob:` в обоих SPA-`location` nginx. ✅ **Тесты по ADR-068 написаны и зелёные** (`qa`, 2026-07-23 — см. запись выше); открытым остаётся только контейнерный runtime-smoke. **Эскалация исполнителей зафиксирована как норма:** `DELETE /api/users/{id}` для автора документа/вложения обязан отдавать **`409 user_in_use`** ([04-api.md](../../04-api.md#delete-apiusersid)) — на момент записи отдавал `500`; **починено и покрыто тестами 2026-07-23**, остаток долга — [TD-077](../../100-known-tech-debt.md).
- 2026-07-22 ([ADR-068](../../adr/ADR-068-documents-image-attachments.md), спека — на момент записи `spec-ready`, реализована в тот же день): **изображения в документах**. Хранение (решение владельца): байты — **файлом на volume** `documents-attachments`, метаданные — таблица `document_attachments` (миграция `0032`), в markdown — `![alt](/api/documents/attachments/{id})`. Три эндпоинта: загрузка (**`documents:edit`** — вложение есть часть контента существующего узла), отдача (`documents:view` + **тот же CTE видимости узла**), удаление (`documents:edit`); все негативные исходы отдачи → **единый** `404 document_attachment_not_found` (анти-энумерация). Валидация: whitelist `png/jpeg/webp/gif` **по magic bytes** (клиентский `Content-Type` подделывается), лимит `DOCUMENTS_MAX_IMAGE_BYTES`=5 МБ по потоку, **SVG исключён нормативно** (активный документ с нашего origin = XSS). Path traversal невозможен **конструктивно** — путь строится только из `id`+`mime`, `filename` в нём не участвует. **Отдача — авторизованный `fetch` + `blob:`**, т.к. `<img src>` уходит без `Authorization`, а анонимный доступ означал бы утечку в обход видимости ⇒ **CSP `img-src` += `blob:`**; `allowBase64: false` запрещает `data:`-картинки в `content_md`. Copy копирует вложения физически и **переписывает ссылки** в копии; soft-delete файлы не трогает (отдача → `404`). **Внешний RAG-контур НЕ расширен** (вложения иммутабельны ⇒ `content_version` уже описывает состав узла — [TD-074](../../100-known-tech-debt.md)). Новая зависимость `@tiptap/extension-image`; **бэкап приложения стал двухкомпонентным** (`pgdata` + volume). [TD-065](../../100-known-tech-debt.md) сужен (изображения закрыты, произвольные файлы — нет), заведены [TD-074](../../100-known-tech-debt.md)/[TD-075](../../100-known-tech-debt.md)/[TD-076](../../100-known-tech-debt.md).
- 2026-07-20 ([ADR-063](../../adr/ADR-063-documents-editor-cache-lifecycle-focus.md), спека фикс-пакета): зафиксированы клиентские нормы — **слияние** ответа `PATCH /nodes/{id}` в кэш узла вместо перезаписи (ответ несёт `content_md: null`; правило закрывает потерю текста и при сохранении, и при переименовании), **ключ ремоунта редактора — только `id`** (прежняя «keyed по `id:content_version`» отменена) + ресинк по расхождению `content_version` с базовой версией маунта, **фокус по клику в любое место** области документа при `documents:edit` + центрирование колонки, немедленный показ созданного узла; локализация каталога прав (`documents → «Документы»`) как инвариант. Backend-контракт, модель и внешний RAG-контур **не менялись** — в [04-api.md](../../04-api.md#patch-apidocumentsnodesid) устранена неполнота формулировки ответа `PATCH`. Заведён [TD-068](../../100-known-tech-debt.md) (действия сверх CRUD, включая `documents:share`, не выдаются через UI).
- 2026-07-18 (`spec-ready` → `implemented`): модуль реализован по замороженной спеке — backend (спринты 1–2 + фикс контракта `422`), frontend (двухпанельный shell, `TreeView`, kebab, `DocumentEditor` на TipTap + `@tiptap/extension-link`), тесты qa (backend 1377 зелёных, coverage 86.91%; frontend 862 зелёных; рендер `/documents` подтверждён Playwright). Все пункты DoD закрыты и сверены с кодом; контракт/модель/инварианты — без изменений.
- 2026-07-17 (гринфилд-спека, [ADR-059](../../adr/ADR-059-documents-module.md)/[ADR-060](../../adr/ADR-060-documents-external-readonly-api-key.md)/[ADR-061](../../adr/ADR-061-documents-sidebar-two-panel-nav.md)/[ADR-062](../../adr/ADR-062-documents-wysiwyg-tiptap.md), `spec-ready`): модуль «Документы» спроектирован — модель, видимость по ролям, RBAC, внутренний и внешний API, edge-cases.
