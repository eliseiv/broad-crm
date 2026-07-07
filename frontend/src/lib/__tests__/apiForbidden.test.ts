import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from '@/lib/api';
import { useAuthStore } from '@/store/auth';

describe('api client — 403 forbidden handling (RBAC, ADR-021)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.getState().clearSession();
  });

  it('does NOT clear the session on 403 and surfaces a forbidden ApiError', async () => {
    useAuthStore.getState().setSession('jwt-token', 'operator');
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { code: 'forbidden', message: 'Недостаточно прав' } }),
            { status: 403 },
          ),
        ),
    );

    const error = await apiRequest('/servers').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    expect((error as ApiError).code).toBe('forbidden');
    expect((error as ApiError).message).toBe('Недостаточно прав');
    // В отличие от 401 — сессия сохраняется (403 не разлогинивает, показываем «Недостаточно прав»).
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('falls back to a forbidden code/message when the 403 body is empty', async () => {
    useAuthStore.getState().setSession('jwt-token', 'operator');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 403 })));

    const error = (await apiRequest('/roles').catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(403);
    expect(error.code).toBe('forbidden');
    expect(error.message).toBe('Недостаточно прав');
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
});
