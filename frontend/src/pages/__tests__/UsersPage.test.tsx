import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UsersPage } from '@/pages/UsersPage';
import type { PermissionsCatalogResponse, RoleListResponse, UserListResponse } from '@/types/api';

const state = vi.hoisted(() => ({
  users: undefined as UserListResponse | undefined,
  roles: undefined as RoleListResponse | undefined,
  catalog: undefined as PermissionsCatalogResponse | undefined,
}));

vi.mock('@/features/users/hooks', () => ({
  useUsers: () => ({
    data: state.users,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useRoles: () => ({
    data: state.roles,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  usePermissionsCatalog: () => ({
    data: state.catalog,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteUser: () => ({ mutate: vi.fn(), isPending: false }),
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

describe('UsersPage (RBAC-администрирование, ADR-021)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.users = undefined;
    state.roles = undefined;
    state.catalog = CATALOG;
  });

  it('renders empty states for users and roles', () => {
    state.users = { items: [] };
    state.roles = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.getByText('Пока нет пользователей')).toBeInTheDocument();
    expect(screen.getByText('Пока нет ролей')).toBeInTheDocument();
  });

  it('renders users (with status) and roles (with permissions summary)', () => {
    state.users = {
      items: [
        {
          id: 'u1',
          username: 'Никита',
          role_id: 'r2',
          role_name: 'Оператор',
          is_active: true,
          created_at: '2026-07-07T09:00:00Z',
          updated_at: '2026-07-07T09:00:00Z',
        },
        {
          id: 'u2',
          username: 'Пётр',
          role_id: 'r2',
          role_name: 'Оператор',
          is_active: false,
          created_at: '2026-07-07T09:05:00Z',
          updated_at: '2026-07-07T09:05:00Z',
        },
      ],
    };
    state.roles = {
      items: [
        {
          id: 'r2',
          name: 'Оператор',
          permissions: { servers: ['view'], mail: ['view'] },
          created_at: '2026-07-07T09:00:00Z',
          updated_at: '2026-07-07T09:00:00Z',
        },
      ],
    };

    render(<UsersPage />, { wrapper });

    expect(screen.getByText('Никита')).toBeInTheDocument();
    expect(screen.getByText('Пётр')).toBeInTheDocument();
    expect(screen.getByText('Активен')).toBeInTheDocument();
    expect(screen.getByText('Неактивен')).toBeInTheDocument();
    // Сводка прав роли — локализованные разделы.
    expect(screen.getByText('Серверы, Почты')).toBeInTheDocument();
  });

  it('opens the add-user modal from the toolbar', async () => {
    const user = userEvent.setup();
    state.users = { items: [] };
    state.roles = {
      items: [
        {
          id: 'r2',
          name: 'Оператор',
          permissions: { servers: ['view'] },
          created_at: '2026-07-07T09:00:00Z',
          updated_at: '2026-07-07T09:00:00Z',
        },
      ],
    };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Добавить пользователя' }));

    expect(
      screen.getByText('Логин и пароль для входа в систему; доступ определяется ролью.'),
    ).toBeInTheDocument();
  });

  it('opens the edit-user modal when a user card is activated', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        {
          id: 'u1',
          username: 'Никита',
          role_id: 'r2',
          role_name: 'Оператор',
          is_active: true,
          created_at: '2026-07-07T09:00:00Z',
          updated_at: '2026-07-07T09:00:00Z',
        },
      ],
    };
    state.roles = { items: [] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Изменить пользователя Никита' }));

    expect(screen.getByText('Логин «Никита» не редактируется.')).toBeInTheDocument();
  });

  it('opens the add-role modal from the toolbar', async () => {
    const user = userEvent.setup();
    state.users = { items: [] };
    state.roles = { items: [] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Добавить роль' }));

    expect(screen.getByRole('dialog', { name: 'Добавить роль' })).toBeInTheDocument();
  });
});
