import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Combobox } from '@/components/ui/Combobox';
import type { ComboboxOption } from '@/components/ui/Combobox';

/**
 * `ui/Combobox` — 08-design-system.md «Компонент `ui/Combobox`» (ADR-052 §1).
 *
 * Примитив не-нативный ⇒ доступность даёт ТОЛЬКО наш код: ARIA-контракт и КАЖДАЯ клавиша
 * нормированы поэлементно и обязаны быть покрыты (ADR-052 §1.2, «Реализация „мышью работает,
 * клавиатурой нет“ = дефект»). Здесь проверяется сам примитив; семантика вкладок — в
 * MailPage.test.tsx (`mode='select'`) и MailboxesTab.test.tsx (`mode='search'`).
 */

// jsdom не реализует Element.prototype.scrollIntoView (примитив зовёт его при смене активной
// опции — 08 §Клавиатура). Ставим фейк В ФИКСТУРЕ (глобальное состояние сбрасывается между
// тестами) и попутно записываем, НА КАКОМ элементе он был вызван, — этим проверяется норма
// «активная опция прокручивается в видимую область».
let scrolled: { el: Element; arg: unknown }[] = [];
const originalScrollIntoView = Element.prototype.scrollIntoView as unknown;

beforeEach(() => {
  scrolled = [];
  Element.prototype.scrollIntoView = function (this: Element, arg?: unknown) {
    scrolled.push({ el: this, arg });
  } as typeof Element.prototype.scrollIntoView;
});

afterEach(() => {
  Element.prototype.scrollIntoView =
    originalScrollIntoView as typeof Element.prototype.scrollIntoView;
});

/** Опции вкладки «Почты»: `pinned`-опции сброса НЕТ (ADR-052 §3). */
const SEARCH_OPTIONS: ComboboxOption[] = [
  {
    value: '1',
    label: '5108 Klyro Forge alpha@postapp.store',
    keywords: ['5108', 'Klyro Forge', 'alpha@postapp.store'],
  },
  {
    value: '2',
    label: '7011 Nova Ledger beta@postapp.store',
    keywords: ['7011', 'Nova Ledger', 'beta@postapp.store'],
  },
  { value: '3', label: 'gamma@other.store', keywords: ['', '', 'gamma@other.store'] },
];

/** Опции вкладки «Сообщения»: первая — `pinned`-опция сброса «Все почты» (ADR-052 §2). */
const SELECT_OPTIONS: ComboboxOption[] = [
  { value: '', label: 'Все почты', pinned: true },
  ...SEARCH_OPTIONS,
];

const onChange = vi.fn();
const onQueryChange = vi.fn();

interface HarnessProps {
  options?: ComboboxOption[];
  mode?: 'select' | 'search';
  initialValue?: string | null;
  initialQuery?: string;
  loading?: boolean;
  disabled?: boolean;
  placeholder?: string;
  noOptionsMessage?: string;
}

/**
 * Контролируемая обёртка: `value`/`query` живут снаружи (контракт примитива — 08 §Контракт),
 * спаи фиксируют ФАКТ и АРГУМЕНТ вызова. Кнопка «после» — для проверки таб-порядка.
 */
function Harness({
  options = SELECT_OPTIONS,
  mode = 'select',
  initialValue = null,
  initialQuery = '',
  ...rest
}: HarnessProps) {
  const [value, setValue] = useState<string | null>(initialValue);
  const [query, setQuery] = useState(initialQuery);
  return (
    <div>
      <span>вне поля</span>
      <Combobox
        aria-label="Почта"
        mode={mode}
        options={options}
        value={value}
        onChange={(v) => {
          onChange(v);
          setValue(v);
        }}
        query={query}
        onQueryChange={(q) => {
          onQueryChange(q);
          setQuery(q);
        }}
        {...rest}
      />
      <button type="button">после</button>
    </div>
  );
}

