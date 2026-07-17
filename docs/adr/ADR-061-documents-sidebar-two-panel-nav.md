# ADR-061 — Двухпанельный сайдбар-layout страницы `/documents` (частичный разворот плоской навигации ADR-033)

- **Статус:** accepted
- **Дата:** 2026-07-17
- **Контекст-модули:** [documents](../modules/documents/README.md), [ui](../modules/ui/README.md)
- **⚠️ Разворачивает действующую норму (ADR-reversal, оформлено явно):** [ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md) §1 — не в части «плоский ряд `NavLink` в хэдере» (он сохраняется), а в части **layout-ветки** «все пункты не-full-bleed, кроме `/mail`» ([08-design-system.md](../08-design-system.md#навигация-плоская-applayout) строки layout-ветки): `/documents` становится **вторым** full-bleed маршрутом с собственным двухпанельным layout внутри страницы. `isFullBleed` расширяется на `/documents`.
- **Связано:** [ADR-059](ADR-059-documents-module.md); дизайн-система — [08-design-system.md](../08-design-system.md#страница-документы-нормативно-adr-061)

## Контекст

Модуль «Документы» ([ADR-059](ADR-059-documents-module.md)) — Notion/Kaiten-подобный менеджер: **левый сайдбар с деревом папок** + правая панель контента/редактора. Действующая навигация ([ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md)) — плоский ряд `NavLink` в хэдере, и **все** страницы идут по не-full-bleed ветке `AppLayout` (обычный поток документа, `mx-auto max-w-[1400px]`), **кроме `/mail`** (full-bleed master-detail). Дерево-сайдбар документов не помещается в эту модель: ему нужна вся высота вьюпорта и собственная двухпанельная раскладка — как у `/mail`.

Молча реализовать сайдбар против нормы ADR-033 нельзя (docs↔код расхождение) — отсюда явная поправка.

## Решение

### §1. Хэдер-навигация ADR-033 СОХРАНЯЕТСЯ; добавляется пункт «Документы»

Плоский ряд `NavLink` в хэдере ([ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md) §1) **не отменяется**. Добавляется пункт **«Документы»** (`/documents`, гейт `documents:view`) — как **пункт 10** в конце ряда (после «Команды»), чтобы не перенумеровывать существующие пункты и не менять порядок резолва `DefaultRoute` (он по ADR-033 стабилен). Плоский порядок листьев `DefaultRoute` дополняется `documents` в конце: `mail → sms → servers → ai-keys → proxies → backends → users → roles → teams → documents`. Нормативная таблица навигации — [08-design-system.md](../08-design-system.md#навигация-плоская-applayout).

### §2. `/documents` — full-bleed двухпанельный layout (второй маршрут после `/mail`)

Страница `/documents` идёт по **full-bleed** ветке `AppLayout` (как `/mail`): занимает всю ширину/высоту под хэдером, без внешнего `max-w-[1400px]`/`py-8`. Внутри — **двухпанельный layout**: **левый сайдбар** (`TreeView` дерева папок/документов, ~28–32% ширины) + **правая панель** (просмотр/`DocumentEditor`). Вертикальный скролл — **внутри** панелей (`overflow-y-auto`), не на странице; на узких вьюпортах (`< md`) — одна колонка (сайдбар → выбор узла → full-width контент с кнопкой «Назад»), значимый контент не скрывается/не обрезается (правило CLAUDE.md).

**`isFullBleed` расширяется:** `isFullBleed = pathname.startsWith('/mail') || pathname.startsWith('/documents')`. Layout-ветка (строки [08-design-system.md](../08-design-system.md#навигация-плоская-applayout)) обновлена: full-bleed теперь у `/mail` **и** `/documents`; остальные — не-full-bleed. Смешивать режимы запрещено (та же норма, что для `/mail`: не-full-bleed страницы не оборачиваются в `h-screen`/`overflow-hidden`-shell).

### §3. Новые UI-примитивы (без новых обязательных зависимостей, кроме WYSIWYG)

Нормируются в [08-design-system.md §Страница «Документы»](../08-design-system.md#страница-документы-нормативно-adr-061): **kebab-меню** (3 точки, обёртка над `@radix-ui/react-dropdown-menu` — **уже** в `package.json`, новой зависимости нет), **`TreeView`** (рекурсивное дерево), **`DocumentEditor`** (WYSIWYG — [ADR-062](ADR-062-documents-wysiwyg-tiptap.md), единственная новая зависимость), **сайдбар-shell** `/documents`. Модалка видимости — существующие Radix-Dialog + `MultiSelect`.

## Последствия

- [08-design-system.md](../08-design-system.md) обновлён: таблица навигации += «Документы» (пункт 10), layout-ветки и `isFullBleed` включают `/documents`, добавлена секция «Страница «Документы»».
- Full-bleed теперь у **двух** маршрутов — общий shell `AppLayout` должен ветвиться по `startsWith('/mail') || startsWith('/documents')`; frontend-reviewer обязан проверить отсутствие регрессии layout `/mail` и остальных не-full-bleed страниц.
- Порядок `DefaultRoute` не ломается — `documents` добавлен в конец, существующие приоритеты сохранены.
- kebab-меню переиспользует **уже установленный** `@radix-ui/react-dropdown-menu` (в [02-tech-stack.md](../02-tech-stack.md#frontend) он теперь задокументирован — ранее был в `package.json`, но не в таблице; дрейф docs↔код устранён этим пакетом ADR).

## Альтернативы

- **Сайдбар не-full-bleed внутри `max-w-[1400px]`** — отклонено: дерево+редактор требуют полной высоты; в ограниченном контейнере появился бы контейнерный/фантомный скролл (ровно та регрессия, что чинил `/mail`-full-bleed в ADR-033/ADR-046).
- **Отдельный `AppLayout` для `/documents`** (вне общего shell) — отклонено: дублирование хэдера/темы/гейтинга; расширение `isFullBleed` на общий shell проще и переиспользует модель `/mail`.
- **Категория-дропдаун в хэдере под документы** — отклонено: ADR-033 упразднил дропдауны; возвращать их нельзя.
