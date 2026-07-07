import { useEffect } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { getMe, login } from '@/features/auth/api';
import { useAuthStore } from '@/store/auth';
import type { LoginRequest } from '@/types/api';

export const meKey = ['me'] as const;

/**
 * Мутация входа: при успехе кладёт токен в стор (память + sessionStorage),
 * затем догружает права принципала через GET /api/auth/me и заполняет стор
 * (ADR-021, 08-design-system.md «Гейтинг»). Ошибка /me не валит вход — гейтинг
 * доуточнит useMe после навигации.
 */
export function useLogin() {
  const setSession = useAuthStore((s) => s.setSession);
  const setPrincipal = useAuthStore((s) => s.setPrincipal);
  return useMutation({
    mutationFn: (payload: LoginRequest) => login(payload),
    onSuccess: async (data, variables) => {
      setSession(data.access_token, variables.username);
      try {
        const me = await getMe();
        setPrincipal(me);
      } catch {
        // Права доуточнит useMe на защищённых страницах; вход не блокируется.
      }
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
 * Доступ к странице «Пользователи» (RBAC-администрирование): супер-админ или
 * роль `admin`. Гейтится не матрицей, а признаком admin (04-api.md, ADR-021).
 */
export function useIsAdmin(): boolean {
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const role = useAuthStore((s) => s.role);
  return isSuperadmin || role === 'admin';
}
