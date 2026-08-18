# ADR-077 — Визуальный редизайн страницы «Рассылка»

- **Статус:** implemented (композиция композера + раскладка в рабочей области; сверка `frontend/src/pages/BroadcastPage.tsx:24-28` 2026-08-17 — `min-h-[calc(100dvh-3.875rem-4rem)]` + `flex items-center justify-center`; qa 2026-08-17: focused `BroadcastPage.test.tsx` 28 passed + related зелёные; полный suite не прогонялся)
- **Дата:** 2026-08-17
- **Контекст-модули:** [broadcast](../modules/broadcast/README.md)
- **Связано:** [ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md), [ADR-029](ADR-029-ui-login-password-nav-team-form.md), [08-design-system.md](../08-design-system.md#страница-рассылка)

## Context

Функционал `/broadcast` подтверждён владельцем: текст, аудитория, отправка, empty/error/toast работают. Страница при этом — плоская колонка `Textarea` + список чекбоксов + абзац сводки + кнопка (`frontend/src/pages/BroadcastPage.tsx`). На фоне карточных страниц CRM (`/users`, `/teams`, `/roles`) композер выглядит «голым»: нет внешней поверхности, нет иерархии «сообщение / аудитория / действие», счётчики ролей зашиты в одну строку лейбла.

Владелец просит сделать страницу красивее. API, RBAC, тосты и смысл полей менять нельзя.

## Decision

Реализован (сверка состава `frontend/src/pages/BroadcastPage.tsx` 2026-08-17) **минимальный визуальный редизайн композера** на существующих примитивах (`ui/Card`, `ui/Textarea`, `ui/Checkbox`, `ui/Badge`, `ui/Button`, `ui/Spinner`). Новых зависимостей, новых примитивов, preview/истории/Markdown **нет**. Норматив раскладки — [08-design-system.md §Страница «Рассылка»](../08-design-system.md#страница-рассылка).

### Что не меняется (инварианты ADR-076)

- Маршрут `/broadcast`, не-full-bleed ветка `AppLayout` (`w-full px-6 py-8`, `frontend/src/components/AppLayout.tsx:186`), гейт `broadcast:view`, кнопка «Отправить» только при `broadcast:send`. **`/broadcast` в `isFullBleed` не добавлять.**
- **Без H1** ([ADR-029](ADR-029-ui-login-password-nav-team-form.md)).
- Тело `POST /api/broadcasts`: `{ text, all, role_ids }` — без изменений. `GET /api/broadcasts/audience` — без изменений.
- «Всем» → роли `disabled`, в теле `all=true`, `role_ids=[]`.
- Сводка считается как в ADR-076 (при «Всем» — `all_*`, иначе сумма выбранных; UX-двойной счётчик при пересечении ролей допустим).
- Строки toast / empty / error / 422 / view-guard — дословно те же.
- Accessible name чекбокса роли **остаётся** `{name} (получат: {started_count}, без бота: {not_started_count})` (через `aria-label` на `input`). Accessible name «Всем» — **«Всем»**.

### Что меняется (только вид)

1. **Одна внешняя карточка-композер** — `ui/Card` (`rounded-card border-border-subtle bg-surface-1 shadow-card`), ширина `w-full max-w-3xl`, внутренний стек `flex flex-col gap-6 p-5 sm:p-6`. Живёт внутри центрирующей обёртки — §«Раскладка в рабочей области» (implemented).
2. **Визуальная иерархия внутри карточки:** блок сообщения → fieldset «Аудитория» (видимый `legend`, не page-H1) → footer `[сводка, CTA]`.
3. **Textarea** — тот же примитив, `label="Сообщение"`, `rows={8}`, `maxLength={4096}`. Счётчик через проп `hint`: **`{n} / 4096`**, где `n = text.length` (не `trim`). Связь `aria-describedby` — штатная у примитива.
4. **Строка «Всем»** — под-карточка `rounded-sub border border-border-subtle bg-surface-2` (`flex-wrap`): чип иконки `Megaphone` (`h-10 w-10 rounded-chip bg-surface-3`, как `/users`/`/teams`) + чекбокс «Всем» + бейджи `all_*` **вне** `Checkbox.label`. Отмечена → дополнительно `border-accent`.
5. **Строки ролей** — те же под-карточки `rounded-sub border border-border-subtle bg-surface-2` (`flex-wrap`): видимый `Checkbox.label` = **только имя**; два `Badge` (**«Получат: N»** `tone="green"`, **«Без бота: M»** `tone="red"`) — **соседи**, вне `label` (как у «Всем»). На `input` — `aria-label` с формулой ADR-076. Выбрана → дополнительно `border-accent`. При «Всем» — только штатный `disabled` чекбокса; **`opacity-60` на wrapper запрещён**. Пустой список → **«Ролей для выбора нет.»**
6. **Сводка** — полоса `rounded-sub border border-border-subtle bg-surface-2`, две видимые ячейки (`aria-hidden="true"`). Единственный text content live-region (`aria-live="polite"`) — **sr-only** **«Получат: N · Без бота: M»**. **`aria-label` на сводке запрещён.**
7. **Footer** — DOM-порядок `[сводка, CTA]`, классы `flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between`. **`flex-col-reverse` запрещён.** CTA — `Button` primary + `Send`, `w-full sm:w-auto`. `disabled` / `loading` — как сейчас.
8. **Loading / 403 / error audience / empty 503** — композиция карточек **не меняется** (те же карточки, что у `/teams`/`/users`). Success — только существующий toast, инлайн-баннера успеха нет.

### Раскладка в рабочей области (амендмент, implemented)

> **Амендмент (ADR-077, 2026-08-17):** утверждение «только композер, layout страницы не-full-bleed не меняется» в части **внутренней** раскладки страницы более не полно. Ветка `AppLayout` не-full-bleed **в силе**; меняется только обёртка внутри `BroadcastPage`.

Владелец: страница выглядит пустой — блок прижат влево/вверх. Норма (implemented, сверка `frontend/src/pages/BroadcastPage.tsx:24-28`): **основной блок** формы/карточки «Рассылка» **горизонтально и вертикально центрируется в рабочей области** (под sticky-хэдером, **не** в навбаре), на **пустом фоне** страницы (`bg-bg-base` shell, `frontend/src/components/AppLayout.tsx:110-114`). Дополнительную подложку / вторую `Card` / панель на всю область **не** вводить.

Правила (только `frontend/src/pages/BroadcastPage.tsx`, **не** `AppLayout`):

1. Обёртка `BroadcastWorkspace` вокруг композера **и** page-level состояний loading / error audience / empty 503: `flex w-full items-center justify-center` + **`min-h`** (не `h-*` / не `max-h-*`). `overflow-y-auto` на обёртке **запрещён** — скролл при переполнении остаётся нативным `body` (норма не-full-bleed, [08-design-system.md §Full-bleed](../08-design-system.md#full-bleed-layout-нормативно)).
2. Литерал в коде (`BroadcastPage.tsx:25`): `min-h-[calc(100dvh-3.875rem-4rem)]`. `3.875rem` — высота хэдера из разметки `AppLayout.tsx:116-176` (`py-3`×2 + ряд `max(h-8, nav py-2 + text-[14px])` + `border-b`); `4rem` — `py-8`×2 обёртки `AppLayout` (Tailwind 3.4 spacing, [02-tech-stack.md](../02-tech-stack.md)). Критерий: **нет фантомного скролла body**, когда карточка влезает во вьюпорт.
3. Карточка выше остатка → контейнер растёт (`min-h`, не `height`): верх блока доступен, равные поля «над/под» карточкой не прячут её за хэдером. `truncate` / клип композера **запрещены**.
4. Форма по-прежнему `w-full max-w-3xl`. Одного `mx-auto` без вертикального `min-h` + `items-center` **недостаточно**.
5. `InsufficientPermissions` (page-level view-guard и 403 audience) **не** оборачивать в эту обёртку — ранний return как сейчас.
6. API, RBAC, toast, тексты, композиция внутри `ui/Card` — без изменений.

### Responsive / тема / a11y

- `< md`: строка роли — имя, затем бейджи переносом (`flex-wrap`); `min-w-0` + `break-words`; значимые значения не клипать.
- Токены темы (`surface-*`, `border-*`, `text-*`, `accent`, `status-*`) — без hardcoded hex. Светлая и тёмная темы — теми же классами.
- Фокус — штатные `focus-visible` примитивов. Новых анимаций нет (кроме уже существующих transition карточки/кнопки); `prefers-reduced-motion` не нарушать.
- Строка роли **не** делается `role="button"`: интерактив — только `Checkbox` (без вложенных interactive).

## Consequences

- (+) Страница читается как остальные карточные экраны CRM, без нового визуального языка.
- (+) Счётчики отделены от имени — иерархия и сканирование аудитории лучше, чем одна длинная строка.
- (+) Существующие тесты по accessible name чекбоксов и по контракту submit/toast/503 остаются валидными.
- (−) Видимый `label` роли = только имя; accessible name = полная формула через `aria-label`. Бейджи вне `label` (как у «Всем»).
- (−) `max-w-3xl` чуть шире прежнего `max-w-2xl` в коде — только композер.
- (−) Амендмент раскладки (implemented): page-local `min-h-[calc(100dvh-3.875rem-4rem)]` в `BroadcastPage` заполняет рабочую область под хэдером. Ветка `AppLayout` не-full-bleed **не** меняется (`/broadcast` не full-bleed; `<main>` без `flex-1` / `overflow-y-auto`).

## Alternatives

- **Две внешние Card (сообщение / аудитория)** — отвергнуто: на однодействиях странице даёт пустоту и лишний воздух; ServerCard-язык = одна внешняя + внутренние surface-2.
- **Новый примитив / зависимость (editor, markdown)** — отвергнуто: NFR простоты, ADR-076 «без parse_mode».
- **Вернуть page-H1 «Рассылка»** — отвергнуто: [ADR-029](ADR-029-ui-login-password-nav-team-form.md).
- **Менять API или добавлять preview/историю** — вне scope (владелец подтвердил, что функционал работает).
- **Перевести `/broadcast` в full-bleed / дать `<main>` `flex-1`** — отвергнуто: ломает инвариант не-full-bleed ([08-design-system.md §Full-bleed](../08-design-system.md#full-bleed-layout-нормативно)); центрирование — page-local в `BroadcastPage`.
