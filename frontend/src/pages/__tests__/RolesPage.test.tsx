import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RolesPage } from '@/pages/RolesPage';
import type { PermissionsCatalogResponse, RoleListResponse } from '@/types/api';

const state = vi.hoisted(() => ({
  roles: undefined as RoleListResponse | undefined,
  catalog: undefined as PermissionsCatalogResponse | undefined,
  canView: true,
  can: { create: true, edit: true, delete: true } as Record<string, boolean>,
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: (_page: string, action: string) => state.can[action] ?? false,
}));

vi.mock('@/features/users/hooks', () => ({
  useRoles: () => ({
    data: state.roles,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  usePermissionsCatalog: () => ({
    data: state.catalog,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRole: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const CATALOG: PermissionsCatalogResponse = {
  pages: [
    { page: 'dashboard', actions: ['view'] },
    { page: 'servers', actions: ['view', 'create', 'edit', 'delete'] },
    { page: 'mail', actions: ['view'] },
  ],
};

const ROLES: RoleListResponse = {
  items: [
    {
      id: 'r1',
      name: 'Оператор',
      permissions: { servers: ['view'], mail: ['view'] },
      user_count: 3,
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:00:00Z',
    },
    {
      id: 'r2',
      name: 'Наблюдатель',
      permissions: {},
      user_count: 1,
      created_at: '2026-07-07T09:05:00Z',
      updated_at: '2026-07-07T09:05:00Z',
    },
  ],
};

describe('RolesPage (список ролей, гейтинг, ADR-022)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.roles = ROLES;
    state.catalog = CATALOG;
    state.canView = true;
    state.can = { create: true, edit: true, delete: true };
  });

  it('page-level view-guard: без roles:view показывает заглушку «Недостаточно прав»', () => {
    state.canView = false;
    render(<RolesPage />, { wrapper });
    expect(screen.getByText('Недостаточно прав')).toBeInTheDocument();
    // Список ролей не рендерится.
    expect(screen.queryByText('Оператор')).not.toBeInTheDocument();
  });

  it('рендерит роли с числом носителей (user_count, формы мн.ч.) и сводкой прав', () => {
    render(<RolesPage />, { wrapper });
    expect(screen.getByText('Оператор')).toBeInTheDocument();
    // user_count → «3 пользователя» / «1 пользователь».
    expect(screen.getByText('3 пользователя')).toBeInTheDocument();
    expect(screen.getByText('1 пользователь')).toBeInTheDocument();
    // Сводка прав — локализованные разделы.
    expect(screen.getByText('Серверы, Почты')).toBeInTheDocument();
    expect(screen.getByText('Нет прав')).toBeInTheDocument();
  });

  it('кнопка «Добавить роль» видна при roles:create и скрыта без него', () => {
    render(<RolesPage />, { wrapper });
    expect(screen.getByRole('button', { name: 'Добавить роль' })).toBeInTheDocument();
  });

  it('без roles:create кнопка «Добавить роль» не рендерится', () => {
    state.can = { create: false, edit: true, delete: true };
    render(<RolesPage />, { wrapper });
    expect(screen.queryByRole('button', { name: 'Добавить роль' })).not.toBeInTheDocument();
  });

  it('без roles:edit карточка роли не интерактивна (нет кнопки «Изменить роль …»)', () => {
    state.can = { create: false, edit: false, delete: false };
    render(<RolesPage />, { wrapper });
    expect(
      screen.queryByRole('button', { name: 'Изменить роль Оператор' }),
    ).not.toBeInTheDocument();
  });

  it('с roles:edit карточка интерактивна и открывает редактор роли', async () => {
    const user = userEvent.setup();
    render(<RolesPage />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Изменить роль Оператор' }));

    expect(await screen.findByRole('dialog', { name: 'Изменить роль' })).toBeInTheDocument();
  });

  it('empty state зависит от roles:create (create-вариант vs read-only)', () => {
    state.roles = { items: [] };
    const { unmount } = render(<RolesPage />, { wrapper });
    expect(screen.getByText('Пока нет ролей')).toBeInTheDocument();
    unmount();

    state.can = { create: false, edit: false, delete: false };
    render(<RolesPage />, { wrapper });
    expect(screen.getByText('Список ролей пуст')).toBeInTheDocument();
  });
});
