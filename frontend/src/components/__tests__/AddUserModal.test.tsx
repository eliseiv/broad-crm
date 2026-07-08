import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddUserModal } from '@/components/AddUserModal';
import { ApiError } from '@/lib/api';
import type { RoleListItem, TeamListItem } from '@/types/api';

const mutations = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  del: vi.fn(),
}));

vi.mock('@/features/users/hooks', () => ({
  useCreateUser: () => ({ mutate: mutations.create, isPending: false }),
  useUpdateUser: () => ({ mutate: mutations.update, isPending: false }),
  useDeleteUser: () => ({ mutate: mutations.del, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ROLES: RoleListItem[] = [
  {
    id: 'r1',
    name: 'Оператор',
    permissions: { servers: ['view'] },
    user_count: 2,
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T09:00:00Z',
  },
];

const TEAMS: TeamListItem[] = [
  {
    id: 't1',
    name: 'Продажи',
    leader_id: 'x',
    leader_username: 'Лидер',
    member_count: 1,
    members: [{ id: 'x', username: 'Лидер' }],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  },
];

describe('AddUserModal (создание пользователя, коды ошибок, ADR-021/022)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('submits a create payload with the trimmed username and selected role', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // email/team_ids не отправляются, если не заданы (04-api.md — опциональны).
    expect(mutations.create).toHaveBeenCalledWith(
      { username: 'Никита', password: 's3cret-pass', role_id: 'r1' },
      expect.any(Object),
    );
  });

  it('includes email and team_ids in the payload when provided (ADR-022)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Почта'), 'nikita@example.com');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    await user.click(screen.getByRole('checkbox', { name: 'Продажи' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledWith(
      {
        username: 'Никита',
        password: 's3cret-pass',
        role_id: 'r1',
        email: 'nikita@example.com',
        team_ids: ['t1'],
      },
      expect.any(Object),
    );
  });

  it('the Логин field has no placeholder example «Никита» (ADR-022)', () => {
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);
    const loginInput = screen.getByLabelText('Логин');
    expect(loginInput).not.toHaveAttribute('placeholder', 'Никита');
    // Нигде в форме нет placeholder-примера «Никита».
    expect(screen.queryByPlaceholderText('Никита')).not.toBeInTheDocument();
  });

  it('maps 409 username_taken to an inline username error', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'username_taken', 'Пользователь уже существует')),
    );

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Пользователь с таким логином уже существует')).toBeInTheDocument();
  });

  it('maps 409 email_taken to an inline email error (ADR-022)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'email_taken', 'Почта уже занята')),
    );

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Почта'), 'nikita@example.com');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Пользователь с такой почтой уже существует')).toBeInTheDocument();
  });

  it('validates the password length client-side before hitting the API', async () => {
    const user = userEvent.setup();

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Пароль'), 'short');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Не менее 8 символов')).toBeInTheDocument();
    expect(mutations.create).not.toHaveBeenCalled();
  });
});
