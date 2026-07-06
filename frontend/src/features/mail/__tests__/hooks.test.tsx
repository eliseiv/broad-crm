import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMailFeed, useMailMailboxes, useMailTeams } from '@/features/mail/hooks';
import { ApiError } from '@/lib/api';
import type { MailListResponse, MailMessage } from '@/types/api';

const api = vi.hoisted(() => ({
  listMail: vi.fn(),
  replyMail: vi.fn(),
  listTeams: vi.fn(),
  listMailboxes: vi.fn(),
  MAIL_PAGE_LIMIT: 20,
}));

vi.mock('@/features/mail/api', () => api);

// polling-хуки читают env.pollIntervalMs — фиксируем большой интервал, чтобы фоновый refetch
// справочников не срабатывал в тестах.
vi.mock('@/lib/env', () => ({
  env: { apiBaseUrl: '', pollIntervalMs: 999_999, statusPollIntervalMs: 2500 },
}));

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

/** desc-страница: заполнен `next_before_id`, `next_since_id` = null (04-api.md). */
function page(
  messages: MailMessage[],
  nextBeforeId: number | null,
  hasMore: boolean,
): MailListResponse {
  return { messages, next_since_id: null, next_before_id: nextBeforeId, has_more: hasMore };
}

/** Свежий QueryClient на каждый тест — изоляция кэша между прогонами. */
function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('useMailFeed (ADR-013 infinite desc feed)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the first batch newest-first without a cursor (order=desc, limit=20)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2), makeMessage(1)], 1, true));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    // Первая страница — без before_id, order=desc, дефолтный лимит 20.
    expect(api.listMail.mock.calls[0][0]).toEqual({
      order: 'desc',
      beforeId: undefined,
      limit: 20,
    });
    expect(result.current.messages.map((m) => m.id)).toEqual([2, 1]);
    expect(result.current.hasMore).toBe(true);
  });

  it('paginates older by next_before_id, dedups overlaps and keeps id DESC', async () => {
    api.listMail
      .mockResolvedValueOnce(page([makeMessage(5), makeMessage(4)], 4, true))
      .mockResolvedValueOnce(page([makeMessage(4), makeMessage(3)], 3, false));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.hasMore).toBe(false));
    // Догрузка старых шлёт before_id = next_before_id первой страницы (4).
    expect(api.listMail.mock.calls[1][0]).toEqual({
      order: 'desc',
      beforeId: 4,
      limit: 20,
    });
    // Дедуп по id: письмо 4 не задвоилось; порядок — новые сверху.
    expect(result.current.messages.map((m) => m.id)).toEqual([5, 4, 3]);
  });

  it('does not fetch more when hasMore is false (idempotent guard)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(1)], null, false));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    // Единственный вызов — начальная загрузка; loadMore при hasMore=false не шлёт запрос.
    expect(api.listMail).toHaveBeenCalledTimes(1);
  });

  it('enters not_configured phase on 503 mail_not_configured', async () => {
    api.listMail.mockRejectedValueOnce(new ApiError(503, 'mail_not_configured', 'x'));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('not_configured'));
    expect(result.current.messages).toEqual([]);
  });

  it('enters error phase on 502 mail_unavailable', async () => {
    api.listMail.mockRejectedValueOnce(new ApiError(502, 'mail_unavailable', 'x'));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('error'));
  });

  it('forwards mail_account_id server filter to listMail', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null, false));
    const { result } = renderHook(() => useMailFeed({ mailAccountId: 7 }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail.mock.calls[0][0]).toEqual({
      order: 'desc',
      beforeId: undefined,
      limit: 20,
      mailAccountId: 7,
      groupId: undefined,
    });
  });

  it('forwards group_id server filter to listMail', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null, false));
    const { result } = renderHook(() => useMailFeed({ groupId: 3 }), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail.mock.calls[0][0]).toEqual({
      order: 'desc',
      beforeId: undefined,
      limit: 20,
      mailAccountId: undefined,
      groupId: 3,
    });
  });

  it('re-requests the feed when the server filter changes (filter is part of queryKey)', async () => {
    // Смена фильтра = новый queryKey → новый запрос ленты с новым фильтром (ADR-017).
    api.listMail.mockResolvedValue(page([makeMessage(2)], null, false));
    const { result, rerender } = renderHook(
      ({ groupId }: { groupId?: number }) => useMailFeed({ groupId }),
      { wrapper: makeWrapper(), initialProps: { groupId: undefined as number | undefined } },
    );
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail).toHaveBeenCalledTimes(1);

    rerender({ groupId: 3 });

    // Новый ключ запускает отдельный запрос ленты с фильтром group_id=3.
    await waitFor(() => expect(api.listMail).toHaveBeenCalledTimes(2));
    expect(api.listMail.mock.calls[1][0]).toMatchObject({ groupId: 3, mailAccountId: undefined });
  });
});

describe('useMailTeams / useMailMailboxes (ADR-017 справочники фильтров)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('useMailTeams fetches the teams reference', async () => {
    api.listTeams.mockResolvedValueOnce({ teams: [{ id: 3, name: 'Продажи' }] });
    const { result } = renderHook(() => useMailTeams(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.teams).toEqual([{ id: 3, name: 'Продажи' }]);
    expect(api.listTeams).toHaveBeenCalledTimes(1);
  });

  it('useMailMailboxes fetches the mailboxes reference', async () => {
    const mailboxes = [
      {
        id: 7,
        email: 'inbox@postapp.store',
        display_name: 'Входящие',
        group_id: 3,
        is_active: true,
      },
    ];
    api.listMailboxes.mockResolvedValueOnce({ mailboxes });
    const { result } = renderHook(() => useMailMailboxes(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.mailboxes).toEqual(mailboxes);
    expect(api.listMailboxes).toHaveBeenCalledTimes(1);
  });

  it('useMailMailboxes surfaces a 503 error (dashboard "not configured" branch)', async () => {
    api.listMailboxes.mockRejectedValueOnce(new ApiError(503, 'mail_not_configured', 'x'));
    const { result } = renderHook(() => useMailMailboxes(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(503);
  });
});
