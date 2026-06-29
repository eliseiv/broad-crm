import { useMutation } from '@tanstack/react-query';
import { login } from '@/features/auth/api';
import { useAuthStore } from '@/store/auth';
import type { LoginRequest } from '@/types/api';

/** Мутация входа: при успехе кладёт токен в стор (память + sessionStorage). */
export function useLogin() {
  const setSession = useAuthStore((s) => s.setSession);
  return useMutation({
    mutationFn: (payload: LoginRequest) => login(payload),
    onSuccess: (data, variables) => {
      setSession(data.access_token, variables.username);
    },
  });
}
