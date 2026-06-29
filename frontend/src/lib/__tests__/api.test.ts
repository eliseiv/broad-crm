import { beforeEach, describe, expect, it, vi } from 'vitest';
import { login } from '@/features/auth/api';
import { createServer, deleteServer, getServerStatus, listServers } from '@/features/servers/api';
import { apiRequest } from '@/lib/api';
import { useAuthStore } from '@/store/auth';

describe('api client and endpoint wrappers', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.doUnmock('@/lib/env');
    vi.doUnmock('@/store/auth');
    vi.unstubAllGlobals();
    useAuthStore.getState().clearSession();
  });

  it('adds Authorization for protected endpoints and uses documented paths', async () => {
    useAuthStore.getState().setSession('jwt-token', 'admin');
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith('/status')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: 'server-1',
              provision_status: 'installing',
              error_message: null,
              updated_at: '2026-06-28T12:00:00Z',
            }),
          ),
        );
      }
      if (url === '/api/servers/server-1') {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.resolve(new Response(JSON.stringify({ items: [] })));
    });
    vi.stubGlobal('fetch', fetchMock);

    await listServers();
    await getServerStatus('server-1');
    await createServer({
      name: 'Server',
      ip: '10.0.0.10',
      ssh_user: 'root',
      ssh_password: 'secret',
    });
    await deleteServer('server-1');

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/servers',
      expect.objectContaining({ method: 'GET', headers: { Authorization: 'Bearer jwt-token' } }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/servers/server-1/status',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/servers',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/servers/server-1',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain('/api/servers/server-1/metrics');
  });

  it('does not send Authorization on login', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ access_token: 'jwt', token_type: 'bearer', expires_in: 3600 }),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);

    await login({ username: 'admin', password: 'secret' });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });

  it.each([
    ['', '/api/auth/login'],
    ['https://crm.example.com', 'https://crm.example.com/api/auth/login'],
  ])('builds API URL once for origin %s', async (apiBaseUrl, expectedUrl) => {
    vi.resetModules();
    vi.doMock('@/lib/env', () => ({ env: { apiBaseUrl } }));
    vi.doMock('@/store/auth', () => ({
      clearSession: vi.fn(),
      getToken: () => null,
    }));
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ access_token: 'jwt', token_type: 'bearer', expires_in: 3600 }),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);
    const { apiRequest } = await import('@/lib/api');

    await apiRequest('/auth/login', {
      method: 'POST',
      body: { username: 'admin', password: 'secret' },
      skipAuth: true,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expectedUrl,
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock.mock.calls[0][0]).not.toContain('/api/api/');
  });

  it('parses API errors and clears session on protected 401', async () => {
    useAuthStore.getState().setSession('jwt-token', 'admin');
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { code: 'unauthorized', message: 'Требуется авторизация' } }),
            { status: 401 },
          ),
        ),
    );

    await expect(apiRequest('/servers')).rejects.toMatchObject({
      status: 401,
      code: 'unauthorized',
    });
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
