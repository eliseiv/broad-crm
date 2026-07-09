import { useEffect } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { getMe, login, setPassword } from '@/features/auth/api';
import { useAuthStore } from '@/store/auth';
import type { LoginRequest, LoginSuccessResponse, SetPasswordRequest } from '@/types/api';

export const meKey = ['me'] as const;

/** Кладёт access-токен в стор и догружает права принципала (общий хвост входа). */
async function establishSession(
  data: LoginSuccessResponse,
  username: string,
  setSession: (token: string, username: string) => void,
  setPrincipal: (me: Awaited<ReturnType<typeof getMe>>) => void,
): Promise<void> {
  setSession(data.access_token, username);
  try {
    const me = await getMe();
    setPrincipal(me);
  } catch {
    // Права доуточнит useMe на защищённых страницах; вход не блокируется.
  }
}

/**
 * Мутация входа: при успехе-`false` кладёт токен в стор (память + sessionStorage)
 * и догружает права принципала через GET /api/auth/me (ADR-021, «Гейтинг»). При
 * `password_setup_required:true` сессия НЕ устанавливается — компонент показывает
 * окно «Придумайте пароль» с setup-токеном (ADR-025/ADR-029). Ошибка /me не валит вход.
 */
export function useLogin() {
  const setSession = useAuthStore((s) => s.setSession);
  const setPrincipal = useAuthStore((s) => s.setPrincipal);
  return useMutation({
    mutationFn: (payload: LoginRequest) => login(payload),
    onSuccess: async (data, variables) => {
      if (data.password_setup_required) return; // ветку setup ведёт компонент
      await establishSession(data, variables.username, setSession, setPrincipal);
    },
  });
}

/**
 * Мутация установки пароля «первого входа» (ADR-025): POST /api/auth/set-password
 * с setup-токеном. Успех → пользователь сразу залогинен (access-токен) + права.
 */
export function useSetPassword() {
  const setSession = useAuthStore((s) => s.setSession);
  const setPrincipal = useAuthStore((s) => s.setPrincipal);
  return useMutation({
    mutationFn: (vars: { payload: SetPasswordRequest; setupToken: string; username: string }) =>
      setPassword(vars.payload, vars.setupToken),
    onSuccess: async (data, variables) => {
      await establishSession(data, variables.username, setSession, setPrincipal);
    },
  });
}

/**
 * Загрузка/обновление прав принципала на защищённых страницах. Права могут
 * измениться без пере-логина (принципал грузится из БД на каждый запрос,
 * ADR-021) — поэтому обновляем на маунте AppLayout и наполняем стор.
 */
export function useMe() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setPrincipal = useAuthStore((s) => s.setPrincipal);
  const query = useQuery({
    queryKey: meKey,
    queryFn: ({ signal }) => getMe(signal),
    enabled: isAuthenticated,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (query.data) setPrincipal(query.data);
  }, [query.data, setPrincipal]);

  return query;
}

/**
 * Хук доступа к действию на странице (UI-гейтинг, только UX). Супер-админ —
 * всегда true; иначе `action` ∈ `permissions[page]`. Безопасность — на сервере
 * (403). 08-design-system.md «Гейтинг навигации и действий».
 */
export function useCan(page: string, action: string): boolean {
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const permissions = useAuthStore((s) => s.permissions);
  if (isSuperadmin) return true;
  return Boolean(permissions?.[page]?.includes(action));
}

/**
 * Admin-уровень видимости SMS (ADR-036): виден ли фильтр «Все команды» на /sms.
 * Источник — `me.sees_all_sms_teams` из GET /api/auth/me (backend вычисляет
 * `is_superadmin OR полный каталог`); фронт НЕ дублирует предикат. UI-гейтинг, только UX.
 */
export function useSeesAllSmsTeams(): boolean {
  return useAuthStore((s) => s.seesAllSmsTeams);
}

/**
 * Доступ к странице «Пользователи» (RBAC-администрирование): супер-админ или
 * роль `admin`. Гейтится не матрицей, а признаком admin (04-api.md, ADR-021).
 */
export function useIsAdmin(): boolean {
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const role = useAuthStore((s) => s.role);
  return isSuperadmin || role === 'admin';
}

/**
 * Page-level view-guard для permission-gated страницы (UI-гейтинг, только UX).
 * Супер-админ и `role=="admin"` видят все страницы; иначе доступ ⇔ `view` ∈
 * `permissions[page]`. Единый источник безопасности — серверный `403`
 * (ADR-021 §6, 08-design-system.md «Page-level view-guard»).
 */
export function useCanViewPage(page: string): boolean {
  const isAdmin = useIsAdmin();
  const canView = useCan(page, 'view');
  return isAdmin || canView;
}
