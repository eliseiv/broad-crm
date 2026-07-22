# ADR-062 — WYSIWYG-редактор документов на TipTap (новая frontend-зависимость; обоснование против NFR-1)

- **Статус:** accepted
- **Дата:** 2026-07-17 (поправка §2 — 2026-07-18: граница расширена `@tiptap/extension-link`, см. [§2](#поправка-2026-07-18--граница-расширена-tiptapextension-link))
- **Контекст-модули:** [documents](../modules/documents/README.md)
- **⚠️ Разворачивает курс «минимум зависимостей» (NFR-1) — оформлено явно как осознанное исключение.**
- **Связано:** [ADR-059](ADR-059-documents-module.md); стек — [02-tech-stack.md](../02-tech-stack.md#frontend), примитив — [08-design-system.md](../08-design-system.md#компонент-documenteditor-wysiwyg-нормативно-adr-062)
- **Амендирован:** [ADR-068](ADR-068-documents-image-attachments.md) (2026-07-22) — граница зависимости TipTap расширена `@tiptap/extension-image` **и `@tiptap/pm`**; перечень пакетов в поправке §2 перестал быть закрытым (см. врезку ниже)

> **Амендмент 2026-07-22 ([ADR-068](ADR-068-documents-image-attachments.md)):** вставка изображений в документы расширяет **закрытый перечень** пакетов TipTap из поправки §2. Само решение (WYSIWYG на TipTap, markdown — источник истины, редактор заменяем без миграции данных) — **в силе**.
>
> | Перестало быть нормой (ADR-062 §2) | Действующая норма |
> |---|---|
> | «`@tiptap/react` + `@tiptap/starter-kit` + `@tiptap/extension-link` + `tiptap-markdown` — используются **только** в `DocumentEditor`» как **закрытый** перечень | **Шесть пакетов:** `@tiptap/react` + `@tiptap/starter-kit` + `@tiptap/extension-link` + **`@tiptap/extension-image`** + **`@tiptap/pm`** + `tiptap-markdown` — по-прежнему **только** в `DocumentEditor` и `features/documents`, на другие модули не расширяются. `@tiptap/extension-image` регистрируется с **`allowBase64: false`** — [ADR-068](ADR-068-documents-image-attachments.md) §5 |
>
> **`@tiptap/pm` 2.27.2 добавлен в манифест при реализации (2026-07-22) и новой библиотекой в дереве НЕ является:** это ProseMirror-бандл TipTap, и раньше присутствовавший транзитивно как peer-зависимость `@tiptap/core` (версия в `frontend/package-lock.json` не изменилась). Явный пин потребовался из-за **прямых** импортов ProseMirror-примитивов (`frontend/src/features/documents/imageExtension.ts`, `imageUploadPlaceholder.ts` — `@tiptap/pm/{markdown,model,state,view}`): то, что импортируется напрямую, обязано быть в `dependencies`, иначе версия управляется чужим деревом. Отхода от NFR-1 сверх уже задокументированного §1 здесь нет.
>
> Логика поправки §2 («расширение в пределах уже принятой экосистемы, а не новый разворот NFR-1») применяется к `@tiptap/extension-image` **без изменений**: это официальное расширение той же линии TipTap 2.x. Хранение остаётся каноничным markdown — изображение живёт в `content_md` ссылкой `![alt](/api/documents/attachments/{id})`, байты в документ не попадают.

## Контекст

Требование владельца — **WYSIWYG-редактирование** Markdown-документов (Notion-подобный опыт), не «сырой» markdown-textarea. До сих пор все интерактивные UI-примитивы репо писались **на нативных элементах без новой зависимости** (NFR-1): `Select`/`MultiSelect`/`Textarea`/`Checkbox` — нативные; даже `ui/Combobox` — своя реализация ([ADR-052](ADR-052-mail-mailbox-combobox.md), первый не-нативный примитив, но **без** библиотеки). Полноценный WYSIWYG (rich-text: заголовки, списки, таблицы, инлайн-форматирование, undo/redo, сериализация в markdown) на нативном `contenteditable` с нуля — несоизмеримо дороже и рискованнее (курсор/selection/IME/undo — классические источники багов).

## Решение

### §1. WYSIWYG — на TipTap (ProseMirror), новая runtime-зависимость frontend

Вводится **TipTap**: `@tiptap/react` + `@tiptap/starter-kit` + `@tiptap/extension-link` + `tiptap-markdown` (сериализация ProseMirror ↔ markdown). ⚠️ **Перечень исторический** — действующий состав с 2026-07-22 — **шесть** пакетов (+`@tiptap/extension-image`, +`@tiptap/pm`), см. врезку-амендмент в шапке. Регистрируется в таблице зависимостей [02-tech-stack.md §Frontend](../02-tech-stack.md#frontend). Это **первая rich-text библиотека** в проекте и **осознанный отход** от NFR-1. (`@tiptap/extension-link` добавлен [поправкой §2](#поправка-2026-07-18--граница-расширена-tiptapextension-link) — см. ниже.)

**Обоснование против NFR-1:**
- Notion-подобный WYSIWYG с нуля на `contenteditable` — большой объём и высокий риск (selection/IME/undo/copy-paste), несоизмеримый с одной точечной задачей; TipTap/ProseMirror — зрелый headless-редактор без навязанного UI (тёмная/светлая тема через наши токены).
- **Хранение остаётся каноничным markdown** в `content_md` — редактор лишь сериализует ProseMirror-документ в markdown и обратно; БД/внешний RAG-контракт видят **только markdown**, не ProseMirror-JSON. Редактор **заменяем** без миграции данных (можно вернуться к textarea/другому редактору — формат хранения не меняется).
- Загрузка `.md` и внешний API работают с тем же markdown; WYSIWYG — слой ввода, а не формат.

### §2. Границы зависимости

- TipTap используется **только** в `DocumentEditor` (`features/documents`), не расширяется на другие модули.
- **`@radix-ui/react-dropdown-menu`** (kebab-меню документов, [ADR-061](ADR-061-documents-sidebar-two-panel-nav.md)) — **НЕ** новая зависимость: уже в `package.json` (`2.1.2`), в [02-tech-stack.md](../02-tech-stack.md#frontend) он теперь задокументирован (устранён дрейф docs↔код).
- Примитив `DocumentEditor` нормируется в [08-design-system.md](../08-design-system.md#компонент-documenteditor-wysiwyg-нормативно-adr-062): тёмные/светлые токены, тулбар форматирования, focus-ring, a11y; markdown — источник истины.

### Поправка (2026-07-18) — граница расширена `@tiptap/extension-link`

**Проблема (выявлена frontend'ом при реализации).** `@tiptap/starter-kit` (2.27) **НЕ** содержит Link-расширения. Без него пункт тулбара «ссылка» ([08-design-system.md §DocumentEditor](../08-design-system.md#компонент-documenteditor-wysiwyg-нормативно-adr-062)) нереализуем, а markdown-ссылки `[text](url)` в загруженных документах **схлопываются в обычный текст — URL теряется** при round-trip ProseMirror↔markdown. Это прямо нарушает требование [§Последствия](#последствия) («markdown-сериализация round-trip не теряет разметку канонических конструкций»): гиперссылка — каноничная конструкция markdown, а «Документы» — база знаний под RAG, где потеря URL = потеря данных пользователя.

**Решение (осознанная поправка границы, а не молчаливое расширение).** В авторизованный список пакетов TipTap добавляется **`@tiptap/extension-link`** (версия из линии установленного TipTap **2.27.x**; 2.x-расширения версионируются в lockstep с ядром — в [02-tech-stack.md §Frontend](../02-tech-stack.md#frontend) зафиксировано как `2.x`). Итоговая граница зависимости:

> ⚠️ **Перечень устарел** — амендирован [ADR-068](ADR-068-documents-image-attachments.md) (добавлены `@tiptap/extension-image` **и `@tiptap/pm`** — итого **шесть** пакетов), действующий состав — во врезке в шапке ADR.
>
> `@tiptap/react` + `@tiptap/starter-kit` + **`@tiptap/extension-link`** + `tiptap-markdown` — используются **только** в `DocumentEditor` (`features/documents`), на другие модули не расширяются.

**Почему это остаётся в духе исходного решения, а не новый разворот NFR-1.** `@tiptap/extension-link` — **официальное расширение той же библиотеки TipTap/ProseMirror**, уже авторизованной §1. Граница расширяется **в пределах уже принятой экосистемы**, а не новой сторонней библиотекой; принципиального нового отхода от NFR-1 сверх уже задокументированного в §1 нет. Хранение остаётся каноничным markdown в `content_md` (extension-link лишь даёт ProseMirror-узел ссылки, который `tiptap-markdown` сериализует обратно в `[text](url)`), редактор по-прежнему заменяем без миграции данных.

## Последствия

- [02-tech-stack.md §Frontend](../02-tech-stack.md#frontend): добавлена строка TipTap + block-quote-обоснование против NFR-1.
- Размер бандла растёт (ProseMirror) — приемлемо: редактор грузится только на `/documents` (можно lazy-route). Frontend-reviewer проверяет, что markdown-сериализация round-trip не теряет разметку канонических конструкций.
- **Гиперссылки сохраняются при round-trip** ([поправка §2](#поправка-2026-07-18--граница-расширена-tiptapextension-link) от 2026-07-18): с `@tiptap/extension-link` markdown-ссылка `[text](url)` открывается в WYSIWYG как кликабельная ссылка и при сохранении сериализуется обратно в `[text](url)` — URL больше не теряется. Frontend-reviewer обязан включить ссылку в проверку round-trip канонических конструкций.
- Формат хранения (`content_md`) и внешний RAG-контракт **не зависят** от TipTap ⇒ смена редактора не ломает данные/API.

## Альтернативы

- **Сырой markdown в `Textarea` + предпросмотр** — отклонено требованием владельца (нужен именно WYSIWYG).
- **Свой WYSIWYG на `contenteditable`** — отклонено (объём/риск несоизмеримы, §1).
- **Тяжёлые редакторы-фреймворки (Slate/Lexical/Quill)** — TipTap (ProseMirror) выбран за headless-подход, зрелую markdown-сериализацию и отсутствие навязанного UI; конкретный выбор — рекомендация, заменяем (формат хранения не завязан на редактор).
