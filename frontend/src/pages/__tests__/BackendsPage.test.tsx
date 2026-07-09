import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { BackendsPage } from '@/pages/BackendsPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { loginAs, loginSuperadmin } from '@/test/authTestUtils';
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
  const actual = await vi.importActual<typeof import('@/features/backends/hooks')>(
    '@/features/backends/hooks',
  );
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
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: false,
    has_admin_api_key: false,
    git: null,
    note: null,
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
    loginSuperadmin();
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
    // Пустое состояние (ADR-039) — ТОЛЬКО карточка добавления, без пояснительного текста.
    expect(screen.getByText('Подключить новый бэк для мониторинга')).toBeInTheDocument();
    expect(screen.queryByText('Пока нет бэков')).not.toBeInTheDocument();

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

  it('user without backends:view sees the page-scoped stub, list is not rendered (ADR-021 §6)', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    backendsHook.value = { ...backendsHook.value, data: { items: [backend()] } };
    render(<BackendsPage />, { wrapper });

    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Контент списка скрыт (guard короткозамыкает до рендера списка бэков).
    expect(screen.queryByText('API EU')).not.toBeInTheDocument();
  });
});

describe('BackendsPage — группировка и поиск (ADR-039)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loginSuperadmin();
    backendsHook.value = {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  });

  it('группирует бэки с одинаковым name в кластер «name · N»', () => {
    backendsHook.value = {
      ...backendsHook.value,
      data: {
        items: [
          backend({ id: 'b1', code: 'api-eu', name: 'API', domain: 'https://eu/', position: 0 }),
          backend({ id: 'b2', code: 'api-us', name: 'API', domain: 'https://us/', position: 1 }),
          backend({ id: 'b3', code: 'web', name: 'Web', domain: 'https://web/', position: 2 }),
        ],
      },
    };
    render(<BackendsPage />, { wrapper });

    // Кластер из двух «API» → групповой заголовок «API · 2».
    expect(screen.getByRole('heading', { name: 'API · 2' })).toBeInTheDocument();
    // Одиночный «Web» группового заголовка не получает.
    expect(screen.queryByRole('heading', { name: /^Web · / })).not.toBeInTheDocument();
  });

  it('поиск фильтрует по code/name/domain (регистронезависимо)', async () => {
    const user = userEvent.setup();
    backendsHook.value = {
      ...backendsHook.value,
      data: {
        items: [
          backend({
            id: 'b1',
            code: 'api-eu',
            name: 'API EU',
            domain: 'https://eu.example/',
            position: 0,
          }),
          backend({
            id: 'b2',
            code: 'web-app',
            name: 'Web App',
            domain: 'https://web.example/',
            position: 1,
          }),
        ],
      },
    };
    render(<BackendsPage />, { wrapper });

    expect(screen.getByText('API EU')).toBeInTheDocument();
    expect(screen.getByText('Web App')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Поиск по бэкам'), 'WEB');
    expect(screen.getByText('Web App')).toBeInTheDocument();
    expect(screen.queryByText('API EU')).not.toBeInTheDocument();
  });

  it('поиск без совпадений → «Ничего не найдено»', async () => {
    const user = userEvent.setup();
    backendsHook.value = {
      ...backendsHook.value,
      data: {
        items: [backend({ id: 'b1', code: 'api-eu', name: 'API EU', domain: 'https://eu/' })],
      },
    };
    render(<BackendsPage />, { wrapper });

    await user.type(screen.getByLabelText('Поиск по бэкам'), 'zzz-nomatch');
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });
});
