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
    number_count: 0,
    mailbox_count: 0,
    members: [{ id: 'x', username: 'Лидер' }],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  },
];

/** Заполняет обязательный минимум формы создания: ФИО + телеграм + одна роль. */
async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Фамилия'), 'Петров');
  await user.type(screen.getByLabelText('Имя'), 'Никита');
  await user.type(screen.getByLabelText('Телеграм'), '@Nikita_01');
  await user.click(screen.getByRole('checkbox', { name: 'Оператор' }));
}

describe('AddUserModal (создание пользователя, ADR-079: ФИО, телеграм, роли-мультивыбор)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('submits a create payload with the trimmed ФИО, telegram and role_ids', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // Отчество/team_ids не отправляются, если не заданы (04-api.md — опциональны).
    expect(mutations.create).toHaveBeenCalledWith(
      {
        last_name: 'Петров',
        first_name: 'Никита',
        telegram: '@Nikita_01',
        role_ids: ['r1'],
        password: 's3cret-pass',
      },
      expect.any(Object),
    );
  });

  it('includes middle_name and team_ids in the payload when provided', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.type(screen.getByLabelText('Отчество'), 'Сергеевич');
    await user.click(screen.getByRole('checkbox', { name: 'Продажи' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledWith(
      {
        last_name: 'Петров',
        first_name: 'Никита',
        middle_name: 'Сергеевич',
        telegram: '@Nikita_01',
        role_ids: ['r1'],
        team_ids: ['t1'],
      },
      expect.any(Object),
    );
  });

  it('creates a passwordless user when the password is left empty (ADR-025)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // Пароль пуст → в payload не попадает (беспарольный «открытый первый вход»).
    expect(mutations.create).toHaveBeenCalledWith(
      {
        last_name: 'Петров',
        first_name: 'Никита',
        telegram: '@Nikita_01',
        role_ids: ['r1'],
      },
      expect.any(Object),
    );
  });

  it('поля «Логин» в форме нет — вход по телеграму (ADR-079 §9)', () => {
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    expect(screen.queryByLabelText('Логин')).not.toBeInTheDocument();
    // Нигде в форме нет placeholder-примера «Никита» (ADR-022).
    expect(screen.queryByPlaceholderText('Никита')).not.toBeInTheDocument();
  });

  it('требует телеграм и хотя бы одну роль до обращения к API (ADR-079 §1/§8)', async () => {
    const user = userEvent.setup();

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Фамилия'), 'Петров');
    await user.type(screen.getByLabelText('Имя'), 'Никита');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите Телеграм')).toBeInTheDocument();
    expect(screen.getByText('Выберите хотя бы одну роль')).toBeInTheDocument();
    expect(mutations.create).not.toHaveBeenCalled();
  });

  it('maps 409 username_taken to the Телеграм field (ADR-079 §9)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'username_taken', 'Пользователь уже существует')),
    );

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(
      screen.getByText('Этот Телеграм уже занят как логин другого пользователя'),
    ).toBeInTheDocument();
  });

  it('требует Фамилию и Имя до обращения к API (ADR-079 §7)', async () => {
    const user = userEvent.setup();

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Телеграм'), '@Nikita_01');
    await user.click(screen.getByRole('checkbox', { name: 'Оператор' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите фамилию')).toBeInTheDocument();
    expect(screen.getByText('Укажите имя')).toBeInTheDocument();
    expect(mutations.create).not.toHaveBeenCalled();
  });

  it('роли — МУЛЬТИвыбор: две отмеченные роли уходят обе (ADR-079 §8)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());
    const roles: RoleListItem[] = [
      ...ROLES,
      { ...ROLES[0], id: 'r2', name: 'Менеджер', user_count: 0 },
    ];

    render(<AddUserModal open onOpenChange={vi.fn()} roles={roles} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Фамилия'), 'Петров');
    await user.type(screen.getByLabelText('Имя'), 'Никита');
    await user.type(screen.getByLabelText('Телеграм'), '@Nikita_01');
    await user.click(screen.getByRole('checkbox', { name: 'Оператор' }));
    // Регресс-гейт против одиночного выбора: вторая отметка не должна снимать первую.
    await user.click(screen.getByRole('checkbox', { name: 'Менеджер' }));
    expect(screen.getByRole('checkbox', { name: 'Оператор' })).toBeChecked();

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = mutations.create.mock.calls[0][0] as { role_ids: string[] };
    expect([...payload.role_ids].sort()).toEqual(['r1', 'r2']);
  });

  it('ошибка 409 username_taken привязана ИМЕННО к полю «Телеграм» (aria-describedby)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'username_taken', 'Пользователь уже существует')),
    );

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // Поля «Логин» нет — сообщение обязано сесть на «Телеграм», а не улететь в toast.
    const telegram = screen.getByLabelText('Телеграм');
    const message = screen.getByText('Этот Телеграм уже занят как логин другого пользователя');
    expect(telegram).toHaveAttribute('aria-invalid', 'true');
    expect(telegram.getAttribute('aria-describedby')?.split(' ')).toContain(message.id);
    // Соседние поля чистые — ошибка не размазана по форме.
    expect(screen.getByLabelText('Фамилия')).toHaveAttribute('aria-invalid', 'false');
    expect(screen.getByLabelText('Имя')).toHaveAttribute('aria-invalid', 'false');
  });

  it('maps 409 telegram_taken to an inline telegram error (ADR-025)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'telegram_taken', 'Телеграм уже занят')),
    );

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Пользователь с таким Телеграмом уже существует')).toBeInTheDocument();
  });

  it('validates the password length client-side before hitting the API', async () => {
    const user = userEvent.setup();

    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await fillRequired(user);
    await user.type(screen.getByLabelText('Пароль'), 'short');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Не менее 8 символов')).toBeInTheDocument();
    expect(mutations.create).not.toHaveBeenCalled();
  });
});
