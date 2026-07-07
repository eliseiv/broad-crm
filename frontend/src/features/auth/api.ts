import { apiRequest } from '@/lib/api';
import type { LoginRequest, LoginResponse, MeResponse } from '@/types/api';

export function login(payload: LoginRequest): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    skipAuth: true,
  });
}

/** GET /api/auth/me — профиль + права принципала для UI-гейтинга (04-api.md, ADR-021). */
export function getMe(signal?: AbortSignal): Promise<MeResponse> {
  return apiRequest<MeResponse>('/auth/me', { signal });
}
