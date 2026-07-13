import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  useMailFeed,
  useMailMailboxes,
  useMarkMailRead,
  useUnmarkMailRead,
} from '@/features/mail/hooks';
import { ApiError } from '@/lib/api';
import type { MailListResponse, MailMessage } from '@/types/api';

const api = vi.hoisted(() => ({
  listMail: vi.fn(),
  replyMail: vi.fn(),
  listMailboxes: vi.fn(),
  markMailRead: vi.fn(),
  unmarkMailRead: vi.fn(),
  MAIL_PAGE_LIMIT: 20,
}));

vi.mock('@/features/mail/api', () => api);

// polling-хуки читают env.pollIntervalMs — фиксируем большой интервал, чтобы фоновый refetch
// справочников не срабатывал в тестах.
vi.mock('@/lib/env', () => ({
  env: { apiBaseUrl: '', pollIntervalMs: 999_999, statusPollIntervalMs: 2500 },
}));

/** Письмо с управляемой датой (internal_date НЕ уникален — проверяем склейку по курсору). */
function makeMessage(
  id: number,
  internalDate = '2026-07-02T09:15:00Z',
  isUnread = true,
): MailMessage {
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
    // Персональная непрочитанность (ADR-050 §2.2) — обязательное поле схемы ленты.
    is_unread: isUnread,
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

// --- Личная прочитанность писем (ADR-050 §2) --------------------------------
describe('useMailFeed — серверный фильтр «Непрочитанные» (ADR-050 §2.8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('unread=true уходит в listMail (фильтрация серверная, курсор не ломается)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null));
    const { result } = renderHook(() => useMailFeed({ unread: true }), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail.mock.calls[0][0]).toMatchObject({ unread: true, before: undefined });
  });

  it('включение фильтра — новый queryKey → ре-запрос ленты БЕЗ before (сброс пагинации)', async () => {
    api.listMail.mockResolvedValue(page([makeMessage(2)], 'cur-x'));
    const { result, rerender } = renderHook(
      ({ unread }: { unread: boolean }) => useMailFeed({ unread }),
      { wrapper: makeWrapper(), initialProps: { unread: false } },
    );
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(api.listMail).toHaveBeenCalledTimes(1);

    rerender({ unread: true });

    await waitFor(() => expect(api.listMail).toHaveBeenCalledTimes(2));
    expect(api.listMail.mock.calls[1][0]).toMatchObject({ unread: true, before: undefined });
  });
});

describe('useMarkMailRead / useUnmarkMailRead — кэш правится ТОЛЬКО по успешному 204 (ADR-050 §2.6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** Лента + обе мутации на ОДНОМ QueryClient — так виден эффект правки кэша ленты. */
  function renderFeedWithMutations() {
    return renderHook(
      () => ({
        feed: useMailFeed(),
        mark: useMarkMailRead(),
        unmark: useUnmarkMailRead(),
      }),
      { wrapper: makeWrapper() },
    );
  }

  it('успешный POST → is_unread=false точечно у ОДНОГО письма, БЕЗ ре-запроса ленты', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2), makeMessage(1)], null));
    api.markMailRead.mockResolvedValueOnce(undefined);
    const { result } = renderFeedWithMutations();
    await waitFor(() => expect(result.current.feed.phase).toBe('ready'));
    expect(result.current.feed.messages.map((m) => m.is_unread)).toEqual([true, true]);

    await act(async () => {
      result.current.mark.mutate(2);
    });

    await waitFor(() =>
      expect(result.current.feed.messages.map((m) => m.is_unread)).toEqual([false, true]),
    );
    expect(api.markMailRead).toHaveBeenCalledWith(2);
    // Полный инвалидэйт ленты ЗАПРЕЩЁН (§2.6): страницы бесконечного скролла не перезапрашиваются.
    expect(api.listMail).toHaveBeenCalledTimes(1);
  });

  it('ошибка POST → кэш НЕ трогается: индикатор продолжает гореть (оптимистика запрещена)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2)], null));
    api.markMailRead.mockRejectedValueOnce(new ApiError(500, 'internal_error', 'boom'));
    const { result } = renderFeedWithMutations();
    await waitFor(() => expect(result.current.feed.phase).toBe('ready'));

    await act(async () => {
      result.current.mark.mutate(2);
    });

    await waitFor(() => expect(result.current.mark.isError).toBe(true));
    // Best-effort: письмо осталось непрочитанным на сервере ⇒ и в UI индикатор горит.
    expect(result.current.feed.messages[0].is_unread).toBe(true);
    expect(api.listMail).toHaveBeenCalledTimes(1);
  });

  it('успешный DELETE → is_unread=true (откат), лента не перезапрашивается', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2, '2026-07-02T09:15:00Z', false)], null));
    api.unmarkMailRead.mockResolvedValueOnce(undefined);
    const { result } = renderFeedWithMutations();
    await waitFor(() => expect(result.current.feed.phase).toBe('ready'));
    expect(result.current.feed.messages[0].is_unread).toBe(false);

    await act(async () => {
      result.current.unmark.mutate(2);
    });

    await waitFor(() => expect(result.current.feed.messages[0].is_unread).toBe(true));
    expect(api.unmarkMailRead).toHaveBeenCalledWith(2);
    expect(api.listMail).toHaveBeenCalledTimes(1);
  });

  it('ошибка DELETE → кэш не трогается (письмо остаётся прочитанным)', async () => {
    api.listMail.mockResolvedValueOnce(page([makeMessage(2, '2026-07-02T09:15:00Z', false)], null));
    api.unmarkMailRead.mockRejectedValueOnce(new ApiError(502, 'mail_unavailable', 'x'));
    const { result } = renderFeedWithMutations();
    await waitFor(() => expect(result.current.feed.phase).toBe('ready'));

    await act(async () => {
      result.current.unmark.mutate(2);
    });

    await waitFor(() => expect(result.current.unmark.isError).toBe(true));
    expect(result.current.feed.messages[0].is_unread).toBe(false);
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
