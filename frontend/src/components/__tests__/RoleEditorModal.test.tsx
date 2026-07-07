import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RoleEditorModal } from '@/components/RoleEditorModal';
import { ApiError } from '@/lib/api';
import type { PermissionCatalogPage, RoleListItem } from '@/types/api';

const mutations = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  del: vi.fn(),
}));

vi.mock('@/features/users/hooks', () => ({
  useCreateRole: () => ({ mutate: mutations.create, isPending: false }),
  useUpdateRole: () => ({ mutate: mutations.update, isPending: false }),
  useDeleteRole: () => ({ mutate: mutations.del, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const CATALOG: PermissionCatalogPage[] = [
  { page: 'dashboard', actions: ['view'] },
  { page: 'servers', actions: ['view', 'create', 'edit', 'delete'] },
  { page: 'mail', actions: ['view'] },
];

describe('RoleEditorModal (матрица прав и коды ошибок, ADR-021)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders a permission matrix where dashboard/mail expose only view', () => {
    render(<RoleEditorModal open onOpenChange={vi.fn()} catalog={CATALOG} mode="add" />);

    // dashboard и mail — единственное действие view (чекбокс есть только на «Просмотр»).
    expect(screen.getByRole('checkbox', { name: 'Дашборд — Просмотр' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Дашборд — Создание' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Дашборд — Удаление' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Почты — Просмотр' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Почты — Изменение' })).not.toBeInTheDocument();

    // servers — полный набор действий.
    for (const action of ['Просмотр', 'Создание', 'Изменение', 'Удаление']) {
      expect(screen.getByRole('checkbox', { name: `Серверы — ${action}` })).toBeInTheDocument();
    }
  });

  it('submits a built permissions payload for a new role', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) => opts.onSuccess());

    render(<RoleEditorModal open onOpenChange={vi.fn()} catalog={CATALOG} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'Оператор');
    await user.click(screen.getByRole('checkbox', { name: 'Серверы — Просмотр' }));
    await user.click(screen.getByRole('checkbox', { name: 'Серверы — Изменение' }));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledWith(
      { name: 'Оператор', permissions: { servers: ['view', 'edit'] } },
      expect.any(Object),
    );
  });

  it('maps 409 role_name_taken to an inline name error', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_payload, opts) =>
      opts.onError(new ApiError(409, 'role_name_taken', 'Роль с таким именем уже существует')),
    );

    render(<RoleEditorModal open onOpenChange={vi.fn()} catalog={CATALOG} mode="add" />);

    await user.type(screen.getByLabelText('Название'), 'admin');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Роль с таким названием уже существует')).toBeInTheDocument();
  });

  it('maps 409 role_in_use on delete to a toast', async () => {
    const user = userEvent.setup();
    const role: RoleListItem = {
      id: 'r1',
      name: 'Оператор',
      permissions: { servers: ['view'] },
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:00:00Z',
    };
    mutations.del.mockImplementation((_id, opts) =>
      opts.onError(new ApiError(409, 'role_in_use', 'Роль назначена пользователям')),
    );

    render(
      <RoleEditorModal open onOpenChange={vi.fn()} catalog={CATALOG} mode="edit" role={role} />,
    );

    await user.click(screen.getByRole('button', { name: /удалить/i }));
    // Подтверждение удаления во втором модальном окне.
    const confirm = await screen.findByRole('dialog', { name: 'Удалить роль?' });
    await user.click(within(confirm).getByRole('button', { name: 'Удалить' }));

    expect(toast.error).toHaveBeenCalledWith('Роль назначена пользователям — удаление невозможно');
  });
});
