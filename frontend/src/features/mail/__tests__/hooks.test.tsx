import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMailFeed } from '@/features/mail/hooks';
import { ApiError } from '@/lib/api';
import type { MailListResponse, MailMessage } from '@/types/api';

const api = vi.hoisted(() => ({
  listMail: vi.fn(),
  replyMail: vi.fn(),
  MAIL_PAGE_LIMIT: 50,
}));

vi.mock('@/features/mail/api', () => api);
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

function page(messages: MailMessage[], next: number, hasMore: boolean): MailListResponse {
  return { messages, next_since_id: next, has_more: hasMore };
}

describe('useMailFeed', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the first batch newest-first and requests without since_id', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(1), makeMessage(2)], 2, true));
    const { result } = renderHook(() => useMailFeed());

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    // Первый запрос без since_id (первый батч от начала окна).
    expect(api.listMail).toHaveBeenCalledWith(undefined, 50);
    // Отображение новыми сверху (id DESC).
    expect(result.current.messages.map((m) => m.id)).toEqual([2, 1]);
    expect(result.current.hasMore).toBe(true);
  });

  it('paginates forward by next_since_id and dedups overlapping ids', async () => {
    api.listMail
      .mockResolvedValueOnce(page([makeMessage(1), makeMessage(2)], 2, true))
      .mockResolvedValueOnce(page([makeMessage(2), makeMessage(3)], 3, false));
    const { result } = renderHook(() => useMailFeed());
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.hasMore).toBe(false));
    // «Загрузить ещё» шлёт since_id = предыдущий next_since_id (2).
    expect(api.listMail).toHaveBeenNthCalledWith(2, 2, 50);
    // Дедуп по id: письмо 2 не задвоилось; порядок — новые сверху.
    expect(result.current.messages.map((m) => m.id)).toEqual([3, 2, 1]);
  });

  it('does not fetch more when hasMore is false (idempotent guard)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(1)], 1, false));
    const { result } = renderHook(() => useMailFeed());
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    // Единственный вызов — начальная загрузка; loadMore при hasMore=false не шлёт запрос.
    expect(api.listMail).toHaveBeenCalledTimes(1);
  });

  it('enters not_configured phase on 503', async () => {
    api.listMail.mockRejectedValueOnce(new ApiError(503, 'mail_not_configured', 'x'));
    const { result } = renderHook(() => useMailFeed());

    await waitFor(() => expect(result.current.phase).toBe('not_configured'));
    expect(result.current.messages).toEqual([]);
  });

  it('enters error phase on 502', async () => {
    api.listMail.mockRejectedValueOnce(new ApiError(502, 'mail_unavailable', 'x'));
    const { result } = renderHook(() => useMailFeed());

    await waitFor(() => expect(result.current.phase).toBe('error'));
  });
});
