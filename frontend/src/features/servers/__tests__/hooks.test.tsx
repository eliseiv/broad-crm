import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useServerStatus, useServers } from '@/features/servers/hooks';

const api = vi.hoisted(() => ({
  listServers: vi.fn(),
  getServerStatus: vi.fn(),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
}));

vi.mock('@/features/servers/api', () => api);

function wrapper({ children }: PropsWithChildren) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('server polling hooks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listServers.mockResolvedValue({ items: [] });
    api.getServerStatus.mockResolvedValue({
      id: 'server-1',
      provision_status: 'installing',
      error_message: null,
      updated_at: '2026-06-28T12:00:00Z',
    });
  });

  it('routine polling uses GET /servers list only and no per-card /metrics request', async () => {
    const { result } = renderHook(() => useServers(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(api.listServers).toHaveBeenCalledTimes(1);
    expect(api.getServerStatus).not.toHaveBeenCalled();
  });

  it('polls status only for pending or installing cards', async () => {
    renderHook(() => useServerStatus('server-1', 'pending'), { wrapper });
    renderHook(() => useServerStatus('server-2', 'online'), { wrapper });
    renderHook(() => useServerStatus('server-3', 'error'), { wrapper });

    await waitFor(() => expect(api.getServerStatus).toHaveBeenCalledTimes(1));
    expect(api.getServerStatus).toHaveBeenCalledWith('server-1', expect.any(AbortSignal));
  });
});
