# ADR-064 — Цветовые токены в channel-формате (`rgb(var(--x) / <alpha-value>)`): системный фикс невидимой danger-кнопки и всех alpha-модификаторов

- **Статус:** accepted
- **Дата:** 2026-07-21
- **Область:** frontend / дизайн-система (токены, `tailwind.config.ts`, `index.css`, компонент `Button`, а также сырые потребители конвертируемых токенов в `.ts/.tsx` — инлайновые стили, SVG-атрибуты, аргументы `color-mix()`, JS-константы цвета: `main.tsx`, `components/Gauge.tsx`, `components/MailTagChip.tsx`, `components/ui/Pill.tsx`, `lib/zones.ts`)
- **Связи:** уточняет §Цветовые токены и §Темизация [08-design-system.md](../08-design-system.md); механизм темы ([ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md), [ADR-041](ADR-041-login-theme-session-ux.md), [ADR-046](ADR-046-ui-infra-fix-pack.md) §4) **не меняется**. Токен-контракт-тест `Button.tokens.test.ts`, введённый в рамках этого ADR, статически импортирует `tailwind.config.ts` — это потребовало включить конфиг в файловый список app-проекта `tsconfig.app.json` (build-фикс TS6307, [ADR-066](ADR-066-tailwind-config-in-app-tsproject.md)).

## Контекст

Цветовые токены заданы как ссылки на CSS-переменные с **готовым цветом**: в `tailwind.config.ts` — `status.red: 'var(--status-red)'`, в `index.css` — `--status-red: #DC2626` (light) / `#EF4444` (dark). Такой формат несовместим с **opacity-модификаторами Tailwind** (`bg-status-red/90`, `ring-accent/40`, `bg-surface-1/40` …).

**Механизм бага (эмпирически подтверждён на установленном Tailwind 3.4).** Утилита `bg-status-red/90` компилируется Tailwind в `background-color: rgb(var(--status-red) / 0.9)` **только если** переменная содержит channel-триплет (`220 38 38`). Когда переменная содержит готовый цвет (`#DC2626`), подстановка альфы даёт синтаксически невалидное `rgb(#DC2626 / 0.9)` → Tailwind опускает свойство, **фон не выводится вовсе** (прозрачный). У `Button` вариант `danger` (`bg-status-red/90 text-white`) на белой поверхности модалки (`--surface-1: #FFFFFF`) даёт **белый текст на прозрачном фоне — кнопка невидима в светлой теме**. Красный появляется только на `hover` (`hover:bg-status-red` — без модификатора, работает).

**Масштаб — НЕ локальный баг `/documents`.** Аудит кодовой базы (`grep` по `(bg|text|border|ring|from|to|fill|stroke)-<token>/NN`) выявил **~25 мест**, где alpha-модификатор навешан на var-цвет и **молча не работает** прямо сейчас:

- `Button` — `danger: bg-status-red/90`, `primary: disabled:bg-accent/60` (латентно: disabled-фон primary тоже пропадает);
- границы карточек ошибок — `border-status-red/70` (`ServerCard`, `ProxyCard`, `AiKeyCard`, `BackendCard`);
- тонированные фоны/границы ошибок и предупреждений — `bg-status-red/5`, `bg-status-red/10`, `border-status-red/40`, `bg-status-yellow/5`, `border-status-yellow/40` (`ServerCard`, `DocumentVisibilityModal`, `MailboxFormModal`, `AddUserModal`, `AddTeamModal`);
- акцентные подсветки — `bg-accent/5`, `bg-accent/10`, `bg-accent/15` (`AppLayout`, `DocumentEditor`, `TreeView`, `DocumentVisibilityModal`);
- фокус-кольца — `ring-accent/50`, `ring-accent/40`, `ring-status-red/40` (`Checkbox`, `Textarea`, `Combobox`, `Select`, `Input`);
- полупрозрачные фоны — `bg-surface-1/40` (пустые состояния `UsersPage`/`TeamsPage`/`RolesPage`), `bg-bg-base/80` (backdrop хэдера `AppLayout`);
- подсветка пункта удаления — `data-[highlighted]:bg-status-red/10` (`DropdownMenu`).

Все они сейчас деградируют «тихо» (нет ошибки сборки, просто отсутствует фон/кольцо). Корневая причина — **формат токенов**, а не отдельные компоненты.

