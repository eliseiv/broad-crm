import { create } from 'zustand';

const STORAGE_KEY = 'crm.auth.token';
const USER_KEY = 'crm.auth.username';

/**
 * Токен в памяти (Zustand). Для переживания перезагрузки — sessionStorage,
 * НЕ localStorage (05-security.md, modules/auth). Очищается на 401/logout.
 */
function readToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function readUsername(): string | null {
  try {
    return sessionStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

function persist(token: string | null, username: string | null): void {
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
    if (username) sessionStorage.setItem(USER_KEY, username);
    else sessionStorage.removeItem(USER_KEY);
  } catch {
    // sessionStorage недоступен (приватный режим) — работаем только в памяти.
  }
}

interface AuthState {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  setSession: (token: string, username: string) => void;
  clearSession: () => void;
}

export const useAuthStore = create<AuthState>((set) => {
  const initialToken = readToken();
  const initialUser = readUsername();
  return {
    token: initialToken,
    username: initialUser,
    isAuthenticated: Boolean(initialToken),
    setSession: (token, username) => {
      persist(token, username);
      set({ token, username, isAuthenticated: true });
    },
    clearSession: () => {
      persist(null, null);
      set({ token: null, username: null, isAuthenticated: false });
    },
  };
});

/** Не-реактивный доступ к токену (для api-client вне React). */
export function getToken(): string | null {
  return useAuthStore.getState().token;
}

export function clearSession(): void {
  useAuthStore.getState().clearSession();
}
