import '@testing-library/jest-dom/vitest';

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
