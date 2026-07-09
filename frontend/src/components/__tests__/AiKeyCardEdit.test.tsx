import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AiKeyCard } from '@/components/AiKeyCard';
import type { AiKey } from '@/types/api';

const hooks = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
}));

vi.mock('@/features/ai-keys/hooks', () => ({
  aiKeysKey: ['ai-keys'],
  useAiKeyStatus: () => ({ data: undefined }),
  // AiKeyDetailModal (рендерится AiKeyCard) вызывает ленивый reverse-lookup «Бэки» (ADR-040).
  useAiKeyBackends: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useDeleteAiKey: () => ({ mutate: hooks.deleteMutate, isPending: false }),
  useUpdateAiKey: () => ({ mutate: hooks.updateMutate, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeKey(overrides: Partial<AiKey> = {}): AiKey {
  return {
    id: 'key-1',
    name: 'OpenAI Prod',
    provider: 'openai',
    key_masked: 'sk-p…bA3T',
    check_status: 'working',
    error_message: null,
    position: 0,
    backend_count: 0,
    last_checked_at: '2026-07-01T10:15:00Z',
    created_at: '2026-07-01T09:00:00Z',
    ...overrides,
  };
}

describe('AiKeyCard detail → edit (ADR-035)', () => {
  beforeEach(() => vi.clearAllMocks());

  /** Открывает detail-модалку кликом по карточке и жмёт карандаш → edit-модалка. */
  async function openEdit(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'Просмотр ключа OpenAI Prod' }));
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }));
    await screen.findByText('Изменить ключ');
  }

  it('клик по карточке открывает detail-модалку (Просмотр) с маской ключа, НЕ edit', async () => {
    const user = userEvent.setup();
    render(<AiKeyCard aiKey={makeKey()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр ключа OpenAI Prod' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('Просмотр')).toBeInTheDocument();
    // Поле «Ключ» detail-view показывает маску key_masked (полный ключ скрыт).
    expect(dialog.getByText('sk-p…bA3T')).toBeInTheDocument();
    expect(screen.queryByText('Изменить ключ')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });

  it('opens edit prefilled with name+provider, key empty, and does not send empty key', async () => {
    const user = userEvent.setup();
    render(<AiKeyCard aiKey={makeKey()} />, { wrapper });

    await openEdit(user);

    // Поля формы — внутри диалога.
    const dialog = within(screen.getByRole('dialog'));
    // Префил name+provider; поле «Ключ» ПУСТОЕ (секрет не префилится).
    const nameInput = dialog.getByLabelText('Название') as HTMLInputElement;
    const providerSelect = dialog.getByLabelText('Провайдер') as HTMLSelectElement;
    const keyInput = dialog.getByLabelText('Ключ') as HTMLInputElement;
    expect(nameInput.value).toBe('OpenAI Prod');
    expect(providerSelect.value).toBe('openai');
    expect(keyInput.value).toBe('');

    // Сохранение без ввода ключа: тело без поля `key` (пустой key НЕ отправляется).
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(hooks.updateMutate).toHaveBeenCalledTimes(1);
    const payload = hooks.updateMutate.mock.calls[0][0];
    expect(payload).toEqual({ name: 'OpenAI Prod', provider: 'openai' });
    expect(payload).not.toHaveProperty('key');
  });

  it('sends key only when a new value is entered', async () => {
    const user = userEvent.setup();
    render(<AiKeyCard aiKey={makeKey()} />, { wrapper });

    await openEdit(user);
    const dialog = within(screen.getByRole('dialog'));
    await user.type(dialog.getByLabelText('Ключ'), 'sk-proj-NEW-value-9QzK');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = hooks.updateMutate.mock.calls[0][0];
    expect(payload.key).toBe('sk-proj-NEW-value-9QzK');
  });

  it('delete button does not open detail/edit modal (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<AiKeyCard aiKey={makeKey()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Удалить ключ OpenAI Prod' }));

    expect(screen.getByText('Удалить ключ?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить ключ')).not.toBeInTheDocument();
    expect(screen.queryByText('Просмотр')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});
