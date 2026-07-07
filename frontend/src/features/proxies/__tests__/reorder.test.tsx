import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { proxiesKey, useReorderProxies } from '@/features/proxies/hooks';
import type { Proxy } from '@/types/api';

const proxiesApi = vi.hoisted(() => ({
  listProxies: vi.fn(),
  createProxy: vi.fn(),
  updateProxy: vi.fn(),
  reorderProxies: vi.fn(),
  getProxyStatus: vi.fn(),
  deleteProxy: vi.fn(),
}));

vi.mock('@/features/proxies/api', () => proxiesApi);
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let queryClient: QueryClient;

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function proxy(id: string, position: number): Proxy {
  return {
    id,
    name: id,
    proxy_type: 'http',
    host: 'host',
    port: 8080,
    username: null,
    has_password: false,
    check_status: 'working',
    error_message: null,
    position,
    last_checked_at: null,
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T09:00:00Z',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});

describe('useReorderProxies', () => {
  it('sends { ids } body and optimistically rewrites position by new order', async () => {
    proxiesApi.reorderProxies.mockResolvedValue(undefined);
    queryClient.setQueryData(proxiesKey, {
      items: [proxy('a', 0), proxy('b', 1), proxy('c', 2)],
    });

    const { result } = renderHook(() => useReorderProxies(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['c', 'a', 'b']);
    });

    // Тело запроса — полный упорядоченный список id (04-api.md).
    expect(proxiesApi.reorderProxies).toHaveBeenCalledWith({ ids: ['c', 'a', 'b'] });

    // Оптимистично position переписан по индексу в новом порядке.
    const cached = queryClient.getQueryData<{ items: Proxy[] }>(proxiesKey);
    const byId = Object.fromEntries((cached?.items ?? []).map((p) => [p.id, p.position]));
    expect(byId).toEqual({ c: 0, a: 1, b: 2 });
  });

  it('rolls back the cache and toasts on API error', async () => {
    proxiesApi.reorderProxies.mockRejectedValue(new Error('network'));
    const previous = [proxy('a', 0), proxy('b', 1)];
    queryClient.setQueryData(proxiesKey, { items: previous });

    const { result } = renderHook(() => useReorderProxies(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['b', 'a']).catch(() => undefined);
    });

    // Откат: порядок/position восстановлены к исходным.
    const cached = queryClient.getQueryData<{ items: Proxy[] }>(proxiesKey);
    expect(cached?.items.map((p) => [p.id, p.position])).toEqual([
      ['a', 0],
      ['b', 1],
    ]);
    expect(toast.error).toHaveBeenCalledWith('Не удалось сохранить порядок');
  });
});
