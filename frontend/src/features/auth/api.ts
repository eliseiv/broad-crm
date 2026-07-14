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

/**
 * GET /api/auth/me — профиль + права принципала + ЭФФЕКТИВНЫЙ scope команд каналов
 * (`mail_teams`/`sms_teams`, `*_includes_unassigned` — ADR-055 §5.1). Единственный источник
 * опций команд канала на клиенте (§6.3), включая обе Mini App: там `GET /api/teams` запрещён.
 * `authToken`/`skipAuthReset` — для Mini App (изолированный SSO-JWT; 401 не роняет админ-стор).
 */
export function getMe(
  signal?: AbortSignal,
  authToken?: string,
  skipAuthReset?: boolean,
): Promise<MeResponse> {
  return apiRequest<MeResponse>('/auth/me', { signal, authToken, skipAuthReset });
}
