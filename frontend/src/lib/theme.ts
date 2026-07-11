import { useCallback, useEffect, useSyncExternalStore } from 'react';

/**
 * Светлая/тёмная тема (08-design-system.md «Темизация», ADR-033/ADR-041/ADR-046 §4).
 * Носитель — `data-theme` на `<html>`. Дефолт при отсутствии сохранённого выбора —
 * СВЕТЛАЯ (`light`, ADR-041), НЕ `prefers-color-scheme`. Явный выбор пользователя
 * пишется в `localStorage['crm-theme']` и переопределяет дефолт («залипает»);
 * за сменой темы ОС приложение НЕ следует (подписка снята, ADR-041).
 *
 * Первичная простановка атрибута — статический no-FOUC-скрипт `/theme-init.js`
 * (`frontend/public/`, ADR-046 §4.1; inline запрещён CSP). Если он не отработал —
 * `useTheme()` самолечится при монтировании (ADR-046 §4.3).
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
 * Подписчики на смену темы (внешний стор для `useSyncExternalStore`): все компоненты,
 * зависящие от активной темы (хэдер-переключатель, sandbox-iframe тела письма — ADR-047 §6),
 * перерисовываются одним источником, а не каждый со своей копией состояния.
 */
const listeners = new Set<() => void>();

function emitThemeChange(): void {
  for (const l of listeners) l();
}

function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Текущая тема по атрибуту `data-theme` на `<html>`. Отсутствие/мусор → дефолт `light`
 * (инвариант ADR-046 §4.2: «нет атрибута» = светлая, а не тёмная).
 */
export function currentTheme(): Theme {
  const v = document.documentElement.dataset.theme;
  return v === 'dark' || v === 'light' ? v : DEFAULT_THEME;
}

/** Задан ли на `<html>` валидный `data-theme` (иначе нужен self-heal, ADR-046 §4.3). */
function hasValidThemeAttr(): boolean {
  const v = document.documentElement.dataset.theme;
  return v === 'dark' || v === 'light';
}

/**
 * Проставить `data-theme` на `<html>` (без записи в storage), синхронизировать
 * `<meta name="theme-color">` (браузерный chrome следует теме, ADR-033) и уведомить
 * подписчиков.
 */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', THEME_COLOR[theme]);
  emitThemeChange();
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
 * Реактивное значение активной темы (read-only). Используется там, где тему нужно
 * подставить литералом и перерисовать при переключении — sandbox-iframe тела письма
 * (собственный документ, CSS-переменные родителя в него не наследуются, ADR-047 §6).
 */
export function useThemeValue(): Theme {
  return useSyncExternalStore(subscribeTheme, currentTheme, () => DEFAULT_THEME);
}

/**
 * Хук темы для хэдера `AppLayout`. Значение — из `data-theme` на `<html>` (проставлен
 * статическим `/theme-init.js`). **Self-heal (ADR-046 §4.3):** если при монтировании
 * атрибута нет или в нём мусор (скрипт не отработал — CSP/JS выключен/ошибка), хук сам
 * вызывает `applyTheme(resolveTheme())`; при штатно отработавшем скрипте — no-op.
 * За сменой темы ОС приложение НЕ следует (ADR-041). `toggle` пишет явный выбор в
 * `localStorage` и «залипает».
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useThemeValue();

  useEffect(() => {
    if (!hasValidThemeAttr()) applyTheme(resolveTheme());
  }, []);

  const toggle = useCallback(() => {
    persistTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }, []);

  return { theme, toggle };
}
