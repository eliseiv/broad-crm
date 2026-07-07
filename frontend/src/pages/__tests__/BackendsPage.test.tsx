import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { BackendsPage } from '@/pages/BackendsPage';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/store/auth';
import type { Backend } from '@/types/api';

const backendsHook = vi.hoisted(() => ({
  value: {
    data: undefined as { items: Backend[] } | undefined,
    isLoading: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
    isFetching: false,
  },
}));

vi.mock('@/features/backends/hooks', async () => {
  const actual =
    await vi.importActual<typeof import('@/features/backends/hooks')>('@/features/backends/hooks');
  return {
    ...actual,
    useBackends: () => backendsHook.value,
    useBackendStatus: () => ({ data: undefined }),
    useDeleteBackend: () => ({ mutate: vi.fn(), isPending: false }),
    useCreateBackend: () => ({ mutate: vi.fn(), isPending: false }),
    useUpdateBackend: () => ({ mutate: vi.fn(), isPending: false }),
    useReorderBackends: () => ({ mutate: vi.fn(), isPending: false }),
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

function backend(overrides: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'api.example.com',
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('BackendsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().setSession('jwt-token', 'admin');
    backendsHook.value = {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  });

  it('renders loading, empty and list states', () => {
    backendsHook.value = { ...backendsHook.value, isLoading: true };
    const { rerender } = render(<BackendsPage />, { wrapper });

    expect(screen.getByText('Загрузка…')).toBeInTheDocument();

    backendsHook.value = { ...backendsHook.value, isLoading: false, data: { items: [] } };
    rerender(<BackendsPage />);
    expect(screen.getByText('Пока нет бэков')).toBeInTheDocument();
    expect(screen.getByText('Добавьте первый бэк')).toBeInTheDocument();
    // Пустое состояние показывает карточку добавления (AddBackendCard).
    expect(screen.getByText('Подключить новый бэк для мониторинга')).toBeInTheDocument();

    backendsHook.value = { ...backendsHook.value, data: { items: [backend()] } };
    rerender(<BackendsPage />);
    expect(screen.getByText('1 бэков под мониторингом')).toBeInTheDocument();
    expect(screen.getByText('API EU')).toBeInTheDocument();
    expect(document.querySelector('.md\\:grid-cols-2')).toBeInTheDocument();
    expect(document.querySelector('.xl\\:grid-cols-3')).toBeInTheDocument();
  });

  it('renders non-auth error and calls refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    backendsHook.value = {
      ...backendsHook.value,
      isError: true,
      error: new Error('network'),
      refetch,
    };
    render(<BackendsPage />, { wrapper });

    expect(screen.getByText('Не удалось загрузить бэки')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /повторить/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalled();
  });

  it('does not render error UI or toast on 401 auth error', () => {
    backendsHook.value = {
      ...backendsHook.value,
      isError: true,
      error: new ApiError(401, 'unauthorized', 'Требуется авторизация'),
    };
    render(<BackendsPage />, { wrapper });

    // 401 → не показываем блок ошибки и не шумим toast (редирект на логин делает shell).
    expect(screen.queryByText('Не удалось загрузить бэки')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /повторить/i })).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('renders pending backend with "Проверка…" status', () => {
    backendsHook.value = {
      ...backendsHook.value,
      data: { items: [backend({ check_status: 'pending' })] },
    };
    render(<BackendsPage />, { wrapper });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
  });
});
