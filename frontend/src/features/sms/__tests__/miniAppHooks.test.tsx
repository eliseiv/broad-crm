import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMiniAppSmsMessages, useMiniAppSmsNumbers } from '@/features/sms/miniAppHooks';
import { useMiniAppAuthStore } from '@/features/sms/miniAppAuth';
import { ApiError } from '@/lib/api';
import type { SmsMessage, SmsMessagesResponse, SmsNumber } from '@/types/api';

/**
 * Хуки данных Mini App (`/tg/sms`, ADR-031): изолированный SSO-токен (getMiniAppToken)
 * пробрасывается в api-слой как `authToken` (НЕ токен админ-стора), свой queryKey.
 * Мокаем api-слой (`@/features/sms/api`), auth-store Mini App — РЕАЛЬНЫЙ.
 */
const api = vi.hoisted(() => ({
  listSmsNumbers: vi.fn(),
  listSmsMessages: vi.fn(),
  SMS_PAGE_LIMIT: 50,
}));

vi.mock('@/features/sms/api', () => api);

function makeNumber(id: number, over: Partial<SmsNumber> = {}): SmsNumber {
  return {
    id,
    phone_number: `+1555000${id}`,
    label: null,
    team: null,
    login: null,
    app_name: null,
    note: null,
    is_active: true,
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
    ...over,
  };
}

function makeMessage(id: number): SmsMessage {
  return {
    id,
    from_number: '+15550000001',
    to_number: '+15550000002',
    body: `SMS ${id}`,
    received_at: '2026-07-02T09:15:00Z',
    number: null,
  };
}

function page(messages: SmsMessage[], nextCursor: string | null): SmsMessagesResponse {
  return { messages, next_cursor: nextCursor };
}

/** Свежий QueryClient на каждый тест — изоляция кэша между прогонами. */
function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('useMiniAppSmsNumbers (SSO-токен, sms:view)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMiniAppAuthStore.getState().clear();
  });

  it('пробрасывает изолированный SSO-токен Mini App в listSmsNumbers как authToken', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-abc', 42);
    api.listSmsNumbers.mockResolvedValueOnce({ numbers: [makeNumber(5)] });
    const { result } = renderHook(() => useMiniAppSmsNumbers(true), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.numbers.map((n) => n.id)).toEqual([5]);
    // listSmsNumbers(signal, authToken): authToken — именно SSO-токен Mini App.
    expect(api.listSmsNumbers).toHaveBeenCalledTimes(1);
    expect(api.listSmsNumbers.mock.calls[0][1]).toBe('sso-token-abc');
  });

  it('без сессии Mini App authToken = undefined (не подставляет чужой токен)', async () => {
    api.listSmsNumbers.mockResolvedValueOnce({ numbers: [] });
    const { result } = renderHook(() => useMiniAppSmsNumbers(true), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.listSmsNumbers.mock.calls[0][1]).toBeUndefined();
  });

  it('403 (нет sms:view) → isError с ApiError 403', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-abc', 42);
    api.listSmsNumbers.mockRejectedValueOnce(new ApiError(403, 'forbidden', 'Недостаточно прав'));
    const { result } = renderHook(() => useMiniAppSmsNumbers(true), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(403);
  });

  it('enabled=false → запрос не уходит (SSO ещё не завершён)', () => {
    renderHook(() => useMiniAppSmsNumbers(false), { wrapper: makeWrapper() });
    expect(api.listSmsNumbers).not.toHaveBeenCalled();
  });
});

describe('useMiniAppSmsMessages (SSO-токен, keyset-лента)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMiniAppAuthStore.getState().clear();
  });

  it('первая страница newest-first под SSO-токеном (cursor=undefined, limit=50)', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-xyz', 42);
    api.listSmsMessages.mockResolvedValueOnce(page([makeMessage(2), makeMessage(1)], null));
    const { result } = renderHook(() => useMiniAppSmsMessages(true), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(result.current.messages.map((m) => m.id)).toEqual([2, 1]);
    expect(result.current.hasMore).toBe(false);
    // listSmsMessages(params, signal, authToken): params + изолированный токен.
    expect(api.listSmsMessages.mock.calls[0][0]).toEqual({ cursor: undefined, limit: 50 });
    expect(api.listSmsMessages.mock.calls[0][2]).toBe('sso-token-xyz');
  });

  it('пагинация по next_cursor: loadMore шлёт cursor прошлой страницы, дедуп по id, DESC', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-xyz', 42);
    api.listSmsMessages
      .mockResolvedValueOnce(page([makeMessage(5), makeMessage(4)], 'cursor-1'))
      .mockResolvedValueOnce(page([makeMessage(4), makeMessage(3)], null));
    const { result } = renderHook(() => useMiniAppSmsMessages(true), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.hasMore).toBe(false));
    expect(api.listSmsMessages.mock.calls[1][0]).toEqual({ cursor: 'cursor-1', limit: 50 });
    // Дедуп: сообщение 4 не задвоилось; порядок — новые сверху.
    expect(result.current.messages.map((m) => m.id)).toEqual([5, 4, 3]);
  });

  it('loadMore идемпотентен при hasMore=false (нет лишнего запроса)', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-xyz', 42);
    api.listSmsMessages.mockResolvedValueOnce(page([makeMessage(1)], null));
    const { result } = renderHook(() => useMiniAppSmsMessages(true), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.phase).toBe('ready'));

    await act(async () => {
      result.current.loadMore();
    });

    expect(api.listSmsMessages).toHaveBeenCalledTimes(1);
  });

  it('403 (нет sms:view) → phase error с ApiError 403', async () => {
    useMiniAppAuthStore.getState().setSession('sso-token-xyz', 42);
    api.listSmsMessages.mockRejectedValueOnce(new ApiError(403, 'forbidden', 'Недостаточно прав'));
    const { result } = renderHook(() => useMiniAppSmsMessages(true), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.phase).toBe('error'));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(403);
    expect(result.current.messages).toEqual([]);
  });

  it('enabled=false → лента не запрашивается', () => {
    renderHook(() => useMiniAppSmsMessages(false), { wrapper: makeWrapper() });
    expect(api.listSmsMessages).not.toHaveBeenCalled();
  });
});
