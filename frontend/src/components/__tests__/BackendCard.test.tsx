import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BackendCard } from '@/components/BackendCard';
import type { Backend } from '@/types/api';

const hooks = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
  updateMutate: vi.fn(),
  createMutate: vi.fn(),
}));

vi.mock('@/features/backends/hooks', () => ({
  backendsKey: ['backends'],
  useBackendStatus: () => ({ data: undefined }),
  useDeleteBackend: () => ({ mutate: hooks.deleteMutate, isPending: false }),
  useUpdateBackend: () => ({ mutate: hooks.updateMutate, isPending: false }),
  useCreateBackend: () => ({ mutate: hooks.createMutate, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeBackend(overrides: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'api.example.com',
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('BackendCard — status badges', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders "Работает" badge with code and domain for working status', () => {
    render(<BackendCard backend={makeBackend({ check_status: 'working' })} />, { wrapper });

    expect(screen.getByText('Работает')).toBeInTheDocument();
    expect(screen.getByText('api-eu')).toBeInTheDocument();
    expect(screen.getByText('api.example.com')).toBeInTheDocument();
    expect(screen.queryByText('Не работает')).not.toBeInTheDocument();
  });

  it('renders "Не работает" badge with reason for error status', () => {
    render(
      <BackendCard
        backend={makeBackend({ check_status: 'error', error_message: 'Бэк недоступен' })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не работает')).toBeInTheDocument();
    expect(screen.getByText('Бэк недоступен')).toBeInTheDocument();
  });

  it('renders "Проверка…" for pending status', () => {
    render(<BackendCard backend={makeBackend({ check_status: 'pending' })} />, { wrapper });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
    expect(screen.queryByText('Работает')).not.toBeInTheDocument();
  });

  it('в error-состоянии — ровно одна кнопка «Удалить» (нет второй, ADR-023)', () => {
    render(
      <BackendCard
        backend={makeBackend({ check_status: 'error', error_message: 'Бэк недоступен' })}
      />,
      { wrapper },
    );

    expect(screen.getAllByRole('button', { name: 'Удалить бэк API EU' })).toHaveLength(1);
  });
});

describe('BackendCard — detail → edit (ADR-035)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('клик по карточке открывает detail-модалку (Просмотр), НЕ edit', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр бэка API EU' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('Просмотр')).toBeInTheDocument();
    // Detail-поля read-only: Код / Название / Домен (у бэка секрета нет).
    expect(dialog.getByText('Код')).toBeInTheDocument();
    expect(dialog.getByText('Домен')).toBeInTheDocument();
    expect(screen.queryByText('Изменить бэк')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });

  it('карандаш в detail-модалке открывает edit prefilled', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр бэка API EU' }));
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }));

    expect(await screen.findByText('Изменить бэк')).toBeInTheDocument();
    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Код') as HTMLInputElement).value).toBe('api-eu');
    expect((dialog.getByLabelText('Домен') as HTMLInputElement).value).toBe('api.example.com');
  });

  it('без права backends:edit (canEdit=false) карандаш в detail-модалке скрыт', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} canEdit={false} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр бэка API EU' }));
    await screen.findByText('Просмотр');

    expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
  });

  it('delete button opens confirm dialog without opening detail/edit (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Удалить бэк API EU' }));

    expect(screen.getByText('Удалить бэк?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить бэк')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});
