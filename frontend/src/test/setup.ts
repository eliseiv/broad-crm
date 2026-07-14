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