function input(): HTMLInputElement {
  return screen.getByRole('combobox', { name: 'Почта' }) as HTMLInputElement;
}
function listbox(): HTMLElement {
  return screen.getByRole('listbox');
}
function optionLabels(): string[] {
  return within(listbox())
    .getAllByRole('option')
    .map((o) => o.textContent ?? '');
}
function clearButton(): HTMLElement | null {
  return screen.queryByRole('button', { name: 'Очистить' });
}
/** Шеврон — `aria-hidden`, вне таб-порядка (08 §Клавиатура) ⇒ по роли не находится. */
function chevron(): HTMLButtonElement {
  const btn = document.querySelector<HTMLButtonElement>('button[aria-hidden="true"]');
  if (!btn) throw new Error('ChevronDown button not found');
  return btn;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Combobox — правило открытия и фильтрация (08 §Режимы)', () => {
  it('открытие кликом показывает ВСЕ опции — даже когда в поле уже стоит текст', async () => {
    const user = userEvent.setup();
    // Поле не пусто: в нём лейбл выбранной опции (штатное состояние `mode='select'`).
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    await user.click(input());

    // Список НЕ схлопнут до одной опции: видны все 4 (включая `pinned` «Все почты»).
    expect(optionLabels()).toEqual([
      'Все почты',
      '5108 Klyro Forge alpha@postapp.store',
      '7011 Nova Ledger beta@postapp.store',
      'gamma@other.store',
    ]);
  });

  it('фильтрация включается с ПЕРВОГО введённого символа (открытие вводом не теряет символ)', async () => {
    const user = userEvent.setup();
    render(<Harness options={SEARCH_OPTIONS} mode="search" />);

    // Панель закрыта; печать ОДНОГО символа открывает её И сразу фильтрует (dirty при открытии
    // вводом НЕ сбрасывается — иначе первый символ был бы потерян).
    await user.type(input(), 'n');

    expect(input()).toHaveAttribute('aria-expanded', 'true');
    expect(optionLabels()).toEqual(['7011 Nova Ledger beta@postapp.store']);
  });

  it('фильтрует по `keywords` (подстрока, регистронезависимо), а не только по лейблу', async () => {
    const user = userEvent.setup();
    render(<Harness options={SEARCH_OPTIONS} mode="search" />);

    await user.type(input(), 'NOVA');

    expect(optionLabels()).toEqual(['7011 Nova Ledger beta@postapp.store']);
  });

  it('`pinned`-опция не отсекается фильтром и остаётся первой', async () => {
    const user = userEvent.setup();
    render(<Harness initialQuery="" />);

    await user.type(input(), '7011');

    expect(optionLabels()).toEqual(['Все почты', '7011 Nova Ledger beta@postapp.store']);
  });

  it('нет совпадений (§2): видны И `pinned` «Все почты», И строка «Ничего не найдено»', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(input(), 'zzz-nomatch');

    // Предикат «нет совпадений» считается по НЕ-`pinned` опциям (08 §Состояния).
    expect(optionLabels()).toEqual(['Все почты']);
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });
});

