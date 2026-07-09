/**
 * Типы self-hosted Telegram WebApp SDK (`/telegram-web-app.js`, ADR-031).
 * Описываем только поверхность, используемую операторской Mini App (`/tg/sms`):
 * `initData` (аутентификатор SSO), `themeParams` (нативный вид), `ready`/`expand`,
 * подписка `themeChanged`. Полный SDK богаче — типизируем минимум.
 */
export {};

declare global {
  /** themeParams Telegram (snake_case-ключи) → CSS custom properties Mini App. */
  interface TelegramWebAppThemeParams {
    bg_color?: string;
    secondary_bg_color?: string;
    text_color?: string;
    hint_color?: string;
    link_color?: string;
    button_color?: string;
    button_text_color?: string;
    [key: string]: string | undefined;
  }

  interface TelegramWebApp {
    /** Raw initData (query-string) — аутентификатор SSO. Пусто вне Telegram. */
    initData: string;
    initDataUnsafe?: Record<string, unknown>;
    version?: string;
    platform?: string;
    colorScheme?: 'light' | 'dark';
    themeParams?: TelegramWebAppThemeParams;
    isExpanded?: boolean;
    ready: () => void;
    expand: () => void;
    onEvent?: (eventType: string, handler: () => void) => void;
    offEvent?: (eventType: string, handler: () => void) => void;
  }

  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}
