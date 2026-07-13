import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent } from 'react';
import { ChevronDown, X } from 'lucide-react';
import { cn } from '@/lib/cn';

export interface ComboboxOption {
  /** Значение опции. Пустая строка допустима (опция сброса). */
  value: string;
  /** Основной текст опции. Он же подставляется в поле при выборе. */
  label: string;
  /** Поля, по которым фильтруется список. По умолчанию — [label]. */
  keywords?: string[];
  /** Опция всегда видима: фильтром НЕ отсекается, рендерится первой (опция сброса). */
  pinned?: boolean;
}

interface ComboboxProps {
  /** ПОЛНЫЙ список опций (нефильтрованный) — фильтрует сам примитив. */
  options: ComboboxOption[];
  /** Controlled: value выбранной опции; null — не выбрано. */
  value: string | null;
  onChange: (value: string | null) => void;
  /** Controlled: текст поля. */
  query: string;
  onQueryChange: (next: string) => void;
  /** Семантика текста — 08-design-system.md «Режимы». По умолчанию 'select'. */
  mode?: 'select' | 'search';
  label?: string;
  'aria-label'?: string;
  placeholder?: string;
  disabled?: boolean;
  /** Опции ещё грузятся → в панели «Загрузка…». Поле остаётся активным. */
  loading?: boolean;
  /** Нет совпадений с запросом. */
  emptyMessage?: string;
  /** Опций нет вовсе (пустой источник). */
  noOptionsMessage?: string;
  id?: string;
  className?: string;
}

/**
 * `ui/Combobox` — поле ввода с выпадающим списком опций, фильтруемым вводом
 * (08-design-system.md «Компонент `ui/Combobox`», ADR-052). Реализация своя, без новой
 * зависимости: нативный `<select>` физически не умеет фильтровать опции вводом.
 *
 * Два независимых КОНТРОЛИРУЕМЫХ состояния — `value` (выбор) и `query` (текст); примитив
 * владеет только `open` / активной опцией / флагом `dirty`. Фильтрация — ВНУТРИ примитива
 * по `keywords` (подстрока, ci, по `trim()`-нутому запросу); страница отдаёт полный список.
 */
