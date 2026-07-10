import { create } from 'zustand';

/**
 * Изолированный in-memory auth-store Telegram Mini App почты (`/tg/mail`, ADR-044 §7).
 *
 * Намеренно ОТДЕЛЁН от админского `store/auth` (`crm.auth.*` в localStorage, ADR-041) и
 * от SMS Mini App-стора: SSO-JWT не должен смешиваться с сессией администратора, а Mini
 * App не должна триггерить админский login-редирект/`AppLayout`. Токен держим только в
 * памяти (без persist) — Mini App беспарольно ре-аутентифицируется при каждом открытии
 * по кнопке бота (`POST /api/mail/telegram/auth`).
 */
interface MailMiniAppAuthState {
  token: string | null;
  telegramUserId: number | null;
  setSession: (token: string, telegramUserId: number) => void;
  clear: () => void;
}

export const useMailMiniAppAuthStore = create<MailMiniAppAuthState>((set) => ({
  token: null,
  telegramUserId: null,
  setSession: (token, telegramUserId) => set({ token, telegramUserId }),
  clear: () => set({ token: null, telegramUserId: null }),
}));

/** Не-реактивный доступ к SSO-токену Mini App почты (для api-слоя вне React). */
export function getMailMiniAppToken(): string | null {
  return useMailMiniAppAuthStore.getState().token;
}
