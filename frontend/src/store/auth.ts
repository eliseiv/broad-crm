import { create } from 'zustand';
import type { MeResponse, PermissionsMap } from '@/types/api';

const STORAGE_KEY = 'crm.auth.token';
const USER_KEY = 'crm.auth.username';
const ROLE_KEY = 'crm.auth.role';
const SUPERADMIN_KEY = 'crm.auth.superadmin';
const SEES_ALL_SMS_KEY = 'crm.auth.seesAllSmsTeams';
const PERMISSIONS_KEY = 'crm.auth.permissions';

/**
 * Токен в памяти (Zustand). Для переживания перезагрузки — sessionStorage,
 * НЕ localStorage (05-security.md, modules/auth). Очищается на 401/logout.
 * Права принципала (role/permissions/is_superadmin) из GET /api/auth/me тоже
 * персистятся в sessionStorage (crm.auth.*), чтобы UI-гейтинг работал сразу
 * после reload до перезапроса /me (ADR-021, 08-design-system.md «Гейтинг»).
 */
function readString(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function readPermissions(): PermissionsMap | null {
  const raw = readString(PERMISSIONS_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as PermissionsMap) : null;
  } catch {
    return null;
  }
}

function persistToken(token: string | null, username: string | null): void {
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
    if (username) sessionStorage.setItem(USER_KEY, username);
    else sessionStorage.removeItem(USER_KEY);
  } catch {
    // sessionStorage недоступен (приватный режим) — работаем только в памяти.
  }
}

function persistPrincipal(
  role: string | null,
  isSuperadmin: boolean,
  seesAllSmsTeams: boolean,
  permissions: PermissionsMap | null,
): void {
  try {
    if (role) sessionStorage.setItem(ROLE_KEY, role);
    else sessionStorage.removeItem(ROLE_KEY);
    sessionStorage.setItem(SUPERADMIN_KEY, isSuperadmin ? '1' : '0');
    sessionStorage.setItem(SEES_ALL_SMS_KEY, seesAllSmsTeams ? '1' : '0');
    if (permissions) sessionStorage.setItem(PERMISSIONS_KEY, JSON.stringify(permissions));
    else sessionStorage.removeItem(PERMISSIONS_KEY);
  } catch {
    // sessionStorage недоступен — работаем только в памяти.
  }
}

function clearStorage(): void {
  try {
    for (const key of [
      STORAGE_KEY,
      USER_KEY,
      ROLE_KEY,
      SUPERADMIN_KEY,
      SEES_ALL_SMS_KEY,
      PERMISSIONS_KEY,
    ]) {
      sessionStorage.removeItem(key);
    }
  } catch {
    // no-op
  }
}

interface AuthState {
  token: string | null;
  username: string | null;
  /** Имя роли принципала (для супер-админа — "admin"); null до загрузки /me. */
  role: string | null;
  /** true — .env-супер-админ (полный доступ). */
  isSuperadmin: boolean;
  /**
   * Производный admin-уровень видимости SMS (ADR-036): виден ли фильтр «Все команды»
   * на /sms. Источник — `me.sees_all_sms_teams` (backend); фронт не вычисляет сам.
   */
  seesAllSmsTeams: boolean;
  /** Права `{ page: [actions] }` из /api/auth/me; null до загрузки. */
  permissions: PermissionsMap | null;
  isAuthenticated: boolean;
  /** Устанавливает токен/логин после успешного входа (права — через setPrincipal). */
  setSession: (token: string, username: string) => void;
  /** Заполняет профиль/права из GET /api/auth/me (login и refresh на reload). */
  setPrincipal: (me: MeResponse) => void;
  clearSession: () => void;
}

export const useAuthStore = create<AuthState>((set) => {
  const initialToken = readString(STORAGE_KEY);
  const initialUser = readString(USER_KEY);
  const initialRole = readString(ROLE_KEY);
  const initialSuperadmin = readString(SUPERADMIN_KEY) === '1';
  const initialSeesAllSms = readString(SEES_ALL_SMS_KEY) === '1';
  const initialPermissions = readPermissions();
  return {
    token: initialToken,
    username: initialUser,
    role: initialRole,
    isSuperadmin: initialSuperadmin,
    seesAllSmsTeams: initialSeesAllSms,
    permissions: initialPermissions,
    isAuthenticated: Boolean(initialToken),
    setSession: (token, username) => {
      persistToken(token, username);
      set({ token, username, isAuthenticated: true });
    },
    setPrincipal: (me) => {
      persistPrincipal(me.role, me.is_superadmin, me.sees_all_sms_teams, me.permissions);
      set({
        username: me.username,
        role: me.role,
        isSuperadmin: me.is_superadmin,
        seesAllSmsTeams: me.sees_all_sms_teams,
        permissions: me.permissions,
      });
    },
    clearSession: () => {
      clearStorage();
      set({
        token: null,
        username: null,
        role: null,
        isSuperadmin: false,
        seesAllSmsTeams: false,
        permissions: null,
        isAuthenticated: false,
      });
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
