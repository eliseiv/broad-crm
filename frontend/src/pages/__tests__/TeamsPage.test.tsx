import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TeamsPage } from '@/pages/TeamsPage';
import type { TeamListResponse, UserListResponse } from '@/types/api';

const state = vi.hoisted(() => ({
  teams: undefined as TeamListResponse | undefined,
  users: undefined as UserListResponse | undefined,
  canView: true,
  can: { create: true, edit: true, delete: true } as Record<string, boolean>,
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: (_page: string, action: string) => state.can[action] ?? false,
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({
    data: state.teams,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateTeam: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateTeam: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteTeam: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/features/users/hooks', () => ({
  useUsers: () => ({
    data: state.users,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const USERS: UserListResponse = {
  items: [
    {
      id: 'u1',
      username: 'Никита',
      telegram: null,
      has_password: true,
      role_id: 'r1',
      role_name: 'Оператор',
      is_active: true,
      status: 'active',
      teams: [],
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:00:00Z',
    },
    {
      id: 'u2',
      username: 'Мария',
      telegram: null,
      has_password: true,
      role_id: 'r1',
      role_name: 'Оператор',
      is_active: true,
      status: 'active',
      teams: [],
      created_at: '2026-07-07T09:01:00Z',
      updated_at: '2026-07-07T09:01:00Z',
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
      member_count: 3,
      number_count: 2,
      members: [
        { id: 'u1', username: 'Никита' },
        { id: 'u2', username: 'Мария' },
        { id: 'u3', username: 'Иван' },
      ],
      created_at: '2026-07-08T09:00:00Z',
      updated_at: '2026-07-08T09:00:00Z',
    },
  ],
};

describe('TeamsPage (CRM-команды, гейтинг, ADR-022)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.teams = TEAMS;
    state.users = USERS;
    state.canView = true;
    state.can = { create: true, edit: true, delete: true };
  });

  it('page-level view-guard: без teams:view показывает заглушку «Недостаточно прав»', () => {
    state.canView = false;
    render(<TeamsPage />, { wrapper });
    expect(screen.getByText('Недостаточно прав')).toBeInTheDocument();
    expect(screen.queryByText('Продажи')).not.toBeInTheDocument();
  });

  it('не рендерит H1-заголовок страницы (убран, ADR-029)', () => {
    render(<TeamsPage />, { wrapper });
    // Внутристраничный H1 «Команды» + подпись убраны — раздел обозначен навигацией.
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  });

  it('рендерит команду: название, лидер и «N участников» (плюрализация member_count)', () => {
    render(<TeamsPage />, { wrapper });
    expect(screen.getByText('Продажи')).toBeInTheDocument();
    expect(screen.getByText('Никита')).toBeInTheDocument();
    // member_count=3 → «3 участника».
    expect(screen.getByText('3 участника')).toBeInTheDocument();
  });

  it('команда без лидера отображается как «Без лидера» (ADR-026)', () => {
    state.teams = {
      items: [
        {
          id: 't9',
          name: 'Резерв',
          leader_id: null,
          leader_username: null,
          member_count: 0,
          number_count: 0,
          members: [],
          created_at: '2026-07-08T09:00:00Z',
          updated_at: '2026-07-08T09:00:00Z',
        },
      ],
    };
    render(<TeamsPage />, { wrapper });
    expect(screen.getByText('Резерв')).toBeInTheDocument();
    expect(screen.getByText('Без лидера')).toBeInTheDocument();
    // member_count=0 → «0 участников».
    expect(screen.getByText('0 участников')).toBeInTheDocument();
  });

  it('кнопка «Добавить команду» видна при teams:create и скрыта без него', () => {
    const { unmount } = render(<TeamsPage />, { wrapper });
    expect(screen.getByRole('button', { name: 'Добавить команду' })).toBeInTheDocument();
    unmount();

    state.can = { create: false, edit: true, delete: true };
    render(<TeamsPage />, { wrapper });
    expect(screen.queryByRole('button', { name: 'Добавить команду' })).not.toBeInTheDocument();
  });

  it('без teams:edit карточка команды не интерактивна', () => {
    state.can = { create: false, edit: false, delete: false };
    render(<TeamsPage />, { wrapper });
    expect(
      screen.queryByRole('button', { name: 'Изменить команду Продажи' }),
    ).not.toBeInTheDocument();
  });

  it('с teams:edit клик по карточке открывает форму редактирования', async () => {
    const user = userEvent.setup();
    render(<TeamsPage />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Изменить команду Продажи' }));

    expect(await screen.findByRole('dialog', { name: 'Изменить команду' })).toBeInTheDocument();
  });

  it('empty state зависит от teams:create (create-вариант vs read-only)', () => {
    state.teams = { items: [] };
    const { unmount } = render(<TeamsPage />, { wrapper });
    expect(screen.getByText('Пока нет команд')).toBeInTheDocument();
    unmount();

    state.can = { create: false, edit: false, delete: false };
    render(<TeamsPage />, { wrapper });
    expect(screen.getByText('Список команд пуст')).toBeInTheDocument();
  });
});