describe('Combobox — клавиатура (08 §Клавиатура, перечень полный)', () => {
  it('↓ при ЗАКРЫТОМ списке: открыть + активировать ПЕРВУЮ опцию', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    input().focus();
    // Фокус уже открыл панель (onFocus) — закрываем, чтобы стартовать из «закрыто».
    await user.keyboard('{Escape}');
    expect(input()).toHaveAttribute('aria-expanded', 'false');

    await user.keyboard('{ArrowDown}');

    expect(input()).toHaveAttribute('aria-expanded', 'true');
    expect(input()).toHaveAttribute(
      'aria-activedescendant',
      within(listbox()).getAllByRole('option')[0].id,
    );
  });

  it('↓ из состояния «панель открыта, активной опции НЕТ» → ПЕРВАЯ опция', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input()); // открытие мышью ⇒ активной опции нет
    expect(input()).not.toHaveAttribute('aria-activedescendant');

    await user.keyboard('{ArrowDown}');

    const options = within(listbox()).getAllByRole('option');
    expect(input()).toHaveAttribute('aria-activedescendant', options[0].id);
  });

  it('↑ из состояния «панель открыта, активной опции НЕТ» → ПОСЛЕДНЯЯ опция', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.keyboard('{ArrowUp}');

    const options = within(listbox()).getAllByRole('option');
    expect(input()).toHaveAttribute('aria-activedescendant', options[options.length - 1].id);
  });

  it('↓ не зацикливается на последней, ↑ не зацикливается на первой', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    // 4 опции: 4 нажатия ↓ + одно лишнее — остаёмся на последней.
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}');
    const options = within(listbox()).getAllByRole('option');
    expect(input()).toHaveAttribute('aria-activedescendant', options[3].id);

    await user.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}');
    expect(input()).toHaveAttribute('aria-activedescendant', options[0].id);
  });

  it('Enter при открытой панели БЕЗ активной опции: выбора НЕТ, панель ОСТАЁТСЯ открытой', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.keyboard('{Enter}');

    expect(onChange).not.toHaveBeenCalled();
    expect(input()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('Enter на активной опции выбирает её и закрывает панель', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}'); // вторая опция — ящик id=1

    expect(onChange).toHaveBeenCalledWith('1');
    expect(onQueryChange).toHaveBeenLastCalledWith('5108 Klyro Forge alpha@postapp.store');
    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('Home / End при ОТКРЫТОЙ панели активируют первую / последнюю опцию', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.keyboard('{End}');
    let options = within(listbox()).getAllByRole('option');
    expect(input()).toHaveAttribute('aria-activedescendant', options[3].id);

    await user.keyboard('{Home}');
    options = within(listbox()).getAllByRole('option');
    expect(input()).toHaveAttribute('aria-activedescendant', options[0].id);
  });

  it('Home / End при ЗАКРЫТОЙ панели НЕ перехватываются (панель не открывается)', async () => {
    const user = userEvent.setup();
    render(<Harness options={SEARCH_OPTIONS} mode="search" />);

    input().focus();
    await user.keyboard('{Escape}'); // закрываем панель, открытую фокусом
    expect(input()).toHaveAttribute('aria-expanded', 'false');

    await user.keyboard('{Home}{End}');

    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input()).not.toHaveAttribute('aria-activedescendant');
  });

  it('Escape при ОТКРЫТОЙ панели закрывает без выбора; текст возвращается к лейблу (`mode=select`)', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    await user.click(input());
    await user.type(input(), 'zzz');
    await user.keyboard('{Escape}');

    expect(onChange).not.toHaveBeenCalled(); // выбор не менялся
    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(input().value).toBe('5108 Klyro Forge alpha@postapp.store');
  });

  it('Tab закрывает панель без выбора; шеврон ВНЕ таб-порядка (фокус уходит на следующий контрол)', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.keyboard('{ArrowDown}'); // активная опция ЕСТЬ

    await user.tab();

    // Активная опция НЕ выбрана (иначе Tab незаметно менял бы фильтр).
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    // Шеврон (tabIndex=-1) пропущен: фокус на следующем контроле формы. `X` не отрисован
    // (dirty сброшен на закрытии, `pinned`-опция не выбрана).
    expect(screen.getByRole('button', { name: 'после' })).toHaveFocus();
    expect(chevron()).not.toHaveFocus();
  });

  it('активная опция прокручивается в видимую область (`scrollIntoView({block:"nearest"})`)', async () => {
    const user = userEvent.setup();
    // Каталог на проде — 121 ящик; панель `max-h-64` ⇒ без прокрутки `End` уводит опцию за край.
    const many: ComboboxOption[] = Array.from({ length: 121 }, (_, i) => ({
      value: String(i),
      label: `Ящик ${i}`,
    }));
    render(<Harness options={many} mode="search" />);

    await user.click(input());
    await user.keyboard('{End}');

    const options = within(listbox()).getAllByRole('option');
    const last = options[options.length - 1];
    expect(input()).toHaveAttribute('aria-activedescendant', last.id);
    // Прокручен именно активный (последний) элемент, с нормативным аргументом.
    const lastScroll = scrolled[scrolled.length - 1];
    expect(lastScroll.el).toBe(last);
    expect(lastScroll.arg).toEqual({ block: 'nearest' });
  });
});

