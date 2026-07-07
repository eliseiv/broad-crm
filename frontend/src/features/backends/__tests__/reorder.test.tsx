import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { backendsKey, useReorderBackends } from '@/features/backends/hooks';
import type { Backend } from '@/types/api';

const backendsApi = vi.hoisted(() => ({
  listBackends: vi.fn(),
  createBackend: vi.fn(),
  updateBackend: vi.fn(),
  reorderBackends: vi.fn(),
  getBackendStatus: vi.fn(),
  deleteBackend: vi.fn(),
}));

vi.mock('@/features/backends/api', () => backendsApi);
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let queryClient: QueryClient;

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function backend(id: string, position: number): Backend {
  return {
    id,
    code: id,
    name: id,
    domain: `${id}.example.com`,
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

describe('useReorderBackends', () => {
  it('sends { ids } body and optimistically rewrites position by new order', async () => {
    backendsApi.reorderBackends.mockResolvedValue(undefined);
    queryClient.setQueryData(backendsKey, {
      items: [backend('a', 0), backend('b', 1), backend('c', 2)],
    });

    const { result } = renderHook(() => useReorderBackends(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['c', 'a', 'b']);
    });

    // Тело запроса — полный упорядоченный список id (04-api.md).
    expect(backendsApi.reorderBackends).toHaveBeenCalledWith({ ids: ['c', 'a', 'b'] });

    // Оптимистично position переписан по индексу в новом порядке.
    const cached = queryClient.getQueryData<{ items: Backend[] }>(backendsKey);
    const byId = Object.fromEntries((cached?.items ?? []).map((b) => [b.id, b.position]));
    expect(byId).toEqual({ c: 0, a: 1, b: 2 });
  });

  it('rolls back the cache and toasts on API error', async () => {
    backendsApi.reorderBackends.mockRejectedValue(new Error('network'));
    const previous = [backend('a', 0), backend('b', 1)];
    queryClient.setQueryData(backendsKey, { items: previous });

    const { result } = renderHook(() => useReorderBackends(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['b', 'a']).catch(() => undefined);
    });

    // Откат: порядок/position восстановлены к исходным.
    const cached = queryClient.getQueryData<{ items: Backend[] }>(backendsKey);
    expect(cached?.items.map((b) => [b.id, b.position])).toEqual([
      ['a', 0],
      ['b', 1],
    ]);
    expect(toast.error).toHaveBeenCalledWith('Не удалось сохранить порядок');
  });
});
