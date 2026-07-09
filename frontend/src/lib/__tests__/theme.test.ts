import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyTheme,
  getStoredTheme,
  persistTheme,
  resolveTheme,
  systemTheme,
  useTheme,
} from '@/lib/theme';

const STORAGE_KEY = 'crm-theme';

/**
 * Управляемый мок window.matchMedia: возвращает MediaQueryList с работающим
 * addEventListener('change'), позволяя эмулировать смену темы ОС (`emitChange`).
 * theme.ts дергает matchMedia на каждый systemTheme()/resolveTheme() — возвращаем
 * стабильный объект.
 */
function installMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const mql = {
    get matches() {
      return matches;
    },
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: (_type: string, l: (e: MediaQueryListEvent) => void) => {
      listeners.add(l);
    },
    removeEventListener: (_type: string, l: (e: MediaQueryListEvent) => void) => {
      listeners.delete(l);
    },
    addListener: (l: (e: MediaQueryListEvent) => void) => {
      listeners.add(l);
    },
    removeListener: (l: (e: MediaQueryListEvent) => void) => {
      listeners.delete(l);
    },
    dispatchEvent: () => true,
  };
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue(mql));
  return {
    /** Эмулировать смену системной темы ОС (уведомляет подписчиков). */
    emitChange(next: boolean) {
      matches = next;
      listeners.forEach((l) => l({ matches: next } as MediaQueryListEvent));
    },
  };
}

function metaThemeColor(): string | null {
  return document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null;
}

describe('lib/theme', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    // Гарантируем наличие <meta name="theme-color"> для applyTheme.
    if (!document.querySelector('meta[name="theme-color"]')) {
      const meta = document.createElement('meta');
      meta.setAttribute('name', 'theme-color');
      document.head.appendChild(meta);
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  describe('systemTheme', () => {
    it('prefers-color-scheme: dark → "dark"', () => {
      installMatchMedia(true);
      expect(systemTheme()).toBe('dark');
    });

    it('prefers-color-scheme: светлая → "light"', () => {
      installMatchMedia(false);
      expect(systemTheme()).toBe('light');
    });
  });

  describe('getStoredTheme', () => {
    it('пустой storage → null', () => {
      expect(getStoredTheme()).toBeNull();
    });

    it('валидные значения читаются как есть', () => {
      localStorage.setItem(STORAGE_KEY, 'dark');
      expect(getStoredTheme()).toBe('dark');
      localStorage.setItem(STORAGE_KEY, 'light');
      expect(getStoredTheme()).toBe('light');
    });

    it('невалидное значение → null', () => {
      localStorage.setItem(STORAGE_KEY, 'system');
      expect(getStoredTheme()).toBeNull();
    });
  });

  describe('resolveTheme (дефолт light, без follow-OS, ADR-041)', () => {
    it('без сохранённого выбора → дефолт light (тема ОС игнорируется)', () => {
      installMatchMedia(true); // ОС тёмная — на дефолт больше не влияет
      expect(resolveTheme()).toBe('light');
    });

    it('сохранённый выбор приоритетнее дефолта', () => {
      installMatchMedia(false); // ОС светлая
      localStorage.setItem(STORAGE_KEY, 'dark');
      expect(resolveTheme()).toBe('dark');
    });
  });

  describe('applyTheme', () => {
    it('проставляет data-theme и синхронизирует <meta name="theme-color">', () => {
      applyTheme('dark');
      expect(document.documentElement.dataset.theme).toBe('dark');
      expect(metaThemeColor()).toBe('#0A0C10');

      applyTheme('light');
      expect(document.documentElement.dataset.theme).toBe('light');
      expect(metaThemeColor()).toBe('#F2F4F7');
    });
  });

  describe('persistTheme', () => {
    it('пишет выбор в localStorage и применяет тему', () => {
      installMatchMedia(false);
      persistTheme('dark');
      expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
      expect(document.documentElement.dataset.theme).toBe('dark');
    });
  });

  describe('useTheme', () => {
    it('без явного выбора стартует с дефолта light и НЕ следует за сменой ОС (ADR-041)', () => {
      const mm = installMatchMedia(true); // ОС тёмная — но подписка follow-OS снята
      const { result } = renderHook(() => useTheme());
      expect(result.current.theme).toBe('light');

      act(() => mm.emitChange(true)); // смена темы ОС игнорируется (залипает на light)
      expect(result.current.theme).toBe('light');
    });

    it('после явного выбора ОС-изменения игнорируются (залипание)', () => {
      const mm = installMatchMedia(false); // ОС светлая
      localStorage.setItem(STORAGE_KEY, 'light'); // явный выбор пользователя
      const { result } = renderHook(() => useTheme());
      expect(result.current.theme).toBe('light');

      act(() => mm.emitChange(true)); // ОС → тёмная, но выбор залип
      expect(result.current.theme).toBe('light');
    });

    it('toggle переключает тему и персистит в localStorage', () => {
      installMatchMedia(false); // ОС светлая → старт light
      const { result } = renderHook(() => useTheme());
      expect(result.current.theme).toBe('light');

      act(() => result.current.toggle());
      expect(result.current.theme).toBe('dark');
      expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
      expect(document.documentElement.dataset.theme).toBe('dark');

      act(() => result.current.toggle());
      expect(result.current.theme).toBe('light');
      expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
      expect(document.documentElement.dataset.theme).toBe('light');
    });
  });
});
