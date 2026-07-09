import { useAuthStore } from '@/store/auth';
import type { PermissionsMap } from '@/types/api';

/**
 * Тест-хелперы для установки принципала в auth-стор (ADR-021). После ввода RBAC
 * `setSession` больше НЕ задаёт права — они приходят из GET /api/auth/me через
 * `setPrincipal`. UI-гейтинг (useCan/useIsAdmin, вкладки, кнопки) читает
 * role/permissions/is_superadmin из стора, поэтому тестам нужно задать принципала.
 */
export function loginAs(options?: {
  username?: string;
  role?: string;
  isSuperadmin?: boolean;
  /**
   * Admin-уровень видимости SMS (ADR-036, MeResponse.sees_all_sms_teams). По
   * умолчанию совпадает с `isSuperadmin` (backend: `is_superadmin OR полный
   * каталог`): супер-админ видит все SMS-команды, ограниченная роль — нет.
   */
  seesAllSmsTeams?: boolean;
  /**
   * Admin-уровень видимости почты (ADR-038 §3, MeResponse.sees_all_mail_teams). По
   * умолчанию совпадает с `isSuperadmin` (backend: `is_superadmin OR полный каталог`):
   * супер-админ видит все почтовые команды, ограниченная роль — нет.
   */
  seesAllMailTeams?: boolean;
  permissions?: PermissionsMap;
}): void {
  const {
    username = 'admin',
    role = 'admin',
    isSuperadmin = true,
    seesAllSmsTeams = isSuperadmin,
    seesAllMailTeams = isSuperadmin,
    permissions = {},
  } = options ?? {};
  const store = useAuthStore.getState();
  store.setSession('jwt-token', username);
  store.setPrincipal({
    username,
    role,
    is_superadmin: isSuperadmin,
    sees_all_sms_teams: seesAllSmsTeams,
    sees_all_mail_teams: seesAllMailTeams,
    permissions,
  });
}

/** Супер-админ: полный доступ ко всем вкладкам/действиям (is_superadmin=true). */
export function loginSuperadmin(): void {
  loginAs({ isSuperadmin: true });
}

/** Сбрасывает сессию и права (разлогин). */
export function logout(): void {
  useAuthStore.getState().clearSession();
}
