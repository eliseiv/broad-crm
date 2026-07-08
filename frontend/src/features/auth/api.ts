import { apiRequest } from '@/lib/api';
import type {
  LoginRequest,
  LoginResponse,
  LoginSuccessResponse,
  MeResponse,
  SetPasswordRequest,
} from '@/types/api';

export function login(payload: LoginRequest): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    skipAuth: true,
  });
}

/**
 * POST /api/auth/set-password — установка пароля «первого входа» беспарольным
 * пользователем (модель «открытого первого входа», ADR-025). Auth — Bearer
 * setup-token из ответа login (limited-scope). Ответ — обычный успех (access-token).
 */
export function setPassword(
  payload: SetPasswordRequest,
  setupToken: string,
): Promise<LoginSuccessResponse> {
  return apiRequest<LoginSuccessResponse>('/auth/set-password', {
    method: 'POST',
    body: payload,
    authToken: setupToken,
  });
}

/** GET /api/auth/me — профиль + права принципала для UI-гейтинга (04-api.md, ADR-021). */
export function getMe(signal?: AbortSignal): Promise<MeResponse> {
  return apiRequest<MeResponse>('/auth/me', { signal });
}
