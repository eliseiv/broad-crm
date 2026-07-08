import { env } from '@/lib/env';
import { clearSession, getToken } from '@/store/auth';
import type { ApiErrorBody } from '@/types/api';

/** Ошибка API с распарсенным телом (04-api.md, единый формат ошибки). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Array<{ field: string; message: string }> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Array<{ field: string; message: string }> | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** Эндпоинт не требует JWT (например, /auth/login). */
  skipAuth?: boolean;
  /**
   * Явный Bearer-токен вместо токена сессии из стора (например, limited-scope
   * setup-token для POST /api/auth/set-password, ADR-025). Игнорируется при skipAuth.
   */
  authToken?: string;
  signal?: AbortSignal;
}

/**
 * Контракт: env.apiBaseUrl — origin БЕЗ '/api' (пусто для same-origin).
 * '/api' добавляется здесь ровно один раз. base='' + '/auth/login' → '/api/auth/login';
 * base='https://x' + '/auth/login' → 'https://x/api/auth/login'. Двойного '/api' быть не должно.
 */
function buildUrl(path: string): string {
  const base = env.apiBaseUrl || '';
  return `${base}/api${path}`;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = 'internal_error';
  let message = 'Произошла ошибка. Попробуйте ещё раз.';
  let details: ApiError['details'] = null;
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? null;
    }
  } catch {
    // тело не JSON — оставляем дефолтное сообщение
  }
  return new ApiError(res.status, code, message, details);
}

/**
 * Централизованный fetch-клиент. Добавляет Authorization, парсит ошибки,
 * на 401 сбрасывает сессию (редирект на /login выполняет роутер).
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, skipAuth = false, authToken, signal } = options;
  const headers: Record<string, string> = {};

  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (!skipAuth) {
    const token = authToken ?? getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(buildUrl(path), {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });

  if (res.status === 401 && !skipAuth) {
    clearSession();
    throw new ApiError(401, 'unauthorized', 'Сессия истекла. Войдите снова.');
  }

  // 403 (недостаточно прав, RBAC): сессию НЕ сбрасываем (в отличие от 401) —
  // пробрасываем ApiError для toast «Недостаточно прав» (08-design-system.md,
  // 04-api.md forbidden). Фолбэк-сообщение, если тело отсутствует.
  if (res.status === 403) {
    const err = await parseError(res);
    throw new ApiError(
      403,
      err.code === 'internal_error' ? 'forbidden' : err.code,
      err.message === 'Произошла ошибка. Попробуйте ещё раз.' ? 'Недостаточно прав' : err.message,
      err.details,
    );
  }

  if (!res.ok) {
    throw await parseError(res);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}
