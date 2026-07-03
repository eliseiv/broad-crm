import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailPage } from '@/pages/MailPage';
import { ApiError } from '@/lib/api';
import type { MailFeedResult } from '@/features/mail/hooks';
import type { MailMessage } from '@/types/api';

const feed = vi.hoisted(() => ({ value: null as unknown }));

vi.mock('@/features/mail/hooks', () => ({
  useMailFeed: () => feed.value,
  // MailMessageCard использует useReplyMail — мокаем как no-op мутацию.
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeMessage(id: number): MailMessage {
  return {
    id,
    subject: `Письмо ${id}`,
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: { id: 3, email: 'inbox@postapp.store', display_name: 'Входящие' },
    body_text: 'тело',
    body_html: null,
    body_present: true,
    body_truncated: false,
    tags: [],
  };
}

function baseFeed(overrides: Partial<MailFeedResult> = {}): MailFeedResult {
  return {
    messages: [],
    phase: 'ready',
    error: null,
    hasMore: false,
    isFetchingMore: false,
    isRefreshing: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
    ...overrides,
  };
}

describe('MailPage states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "Сервис почт не настроен" on 503 without toast spam', () => {
    feed.value = baseFeed({
      phase: 'not_configured',
      error: new ApiError(503, 'mail_not_configured', 'not configured'),
    });
    render(<MailPage />, { wrapper });

    expect(screen.getByText('Сервис почт не настроен')).toBeInTheDocument();
    // Никакого toast-спама в состоянии «не настроено».
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('shows unavailable message + retry on 502', () => {
    feed.value = baseFeed({
      phase: 'error',
      error: new ApiError(502, 'mail_unavailable', 'unavailable'),
    });
    render(<MailPage />, { wrapper });

    expect(screen.getByText('Почтовый сервис временно недоступен')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Повторить/ })).toBeInTheDocument();
  });

  it('shows empty state when the feed is ready and has no messages', () => {
    feed.value = baseFeed({ phase: 'ready', messages: [] });
    render(<MailPage />, { wrapper });

    expect(screen.getByText('Писем пока нет')).toBeInTheDocument();
  });

  it('renders "Загрузить ещё" when hasMore and triggers loadMore on click', async () => {
    const loadMore = vi.fn();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true, loadMore });
    render(<MailPage />, { wrapper });

    const button = screen.getByRole('button', { name: 'Загрузить ещё' });
    await userEvent.setup().click(button);
    expect(loadMore).toHaveBeenCalledTimes(1);
  });

  it('hides "Загрузить ещё" when hasMore is false', () => {
    feed.value = baseFeed({ messages: [makeMessage(1)], hasMore: false });
    render(<MailPage />, { wrapper });

    expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument();
  });
});
