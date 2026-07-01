import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
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
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
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

describe('ServerCard edit', () => {
  beforeEach(() => vi.clearAllMocks());

  it('opens edit modal prefilled with name and PATCHes {name}', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    // Клик по карточке (role=button) открывает модалку редактирования.
    await user.click(screen.getByRole('button', { name: 'Изменить сервер Server 01' }));
    expect(screen.getByText('Изменить сервер')).toBeInTheDocument();

    // Поле «Название» префилено текущим именем.
    const nameInput = screen.getByLabelText('Название') as HTMLInputElement;
    expect(nameInput.value).toBe('Server 01');

    await user.clear(nameInput);
    await user.type(nameInput, 'Server 01 renamed');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    // PATCH /api/servers/{id} с телом { name } (только name — Этап 1).
    expect(hooks.updateMutate).toHaveBeenCalledTimes(1);
    expect(hooks.updateMutate.mock.calls[0][0]).toEqual({ name: 'Server 01 renamed' });
  });

  it('delete button does not open the edit modal (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    // Клик по кнопке «Удалить» открывает подтверждение удаления, НЕ edit.
    await user.click(screen.getByRole('button', { name: 'Удалить сервер Server 01' }));

    expect(screen.getByText('Удалить сервер?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить сервер')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});
