# ADR-059 — Гринфилд-модуль «Документы» (единая таблица дерева, permission-based enforcement, вычисляемое наследование видимости по ролям, soft-delete для RAG)

- **Статус:** accepted
- **Дата:** 2026-07-17
- **Контекст-модули:** [documents](../modules/documents/README.md), [auth](../modules/auth/README.md)
- **Связано:** [ADR-060](ADR-060-documents-external-readonly-api-key.md) (внешний ключ), [ADR-061](ADR-061-documents-sidebar-two-panel-nav.md) (сайдбар), [ADR-062](ADR-062-documents-wysiwyg-tiptap.md) (WYSIWYG); модель — [03-data-model.md](../03-data-model.md), RBAC — [05-security.md](../05-security.md), API — [04-api.md](../04-api.md)

## Контекст

Требование владельца — Notion/Kaiten-подобный менеджер документов внутри CRM: левый сайдбар с деревом папок, Markdown-документы (создание/открытие/WYSIWYG-редактирование/загрузка `.md`), контекстное меню на строке узла (Удалить/Создать копию/Переименовать/Сменить видимость), **видимость по РОЛЯМ**, и внешний read-only доступ по API-ключу для будущей RAG-базы ИИ.

**Решения владельца (не пересматриваются):** видимость — по `role_id`, НЕ по командам; редактор — WYSIWYG; внешний API — полный read-only сейчас; загрузка — только `.md`.