## Рассматриваемые варианты

- **(A) Локальный фикс `Button`.** Убрать opacity-модификаторы с var-цветов: `danger: 'bg-status-red text-white hover:bg-status-red/…'` → сплошной `bg-status-red`; `disabled:bg-accent/60` → сплошной токен/утилита без альфы. Минимально, риск локальный. **Но не устраняет корень:** остаются ~23 других сломанных места; любой будущий `<token>/NN` на var-цвете сломается так же и снова «тихо».
- **(B) Системный фикс — channel-формат токенов.** Перевести значения переменных на space-separated RGB-триплет (`--status-red: 220 38 38`), а в `tailwind.config.ts` — на `rgb(var(--status-red) / <alpha-value>)`. Тогда alpha-модификаторы заработают для **всех** утилит и токенов разом, а сплошная заливка (`bg-status-red` без альфы) продолжит работать (`<alpha-value>` → `1`). Затрагивает все токены и требует ревизии мест, где переменные читаются как **готовый цвет в сыром CSS**.

## Решение

Принят **вариант (B) — channel-формат токенов.** Критерий «устранить корень при контролируемом риске»: (A) чинит 1 из ~25 мест и оставляет мину на будущее; (B) чинит все места одним изменением формата, а его «цена» (правка сырых потребителей конвертируемых токенов) **полностью перечислима и конечна** (см. §C). Конверсия **глобальна** (единый набор CSS-переменных не может одновременно быть триплетом для Tailwind-утилит и готовым цветом для сырого CSS/инлайна), поэтому цена **не** локализована в `index.css`: она охватывает и сырых потребителей вне `index.css` — инлайновые стили, SVG-атрибуты, аргументы `color-mix()`, JS-константы цвета в `.ts/.tsx` (§C.2). Новой зависимости (B) **не вводит** — это чисто CSS + конфигурация Tailwind, уже присутствующего в стеке; правка `docs/02-tech-stack.md` не требуется.

### §A. Формат токенов (нормативно)

1. **Все цветовые токены, экспонированные как Tailwind-утилиты через `tailwind.config.ts.theme.extend.colors`, хранятся в `index.css` как space-separated RGB-триплет** (`R G B`, каналы 0–255, без `rgb()`, без запятых, без `#`).
2. **В `tailwind.config.ts` каждый такой токен объявляется через `rgb(var(--<token>) / <alpha-value>)`** (не `var(--<token>)`). Плейсхолдер `<alpha-value>` Tailwind подставляет сам: `1` для сплошной заливки, `0.NN` для модификатора `/NN`.
3. **Правило на будущее (инвариант):** новый цветовой токен, добавляемый в `colors` конфига, вводится **только** в channel-формате по пунктам 1–2. Ввод токена как готового цвета (`var(--x)` без обёртки / `#RRGGBB` в переменной, попадающей в `colors`) — **запрещён** (воспроизводит настоящий баг). Ревью токенов сверяет это правило.

### §B. Полная таблица токенов к конверсии (15 токенов)

Конвертируются **только** токены из `tailwind.config.ts.theme.extend.colors`. HEX-значения — те же, что в таблице §Цветовые токены (контрастные обоснования не меняются); ниже — их точный channel-эквивалент, который пишется в `index.css` (light — на голом `:root` и дубль под `[data-theme='light']`; dark — под `[data-theme='dark']`, [ADR-046](ADR-046-ui-infra-fix-pack.md) §4.2).

| Токен | Light HEX → триплет | Dark HEX → триплет |
|-------|---------------------|--------------------|
| `--bg-base` | `#F2F4F7` → `242 244 247` | `#0A0C10` → `10 12 16` |
| `--surface-1` | `#FFFFFF` → `255 255 255` | `#11141A` → `17 20 26` |
| `--surface-2` | `#F7F8FA` → `247 248 250` | `#161A22` → `22 26 34` |
| `--surface-3` | `#EAECF1` → `234 236 241` | `#1E232D` → `30 35 45` |
| `--border-subtle` | `#E3E6EB` → `227 230 235` | `#232834` → `35 40 52` |
| `--border-strong` | `#CDD2DB` → `205 210 219` | `#2E3542` → `46 53 66` |
| `--text-primary` | `#111827` → `17 24 39` | `#E6E9EF` → `230 233 239` |
| `--text-secondary` | `#4B5563` → `75 85 99` | `#9AA4B2` → `154 164 178` |
| `--text-tertiary` | `#61697A` → `97 105 122` | `#5C6573` → `92 101 115` |
| `--accent` | `#4F46E5` → `79 70 229` | `#6366F1` → `99 102 241` |
| `--accent-hover` | `#4338CA` → `67 56 202` | `#818CF8` → `129 140 248` |
| `--status-green` | `#15803D` → `21 128 61` | `#22C55E` → `34 197 94` |
| `--status-yellow` | `#B45309` → `180 83 9` | `#EAB308` → `234 179 8` |
| `--status-red` | `#DC2626` → `220 38 38` | `#EF4444` → `239 68 68` |
| `--gauge-track` | `#E1E4EA` → `225 228 234` | `#262C38` → `38 44 56` |

