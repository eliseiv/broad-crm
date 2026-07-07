import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { ProxiesPage } from '@/pages/ProxiesPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { loginAs, loginSuperadmin } from '@/test/authTestUtils';
import type { Proxy } from '@/types/api';

const proxiesHook = vi.hoisted(() => ({
  value: {
    data: undefined as { items: Proxy[] } | undefined,
    isLoading: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
    isFetching: false,
  },
}));

vi.mock('@/features/proxies/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/features/proxies/hooks')>(
    '@/features/proxies/hooks',
  );
  return {
    ...actual,
    useProxies: () => proxiesHook.value,
    useProxyStatus: () => ({ data: undefined }),
    useDeleteProxy: () => ({ mutate: vi.fn(), isPending: false }),
    useCreateProxy: () => ({ mutate: vi.fn(), isPending: false }),
    useUpdateProxy: () => ({ mutate: vi.fn(), isPending: false }),
    useReorderProxies: () => ({ mutate: vi.fn(), isPending: false }),
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

function proxy(overrides: Partial<Proxy> = {}): Proxy {
  return {
    id: 'proxy-1',
    name: 'DE Residential',
    proxy_type: 'socks5',
    host: 'proxy.example.com',
    port: 1080,
    username: 'user01',
    has_password: true,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('ProxiesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loginSuperadmin();
    proxiesHook.value = {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  });

  it('renders loading, empty and list states', () => {
    proxiesHook.value = { ...proxiesHook.value, isLoading: true };
    const { rerender } = render(<ProxiesPage />, { wrapper });

    expect(screen.getByText('Загрузка…')).toBeInTheDocument();

    proxiesHook.value = { ...proxiesHook.value, isLoading: false, data: { items: [] } };
    rerender(<ProxiesPage />);
    expect(screen.getByText('Пока нет прокси')).toBeInTheDocument();
    expect(screen.getByText('Добавьте первый прокси')).toBeInTheDocument();
    // Пустое состояние показывает карточку добавления (AddProxyCard).
    expect(screen.getByText('Подключить новый прокси для мониторинга')).toBeInTheDocument();

    proxiesHook.value = { ...proxiesHook.value, data: { items: [proxy()] } };
    rerender(<ProxiesPage />);
    expect(screen.getByText('1 прокси под мониторингом')).toBeInTheDocument();
    expect(screen.getByText('DE Residential')).toBeInTheDocument();
    expect(document.querySelector('.md\\:grid-cols-2')).toBeInTheDocument();
    expect(document.querySelector('.xl\\:grid-cols-3')).toBeInTheDocument();
  });

  it('renders non-auth error and calls refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    proxiesHook.value = {
      ...proxiesHook.value,
      isError: true,
      error: new Error('network'),
      refetch,
    };
    render(<ProxiesPage />, { wrapper });

    expect(screen.getByText('Не удалось загрузить прокси')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /повторить/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalled();
  });

  it('does not render error UI or toast on 401 auth error', () => {
    proxiesHook.value = {
      ...proxiesHook.value,
      isError: true,
      error: new ApiError(401, 'unauthorized', 'Требуется авторизация'),
    };
    render(<ProxiesPage />, { wrapper });

    // 401 → не показываем блок ошибки и не шумим toast (редирект на логин делает shell).
    expect(screen.queryByText('Не удалось загрузить прокси')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /повторить/i })).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('renders pending proxy with "Проверка…" status', () => {
    proxiesHook.value = {
      ...proxiesHook.value,
      data: { items: [proxy({ check_status: 'pending' })] },
    };
    render(<ProxiesPage />, { wrapper });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
  });

  it('user without proxies:view sees the page-scoped stub, list is not rendered (ADR-021 §6)', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    proxiesHook.value = { ...proxiesHook.value, data: { items: [proxy()] } };
    render(<ProxiesPage />, { wrapper });

    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Контент списка скрыт (guard короткозамыкает до рендера списка прокси).
    expect(screen.queryByText('DE Residential')).not.toBeInTheDocument();
  });
});
