import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AiKeyCard } from '@/components/AiKeyCard';
import type { AiKey, AiKeyStatus } from '@/types/api';

const deleteMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

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
  useDeleteAiKey: () => deleteMutation,
  // EditAiKeyDialog (внутри AddAiKeyModal, рендерится AiKeyCard) вызывает useUpdateAiKey.
  useUpdateAiKey: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeKey(overrides: Partial<AiKey> = {}): AiKey {
  return {
    id: 'key-1',
    name: 'OpenAI Prod',
    provider: 'openai',
    key_masked: 'sk-p…bA3T',
    check_status: 'working' as AiKeyStatus,
    error_message: null,
    position: 0,
    backend_count: 0,
    last_checked_at: '2026-07-01T10:15:00Z',
    created_at: '2026-07-01T09:00:00Z',
    ...overrides,
  };
}

describe('AiKeyCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deleteMutation.isPending = false;
  });

  it('renders working status with mask and provider', () => {
    render(<AiKeyCard aiKey={makeKey({ check_status: 'working' })} />, { wrapper });

    expect(screen.getByText('OpenAI Prod')).toBeInTheDocument();
    expect(screen.getByText('OpenAI')).toBeInTheDocument();
    expect(screen.getByText('Работает')).toBeInTheDocument();
    // Маска ключа отображается; полный ключ никогда не показывается.
    expect(screen.getByText('sk-p…bA3T')).toBeInTheDocument();
    expect(screen.queryByText('Не работает')).not.toBeInTheDocument();
  });

  it('renders error status with reason and delete button', () => {
    render(
      <AiKeyCard
        aiKey={makeKey({ check_status: 'error', error_message: 'Недостаточно средств' })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не работает')).toBeInTheDocument();
    expect(screen.getByText('Недостаточно средств')).toBeInTheDocument();
    // При error есть явная кнопка «Удалить» (danger).
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeInTheDocument();
  });

  it('renders pending status as «Проверка…»', () => {
    render(<AiKeyCard aiKey={makeKey({ check_status: 'pending', last_checked_at: null })} />, {
      wrapper,
    });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
    expect(screen.queryByText('Работает')).not.toBeInTheDocument();
    expect(screen.queryByText('Не работает')).not.toBeInTheDocument();
  });

  it('opens confirm modal and triggers delete mutation with the key id', async () => {
    const user = userEvent.setup();
    render(<AiKeyCard aiKey={makeKey({ name: 'OpenAI Prod' })} />, { wrapper });

    // Иконка удаления в шапке карточки (aria-label).
    await user.click(screen.getByRole('button', { name: 'Удалить ключ OpenAI Prod' }));

    // Открылась модалка подтверждения.
    expect(screen.getByText('Удалить ключ?')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Удалить' }));

    expect(deleteMutation.mutate).toHaveBeenCalledTimes(1);
    expect(deleteMutation.mutate.mock.calls[0][0]).toBe('key-1');
  });
});
