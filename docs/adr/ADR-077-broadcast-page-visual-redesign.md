# ADR-077 — Визуальный редизайн страницы «Рассылка»

- **Статус:** implemented (сверка состава `frontend/src/pages/BroadcastPage.tsx` 2026-08-17; qa 2026-08-17: focused `BroadcastPage.test.tsx` 21 passed, coverage `BroadcastPage.tsx` 96.88%, related 73 passed; полный suite не прогонялся)
- **Дата:** 2026-08-17
- **Контекст-модули:** [broadcast](../modules/broadcast/README.md)
- **Связано:** [ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md), [ADR-029](ADR-029-ui-login-password-nav-team-form.md), [08-design-system.md](../08-design-system.md#страница-рассылка)

## Context

Функционал `/broadcast` подтверждён владельцем: текст, аудитория, отправка, empty/error/toast работают. Страница при этом — плоская колонка `Textarea` + список чекбоксов + абзац сводки + кнопка (`frontend/src/pages/BroadcastPage.tsx`). На фоне карточных страниц CRM (`/users`, `/teams`, `/roles`) композер выглядит «голым»: нет внешней поверхности, нет иерархии «сообщение / аудитория / действие», счётчики ролей зашиты в одну строку лейбла.

Владелец просит сделать страницу красивее. API, RBAC, тосты и смысл полей менять нельзя.

## Decision

Реализован (сверка состава `frontend/src/pages/BroadcastPage.tsx` 2026-08-17) **минимальный визуальный редизайн композера** на существующих примитивах (`ui/Card`, `ui/Textarea`, `ui/Checkbox`, `ui/Badge`, `ui/Button`, `ui/Spinner`). Новых зависимостей, новых примитивов, preview/истории/Markdown **нет**. Норматив раскладки — [08-design-system.md §Страница «Рассылка»](../08-design-system.md#страница-рассылка).

### Что не меняется (инварианты ADR-076)

- Маршрут `/broadcast`, не-full-bleed `w-full px-6 py-8`, гейт `broadcast:view`, кнопка «Отправить» только при `broadcast:send`.
- **Без H1** ([ADR-029](ADR-029-ui-login-password-nav-team-form.md)).
- Тело `POST /api/broadcasts`: `{ text, all, role_ids }` — без изменений. `GET /api/broadcasts/audience` — без изменений.
- «Всем» → роли `disabled`, в теле `all=true`, `role_ids=[]`.
- Сводка считается как в ADR-076 (при «Всем» — `all_*`, иначе сумма выбранных; UX-двойной счётчик при пересечении ролей допустим).
- Строки toast / empty / error / 422 / view-guard — дословно те же.
- Accessible name чекбокса роли **остаётся** `{name} (получат: {started_count}, без бота: {not_started_count})` (через `aria-label` на `input`). Accessible name «Всем» — **«Всем»**.

### Что меняется (только вид)

1. **Одна внешняя карточка-композер** — `ui/Card` (`rounded-card border-border-subtle bg-surface-1 shadow-card`), ширина `w-full max-w-3xl`, внутренний стек `flex flex-col gap-6 p-5 sm:p-6`.
2. **Визуальная иерархия внутри карточки:** блок сообщения → fieldset «Аудитория» (видимый `legend`, не page-H1) → footer `[сводка, CTA]`.
3. **Textarea** — тот же примитив, `label="Сообщение"`, `rows={8}`, `maxLength={4096}`. Счётчик через проп `hint`: **`{n} / 4096`**, где `n = text.length` (не `trim`). Связь `aria-describedby` — штатная у примитива.
4. **Строка «Всем»** — под-карточка `rounded-sub border border-border-subtle bg-surface-2` (`flex-wrap`): чип иконки `Megaphone` (`h-10 w-10 rounded-chip bg-surface-3`, как `/users`/`/teams`) + чекбокс «Всем» + бейджи `all_*` **вне** `Checkbox.label`. Отмечена → дополнительно `border-accent`.
5. **Строки ролей** — те же под-карточки `rounded-sub border border-border-subtle bg-surface-2` (`flex-wrap`): видимый `Checkbox.label` = **только имя**; два `Badge` (**«Получат: N»** `tone="green"`, **«Без бота: M»** `tone="red"`) — **соседи**, вне `label` (как у «Всем»). На `input` — `aria-label` с формулой ADR-076. Выбрана → дополнительно `border-accent`. При «Всем» — только штатный `disabled` чекбокса; **`opacity-60` на wrapper запрещён**. Пустой список → **«Ролей для выбора нет.»**
6. **Сводка** — полоса `rounded-sub border border-border-subtle bg-surface-2`, две видимые ячейки (`aria-hidden="true"`). Единственный text content live-region (`aria-live="polite"`) — **sr-only** **«Получат: N · Без бота: M»**. **`aria-label` на сводке запрещён.**
7. **Footer** — DOM-порядок `[сводка, CTA]`, классы `flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between`. **`flex-col-reverse` запрещён.** CTA — `Button` primary + `Send`, `w-full sm:w-auto`. `disabled` / `loading` — как сейчас.
8. **Loading / 403 / error audience / empty 503** — композиция **не меняется** (те же карточки, что у `/teams`/`/users`). Success — только существующий toast, инлайн-баннера успеха нет.

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
- (−) `max-w-3xl` чуть шире прежнего `max-w-2xl` в коде — только композер, layout страницы не-full-bleed не меняется.

## Alternatives

- **Две внешние Card (сообщение / аудитория)** — отвергнуто: на однодействиях странице даёт пустоту и лишний воздух; ServerCard-язык = одна внешняя + внутренние surface-2.
- **Новый примитив / зависимость (editor, markdown)** — отвергнуто: NFR простоты, ADR-076 «без parse_mode».
- **Вернуть page-H1 «Рассылка»** — отвергнуто: [ADR-029](ADR-029-ui-login-password-nav-team-form.md).
- **Менять API или добавлять preview/историю** — вне scope (владелец подтвердил, что функционал работает).
