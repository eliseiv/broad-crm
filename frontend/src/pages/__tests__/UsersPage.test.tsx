import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UsersPage } from '@/pages/UsersPage';
import type {
  RoleListResponse,
  TeamListResponse,
  UserListItem,
  UserListResponse,
} from '@/types/api';

const state = vi.hoisted(() => ({
  users: undefined as UserListResponse | undefined,
  roles: undefined as RoleListResponse | undefined,
  teams: undefined as TeamListResponse | undefined,
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
  useCreateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteUser: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({
    data: state.teams,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeUser(
  over: Partial<UserListItem> & Pick<UserListItem, 'id' | 'username'>,
): UserListItem {
  return {
    telegram: null,
    has_password: true,
    role_id: 'r2',
    role_name: 'Оператор',
    is_active: true,
    teams: [],
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T09:00:00Z',
    ...over,
  };
}

const ROLES: RoleListResponse = {
  items: [
    {
      id: 'r2',
      name: 'Оператор',
      permissions: { servers: ['view'] },
      user_count: 2,
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:00:00Z',
    },
  ],
};

const TEAMS: TeamListResponse = {
  items: [
    {
      id: 't1',
      name: 'Продажи',
      leader_id: 'u1',
      leader_username: 'Никита',
      member_count: 1,
      members: [{ id: 'u1', username: 'Никита' }],
      created_at: '2026-07-08T09:00:00Z',
      updated_at: '2026-07-08T09:00:00Z',
    },
  ],
};

describe('UsersPage (пользователи по командам, ADR-022/025)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.users = undefined;
    state.roles = ROLES;
    state.teams = TEAMS;
  });

  it('renders the empty state for users', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.getByText('Пока нет пользователей')).toBeInTheDocument();
  });

  it('does NOT render a roles section or «Добавить роль» (moved to «Роли», ADR-022)', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.queryByRole('button', { name: 'Добавить роль' })).not.toBeInTheDocument();
    expect(screen.queryByText('Пока нет ролей')).not.toBeInTheDocument();
  });

  it('groups users by teams with a «Без команды» bucket, shows telegram and status', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'Никита',
          telegram: 'nikita_01',
          teams: [{ id: 't1', name: 'Продажи' }],
        }),
        makeUser({ id: 'u2', username: 'Пётр', is_active: false }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const teamSection = screen.getByRole('heading', { name: 'Продажи' }).closest('section');
    const noTeamSection = screen.getByRole('heading', { name: 'Без команды' }).closest('section');
    expect(teamSection).not.toBeNull();
    expect(noTeamSection).not.toBeNull();

    // Никита — в секции «Продажи»; Пётр — в бакете «Без команды».
    expect(within(teamSection as HTMLElement).getByText('Никита')).toBeInTheDocument();
    expect(within(noTeamSection as HTMLElement).getByText('Пётр')).toBeInTheDocument();

    // telegram отображается как @ник, если задан; статусы — бейджами.
    expect(screen.getByText('@nikita_01')).toBeInTheDocument();
    expect(screen.getByText('Активен')).toBeInTheDocument();
    expect(screen.getByText('Неактивен')).toBeInTheDocument();
    expect(screen.getAllByText('Оператор').length).toBeGreaterThan(0);
  });

  it('показывает бейдж «Без пароля» для беспарольного и не показывает для парольного (ADR-025)', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'Беспарольный', has_password: false }),
        makeUser({ id: 'u2', username: 'Парольный', has_password: true }),
      ],
    };

    render(<UsersPage />, { wrapper });

    // Ровно один бейдж «Без пароля» — у беспарольного пользователя.
    const badges = screen.getAllByText('Без пароля');
    expect(badges).toHaveLength(1);
    const passwordlessCard = screen
      .getByText('Беспарольный')
      .closest('[role="button"]') as HTMLElement;
    expect(within(passwordlessCard).getByText('Без пароля')).toBeInTheDocument();
    const withPasswordCard = screen
      .getByText('Парольный')
      .closest('[role="button"]') as HTMLElement;
    expect(within(withPasswordCard).queryByText('Без пароля')).not.toBeInTheDocument();
  });

  it('shows a user that belongs to several teams in each of its groups', () => {
    state.teams = {
      items: [
        ...TEAMS.items,
        {
          id: 't2',
          name: 'Маркетинг',
          leader_id: 'u9',
          leader_username: 'Ольга',
          member_count: 1,
          members: [{ id: 'u9', username: 'Ольга' }],
          created_at: '2026-07-08T09:00:00Z',
          updated_at: '2026-07-08T09:00:00Z',
        },
      ],
    };
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'Никита',
          teams: [
            { id: 't1', name: 'Продажи' },
            { id: 't2', name: 'Маркетинг' },
          ],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const sales = screen.getByRole('heading', { name: 'Продажи' }).closest('section');
    const marketing = screen.getByRole('heading', { name: 'Маркетинг' }).closest('section');
    expect(within(sales as HTMLElement).getByText('Никита')).toBeInTheDocument();
    expect(within(marketing as HTMLElement).getByText('Никита')).toBeInTheDocument();
  });

  it('opens the add-user modal from the toolbar', async () => {
    const user = userEvent.setup();
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Добавить пользователя' }));

    expect(
      screen.getByText(
        'Логин обязателен; пароль можно не задавать — пользователь задаст его при первом входе. Доступ определяется ролью.',
      ),
    ).toBeInTheDocument();
  });

  it('opens the edit-user modal when a user card is activated', async () => {
    const user = userEvent.setup();
    state.users = { items: [makeUser({ id: 'u1', username: 'Никита' })] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Изменить пользователя Никита' }));

    expect(screen.getByText('Логин «Никита» не редактируется.')).toBeInTheDocument();
  });
});
