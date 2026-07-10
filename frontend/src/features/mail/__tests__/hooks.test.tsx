import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMailFeed, useMailMailboxes } from '@/features/mail/hooks';
import { ApiError } from '@/lib/api';
import type { MailListResponse, MailMessage } from '@/types/api';

const api = vi.hoisted(() => ({
  listMail: vi.fn(),
  replyMail: vi.fn(),
  listMailboxes: vi.fn(),
  MAIL_PAGE_LIMIT: 20,
}));

vi.mock('@/features/mail/api', () => api);

// polling-хуки читают env.pollIntervalMs — фиксируем большой интервал, чтобы фоновый refetch
// справочников не срабатывал в тестах.
vi.mock('@/lib/env', () => ({
  env: { apiBaseUrl: '', pollIntervalMs: 999_999, statusPollIntervalMs: 2500 },
}));

/** Письмо с управляемой датой (internal_date НЕ уникален — проверяем склейку по курсору). */
function makeMessage(id: number, internalDate = '2026-07-02T09:15:00Z'): MailMessage {
  return {
    id,
    subject: `Письмо ${id}`,
    internal_date: internalDate,
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

/** Страница компаундного keyset: `next_cursor` — opaque-токен либо `null` (конец). */
function page(messages: MailMessage[], nextCursor: string | null): MailListResponse {
  return { messages, next_cursor: nextCursor };
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

describe('useMailFeed (ADR-044 infinite compound-keyset feed)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the first batch newest-first without a cursor (limit=20, no filters)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2), makeMessage(1)], 'cur-1'));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    // Первая страница — без `before`, дефолтный лимит 20, фильтры пусты.
    expect(api.listMail.mock.calls[0][0]).toEqual({
      before: undefined,
      limit: 20,
      mailAccountId: undefined,
      teamId: undefined,
    });
    expect(result.current.messages.map((m) => m.id)).toEqual([2, 1]);
    expect(result.current.hasMore).toBe(true);
  });

  it('paginates older by next_cursor, dedups equal-internal_date overlaps, keeps server order', async () => {
    // Все письма делят один internal_date → порядок держит id DESC, а склейку — курсор.
    const d = '2026-07-02T09:15:00Z';
    api.listMail
      .mockResolvedValueOnce(page([makeMessage(5, d), makeMessage(4, d)], 'cur-4'))
      // Граничное письмо id=4 повторяется на стыке страниц — не должно задвоиться.
      .mockResolvedValueOnce(page([makeMessage(4, d), makeMessage(3, d)], null));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.hasMore).toBe(false));
    // Догрузка старых шлёт before = next_cursor первой страницы.
    expect(api.listMail.mock.calls[1][0]).toEqual({
      before: 'cur-4',
      limit: 20,
      mailAccountId: undefined,
      teamId: undefined,
    });
    // Дедуп по id: письмо 4 не задвоилось, id=3 не потеряно; порядок — новые сверху.
    expect(result.current.messages.map((m) => m.id)).toEqual([5, 4, 3]);
  });

  it('marks the end of the feed when next_cursor is null (hasMore=false)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(1)], null));
    const { result } = renderHook(() => useMailFeed(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(result.current.hasMore).toBe(false);
  });

  it('does not fetch more when hasMore is false (idempotent guard)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(1)], null));
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

  it('forwards the mail_account_id server filter to listMail', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null));
    const { result } = renderHook(() => useMailFeed({ mailAccountId: 7 }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail.mock.calls[0][0]).toEqual({
      before: undefined,
      limit: 20,
      mailAccountId: 7,
      teamId: undefined,
    });
  });

  it('forwards the team_id server filter to listMail', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null));
    const { result } = renderHook(() => useMailFeed({ teamId: 'team-3' }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail.mock.calls[0][0]).toEqual({
      before: undefined,
      limit: 20,
      mailAccountId: undefined,
      teamId: 'team-3',
    });
  });

  it('re-requests the feed (resets pagination) when the server filter changes', async () => {
    // Смена фильтра = новый queryKey → новый запрос ленты с чистой пагинацией (ADR-044 §7).
    api.listMail.mockResolvedValue(page([makeMessage(2)], 'cur-x'));
    const { result, rerender } = renderHook(
      ({ teamId }: { teamId?: string }) => useMailFeed({ teamId }),
      { wrapper: makeWrapper(), initialProps: { teamId: undefined as string | undefined } },
    );
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail).toHaveBeenCalledTimes(1);

    rerender({ teamId: 'team-3' });

    // Новый ключ запускает отдельный запрос ленты с фильтром team_id и БЕЗ before (сброс).
    await waitFor(() => expect(api.listMail).toHaveBeenCalledTimes(2));
    expect(api.listMail.mock.calls[1][0]).toMatchObject({
      teamId: 'team-3',
      mailAccountId: undefined,
      before: undefined,
    });
  });
});

describe('useMailMailboxes (справочник ящиков)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetches the mailboxes reference', async () => {
    const mailboxes = [
      {
        id: 7,
        email: 'inbox@postapp.store',
        display_name: 'Входящие',
        team_id: 'team-3',
        is_active: true,
        last_synced_at: null,
        last_sync_error: null,
        consecutive_failures: 0,
      },
    ];
    api.listMailboxes.mockResolvedValueOnce({ mailboxes });
    const { result } = renderHook(() => useMailMailboxes(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.mailboxes).toEqual(mailboxes);
    expect(api.listMailboxes).toHaveBeenCalledTimes(1);
  });

  it('surfaces a 503 error (dashboard "not configured" branch)', async () => {
    api.listMailboxes.mockRejectedValueOnce(new ApiError(503, 'mail_not_configured', 'x'));
    const { result } = renderHook(() => useMailMailboxes(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(503);
  });
});
