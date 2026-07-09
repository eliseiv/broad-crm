/**
 * Загрузка self-hosted Telegram WebApp SDK и применение `themeParams` (ADR-031).
 *
 * SDK вендорится как статика своего origin (`/telegram-web-app.js`,
 * `frontend/public/`) — CSP `script-src 'self'` НЕ ослабляется. Подключаем скрипт
 * динамически ТОЛЬКО на маршруте `/tg/sms` (не в глобальном админ-shell), чтобы не
 * тянуть SDK в общий бандл. Тема наследуется от клиента Telegram (нативный вид,
 * светлая/тёмная по Telegram); при недоступности — нейтральный тёмный fallback.
 */

const SDK_SRC = '/telegram-web-app.js';

/** Нейтральный тёмный fallback токенов (open вне Telegram / нет themeParams). */
const FALLBACK_THEME = {
  bg_color: '#0a0c10',
  secondary_bg_color: '#12151c',
  text_color: '#e6e8ec',
  hint_color: '#8a8f98',
  link_color: '#4c82fb',
  button_color: '#4c82fb',
  button_text_color: '#ffffff',
};

let sdkPromise: Promise<TelegramWebApp | undefined> | null = null;

/**
 * Идемпотентно инжектит `/telegram-web-app.js` и резолвит `window.Telegram.WebApp`.
 * Повторные вызовы (StrictMode double-mount) переиспользуют один promise/скрипт.
 */
export function loadTelegramSdk(): Promise<TelegramWebApp | undefined> {
  if (window.Telegram?.WebApp) return Promise.resolve(window.Telegram.WebApp);
  if (sdkPromise) return sdkPromise;

  sdkPromise = new Promise<TelegramWebApp | undefined>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${SDK_SRC}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve(window.Telegram?.WebApp));
      existing.addEventListener('error', () => reject(new Error('telegram_sdk_load_failed')));
      return;
    }
    const script = document.createElement('script');
    script.src = SDK_SRC;
    script.async = true;
    script.onload = () => resolve(window.Telegram?.WebApp);
    script.onerror = () => reject(new Error('telegram_sdk_load_failed'));
    document.head.appendChild(script);
  });
  return sdkPromise;
}

/**
 * Применяет `themeParams` Telegram в CSS custom properties (`--tg-*`) на
 * `document.documentElement` — Mini App выглядит нативно и в светлой, и в тёмной
 * теме клиента. Пустые значения замещаются нейтральным тёмным fallback.
 */
export function applyTelegramTheme(wa: TelegramWebApp | undefined): void {
  const params = wa?.themeParams ?? {};
  const style = document.documentElement.style;
  const set = (cssVar: string, key: keyof typeof FALLBACK_THEME) => {
    style.setProperty(cssVar, params[key] || FALLBACK_THEME[key]);
  };
  set('--tg-bg', 'bg_color');
  set('--tg-secondary-bg', 'secondary_bg_color');
  set('--tg-text', 'text_color');
  set('--tg-hint', 'hint_color');
  set('--tg-link', 'link_color');
  set('--tg-button', 'button_color');
  set('--tg-button-text', 'button_text_color');
}
