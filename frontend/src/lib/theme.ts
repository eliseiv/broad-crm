import { useCallback, useState } from 'react';

/**
 * Светлая/тёмная тема (08-design-system.md «Темизация», ADR-033/ADR-041).
 * Носитель — `data-theme` на `<html>`. Дефолт при отсутствии сохранённого выбора —
 * СВЕТЛАЯ (`light`, ADR-041), НЕ `prefers-color-scheme`. Явный выбор пользователя
 * пишется в `localStorage['crm-theme']` и переопределяет дефолт («залипает»);
 * за сменой темы ОС приложение больше НЕ следует (подписка снята, ADR-041).
 */
export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'crm-theme';
const MEDIA_QUERY = '(prefers-color-scheme: dark)';

/** Дефолтная тема при отсутствии сохранённого выбора (ADR-041). */
const DEFAULT_THEME: Theme = 'light';

/** Системная тема ОС на данный момент (в дефолт темы больше не входит, ADR-041). */
export function systemTheme(): Theme {
  return typeof window !== 'undefined' && window.matchMedia(MEDIA_QUERY).matches ? 'dark' : 'light';
}

/** Сохранённый явный выбор (или `null`, если выбор не сделан). */
export function getStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

/** Итоговая тема: сохранённый выбор → иначе дефолт `light` (ADR-041). */
export function resolveTheme(): Theme {
  return getStoredTheme() ?? DEFAULT_THEME;
}

/** Фон страницы (bg-base) по теме — для браузерного `theme-color` (08-design-system). */
const THEME_COLOR: Record<Theme, string> = { dark: '#0A0C10', light: '#F2F4F7' };

/**
 * Проставить `data-theme` на `<html>` (без записи в storage) и синхронизировать
 * `<meta name="theme-color">`, чтобы браузерный chrome следовал теме (ADR-033).
 */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', THEME_COLOR[theme]);
}

/** Явно выбрать тему: записать выбор в storage и применить. */
export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* приватный режим/квота — тема всё равно применится на текущую сессию */
  }
  applyTheme(theme);
}

/**
 * Хук темы для хэдера `AppLayout`. Инициализируется текущим `data-theme`
 * (проставлен no-FOUC-скриптом в `index.html`). За сменой темы ОС приложение НЕ
 * следует (ADR-041): дефолт при отсутствии выбора — `light`, подписка на
 * `matchMedia` снята. `toggle` пишет явный выбор в `localStorage` и «залипает».
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(
    () => (document.documentElement.dataset.theme as Theme | undefined) ?? resolveTheme(),
  );

  const toggle = useCallback(() => {
    setThemeState((prev) => {
      const next: Theme = prev === 'dark' ? 'light' : 'dark';
      persistTheme(next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