**НЕ конвертируются** (и почему — они не проходят через `colors` конфига и/или потребляются как сырой цвет):
- `--shadow-card` / `--shadow-sub` — не цвет, а составное значение тени; экспонируются через `boxShadow`, не `colors`; alpha-модификатор к ним неприменим. Остаются как есть ([ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md)).
- Стопы градиентов дуги `--gauge-green-from/-to`, `--gauge-yellow-from/-to`, `--gauge-red-from/-to` — читаются как **готовый цвет** в SVG `linearGradient`/`filter`, в `colors` конфига **не входят**, alpha-модификатором не используются. Остаются HEX. (Если позже понадобится альфа на градиент-стопе — конвертировать точечно тогда же.)

### §C. Ревизия сырых потребителей конвертируемых токенов (цена варианта B, нормативно)

После §A переменная `--<token>` содержит триплет `220 38 38` — **не** валидный цвет сам по себе. Поэтому **каждое место, где КОНВЕРТИРУЕМАЯ переменная (15 токенов §B) используется как готовый цвет**, обязано быть обёрнуто в **`rgb(var(--x))`**.

**Область ревизии — весь `frontend/src`, а не только `index.css`.** Конверсия глобальна: один и тот же набор CSS-переменных обслуживает и Tailwind-утилиты (нужен триплет), и сырых потребителей (нужен готовый цвет). Разделить их без дублирования источника истины нельзя (гибрид «две переменные» отклонён — см. Альтернативы). Следовательно сырые потребители конвертируемых токенов **вне** `index.css` (инлайновые React-стили, презентационные SVG-атрибуты, аргументы `color-mix()`, JS/TS-константы цвета) после §A дают ровно ту же **молчаливую** регрессию — невалидный цвет, свойство тихо отбрасывается — и обязаны обёртываться идентично. Перечни §C.1 и §C.2 вместе исчерпывают потребителей (аудит: `grep -rEn "var\(--(bg-base|surface-1|surface-2|surface-3|border-subtle|border-strong|text-primary|text-secondary|text-tertiary|accent|accent-hover|status-green|status-yellow|status-red|gauge-track)\)" frontend/src`; иных `.css`-файлов, кроме `index.css`, и arbitrary-value утилит `[var(--x)]` в `.tsx` — нет).

**Критерий обёртки — ТОКЕН, а не CSS-свойство и не место потребления.** Обёртка требуется в **любой** цвет-позиции: не только `background-color`/`color`/`border`/`outline`, но и **shorthand `background`**, **цвет-стопы `linear-gradient()`/`radial-gradient()`**, любой цвет-аргумент `color-mix()`, **презентационный SVG-атрибут** (`fill`/`stroke`), **значение инлайнового `style`** и **строковая JS-константа**, присваиваемая CSS-цвету. Признак «это gradient/тень/shorthand/SVG/inline/JS» **не** освобождает от обёртки — освобождает только то, что переменная **не входит** в 15 конвертируемых токенов (§B: тени `--shadow-*`, gauge-стопы `--gauge-*-from`/`-to`, а также Telegram-нативные `--tg-*` в мини-аппах — их сырые потребители НЕ трогаются). Иначе говоря: `linear-gradient(… var(--surface-2) …)` **обёртывается** (surface-2 конвертируем), а `linear-gradient(… var(--gauge-green-from) …)` в SVG-компонентах — **нет** (gauge-стоп не конвертируем).

#### §C.1. Сырые потребители в `index.css` (нормативный перечень, реализация обёртывает каждый):

