import { useCallback, useEffect, useState } from 'react';

/**
 * Светлая/тёмная тема (08-design-system.md «Темизация», ADR-033).
 * Носитель — `data-theme` на `<html>`. Дефолт — СИСТЕМНАЯ тема ОС
 * (`prefers-color-scheme`); явный выбор пользователя пишется в
 * `localStorage['crm-theme']` и переопределяет систему («залипает»).
 */
export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'crm-theme';
const MEDIA_QUERY = '(prefers-color-scheme: dark)';

/** Системная тема ОС на данный момент. */
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

/** Итоговая тема: сохранённый выбор → иначе системная. */
export function resolveTheme(): Theme {
  return getStoredTheme() ?? systemTheme();
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
 * (проставлен no-FOUC-скриптом в `index.html`). Пока нет явного выбора —
 * следует за сменой темы ОС в реальном времени; после первого явного выбора
 * (`toggle`) сохранённое значение имеет приоритет и ОС больше не влияет.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(
    () => (document.documentElement.dataset.theme as Theme | undefined) ?? resolveTheme(),
  );

  // Следование за ОС — только пока явного выбора нет (localStorage пуст).
  useEffect(() => {
    const mq = window.matchMedia(MEDIA_QUERY);
    const handler = (e: MediaQueryListEvent) => {
      if (getStoredTheme() !== null) return; // явный выбор приоритетнее ОС
      const next: Theme = e.matches ? 'dark' : 'light';
      applyTheme(next);
      setThemeState(next);
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const toggle = useCallback(() => {
    setThemeState((prev) => {
      const next: Theme = prev === 'dark' ? 'light' : 'dark';
      persistTheme(next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