**Факты по репо (`claims-from-code`/docs, 2026-07-17):**
- RBAC — одна роль на пользователя (`users.role_id`, FK `roles(id) ON DELETE RESTRICT`), каталог прав в `app/domain/permissions.py::CATALOG`; enforcement `require(page,action)`/`403` ([ADR-021](ADR-021-rbac-users-roles.md), [05-security.md](../05-security.md#каталог-прав-канон-на-сервере)).
- Предикат admin-уровня «видит всё» — `is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)` ([ADR-032](ADR-032-sms-visibility-admin-full-catalog.md)); анти-энумерация «вне scope → пусто/`404`» — устоявшийся паттерн mail/sms.
- Системная строка-якорь супер-админа — `SUPERADMIN_USER_ID` ([ADR-051](ADR-051-superadmin-db-anchor-personal-state.md)).
- Порядок карточек — колонка `position`, сортировка `position ASC, created_at DESC, id` ([03-data-model.md](../03-data-model.md#колонка-position-порядок-карточек)); reorder — полная перестановка уровня с прецеденцией ошибок ([04-api.md](../04-api.md#перестановка-порядок-карточек)).
- Образец composite-PK link-таблицы — `user_channel_teams` ([ADR-055](ADR-055-per-channel-teams-mail-sms.md)).
- **Soft-delete в репо НЕ применяется нигде** — везде hard-delete + [TD-001](../100-known-tech-debt.md).

## Решение

### §1. Модель — единая self-referencing таблица `document_nodes` (папки+документы)

Одна таблица `document_nodes` вместо раздельных `folders`/`documents`: `node_type ∈ {folder, document}`, дерево через `parent_id` (`NULL` = корень, `ON DELETE CASCADE`). Специфичные колонки: `name` (1..255), `content_md` (у папки `NULL` — CHECK), `owner_id` (FK `users` `ON DELETE RESTRICT`), `visibility_mode ∈ {inherit, restricted}`, `content_version bigint DEFAULT 1`, `deleted_at`. Связь узел↔роль — `document_node_roles` (composite PK `(node_id, role_id)`, обе FK `ON DELETE CASCADE`, индекс `(role_id)`) — по образцу `user_channel_teams`; строки только для `restricted`-узлов. Полная модель, DDL, индексы — [03-data-model.md](../03-data-model.md#таблицы-модуля-документы-document_nodes-document_node_roles).

**Отклонённые альтернативы:** (A) две таблицы `folders`+`documents` — дублирование дерева/видимости, join на каждый обход; (B) `ltree`/materialized path — преждевременная сложность (NFR-1), дерево небольшое; (C) closure-table наследования видимости — материализация, инвалидация при move дороже вычисляемого CTE.

### §2. Enforcement — permission-based, НЕ owner-based

Право читать/править/удалять узел определяется `documents:<action>` + видимостью по роли, а **НЕ** совпадением `owner_id == user_id`. `owner_id` — только автор для отображения. Обоснование: согласованность с RBAC-каноном репо (везде enforcement на правах, не на владении); owner-based ввёл бы вторую, конкурирующую модель доступа. Каталог += страница `documents` с действиями `view, create, edit, delete, **share**` (`share` — отдельное чувствительное действие смены видимости, по образцу `mail:sync`/`tags` сверх CRUD).

**Отклонённая альтернатива:** owner-based (автор всегда может править своё) — конфликтует с видимостью по ролям и RBAC-каноном.

### §3. Два независимых уровня доступа

1. `documents:view` — гейт страницы/API (`require("documents","view")`).
2. **Видимость по ролям (per-node)** — фильтр внутри модуля: узел виден ⇔ публичен ИЛИ его эффективный набор ролей содержит `users.role_id`.

**Admin-уровень видит всё:** `sees_all_documents = is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)` (тот же предикат, что «видит все SMS/почты» — [ADR-032](ADR-032-sms-visibility-admin-full-catalog.md)); новое право не вводится.

**Анти-энумерация:** невидимую ноду нельзя и читать, и править/удалять → **`404 document_node_not_found`** (не `403`); списки/дерево — фильтруются. Симметрично «пустому scope» mail/sms.

Список ролей для модалки видимости не-админу с `documents:share` — **`GET /api/documents/role-refs`** (`{id,name}[]`) под гейтом `documents:share`; **НЕ** переиспользуется admin-gated `GET /api/roles` (иначе не-админ получил бы пустой список — дефект класса [TD-050](../100-known-tech-debt.md)).

### §4. Наследование видимости — вычисляемое (рекурсивный CTE), НЕ материализованное

Эффективный набор ролей узла = `document_node_roles` **ближайшего `restricted`-предка** (рекурсивный CTE вверх по `parent_id`, включая сам узел). Нет `restricted`-предка до корня → узел **публичен** внутри модуля. Собственный `restricted` переопределяет наследование ниже. Резолв — на каждый запрос (дерево мало, NFR-1); move узла автоматически меняет эффективную видимость поддерева без пересчёта.

### §5. Soft-delete (`deleted_at`) — осознанный отход от hard-delete ради RAG

Удаление узла — **логическое** (`deleted_at`), а не физическое. Это **отход** от репо-конвенции hard-delete (везде hard-delete + [TD-001](../100-known-tech-debt.md)). Обоснование: внешняя RAG-база **обязана** узнавать об удалениях (tombstone), иначе удалённый документ навсегда останется в эмбеддингах ИИ. Удалённый узел исключён из всех внутренних выборок (`WHERE deleted_at IS NULL`); во внешнем API отдаётся tombstone `{id, deleted_at, content_version}` без `content_md`. Удаление папки — каскадный soft-delete поддерева (tombstone на каждый узел) в одной транзакции. Ретенция/GC tombstones — [TD-067](../100-known-tech-debt.md).

`content_version bigint` инкрементируется **только** при изменении `content_md`/`name`; `updated_at` — при любой мутации (водяной знак sync). Дубликаты имён в папке **разрешены** (как Notion; уникален только `id`).

**Отклонённая альтернатива:** hard-delete + отдельная таблица `document_tombstones` — лишняя таблица и запись при каждом удалении; `deleted_at` в той же строке проще и уже несёт `content_version`.

### §6. Индексы — уточнение к исходной спецификации задачи (реализуемость)

Исходно предлагался частичный индекс `(updated_at) WHERE deleted_at IS NULL`. **Уточнено:** внешний RAG-sync обязан пагинировать по `(updated_at, id)` **включая tombstones** (`include_deleted`), а частичный `WHERE deleted_at IS NULL` их не покрывает; у внутренних выборок «глобально свежие» потребителя нет (дерево обходится по `parent_id`). Поэтому нормативные индексы: `ix_document_nodes_parent_id (parent_id)`, `ix_document_nodes_owner_id (owner_id)`, **`ix_document_nodes_updated_at_id (updated_at, id)`** (не частичный — служит внешнему keyset по всем строкам, включая удалённые). Детали — [03-data-model.md](../03-data-model.md#таблицы-модуля-документы-document_nodes-document_node_roles).

## Последствия

- Новая страница каталога прав `documents` — валидация `roles.permissions` автоматически принимает её действия; `full_catalog_permissions()` включает `documents:*` ⇒ admin-уровень получает `sees_all_documents` без спец-кода.
- **Модуль вводит soft-delete впервые в репо** — reviewer/qa обязаны проверять `WHERE deleted_at IS NULL` во ВСЕХ внутренних выборках (пропуск = утечка удалённого узла в UI). Это не распространяется на другие таблицы (они остаются hard-delete).
- `owner_id` FK `ON DELETE RESTRICT` — удаление пользователя-автора узлов заблокировано, пока у него есть узлы; действия консольного супер-админа пишут `owner_id = SUPERADMIN_USER_ID` (у него нет иного `user_id`, [ADR-051](ADR-051-superadmin-db-anchor-personal-state.md)).
- Внешний доступ и WYSIWYG — отдельными ADR ([ADR-060](ADR-060-documents-external-readonly-api-key.md)/[ADR-062](ADR-062-documents-wysiwyg-tiptap.md)); сайдбар-навигация — [ADR-061](ADR-061-documents-sidebar-two-panel-nav.md).
- Отложенные усиления: optimistic-lock [TD-064](../100-known-tech-debt.md), вложения/изображения [TD-065](../100-known-tech-debt.md), полнотекстовый поиск [TD-066](../100-known-tech-debt.md), ретенция tombstones [TD-067](../100-known-tech-debt.md).
- Контракты остальных модулей **не затронуты** — гринфилд.

## Альтернативы

- **Видимость по командам** (как mail/sms) — **отклонено решением владельца** (строго по ролям); модель `document_node_roles` симметрична `user_channel_teams`, но ключ — `role_id`, а не `team_id`.
- **Материализованное наследование видимости** — отклонено (инвалидация при move дороже CTE, NFR-1).
- **Owner-based доступ** — отклонено (§2).