| Селектор | Свойство → после обёртки |
|----------|--------------------------|
| `*` | `border-color: rgb(var(--border-subtle))` |
| `body` | `background-color: rgb(var(--bg-base))`; `color: rgb(var(--text-primary))` |
| `:focus-visible` | `outline: 2px solid rgb(var(--accent))` |
| `::selection` | `background: color-mix(in srgb, rgb(var(--accent)) 35%, transparent)` |
| `.skeleton-shimmer` | `background: linear-gradient(90deg, rgb(var(--surface-2)) 25%, rgb(var(--surface-3)) 50%, rgb(var(--surface-2)) 75%)` — **все три цвет-стопа** (surface-2 дважды, surface-3) |
| `.doc-prose` | `color: rgb(var(--text-primary))` |
| `.doc-prose a` | `color: rgb(var(--accent))` |
| `.doc-prose code` | `background: rgb(var(--surface-3))` (shorthand) |
| `.doc-prose pre` | `background: rgb(var(--surface-3))` (shorthand); `border: 1px solid rgb(var(--border-subtle))` |
| `.doc-prose blockquote` | `border-left: 3px solid rgb(var(--border-strong))`; `color: rgb(var(--text-secondary))` |
| `.doc-prose hr` | `border-top: 1px solid rgb(var(--border-subtle))` |

`box-shadow` с конвертируемым токеном в качестве цвета в `index.css` **отсутствует** (тени используют `rgba()`-литералы, не токены) — обёртывать нечего.

#### §C.2. Сырые потребители вне `index.css` (`.ts/.tsx`, нормативный перечень, реализация обёртывает каждый):

| Файл (потребитель) | Позиция → после обёртки |
|--------------------|-------------------------|
| `frontend/src/main.tsx` (инлайновый `style` тоста) | `background: rgb(var(--surface-2))`; `border: 1px solid rgb(var(--border-strong))`; `color: rgb(var(--text-primary))` |
| `frontend/src/components/Gauge.tsx` (презентационные SVG-атрибуты) | `stroke="rgb(var(--gauge-track))"`; `fill` — **обе ветки**: `rgb(var(--text-tertiary))` (placeholder) / `rgb(var(--text-primary))` |
| `frontend/src/components/MailTagChip.tsx` (аргументы `color-mix()`) | `color-mix(in srgb, <tag> 50%, rgb(var(--text-primary)))`; `color-mix(in srgb, <tag> 16%, rgb(var(--surface-2)))`; `color-mix(in srgb, <tag> 40%, rgb(var(--surface-2)))` — surface-2 ×2 |
| `frontend/src/components/ui/Pill.tsx` (инлайновый `style`, в т.ч. внутри `color-mix()`) | `rgb(var(--accent))`, `rgb(var(--accent-hover))`, `rgb(var(--status-yellow))`, `rgb(var(--surface-3))`, `rgb(var(--text-secondary))`, `rgb(var(--status-green))` — каждый токен обёрнут, включая аргумент `color-mix(in srgb, rgb(var(--accent)) 16%, transparent)` и аналоги для yellow/green |
| `frontend/src/lib/zones.ts` (JS-константа `ZONE_COLOR`) | `green: 'rgb(var(--status-green))'`; `yellow: 'rgb(var(--status-yellow))'`; `red: 'rgb(var(--status-red))'` |

> **Замечание к `ui/Pill.tsx`.** `Pill` — примитив, используемый в т.ч. для чипов команд на `/users` ([ADR-065](ADR-065-users-flat-list-team-chips.md)). Буквальная (ошибочная) трактовка §C «только `index.css`» сломала бы и его — это одна из причин расширения перечня на `.ts/.tsx`.

**НЕ трогаются** (потребляют НЕ-конвертируемые переменные, §B):
- в `index.css` — определения теней `--shadow-card`/`--shadow-sub` (`rgba()`-литералы);
- вне `index.css` — gauge-стопы `--gauge-*-from`/`-to` (`frontend/src/lib/zones.ts` `ZONE_GRADIENT`; SVG `linearGradient` в `Gauge.tsx`) и Telegram-нативные `--tg-*` с fallback (`frontend/src/pages/MailMiniAppPage.tsx`, `frontend/src/pages/SmsMiniAppPage.tsx`) — не токены ДС, в §B не входят.

