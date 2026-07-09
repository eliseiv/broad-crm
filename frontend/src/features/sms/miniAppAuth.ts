import { create } from 'zustand';

/**
 * Изолированный in-memory auth-store операторской Mini App (`/tg/sms`, ADR-031).
 *
 * Намеренно ОТДЕЛЁН от админского `store/auth` (`crm.auth.*` в localStorage, ADR-041):
 * SSO-JWT оператора не должен смешиваться с сессией администратора, а Mini App не
 * должна триггерить админский login-редирект/`AppLayout`. Токен держим только в
 * памяти (без persist) — Mini App беспарольно ре-аутентифицируется при каждом
 * открытии по кнопке бота (`POST /api/sms/telegram/auth`).
 */
interface MiniAppAuthState {
  token: string | null;
  telegramUserId: number | null;
  setSession: (token: string, telegramUserId: number) => void;
  clear: () => void;
}

export const useMiniAppAuthStore = create<MiniAppAuthState>((set) => ({
  token: null,
  telegramUserId: null,
  setSession: (token, telegramUserId) => set({ token, telegramUserId }),
  clear: () => set({ token: null, telegramUserId: null }),
}));

/** Не-реактивный доступ к SSO-токену Mini App (для api-слоя вне React). */
export function getMiniAppToken(): string | null {
  return useMiniAppAuthStore.getState().token;
}
