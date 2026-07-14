import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddTeamModal } from '@/components/AddTeamModal';
import { ApiError } from '@/lib/api';
import type { TeamListItem, UserListItem } from '@/types/api';

const mutations = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  del: vi.fn(),
}));

vi.mock('@/features/teams/hooks', () => ({
  useCreateTeam: () => ({ mutate: mutations.create, isPending: false }),
  useUpdateTeam: () => ({ mutate: mutations.update, isPending: false }),
  useDeleteTeam: () => ({ mutate: mutations.del, isPending: false }),
}));

// Дропдаун привязки к группе mail-агрегатора (ADR-038) грузит список команд почты
// через react-query. Мокаем хук, чтобы не поднимать QueryClientProvider в тесте.
vi.mock('@/features/mail/hooks', () => ({
  useMailTeams: () => ({
    data: {
      teams: [
        { id: 3, name: 'Почта-Продажи' },
        { id: 8, name: 'Почта-Маркетинг' },
      ],
    },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeUser(id: string, username: string): UserListItem {
  return {
    id,
    username,
    telegram: null,
    has_password: true,
    role_id: 'r1',
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
  };
}

const USERS: UserListItem[] = [makeUser('u1', 'Никита'), makeUser('u2', 'Мария')];

const TEAM: TeamListItem = {
  id: 't1',
  name: 'Продажи',
  leader_id: 'u1',
  leader_username: 'Никита',
  member_count: 2,
  number_count: 0,
  mailbox_count: 0,
  members: [
    { id: 'u1', username: 'Никита' },
    { id: 'u2', username: 'Мария' },
  ],
  created_at: '2026-07-08T09:00:00Z',
  updated_at: '2026-07-08T09:00:00Z',
};

describe('AddTeamModal (лидер из участников, ADR-026/029)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('пустой состав: выбор лидера недоступен, без опции «Без лидера»', () => {
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    const leaderSelect = screen.getByLabelText('Лидер') as HTMLSelectElement;
    // Пока участники не выбраны — лидер недоступен (пусто, placeholder).
    expect(leaderSelect.value).toBe('');
    expect(leaderSelect).toBeDisabled();
    expect(screen.getByRole('option', { name: 'Сначала выберите участников' })).toBeInTheDocument();
    // Опции «Без лидера» в Select больше нет (ADR-029).
    expect(screen.queryByRole('option', { name: 'Без лидера' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Никита' })).not.toBeChecked();
  });

  it('первый добавленный участник авто-становится лидером (ADR-029)', async () => {
    const user = userEvent.setup();
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));

    const leaderSelect = screen.getByLabelText('Лидер') as HTMLSelectElement;
    expect(leaderSelect).not.toBeDisabled();
    expect(leaderSelect.value).toBe('u1'); // первый добавленный = лидер
    // В Select лидера — только выбранные участники, без «Без лидера».
    expect(screen.queryByRole('option', { name: 'Без лидера' })).not.toBeInTheDocument();
  });

  it('снятие текущего лидера из состава → лидером становится следующий участник', async () => {
    const user = userEvent.setup();
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));
    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));
    const leaderSelect = screen.getByLabelText('Лидер') as HTMLSelectElement;
    expect(leaderSelect.value).toBe('u1'); // первый остаётся лидером

    // Снимаем лидера (Никита) из состава → лидерство переходит следующему (Мария).
    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));
    expect(leaderSelect.value).toBe('u2');
  });

  it('пустой состав после снятия всех участников → без лидера', async () => {
    const user = userEvent.setup();
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));
    expect((screen.getByLabelText('Лидер') as HTMLSelectElement).value).toBe('u1');

    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));
    const leaderSelect = screen.getByLabelText('Лидер') as HTMLSelectElement;
    expect(leaderSelect.value).toBe(''); // пустой состав → без лидера
    expect(leaderSelect).toBeDisabled();
  });

  it('create: member_ids включает лидера (полный состав, ADR-029)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Продажи');
    await user.click(screen.getByRole('checkbox', { name: 'Никита' })); // лидер (первый)
    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const [payload] = mutations.create.mock.calls[0];
    expect(payload.name).toBe('Продажи');
    expect(payload.leader_id).toBe('u1');
    // Лидер входит в member_ids (полный состав).
    expect([...payload.member_ids].sort()).toEqual(['u1', 'u2']);
  });

  it('create: пустая команда без лидера → payload {name, member_ids: []}', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Пустая');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // leader_id не передаётся (пустой состав → без лидера).
    expect(mutations.create).toHaveBeenCalledWith(
      { name: 'Пустая', member_ids: [] },
      expect.any(Object),
    );
  });

  it('валидирует пустое название на клиенте (без вызова API)', async () => {
    const user = userEvent.setup();

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите название')).toBeInTheDocument();
    expect(mutations.create).not.toHaveBeenCalled();
  });

  it('маппит 409 team_name_taken в пофилдовую ошибку под «Название»', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'team_name_taken', 'Команда уже существует')),
    );

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Продажи');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Команда с таким названием уже существует')).toBeInTheDocument();
  });

  it('edit: источник кандидатов в лидеры — участники, без опции «Без лидера»', () => {
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="edit" team={TEAM} />);

    const leaderSelect = screen.getByLabelText('Лидер') as HTMLSelectElement;
    expect(leaderSelect.value).toBe('u1'); // текущий лидер
    expect(screen.queryByRole('option', { name: 'Без лидера' })).not.toBeInTheDocument();
    // Опции лидера = участники команды.
    expect(screen.getByRole('option', { name: 'Никита' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Мария' })).toBeInTheDocument();
  });

  it('edit: отправляет ТОЛЬКО изменённые поля (смена только названия → PATCH {name})', async () => {
    const user = userEvent.setup();
    mutations.update.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="edit" team={TEAM} />);

    const nameInput = screen.getByLabelText('Название');
    await user.clear(nameInput);
    await user.type(nameInput, 'Продажи EU');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(mutations.update).toHaveBeenCalledWith({ name: 'Продажи EU' }, expect.any(Object));
  });

  it('edit: снятие всех участников → PATCH { leader_id: null, member_ids: [] } (ADR-029)', async () => {
    const user = userEvent.setup();
    mutations.update.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="edit" team={TEAM} />);

    // Пустой состав — единственный легитимный кейс «без лидера» (ADR-029).
    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));
    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const [payload] = mutations.update.mock.calls[0];
    expect(payload.leader_id).toBeNull();
    expect(payload.member_ids).toEqual([]);
  });

  it('edit: без изменений закрывает форму без вызова API', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    render(<AddTeamModal open onOpenChange={onOpenChange} users={USERS} mode="edit" team={TEAM} />);

    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(mutations.update).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('edit: удаление команды через подтверждение', async () => {
    const user = userEvent.setup();
    mutations.del.mockImplementation((_id, opts) => opts.onSuccess());

    render(
      <AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="edit" team={TEAM} canDelete />,
    );

    await user.click(screen.getByRole('button', { name: /удалить/i }));
    const confirm = await screen.findByRole('dialog', { name: 'Удалить команду?' });
    await user.click(within(confirm).getByRole('button', { name: 'Удалить' }));

    expect(mutations.del).toHaveBeenCalledWith('t1', expect.any(Object));
  });

  it('edit: без canDelete кнопка «Удалить» не рендерится', () => {
    render(
      <AddTeamModal
        open
        onOpenChange={vi.fn()}
        users={USERS}
        mode="edit"
        team={TEAM}
        canDelete={false}
      />,
    );
    expect(screen.queryByRole('button', { name: /удалить/i })).not.toBeInTheDocument();
  });
});
