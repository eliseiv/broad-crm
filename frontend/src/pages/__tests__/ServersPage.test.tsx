import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServersPage } from '@/pages/ServersPage';
import { useAuthStore } from '@/store/auth';
import type { Server } from '@/types/api';

const serversHook = vi.hoisted(() => ({
  value: {
    data: undefined as { items: Server[] } | undefined,
    isLoading: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
    isFetching: false,
  },
}));

vi.mock('@/features/servers/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/features/servers/hooks')>(
    '@/features/servers/hooks',
  );
  return {
    ...actual,
    useServers: () => serversHook.value,
    useServerStatus: () => ({ data: undefined }),
    useDeleteServer: () => ({ mutate: vi.fn(), isPending: false }),
    useCreateServer: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function server(): Server {
  return {
    id: 'server-1',
    name: 'Server 01',
    ip: '10.0.0.10',
    exporter_port: 9100,
    provision_status: 'online',
    online: false,
    uptime_seconds: null,
    last_updated: null,
    metrics: null,
  };
}

describe('ServersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().setSession('jwt-token', 'admin');
    serversHook.value = {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  });

  it('renders loading, empty and list states', () => {
    serversHook.value = { ...serversHook.value, isLoading: true };
    const { rerender } = render(<ServersPage />, { wrapper });

    expect(screen.getByText('Загрузка…')).toBeInTheDocument();

    serversHook.value = { ...serversHook.value, isLoading: false, data: { items: [] } };
    rerender(<ServersPage />);
    expect(screen.getByText('Пока нет серверов')).toBeInTheDocument();
    expect(screen.getByText('Добавьте первый сервер, чтобы начать мониторинг')).toBeInTheDocument();

    serversHook.value = { ...serversHook.value, data: { items: [server()] } };
    rerender(<ServersPage />);
    expect(screen.getByText('1 сервер под мониторингом')).toBeInTheDocument();
    expect(screen.getByText('Server 01')).toBeInTheDocument();
    expect(document.querySelector('.md\\:grid-cols-2')).toBeInTheDocument();
    expect(document.querySelector('.xl\\:grid-cols-3')).toBeInTheDocument();
  });

  it('renders non-auth error and calls refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    serversHook.value = {
      ...serversHook.value,
      isError: true,
      error: new Error('network'),
      refetch,
    };
    render(<ServersPage />, { wrapper });

    await user.click(screen.getByRole('button', { name: /повторить/i }));

    expect(screen.getByText('Не удалось загрузить серверы')).toBeInTheDocument();
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('clears session on logout', async () => {
    const user = userEvent.setup();
    serversHook.value = { ...serversHook.value, data: { items: [] } };

    render(<ServersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: /выйти/i }));

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
