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
  permissions?: PermissionsMap;
}): void {
  const {
    username = 'admin',
    role = 'admin',
    isSuperadmin = true,
    permissions = {},
  } = options ?? {};
  const store = useAuthStore.getState();
  store.setSession('jwt-token', username);
  store.setPrincipal({ username, role, is_superadmin: isSuperadmin, permissions });
}

/** Супер-админ: полный доступ ко всем вкладкам/действиям (is_superadmin=true). */
export function loginSuperadmin(): void {
  loginAs({ isSuperadmin: true });
}

/** Сбрасывает сессию и права (разлогин). */
export function logout(): void {
  useAuthStore.getState().clearSession();
}
