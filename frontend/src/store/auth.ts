import { create } from 'zustand';
import type { MeResponse, PermissionsMap } from '@/types/api';

const STORAGE_KEY = 'crm.auth.token';
const USER_KEY = 'crm.auth.username';
const ROLE_KEY = 'crm.auth.role';
const SUPERADMIN_KEY = 'crm.auth.superadmin';
const SEES_ALL_SMS_KEY = 'crm.auth.seesAllSmsTeams';
const SEES_ALL_MAIL_KEY = 'crm.auth.seesAllMailTeams';
const PERMISSIONS_KEY = 'crm.auth.permissions';

/**
 * Токен в памяти (Zustand). Для переживания перезагрузки/закрытия браузера и шаринга
 * между вкладками — localStorage (ADR-041, амендмент 05-security.md/ADR-002/ADR-021):
 * прежний sessionStorage стирался при закрытии браузера и был изолирован по вкладке
 * (новая вкладка → редирект на /login). Токен живёт до истечения TTL JWT (24 ч);
 * 401/logout полностью очищают crm.auth.* во всех вкладках. Права принципала
 * (role/permissions/is_superadmin/seesAll*) из GET /api/auth/me тоже персистятся
 * в localStorage (crm.auth.*) → синхронная регидрация в `create()` наполняет стор ДО
 * резолва guard (ProtectedRoute видит сессию на первом рендере, в т.ч. в новой вкладке).
 */
function readString(key: string): string | null {
  try {
    return localStorage.getItem(key);
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
    if (token) localStorage.setItem(STORAGE_KEY, token);
    else localStorage.removeItem(STORAGE_KEY);
    if (username) localStorage.setItem(USER_KEY, username);
    else localStorage.removeItem(USER_KEY);
  } catch {
    // localStorage недоступен (приватный режим) — работаем только в памяти.
  }
}

function persistPrincipal(
  role: string | null,
  isSuperadmin: boolean,
  seesAllSmsTeams: boolean,
  seesAllMailTeams: boolean,
  permissions: PermissionsMap | null,
): void {
  try {
    if (role) localStorage.setItem(ROLE_KEY, role);
    else localStorage.removeItem(ROLE_KEY);
    localStorage.setItem(SUPERADMIN_KEY, isSuperadmin ? '1' : '0');
    localStorage.setItem(SEES_ALL_SMS_KEY, seesAllSmsTeams ? '1' : '0');
    localStorage.setItem(SEES_ALL_MAIL_KEY, seesAllMailTeams ? '1' : '0');
    if (permissions) localStorage.setItem(PERMISSIONS_KEY, JSON.stringify(permissions));
    else localStorage.removeItem(PERMISSIONS_KEY);
  } catch {
    // localStorage недоступен — работаем только в памяти.
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
      SEES_ALL_MAIL_KEY,
      PERMISSIONS_KEY,
    ]) {
      localStorage.removeItem(key);
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
  /**
   * Производный admin-уровень видимости почты (ADR-038): виден ли фильтр «Все команды»
   * на /mail. Источник — `me.sees_all_mail_teams` (backend); фронт не вычисляет сам.
   */
  seesAllMailTeams: boolean;
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
  const initialSeesAllMail = readString(SEES_ALL_MAIL_KEY) === '1';
  const initialPermissions = readPermissions();
  return {
    token: initialToken,
    username: initialUser,
    role: initialRole,
    isSuperadmin: initialSuperadmin,
    seesAllSmsTeams: initialSeesAllSms,
    seesAllMailTeams: initialSeesAllMail,
    permissions: initialPermissions,
    isAuthenticated: Boolean(initialToken),
    setSession: (token, username) => {
      persistToken(token, username);
      set({ token, username, isAuthenticated: true });
    },
    setPrincipal: (me) => {
      persistPrincipal(
        me.role,
        me.is_superadmin,
        me.sees_all_sms_teams,
        me.sees_all_mail_teams,
        me.permissions,
      );
      set({
        username: me.username,
        role: me.role,
        isSuperadmin: me.is_superadmin,
        seesAllSmsTeams: me.sees_all_sms_teams,
        seesAllMailTeams: me.sees_all_mail_teams,
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
        seesAllMailTeams: false,
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