export function Combobox({
  options,
  value,
  onChange,
  query,
  onQueryChange,
  mode = 'select',
  label,
  'aria-label': ariaLabel,
  placeholder,
  disabled = false,
  loading = false,
  emptyMessage = 'Ничего не найдено',
  noOptionsMessage,
  id,
  className,
}: ComboboxProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const listboxId = `${inputId}-listbox`;

  const [open, setOpen] = useState(false);
  // Активная (клавиатурная) опция — индекс в ВИДИМОМ списке. `null` — активной опции НЕТ:
  // штатное состояние при открытии мышью/фокусом (08 §ARIA — `aria-activedescendant` НЕ
  // выводится из `value`, иначе он сослался бы на несуществующий optionId, когда выбранной
  // опции нет в наборе).
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  // `dirty` — пользователь изменил текст. Правило открытия (08 §Режимы): при ОТКРЫТИИ панель
  // показывает ВСЕ опции; фильтрация включается с первого введённого символа.
  const [dirty, setDirty] = useState(false);

  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<(HTMLLIElement | null)[]>([]);

  // `pinned`-опции — первыми и фильтром не отсекаются (опция сброса).
  const orderedOptions = useMemo(
    () => [...options.filter((o) => o.pinned), ...options.filter((o) => !o.pinned)],
    [options],
  );

  const normalizedQuery = query.trim().toLowerCase();
  const filtering = dirty && normalizedQuery !== '';

  const visibleOptions = useMemo(() => {
    if (!filtering) return orderedOptions;
    return orderedOptions.filter(
      (o) =>
        o.pinned ||
        (o.keywords ?? [o.label]).some((k) => k.toLowerCase().includes(normalizedQuery)),
    );
  }, [orderedOptions, filtering, normalizedQuery]);

  // «Нет совпадений» — предикат по НЕ-`pinned` опциям (08 §Состояния): `pinned` — не результат
  // поиска, а сброс, и в предикате НЕ участвует ⇒ панель показывает и `pinned`-опцию, и строку
  // «Ничего не найдено» под ней.
  const noMatches = filtering && !visibleOptions.some((o) => !o.pinned);

  const selectedOption = value === null ? undefined : options.find((o) => o.value === value);
  const resetOption = options.find((o) => o.pinned);

  // Условие видимости `X` (08 §Очистка — ЕДИНСТВЕННАЯ формулировка): выбрана НЕ-`pinned`
  // опция ИЛИ поле `dirty` ИЛИ (mode='search' и текст непуст).
  const showClear =
    (value !== null && !selectedOption?.pinned) || dirty || (mode === 'search' && query !== '');

  // Активная опция всегда прокручивается в видимую область панели (панель `max-h-64`,
  // каталог ящиков на проде — 121 позиция). Активной опция становится ТОЛЬКО от ↓/↑/Home/End.
  useEffect(() => {
    if (!open || activeIndex === null) return;
    optionRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' });
  }, [open, activeIndex]);

  // Набор опций может смениться снаружи (рефетч/серверный фильтр страницы) — активный индекс
  // не должен указывать за границы видимого списка.
  useEffect(() => {
    if (activeIndex !== null && activeIndex >= visibleOptions.length) setActiveIndex(null);
  }, [activeIndex, visibleOptions.length]);

  const openPanel = useCallback(() => {
    if (disabled) return;
    setOpen(true);
    setDirty(false); // точка сброса (1): открытие кликом/фокусом/шевроном/↓/↑/Home/End
  }, [disabled]);

  /** Закрытие БЕЗ выбора (Escape при открытом / Tab / клик вне / шеврон). */
  const closePanel = useCallback(() => {
    setOpen(false);
    setActiveIndex(null);
    setDirty(false); // точка сброса (3): закрытие панели любым способом
    if (mode === 'select') {
      // Текст возвращается к лейблу выбранной опции. Если её нет в текущем наборе —
      // `query` НЕ трогается вовсе (поле не пустеет при живом `value`).
      const lbl = selectedOption?.label;
      if (lbl !== undefined && lbl !== query) onQueryChange(lbl);
    }
  }, [mode, selectedOption, query, onQueryChange]);

  const selectOption = useCallback(
    (option: ComboboxOption) => {
      onChange(option.value);
      onQueryChange(option.label);
      setOpen(false);
      setActiveIndex(null);
      setDirty(false); // точка сброса (2): выбор опции
    },
    [onChange, onQueryChange],
  );

  /** Очистка (`X` / `Escape` при закрытом списке) — 08 §Очистка. Панель ЗАКРЫВАЕТСЯ. */
  const clear = useCallback(() => {
    if (resetOption) {
      // Есть `pinned`-опция сброса ⇒ очистка ≡ ВЫБОР этой опции. `onChange(null)` НЕ эмитится.
      onChange(resetOption.value);
      onQueryChange(resetOption.label);
    } else {
      onChange(null);
      onQueryChange('');
    }
    setOpen(false);
    setActiveIndex(null);
    setDirty(false); // точка сброса (4): очистка
  }, [resetOption, onChange, onQueryChange]);

  // Клик ВНЕ поля/панели → закрыть без выбора (та же семантика, что Escape при открытом).
  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: globalThis.MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) closePanel();
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open, closePanel]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    const last = (open ? visibleOptions.length : orderedOptions.length) - 1;
    // При `loading` панель показывает только «Загрузка…» — опции в DOM НЕ отрисованы (08
    // §Состояния) ⇒ активной опции быть НЕ МОЖЕТ: иначе `aria-activedescendant` сослался бы на
    // отсутствующий узел, а `Enter` выбрал бы невидимую опцию (на «Сообщениях» набор при
    // загрузке непуст — `pinned` «Все почты» подмешивается всегда). Открытие/закрытие/очистка
    // при этом работают штатно — отключена только НАВИГАЦИЯ и ВЫБОР.
    const navigable = !loading;

    switch (e.key) {
      case 'ArrowDown': {
        e.preventDefault();
        if (!open) {
          openPanel();
          if (navigable && orderedOptions.length > 0) setActiveIndex(0);
          return;
        }
        if (!navigable || visibleOptions.length === 0) return;
        // активной нет → ПЕРВАЯ; есть → следующая (на последней остаётся; зацикливания нет).
        setActiveIndex((i) => (i === null ? 0 : Math.min(i + 1, last)));
        return;
      }
      case 'ArrowUp': {
        e.preventDefault();
        if (!open) {
          openPanel();
          if (navigable && orderedOptions.length > 0) setActiveIndex(orderedOptions.length - 1);
          return;
        }
        if (!navigable || visibleOptions.length === 0) return;
        // активной нет → ПОСЛЕДНЯЯ (правило не зависит от способа открытия); есть → предыдущая.
        setActiveIndex((i) => (i === null ? last : Math.max(i - 1, 0)));
        return;
      }
      case 'Home': {
        // Список закрыт — событие НЕ перехватывается (курсор в начало текста поля).
        if (!open || !navigable || visibleOptions.length === 0) return;
        e.preventDefault();
        setActiveIndex(0);
        return;
      }
      case 'End': {
        if (!open || !navigable || visibleOptions.length === 0) return;
        e.preventDefault();
        setActiveIndex(last);
        return;
      }
      case 'Enter': {
        if (!open) return; // список закрыт — событие не перехватывается
        e.preventDefault(); // сабмита формы не происходит: поле в режиме выбора
        // ВЫБОРА НЕТ (панель остаётся ОТКРЫТОЙ): активной опции нет — либо она невозможна
        // (`loading`: опции не отрисованы).
        if (!navigable || activeIndex === null) return;
        const option = visibleOptions[activeIndex];
        if (option) selectOption(option);
        return;
      }
      case 'Escape': {
        if (open) closePanel();
        else clear();
        return;
      }
      case 'Tab': {
        // Закрыть без выбора, фокус уходит дальше. Активная опция НЕ выбирается.
        if (open) closePanel();
        return;
      }
      default:
        return;
    }
  };

  const handleInputChange = (next: string) => {
    // Открытие ВВОДОМ: `openPanel()` сбрасывает `dirty`, поэтому он обязан вызываться ДО
    // `setDirty(true)` — иначе первый напечатанный символ не фильтровал бы список.
    if (!open) openPanel();
    setDirty(true);
    setActiveIndex(null);
    onQueryChange(next);
    // mode='search': ввод СБРАСЫВАЕТ выбор (текст и выбор взаимоисключающи).
    if (mode === 'search' && value !== null) onChange(null);
  };

  /**
   * Клик по САМОМУ ПОЛЮ (08 §Клавиатура, буллеты о мыши): панель ЗАКРЫТА → ОТКРЫТЬ (правило
   * открытия — «видны ВСЕ опции», `dirty=false`, точка сброса (1)); панель ОТКРЫТА → НИЧЕГО
   * (остаётся открытой — пользователь ставит курсор в текст; toggle по полю ЗАПРЕЩЁН, toggle
   * есть только у шеврона).
   *
   * Обязателен: `onFocus` — вход ТОЛЬКО с несфокусированного поля, а ВСЕ штатные способы
   * закрытия (выбор опции / `Escape` / `X` / шеврон) удерживают фокус в `<input>` ⇒ без этого
   * обработчика повторный клик по уже сфокусированному полю не порождал бы `focus` и список
   * не выпадал бы. Двойного открытия при первом клике нет: `focus` — дискретное событие, к
   * моменту `click` состояние уже `open === true` ⇒ ветка не срабатывает.
   */
  const handleInputClick = () => {
    if (!open) openPanel();
  };

  const handleChevronClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (disabled) return;
    if (open) {
      closePanel();
      return;
    }
    openPanel();
    inputRef.current?.focus();
  };

  // `<ul role="listbox">` (08 §ARIA — панель именно `<ul>`) отрисован только когда есть что
  // рендерить: не `loading`, источник непуст, хотя бы одна опция видима. В прочих состояниях
  // панели («Загрузка…» / `noOptionsMessage` / «нет совпадений» без `pinned`) узла с
  // `id={listboxId}` в DOM НЕТ ⇒ `aria-controls`/`aria-activedescendant` не должны на него
  // ссылаться (висячий IDREF).
  const listboxRendered = open && !loading && options.length > 0 && visibleOptions.length > 0;

  const activeOptionId =
    !listboxRendered || activeIndex === null ? undefined : `${listboxId}-opt-${activeIndex}`;

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <label htmlFor={inputId} className="text-[13px] font-medium text-text-secondary">
          {label}
        </label>
      )}
      <div ref={rootRef} className="relative">
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxRendered ? listboxId : undefined}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-activedescendant={activeOptionId}
          aria-label={ariaLabel}
          autoComplete="off"
          placeholder={placeholder}
          disabled={disabled}
          value={query}
          onChange={(e) => handleInputChange(e.target.value)}
          onFocus={openPanel}
          onClick={handleInputClick}
          onKeyDown={handleKeyDown}
          className={cn(
            'h-10 w-full rounded-[10px] border border-border-strong bg-surface-2 pl-3 text-sm text-text-primary',
            'placeholder:text-text-tertiary transition-colors duration-150',
            'focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40',
            'disabled:cursor-not-allowed disabled:opacity-60',
            showClear ? 'pr-16' : 'pr-9',
          )}
        />
        <div className="absolute inset-y-0 right-2 flex items-center gap-0.5">
          {showClear && (
            <button
              type="button"
              aria-label="Очистить"
              disabled={disabled}
              // Фокус ОСТАЁТСЯ в `<input>`: без preventDefault mousedown увёл бы его из поля.
              onMouseDown={(e) => e.preventDefault()}
              onClick={clear}
              className={cn(
                'flex h-6 w-6 items-center justify-center rounded-md text-text-tertiary transition-colors',
                'hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              )}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          {/* Шеврон — КЛИКАБЕЛЕН (toggle), вне таб-порядка; `pointer-events-none` из
              `ui/Select` здесь НЕ копируется (там клик обрабатывает нативный <select>). */}
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            disabled={disabled}
            onMouseDown={(e) => e.preventDefault()}
            onClick={handleChevronClick}
            className="flex h-6 w-6 items-center justify-center rounded-md text-text-tertiary"
          >
            <ChevronDown
              className={cn('h-4 w-4 transition-transform', open && 'rotate-180')}
              aria-hidden="true"
            />
          </button>
        </div>

        {open && (
          <div className="scrollbar-none absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-[10px] border border-border-strong bg-surface-2 shadow-card">
            {loading ? (
              <p className="px-3 py-2 text-sm text-text-secondary">Загрузка…</p>
            ) : options.length === 0 ? (
              <p className="px-3 py-2 text-sm text-text-secondary">
                {noOptionsMessage ?? emptyMessage}
              </p>
            ) : (
              <>
                {visibleOptions.length > 0 && (
                  <ul role="listbox" id={listboxId}>
                    {visibleOptions.map((option, idx) => {
                      const active = idx === activeIndex;
                      const selected = option.value === value;
                      return (
                        <li
                          key={option.value}
                          ref={(node) => {
                            optionRefs.current[idx] = node;
                          }}
                          role="option"
                          id={`${listboxId}-opt-${idx}`}
                          aria-selected={selected}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => selectOption(option)}
                          className={cn(
                            'cursor-pointer break-words px-3 py-2 text-sm transition-colors',
                            selected ? 'text-text-primary' : 'text-text-secondary',
                            active ? 'bg-surface-3' : 'hover:bg-surface-3',
                          )}
                        >
                          {option.label}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {noMatches && (
                  <p className="px-3 py-2 text-sm text-text-secondary">{emptyMessage}</p>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
