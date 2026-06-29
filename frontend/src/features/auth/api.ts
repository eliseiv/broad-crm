import { apiRequest } from '@/lib/api';
import type { LoginRequest, LoginResponse } from '@/types/api';

export function login(payload: LoginRequest): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    skipAuth: true,
  });
}
