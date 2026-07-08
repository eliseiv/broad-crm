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

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeUser(id: string, username: string): UserListItem {
  return {
    id,
    username,
    email: null,
    role_id: 'r1',
    role_name: 'Оператор',
    is_active: true,
    teams: [],
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
  members: [
    { id: 'u1', username: 'Никита' },
    { id: 'u2', username: 'Мария' },
  ],
  created_at: '2026-07-08T09:00:00Z',
  updated_at: '2026-07-08T09:00:00Z',
};

describe('AddTeamModal (создание/редактирование команды, ADR-022)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('лидер зафиксирован как участник (checkbox отмечен и недоступен для снятия)', () => {
    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);
    // Лидер по умолчанию — первый пользователь (u1 Никита).
    const leaderCheckbox = screen.getByRole('checkbox', { name: 'Никита' });
    expect(leaderCheckbox).toBeChecked();
    expect(leaderCheckbox).toBeDisabled();
  });

  it('create: payload содержит name, leader_id и member_ids БЕЗ лидера (инвариант «лидер ∈ участники»)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Продажи');
    // Отмечаем участника Мария (лидер u1 добавится сервером автоматически).
    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledWith(
      { name: 'Продажи', leader_id: 'u1', member_ids: ['u2'] },
      expect.any(Object),
    );
  });

  it('смена лидера исключает нового лидера из member_ids (инвариант соблюдается)', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<AddTeamModal open onOpenChange={vi.fn()} users={USERS} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Продажи');
    // Сначала выбираем Марию участником, затем делаем её лидером.
    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));
    await user.selectOptions(screen.getByLabelText('Лидер'), 'u2');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledWith(
      { name: 'Продажи', leader_id: 'u2', member_ids: [] },
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