describe('Combobox — мышь (08 §Клавиатура, буллеты о мыши)', () => {
  it('клик по опции ≡ Enter на ней (выбор без предварительной активации стрелкой)', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    await user.click(
      within(listbox()).getByRole('option', { name: '7011 Nova Ledger beta@postapp.store' }),
    );

    expect(onChange).toHaveBeenCalledWith('2');
    expect(input().value).toBe('7011 Nova Ledger beta@postapp.store');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('клик по САМОМУ ПОЛЮ при уже открытой панели её НЕ закрывает', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    expect(input()).toHaveAttribute('aria-expanded', 'true');

    await user.click(input()); // пользователь ставит курсор в текст — не toggle

    expect(input()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('клик по ChevronDown — toggle: закрыта → открыть, открыта → закрыть без выбора', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(chevron());
    expect(input()).toHaveAttribute('aria-expanded', 'true');
    expect(input()).toHaveFocus();

    await user.click(chevron());
    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('клик ВНЕ поля/панели закрывает без выбора', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(input());
    expect(screen.getByRole('listbox')).toBeInTheDocument();

    await user.click(screen.getByText('вне поля'));

    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('Combobox — очистка и состояние «нет выбора» (08 §Очистка, ADR-052 §1.1а)', () => {
  it('при выбранной `pinned`-опции и НЕ-dirty поле `X` НЕ рендерится (очищать нечего)', () => {
    render(<Harness initialValue="" initialQuery="Все почты" />);

    expect(input().value).toBe('Все почты');
    expect(clearButton()).not.toBeInTheDocument();
  });

  it('выбрана не-`pinned` опция → `X` рендерится', () => {
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    expect(clearButton()).toBeInTheDocument();
  });

  it('клик по `X` при ЕСТЬ `pinned`-сбросе ≡ выбор опции сброса; панель ЗАКРЫТА, фокус в поле', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="" initialQuery="Все почты" />);

    // Единственный способ увидеть `X` в дефолте «Сообщений» — напечатать текст (dirty).
    await user.click(input());
    await user.type(input(), 'nova');
    expect(clearButton()).toBeInTheDocument();

    await user.click(clearButton() as HTMLElement);

    // `onChange(null)` НЕ эмитится — очистка есть ВЫБОР `pinned`-опции.
    expect(onChange).toHaveBeenLastCalledWith('');
    expect(onChange).not.toHaveBeenCalledWith(null);
    expect(onQueryChange).toHaveBeenLastCalledWith('Все почты');
    expect(input().value).toBe('Все почты');
    expect(input()).toHaveAttribute('aria-expanded', 'false'); // панель ЗАКРЫЛАСЬ
    expect(input()).toHaveFocus(); // фокус ОСТАЛСЯ в поле
    expect(clearButton()).not.toBeInTheDocument(); // dirty=false ⇒ `X` исчез
  });

  it('клик по `X` БЕЗ `pinned`-сброса → onChange(null) + пустой текст, панель закрыта', async () => {
    const user = userEvent.setup();
    render(<Harness options={SEARCH_OPTIONS} mode="search" placeholder="Поиск по почтам…" />);

    await user.type(input(), 'nova');
    await user.click(clearButton() as HTMLElement);

    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(onQueryChange).toHaveBeenLastCalledWith('');
    expect(input().value).toBe('');
    expect(input()).toHaveAttribute('placeholder', 'Поиск по почтам…');
    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(input()).toHaveFocus();
    expect(clearButton()).not.toBeInTheDocument();
  });

  it('Escape при ЗАКРЫТОМ списке очищает: с `pinned` — выбор опции сброса (поле НЕ пустеет)', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    input().focus();
    await user.keyboard('{Escape}'); // 1-й: закрывает панель, открытую фокусом
    await user.keyboard('{Escape}'); // 2-й: очистка при закрытом списке

    expect(onChange).toHaveBeenLastCalledWith('');
    expect(input().value).toBe('Все почты');
    expect(clearButton()).not.toBeInTheDocument();
  });

  it('Escape при ЗАКРЫТОМ списке без `pinned` → onChange(null) + поле пусто', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        options={SEARCH_OPTIONS}
        mode="search"
        initialValue="2"
        initialQuery="7011 Nova Ledger beta@postapp.store"
      />,
    );

    input().focus();
    await user.keyboard('{Escape}{Escape}');

    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(input().value).toBe('');
  });

  it('`dirty` сбрасывается на ЗАКРЫТИИ: напечатал → закрыл без выбора → `X` НЕ отрисован', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="" initialQuery="Все почты" />);

    await user.click(input());
    await user.type(input(), 'nova');
    expect(clearButton()).toBeInTheDocument();

    await user.keyboard('{Escape}'); // закрытие без выбора

    expect(input().value).toBe('Все почты'); // текст вернулся к лейблу
    expect(clearButton()).not.toBeInTheDocument(); // аномалии «X при нечего сбрасывать» нет
  });
});

