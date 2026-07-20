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
    status: 'active',
    teams: [],
    // ADR-055 §5.2: `UserListItem` несёт ТОЛЬКО добавку канала (без базовых `teams`).
    mail_extra_teams: [],
    mail_extra_includes_unassigned: false,
    sms_extra_teams: [],
    sms_extra_includes_unassigned: false,
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
      number_count: 0,
      mailbox_count: 0,
      members: [{ id: 'u1', username: 'Никита' }],
      created_at: '2026-07-08T09:00:00Z',
      updated_at: '2026-07-08T09:00:00Z',
    },
  ],
};

/** Все карточки-строки пользователей в порядке DOM (aria-label «Изменить пользователя …»). */
function userCards(): HTMLElement[] {
  return screen.getAllByRole('button', { name: /^Изменить пользователя / });
}

describe('UsersPage (плоский список с чипами команд, ADR-065)', () => {
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

  it('не рендерит H1-заголовок страницы (убран, ADR-029)', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    // Внутристраничный H1 «Пользователи» + подпись убраны — раздел обозначен навигацией.
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  });

  it('does NOT render a roles section or «Добавить роль» (moved to «Роли», ADR-022)', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.queryByRole('button', { name: 'Добавить роль' })).not.toBeInTheDocument();
    expect(screen.queryByText('Пока нет ролей')).not.toBeInTheDocument();
  });

  it('рендерит плоский список без секций-заголовков команд (ADR-065 §1)', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'Никита',
          telegram: 'nikita_01',
          teams: [{ id: 't1', name: 'Продажи' }],
        }),
        makeUser({ id: 'u2', username: 'Пётр', is_active: false, status: 'inactive' }),
      ],
    };

    const { container } = render(<UsersPage />, { wrapper });

    // Группировка упразднена: нет секций-контейнеров и нет заголовков-названий команд
    // (в т.ч. «Без команды» как заголовок секции). Команда «Продажи» — это чип, не heading.
    expect(container.querySelectorAll('section')).toHaveLength(0);
    expect(screen.queryByRole('heading', { name: 'Продажи' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Без команды' })).not.toBeInTheDocument();

    // Оба пользователя — плоскими строками одного списка (ровно 2 карточки).
    expect(userCards()).toHaveLength(2);
    expect(screen.getByText('Никита')).toBeInTheDocument();
    expect(screen.getByText('Пётр')).toBeInTheDocument();

    // telegram и роль сохранены в строке (ADR-065 §4).
    expect(screen.getByText('@nikita_01')).toBeInTheDocument();
    expect(screen.getAllByText('Оператор').length).toBeGreaterThan(0);
  });

  it('пользователь в нескольких командах встречается в списке РОВНО один раз (ADR-065 §1)', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'Мультикомандный',
          teams: [
            { id: 't1', name: 'Продажи' },
            { id: 't2', name: 'Маркетинг' },
          ],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    // Одна строка = один пользователь: дублирования по секциям больше нет.
    expect(userCards()).toHaveLength(1);
    expect(
      screen.getAllByRole('button', { name: 'Изменить пользователя Мультикомандный' }),
    ).toHaveLength(1);

    // Обе команды видны — но как чипы В ЕДИНСТВЕННОЙ строке пользователя.
    const card = userCards()[0];
    expect(within(card).getByText('Продажи')).toBeInTheDocument();
    expect(within(card).getByText('Маркетинг')).toBeInTheDocument();
  });

  it('сортирует пользователей по username через localeCompare («ru»), а не по code-unit', () => {
    // Порядок API — намеренно неотсортированный. localeCompare('ru'): Анна < борис < Яков
    // (кириллица по алфавиту, регистр — третичный). Наивный code-unit-sort дал бы
    // Анна(0x410) < Яков(0x42F) < борис(0x431) — иной порядок, что и различает кейс.
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'Яков' }),
        makeUser({ id: 'u2', username: 'борис' }),
        makeUser({ id: 'u3', username: 'Анна' }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const order = userCards().map((c) => c.getAttribute('aria-label'));
    expect(order).toEqual([
      'Изменить пользователя Анна',
      'Изменить пользователя борис',
      'Изменить пользователя Яков',
    ]);
  });

  it('рендерит команды чипами (ui/Pill) по user.teams (ADR-065 §2)', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'Никита',
          teams: [{ id: 't1', name: 'Продажи' }],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    // Чип команды — примитив ui/Pill: span с сигнатурными классами rounded-chip + инлайн-tone.
    const chip = screen.getByText('Продажи');
    expect(chip.tagName).toBe('SPAN');
    expect(chip).toHaveClass('rounded-chip');
    expect(chip.getAttribute('style') ?? '').not.toBe('');
    // Название команды — НЕ фолбэк «Без команды».
    expect(screen.queryByText('Без команды')).not.toBeInTheDocument();
  });

  it('при пустом teams показывает фолбэк «Без команды» вторичным цветом (ADR-065 §2)', () => {
    state.users = { items: [makeUser({ id: 'u1', username: 'Одиночка', teams: [] })] };

    render(<UsersPage />, { wrapper });

    const fallback = screen.getByText('Без команды');
    // Фолбэк — подпись вторичным цветом, а НЕ чип ui/Pill (нет rounded-chip).
    expect(fallback).toHaveClass('text-text-secondary');
    expect(fallback).not.toHaveClass('rounded-chip');
  });

  it('рендерит тристатус-бейдж (ADR-028): «Ожидает входа» / «Активен» / «Неактивен»', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'Ожидающий', is_active: true, status: 'pending' }),
        makeUser({ id: 'u2', username: 'Активный', is_active: true, status: 'active' }),
        makeUser({ id: 'u3', username: 'Выключенный', is_active: false, status: 'inactive' }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const pending = screen.getByText('Ожидающий').closest('[role="button"]') as HTMLElement;
    const active = screen.getByText('Активный').closest('[role="button"]') as HTMLElement;
    const inactive = screen.getByText('Выключенный').closest('[role="button"]') as HTMLElement;
    expect(within(pending).getByText('Ожидает входа')).toBeInTheDocument();
    expect(within(active).getByText('Активен')).toBeInTheDocument();
    expect(within(inactive).getByText('Неактивен')).toBeInTheDocument();
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