Полная «цена» варианта B **охватывает `index.css` (§C.1) и перечисленных инлайновых/SVG/JS-потребителей вне `index.css` (§C.2)**; вне этих двух перечней конвертируемые токены как готовый цвет не потребляются.

> **Контракт-тест.** `frontend/src/components/__tests__/MailTagChip.test.tsx` сейчас ассертит сырой `var(--text-primary)`/`var(--surface-2)`; после обёртки он обязан ассертить `rgb(var(--text-primary))`/`rgb(var(--surface-2))`. Правка теста — зона **qa**, не входит в реализацию §C, но синхронизируется в том же спринте (иначе тест ложно «красный»).

**Проверяемый критерий готовности реализации:** (1) в собранном CSS **не осталось ни одного** `linear-gradient`/`background`/`background-color`/`color`/`border`/`outline`/`color-mix`, где конвертируемый токен стоит как «голый» `var(--x)` без `rgb()` (⇒ невалидный цвет и «тихо» отброшенное свойство); (2) в `.ts/.tsx` тот же `grep` по конвертируемым токенам (см. выше) возвращает только обёрнутые `rgb(var(--x))` вхождения — голых `var(--<конвертируемый>)` не осталось (`--tg-*` и `--gauge-*-from`/`-to` — допустимые голые вхождения); (3) **skeleton-мерцание видимо в обеих темах** (не пропало как shorthand-`background`); (4) все ~25 alpha-мест из §Контекст дают видимый фон/кольцо в обеих темах; (5) сплошные заливки (`bg-accent`, `bg-status-red` без альфы), тост, gauge-трек, чипы `MailTagChip`/`Pill` и свечение зоны визуально не пропали ни в одной теме.

### §D. Ожидаемый визуальный результат `Button` (нормативно)

- **`danger` в resting-состоянии — сплошной насыщенный красный фон с белым текстом**, различимый на поверхности модалки (`--surface-1`), **в обеих темах** (light `#DC2626`, dark `#EF4444`). Класс варианта остаётся `bg-status-red/… text-white`, но теперь `/NN` реально даёт красный фон; допустима и сплошная `bg-status-red` — визуальная цель одна: **не прозрачная кнопка**. `hover` — как прежде.
- **`primary` в `disabled` — приглушённый акцент** (`disabled:bg-accent/60`) теперь фактически рендерится (прежде фон пропадал; сейчас — 60 % акцента).
- Изменение — **системное (формат токенов)**, поэтому распространяется на все компоненты из §Контекст; отдельная правка каждого компонента не требуется.

## Последствия

**Плюсы:**
- Один фикс формата чинит ~25 «тихо» сломанных мест и снимает целый класс регрессий.
- Alpha-модификаторы токенов становятся штатным, работающим инструментом ДС; будущие тонировки не ломаются.
- Сплошные заливки без альфы продолжают работать без изменений (`<alpha-value>` → `1`).
- Механизм темы, дефолт light, no-FOUC, self-heal — не затронуты (меняется только формат значений и обёртка в конфиге).

**Минусы / риски (контролируемые):**
- Требуется одновременная правка `tailwind.config.ts` (обёртка) + `index.css` (триплеты в обоих тема-блоках) + **всех** сырых потребителей конвертируемых токенов — как в `index.css` (§C.1), так и вне его в `.ts/.tsx` (§C.2: инлайновые стили, SVG-атрибуты, `color-mix()`, JS-константы). Пропуск любого сырого потребителя вне `index.css` даёт ту же молчаливую регрессию (невалидный цвет, свойство отброшено). Рассинхрон (триплет в переменной, но `var(--x)` без `rgb()` в потребителе, или наоборот) → сломанный цвет. Митигируется полной таблицей §B, перечнями §C.1/§C.2 и grep-критерием готовности §C.
- Таблица §Цветовые токены документирует HEX для читаемости контраста; фактические значения в `index.css` — триплеты по §B. Расхождение форматов зафиксировано нормативно (HEX = референс цвета, триплет = литерал в CSS).

## Альтернативы (отклонены)

- **(A) Локальный фикс `Button`** — отклонён: чинит 1 из ~25 мест, оставляет корневую причину и мину на будущие токены.
- **Гибрид «дублировать токены»** (хранить и HEX-переменную, и channel-переменную) — отклонён: удвоение источника истины цвета, две цветовые модели в одном наборе, выше риск рассинхрона, чем у §C.