describe('Combobox — режимы (08 §Режимы)', () => {
  it('`mode=select`: ввод текста выбор НЕ сбрасывает', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    await user.type(input(), 'zzz');

    expect(onChange).not.toHaveBeenCalled();
  });

  it('`mode=search`: ввод текста СБРАСЫВАЕТ выбор (onChange(null))', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        options={SEARCH_OPTIONS}
        mode="search"
        initialValue="1"
        initialQuery="5108 Klyro Forge alpha@postapp.store"
      />,
    );

    await user.type(input(), 'x');

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('`mode=search`: закрытие без выбора СОХРАНЯЕТ текст как есть', async () => {
    const user = userEvent.setup();
    render(<Harness options={SEARCH_OPTIONS} mode="search" />);

    await user.type(input(), 'nova');
    await user.keyboard('{Escape}'); // закрытие панели (не очистка — список был открыт)

    expect(input().value).toBe('nova');
  });

  it('`mode=select`, выбранной опции НЕТ в наборе: закрытие без выбора поле НЕ пустит', async () => {
    const user = userEvent.setup();
    // `value='99'` отсутствует в options (набор сменился снаружи — 08 §Состояния).
    render(<Harness initialValue="99" initialQuery="Удалённый ящик deleted@postapp.store" />);

    await user.click(input());
    // Открытие мышью ⇒ активной опции нет, ссылки на несуществующий optionId тоже.
    expect(input()).not.toHaveAttribute('aria-activedescendant');

    await user.keyboard('{Escape}');

    // `onQueryChange` НЕ вызывается: поле продолжает показывать лейбл, фильтр не «теряется».
    expect(onQueryChange).not.toHaveBeenCalled();
    expect(input().value).toBe('Удалённый ящик deleted@postapp.store');
  });
});

describe('Combobox — ARIA-контракт и состояния (08 §ARIA, §Состояния)', () => {
  it('поле несёт обязательные ARIA-атрибуты; панель — listbox, связанный с полем', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    const el = input();
    expect(el).toHaveAttribute('aria-haspopup', 'listbox');
    expect(el).toHaveAttribute('aria-autocomplete', 'list');
    expect(el).toHaveAttribute('autocomplete', 'off');
    expect(el).toHaveAttribute('aria-expanded', 'false');

    await user.click(el);

    expect(el).toHaveAttribute('aria-expanded', 'true');
    expect(el.getAttribute('aria-controls')).toBe(listbox().id);
  });

  it('выбранная опция помечена `aria-selected="true"`, прочие — false', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="1" initialQuery="5108 Klyro Forge alpha@postapp.store" />);

    await user.click(input());

    const options = within(listbox()).getAllByRole('option');
    expect(options.map((o) => o.getAttribute('aria-selected'))).toEqual([
      'false',
      'true',
      'false',
      'false',
    ]);
  });

  it('loading → в панели «Загрузка…», поле остаётся активным', async () => {
    const user = userEvent.setup();
    render(<Harness loading />);

    await user.click(input());

    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input()).not.toBeDisabled();
  });

  it('опций нет вовсе → `noOptionsMessage`', async () => {
    const user = userEvent.setup();
    render(<Harness options={[]} noOptionsMessage="Почт пока нет" />);

    await user.click(input());

    expect(screen.getByText('Почт пока нет')).toBeInTheDocument();
  });

  it('disabled → поле не фокусируемо, панель не открывается', async () => {
    const user = userEvent.setup();
    render(<Harness disabled />);

    expect(input()).toBeDisabled();
    await user.click(input());

    expect(input()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
