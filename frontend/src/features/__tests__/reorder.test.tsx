import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { serversKey, useReorderServers } from '@/features/servers/hooks';
import { aiKeysKey, useReorderAiKeys } from '@/features/ai-keys/hooks';
import type { AiKey, Server } from '@/types/api';

const serversApi = vi.hoisted(() => ({
  listServers: vi.fn(),
  createServer: vi.fn(),
  updateServer: vi.fn(),
  reorderServers: vi.fn(),
  getServerStatus: vi.fn(),
  deleteServer: vi.fn(),
}));
const aiKeysApi = vi.hoisted(() => ({
  listAiKeys: vi.fn(),
  createAiKey: vi.fn(),
  updateAiKey: vi.fn(),
  reorderAiKeys: vi.fn(),
  getAiKeyStatus: vi.fn(),
  deleteAiKey: vi.fn(),
}));

vi.mock('@/features/servers/api', () => serversApi);
vi.mock('@/features/ai-keys/api', () => aiKeysApi);
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let queryClient: QueryClient;

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function server(id: string, position: number): Server {
  return {
    id,
    name: id,
    ip: '10.0.0.1',
    exporter_port: 9100,
    provision_status: 'online',
    position,
    online: false,
    uptime_seconds: null,
    last_updated: null,
    metrics: null,
  };
}

function aiKey(id: string, provider: AiKey['provider'], position: number): AiKey {
  return {
    id,
    name: id,
    provider,
    key_masked: 'sk-p…bA3T',
    check_status: 'working',
    error_message: null,
    position,
    last_checked_at: null,
    created_at: '2026-07-01T09:00:00Z',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});

describe('useReorderServers', () => {
  it('sends { ids } body and optimistically rewrites position by new order', async () => {
    serversApi.reorderServers.mockResolvedValue(undefined);
    queryClient.setQueryData(serversKey, {
      items: [server('a', 0), server('b', 1), server('c', 2)],
    });

    const { result } = renderHook(() => useReorderServers(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['c', 'a', 'b']);
    });

    // Тело запроса — полный упорядоченный список id (04-api.md).
    expect(serversApi.reorderServers).toHaveBeenCalledWith({ ids: ['c', 'a', 'b'] });

    // Оптимистично position переписан по индексу в новом порядке.
    const cached = queryClient.getQueryData<{ items: Server[] }>(serversKey);
    const byId = Object.fromEntries((cached?.items ?? []).map((s) => [s.id, s.position]));
    expect(byId).toEqual({ c: 0, a: 1, b: 2 });
  });

  it('rolls back the cache on API error', async () => {
    serversApi.reorderServers.mockRejectedValue(new Error('network'));
    const previous = [server('a', 0), server('b', 1)];
    queryClient.setQueryData(serversKey, { items: previous });

    const { result } = renderHook(() => useReorderServers(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(['b', 'a']).catch(() => undefined);
    });

    // Откат: порядок/position восстановлены к исходным.
    const cached = queryClient.getQueryData<{ items: Server[] }>(serversKey);
    expect(cached?.items.map((s) => [s.id, s.position])).toEqual([
      ['a', 0],
      ['b', 1],
    ]);
  });
});

describe('useReorderAiKeys', () => {
  it('sends { provider, ids } body and reorders only within the provider group', async () => {
    aiKeysApi.reorderAiKeys.mockResolvedValue(undefined);
    queryClient.setQueryData(aiKeysKey, {
      items: [aiKey('o1', 'openai', 0), aiKey('o2', 'openai', 1), aiKey('a1', 'anthropic', 0)],
    });

    const { result } = renderHook(() => useReorderAiKeys(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ provider: 'openai', ids: ['o2', 'o1'] });
    });

    expect(aiKeysApi.reorderAiKeys).toHaveBeenCalledWith({ provider: 'openai', ids: ['o2', 'o1'] });

    const cached = queryClient.getQueryData<{ items: AiKey[] }>(aiKeysKey);
    const byId = Object.fromEntries((cached?.items ?? []).map((k) => [k.id, k.position]));
    // openai перепозиционированы, anthropic — не тронут.
    expect(byId).toEqual({ o2: 0, o1: 1, a1: 0 });
  });

  it('rolls back the cache on API error', async () => {
    aiKeysApi.reorderAiKeys.mockRejectedValue(new Error('network'));
    queryClient.setQueryData(aiKeysKey, {
      items: [aiKey('o1', 'openai', 0), aiKey('o2', 'openai', 1)],
    });

    const { result } = renderHook(() => useReorderAiKeys(), { wrapper });
    await act(async () => {
      await result.current
        .mutateAsync({ provider: 'openai', ids: ['o2', 'o1'] })
        .catch(() => undefined);
    });

    const cached = queryClient.getQueryData<{ items: AiKey[] }>(aiKeysKey);
    expect(cached?.items.map((k) => [k.id, k.position])).toEqual([
      ['o1', 0],
      ['o2', 1],
    ]);
  });
});
