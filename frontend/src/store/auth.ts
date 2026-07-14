import { create } from 'zustand';
import type { MeResponse, PermissionsMap, TeamRef } from '@/types/api';

const STORAGE_KEY = 'crm.auth.token';
const USER_KEY = 'crm.auth.username';
const ROLE_KEY = 'crm.auth.role';
const SUPERADMIN_KEY = 'crm.auth.superadmin';
const SEES_ALL_SMS_KEY = 'crm.auth.seesAllSmsTeams';
const SEES_ALL_MAIL_KEY = 'crm.auth.seesAllMailTeams';
const PERMISSIONS_KEY = 'crm.auth.permissions';
// Эффективный scope команд каналов из /me (ADR-055 §5.1) — ЕДИНСТВЕННЫЙ источник опций
// команд канала на клиенте (§6.3). Персистится вместе с прочими признаками принципала,
// чтобы контролы не «мигали» пустыми до резолва /me в новой вкладке.
const MAIL_TEAMS_KEY = 'crm.auth.mailTeams';
const SMS_TEAMS_KEY = 'crm.auth.smsTeams';
const MAIL_UNASSIGNED_KEY = 'crm.auth.mailIncludesUnassigned';
const SMS_UNASSIGNED_KEY = 'crm.auth.smsIncludesUnassigned';

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

/** Список команд канала из localStorage (битое/чужое значение → пустой список). */
function readTeams(key: string): TeamRef[] {
  const raw = readString(key);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (t): t is TeamRef =>
        typeof t === 'object' &&
        t !== null &&
        typeof (t as TeamRef).id === 'string' &&
        typeof (t as TeamRef).name === 'string',
    );
  } catch {
    return [];
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

function persistPrincipal(me: MeResponse): void {
  try {
    if (me.role) localStorage.setItem(ROLE_KEY, me.role);
    else localStorage.removeItem(ROLE_KEY);
    localStorage.setItem(SUPERADMIN_KEY, me.is_superadmin ? '1' : '0');
    localStorage.setItem(SEES_ALL_SMS_KEY, me.sees_all_sms_teams ? '1' : '0');
    localStorage.setItem(SEES_ALL_MAIL_KEY, me.sees_all_mail_teams ? '1' : '0');
    localStorage.setItem(MAIL_TEAMS_KEY, JSON.stringify(me.mail_teams));
    localStorage.setItem(SMS_TEAMS_KEY, JSON.stringify(me.sms_teams));
    localStorage.setItem(MAIL_UNASSIGNED_KEY, me.mail_includes_unassigned ? '1' : '0');
    localStorage.setItem(SMS_UNASSIGNED_KEY, me.sms_includes_unassigned ? '1' : '0');
    if (me.permissions) localStorage.setItem(PERMISSIONS_KEY, JSON.stringify(me.permissions));
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
      MAIL_TEAMS_KEY,
      SMS_TEAMS_KEY,
      MAIL_UNASSIGNED_KEY,
      SMS_UNASSIGNED_KEY,
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
  /**
   * ЭФФЕКТИВНЫЙ scope команд канала «Почты» (`me.mail_teams`, ADR-055 §5.1) — единственный
   * источник опций команд канала в UI (§6.3): фильтр «Команда», селектор формы ящика,
   * резолв имени команды, дропдаун переноса. `GET /api/teams` для этого не используется.
   */
  mailTeams: TeamRef[];
  /** То же для канала «СМС» (`me.sms_teams`). */
  smsTeams: TeamRef[];
  /** `me.mail_includes_unassigned` — доступны ли объекты почты без команды. */
  mailIncludesUnassigned: boolean;
  /** `me.sms_includes_unassigned` — доступны ли номера/сообщения без команды. */
  smsIncludesUnassigned: boolean;
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
    mailTeams: readTeams(MAIL_TEAMS_KEY),
    smsTeams: readTeams(SMS_TEAMS_KEY),
    mailIncludesUnassigned: readString(MAIL_UNASSIGNED_KEY) === '1',
    smsIncludesUnassigned: readString(SMS_UNASSIGNED_KEY) === '1',
    permissions: initialPermissions,
    isAuthenticated: Boolean(initialToken),
    setSession: (token, username) => {
      persistToken(token, username);
      set({ token, username, isAuthenticated: true });
    },
    setPrincipal: (me) => {
      persistPrincipal(me);
      set({
        username: me.username,
        role: me.role,
        isSuperadmin: me.is_superadmin,
        seesAllSmsTeams: me.sees_all_sms_teams,
        seesAllMailTeams: me.sees_all_mail_teams,
        mailTeams: me.mail_teams,
        smsTeams: me.sms_teams,
        mailIncludesUnassigned: me.mail_includes_unassigned,
        smsIncludesUnassigned: me.sms_includes_unassigned,
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
        mailTeams: [],
        smsTeams: [],
        mailIncludesUnassigned: false,
        smsIncludesUnassigned: false,
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
