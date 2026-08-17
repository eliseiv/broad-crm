import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BroadcastPage } from '@/pages/BroadcastPage';
import { ApiError } from '@/lib/api';
import { INSUFFICIENT_PERMISSIONS_TITLE } from '@/components/InsufficientPermissions';
import type { BroadcastAudienceResponse, BroadcastCreateResponse } from '@/types/api';

const state = vi.hoisted(() => ({
  canView: true,
  canSend: true,
  audience: undefined as BroadcastAudienceResponse | undefined,
  audienceError: null as Error | null,
  isLoading: false,
  mutate: vi.fn(),
  isPending: false,
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: (_page: string, action: string) => (action === 'send' ? state.canSend : false),
}));

vi.mock('@/features/broadcast/hooks', () => ({
  useBroadcastAudience: () => ({
    data: state.audience,
    isLoading: state.isLoading,
    isError: Boolean(state.audienceError),
    error: state.audienceError,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateBroadcast: () => ({
    mutate: state.mutate,
    isPending: state.isPending,
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const AUDIENCE: BroadcastAudienceResponse = {
  roles: [
    { id: 'r1', name: 'Оператор', started_count: 2, not_started_count: 1 },
    { id: 'r2', name: 'Наблюдатель', started_count: 0, not_started_count: 3 },
  ],
  all_started_count: 2,
  all_not_started_count: 4,
};

describe('BroadcastPage (ADR-076)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.canView = true;
    state.canSend = true;
    state.audience = AUDIENCE;
    state.audienceError = null;
    state.isLoading = false;
    state.isPending = false;
    state.mutate.mockReset();
  });

  it('без broadcast:view показывает заглушку «Недостаточно прав»', () => {
    state.canView = false;
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('без broadcast:send кнопка «Отправить» скрыта', () => {
    state.canSend = false;
    render(<BroadcastPage />, { wrapper });
    expect(screen.queryByRole('button', { name: 'Отправить' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Всем')).toBeInTheDocument();
  });

  it('«Всем» дизейблит чекбоксы ролей; submit шлёт all=true без role_ids', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onSuccess: (d: BroadcastCreateResponse) => void }) => {
        opts.onSuccess({ sent: 2, failed: 0, skipped_not_started: 4 });
      },
    );
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Привет команде');
    await user.click(screen.getByLabelText('Всем'));

    const roleBox = screen.getByRole('checkbox', {
      name: /Оператор \(получат: 2, без бота: 1\)/,
    });
    expect(roleBox).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(state.mutate).toHaveBeenCalledWith(
      { text: 'Привет команде', all: true, role_ids: [] },
      expect.any(Object),
    );
  });

  it('тост успеха из sent/failed/skipped_not_started', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onSuccess: (d: BroadcastCreateResponse) => void }) => {
        opts.onSuccess({ sent: 3, failed: 1, skipped_not_started: 2 });
      },
    );
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: /Оператор/ }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(toast.success).toHaveBeenCalledWith('Отправлено: 3. Не доставлено: 1. Без бота: 2');
    expect(state.mutate).toHaveBeenCalledWith(
      { text: 'Текст', all: false, role_ids: ['r1'] },
      expect.any(Object),
    );
  });

  it('503 knowledge_bot_not_configured на отправке → «ИИ-бот не настроен»', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onError: (e: Error) => void }) => {
        opts.onError(new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен'));
      },
    );
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByLabelText('Всем'));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(screen.getByText('ИИ-бот не настроен')).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('503 на загрузке аудитории → «ИИ-бот не настроен»', () => {
    state.audience = undefined;
    state.audienceError = new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен');
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText('ИИ-бот не настроен')).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });
});
