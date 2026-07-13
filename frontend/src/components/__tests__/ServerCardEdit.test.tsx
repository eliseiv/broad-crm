import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerCard } from '@/components/ServerCard';
import type { Server } from '@/types/api';

// Захватываем PATCH-мутацию редактирования (useUpdateServer) и delete.
const hooks = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
}));

vi.mock('@/features/servers/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/features/servers/hooks')>(
    '@/features/servers/hooks',
  );
  return {
    ...actual,
    useServerStatus: () => ({ data: undefined }),
    useDeleteServer: () => ({ mutate: hooks.deleteMutate, isPending: false }),
    useUpdateServer: () => ({ mutate: hooks.updateMutate, isPending: false }),
  };
});

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function server(overrides: Partial<Server> = {}): Server {
  return {
    id: 'server-1',
    name: 'Server 01',
    ip: '10.0.0.10',
    ssh_user: 'root',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    backend_count: 0,
    online: true,
    uptime_seconds: 1323120,
    last_updated: '2026-06-28T12:00:00Z',
    metrics: {
      cpu: { usage_percent: 65, zone: 'green', detail: { value: null, total: 8, unit: 'cores' } },
      ram: { usage_percent: 40, zone: 'green', detail: { value: 6, total: 16, unit: 'GB' } },
      ssd: { usage_percent: 20, zone: 'green', detail: { value: 100, total: 500, unit: 'GB' } },
    },
    ...overrides,
  };
}

describe('ServerCard detail → edit (ADR-035, состав — ADR-049 §1)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('клик по карточке открывает detail-модалку (Просмотр), НЕ edit; креды видны сразу', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр сервера Server 01' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('Просмотр')).toBeInTheDocument();
    // ADR-049 §1: Название → IP → Пользователь → Пароль — в ГЛАВНОМ блоке, без сворачивания;
    // блок «Информация» в detail-модалке сервера УПРАЗДНЁН.
    expect(dialog.getByText('Server 01')).toBeInTheDocument();
    expect(dialog.getByText('10.0.0.10')).toBeInTheDocument();
    expect(dialog.getByText('Пользователь')).toBeInTheDocument();
    expect(dialog.getByText('root')).toBeInTheDocument();
    expect(dialog.getByText('Пароль')).toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Информация' })).not.toBeInTheDocument();
    // edit-модалка ещё не открыта.
    expect(screen.queryByText('Изменить сервер')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });

  it('карандаш в detail-модалке открывает inline-edit и PATCHes {name} (ADR-039)', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр сервера Server 01' }));
    // Карандаш открывает inline-редактирование имени прямо в detail-модалке (ADR-039).
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }));

    const nameInput = (await screen.findByLabelText('Название')) as HTMLInputElement;
    expect(nameInput.value).toBe('Server 01');
    // Отдельной edit-модалки сервера больше нет (ADR-039 — edit инлайн в detail-view).
    expect(screen.queryByText('Изменить сервер')).not.toBeInTheDocument();

    await user.clear(nameInput);
    await user.type(nameInput, 'Server 01 renamed');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    // PATCH /api/servers/{id} с телом { name } (только name — Этап 1).
    expect(hooks.updateMutate).toHaveBeenCalledTimes(1);
    expect(hooks.updateMutate.mock.calls[0][0]).toEqual({ name: 'Server 01 renamed' });
  });

  it('без права servers:edit (canEdit=false) карандаш в detail-модалке скрыт', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} canEdit={false} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр сервера Server 01' }));
    await screen.findByText('Просмотр');

    // Карандаш гейтится servers:edit — без права его нет.
    expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
  });

  it('delete button does not open detail/edit (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    // Клик по кнопке «Удалить» открывает подтверждение удаления, НЕ detail/edit.
    await user.click(screen.getByRole('button', { name: 'Удалить сервер Server 01' }));

    expect(screen.getByText('Удалить сервер?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить сервер')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});
