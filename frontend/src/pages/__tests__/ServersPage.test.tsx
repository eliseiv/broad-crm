import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServersPage } from '@/pages/ServersPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { loginAs, loginSuperadmin } from '@/test/authTestUtils';
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
    ssh_user: 'root',
    auth_method: 'password',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    backend_count: 0,
    online: false,
    uptime_seconds: null,
    last_updated: null,
    metrics: null,
  };
}

describe('ServersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loginSuperadmin();
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
    // Empty (ADR-046 §2б): единая текстовая строка; пары «Пока нет … / Добавьте первый …»
    // и карточка-плейсхолдер AddServerCard упразднены.
    expect(screen.getByText('Серверов пока нет')).toBeInTheDocument();
    expect(screen.queryByText('Пока нет серверов')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Добавьте первый сервер, чтобы начать мониторинг'),
    ).not.toBeInTheDocument();

    serversHook.value = { ...serversHook.value, data: { items: [server()] } };
    rerender(<ServersPage />);
    expect(screen.getByText('1 сервер под мониторингом')).toBeInTheDocument();
    expect(screen.getByText('Server 01')).toBeInTheDocument();
    expect(document.querySelector('.md\\:grid-cols-2')).toBeInTheDocument();
    expect(document.querySelector('.xl\\:grid-cols-3')).toBeInTheDocument();
    // Ручной кнопки «Обновить» на странице больше нет (ADR-013 смежная правка):
    // данные обновляются штатным polling/refetch TanStack Query.
    expect(screen.queryByRole('button', { name: /обновить/i })).not.toBeInTheDocument();
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

  it('renders the empty state without an owning logout button (moved to AppLayout)', () => {
    serversHook.value = { ...serversHook.value, data: { items: [] } };

    render(<ServersPage />, { wrapper });

    // Логаут вынесен в AppLayout — ServersPage его больше не отрисовывает.
    expect(screen.queryByRole('button', { name: /выйти/i })).not.toBeInTheDocument();
    expect(screen.getByText('Серверов пока нет')).toBeInTheDocument();
  });

  it('read-only user (no create): тот же empty-текст, но без кнопки «Добавить» (ADR-046 §2б)', () => {
    loginAs({ isSuperadmin: false, role: 'Наблюдатель', permissions: { servers: ['view'] } });
    serversHook.value = { ...serversHook.value, data: { items: [] } };

    render(<ServersPage />, { wrapper });

    // Отдельного read-only-варианта empty-state больше НЕТ: текст единый независимо от прав,
    // кнопка «Добавить» живёт в шапке и сама гейтится `servers:create`.
    expect(screen.getByText('Серверов пока нет')).toBeInTheDocument();
    expect(screen.queryByText('Список серверов пуст')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Добавить/ })).not.toBeInTheDocument();
  });

  it('кнопка «Добавить» — в правой зоне заголовка при servers:create (ADR-046 §2б)', () => {
    serversHook.value = { ...serversHook.value, data: { items: [] } };

    render(<ServersPage />, { wrapper });

    expect(screen.getByRole('button', { name: /Добавить/ })).toBeInTheDocument();
    // Карточка-плейсхолдер AddServerCard удалена и из сетки, и из пустого состояния.
    expect(
      screen.queryByText('Добавить сервер для мониторинга', { exact: false }),
    ).not.toBeInTheDocument();
  });

  it('read-only user (no create) sees the list without the add-server card', () => {
    loginAs({ isSuperadmin: false, role: 'Наблюдатель', permissions: { servers: ['view'] } });
    serversHook.value = { ...serversHook.value, data: { items: [server()] } };

    render(<ServersPage />, { wrapper });

    expect(screen.getByText('Server 01')).toBeInTheDocument();
    // Кнопка/карточка «Добавить» скрыта по правам (canCreate=false).
    expect(screen.queryByText('Добавить')).not.toBeInTheDocument();
  });

  it('user without servers:view sees the page-scoped stub, list is not rendered (ADR-021 §6)', () => {
    // Есть доступ к другому разделу, но нет `servers:view` → page-level view-guard.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    serversHook.value = { ...serversHook.value, data: { items: [server()] } };

    render(<ServersPage />, { wrapper });

    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Контент списка скрыт (guard короткозамыкает до рендера ServersList).
    expect(screen.queryByText('Server 01')).not.toBeInTheDocument();
  });
});
