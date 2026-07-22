import '@testing-library/jest-dom/vitest';

// --- Полифилл Web Storage (localStorage/sessionStorage) ---------------------------------
// На Node ≥ 22 у `globalThis` есть СОБСТВЕННЫЙ геттер `localStorage`, который без флага
// `--localstorage-file` отдаёт `undefined` («ExperimentalWarning: localStorage is not
// available because --localstorage-file was not provided»). Он перекрывает реализацию
// jsdom, поэтому в тестах `localStorage`/`sessionStorage` === undefined, и любой тест,
// трогающий их (auth-стор ADR-041, тема, AppLayout, api), падает на `.clear()`/`.getItem()`.
// Это дефект РАНТАЙМА, а не кода приложения: в браузере и в CI-образе оба хранилища есть.
// Ставим минимальную spec-совместимую реализацию поверх Map — только если хранилища
// действительно нет (на рантайме с рабочим Web Storage полифилл не активируется).
class MemoryStorage implements Storage {
  private readonly map = new Map<string, string>();

  get length(): number {
    return this.map.size;
  }

  key(index: number): string | null {
    return Array.from(this.map.keys())[index] ?? null;
  }

  getItem(key: string): string | null {
    // Спека требует именно `null` для отсутствующего ключа (не `undefined`).
    return this.map.has(String(key)) ? (this.map.get(String(key)) as string) : null;
  }

  setItem(key: string, value: string): void {
    this.map.set(String(key), String(value));
  }

  removeItem(key: string): void {
    this.map.delete(String(key));
  }

  clear(): void {
    this.map.clear();
  }

  [name: string]: unknown;
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  let existing: Storage | undefined;
  try {
    existing = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  } catch {
    existing = undefined;
  }
  if (!existing) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value: new MemoryStorage(),
    });
  }
}

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

if (!window.requestAnimationFrame) {
  Object.defineProperty(window, 'requestAnimationFrame', {
    writable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(Date.now()), 0),
  });
}

// jsdom не реализует scrollIntoView (в браузере он есть). Нужен компонентам, которые
// подводят сообщение в видимую область — напр. блок причины отказа проверки соединения
// в MailboxFormModal (ADR-053 §2/§4).
if (!Element.prototype.scrollIntoView) {
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    writable: true,
    value: vi.fn(),
  });
}

if (!window.cancelAnimationFrame) {
  Object.defineProperty(window, 'cancelAnimationFrame', {
    writable: true,
    value: (id: number) => window.clearTimeout(id),
  });
}

// jsdom не реализует геометрию (getClientRects/getBoundingClientRect) на Range/тексте. ProseMirror
// (редактор документов, ADR-062/063) вызывает их при scrollToSelection после правки — без полифилла
// это уходит в необработанное исключение при вводе текста в тестах DocumentEditor. Возвращаем пустую
// геометрию: позиционирование в jsdom всё равно недоступно, тестам достаточно, чтобы не падало.
const emptyRectList = () => Object.assign([], { item: () => null }) as unknown as DOMRectList;
const zeroRect = () =>
  ({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    toJSON: () => ({}),
  }) as DOMRect;

if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = emptyRectList;
}
if (!Range.prototype.getBoundingClientRect) {
  Range.prototype.getBoundingClientRect = zeroRect;
}

// jsdom не реализует document.elementFromPoint — ProseMirror зовёт его в posAtCoords на mousedown
// внутри редактируемой области. Без полифилла клик по редактору уходит в необработанное исключение.
// Координатное позиционирование в jsdom недоступно; возвращаем null (ProseMirror это переносит).
if (!document.elementFromPoint) {
  Object.defineProperty(document, 'elementFromPoint', {
    writable: true,
    value: () => null,
  });
}
