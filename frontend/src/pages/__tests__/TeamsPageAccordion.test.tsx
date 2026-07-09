import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TeamsPage } from '@/pages/TeamsPage';
import type { TeamListResponse, UserListResponse } from '@/types/api';

// Аккордеон /teams + чип number_count + карандаш stopPropagation (ADR-030).
const state = vi.hoisted(() => ({
  teams: undefined as TeamListResponse | undefined,
  users: undefined as UserListResponse | undefined,
  canView: true,
  can: { create: true, edit: true, delete: true } as Record<string, boolean>,
  // Ленивый список номеров команды (раскрытая detail-панель) — по умолчанию пусто.
  teamNumbers: {
    data: { numbers: [] },
    isLoading: false,
    isError: false,
    isFetching: false,
  } as unknown,
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

vi.mock('@/features/sms/hooks', () => ({
  useTeamNumbers: () => state.teamNumbers,
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

describe('TeamsPage аккордеон + чип number_count (ADR-030)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.teams = TEAMS;
    state.users = USERS;
    state.canView = true;
    state.can = { create: true, edit: true, delete: true };
    state.teamNumbers = {
      data: { numbers: [] },
      isLoading: false,
      isError: false,
      isFetching: false,
    };
  });

  it('чип number_count: рендерит numbersPlural (2 → «2 номера»)', () => {
    render(<TeamsPage />, { wrapper });
    expect(screen.getByText('2 номера')).toBeInTheDocument();
  });

  it('аккордеон: клик по карточке раскрывает панель (aria-expanded), повторный клик сворачивает', async () => {
    const user = userEvent.setup();
    render(<TeamsPage />, { wrapper });

    const header = screen.getByRole('button', { name: 'Продажи: показать детали' });
    expect(header).toHaveAttribute('aria-expanded', 'false');

    await user.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'true');
    // Detail-панель смонтирована (ленивый список номеров команды).
    expect(screen.getByText('Номера команды')).toBeInTheDocument();

    await user.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Номера команды')).not.toBeInTheDocument();
  });

  it('карандаш: stopPropagation — открывает модалку редактирования, аккордеон НЕ раскрывается', async () => {
    const user = userEvent.setup();
    render(<TeamsPage />, { wrapper });

    const header = screen.getByRole('button', { name: 'Продажи: показать детали' });
    await user.click(screen.getByRole('button', { name: 'Изменить команду Продажи' }));

    // Клик по карандашу не всплыл до карточки — панель осталась свёрнутой.
    expect(header).toHaveAttribute('aria-expanded', 'false');
    // Открылась модалка редактирования команды.
    expect(await screen.findByRole('dialog', { name: 'Изменить команду' })).toBeInTheDocument();
  });
});
