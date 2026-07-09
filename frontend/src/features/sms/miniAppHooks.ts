import { useMemo } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { listSmsMessages, listSmsNumbers, SMS_PAGE_LIMIT } from '@/features/sms/api';
import { getMiniAppToken } from '@/features/sms/miniAppAuth';
import type { SmsMessage } from '@/types/api';

/**
 * Хуки просмотра для операторской Mini App (`/tg/sms`, ADR-031). Ходят на те же
 * JWT-эндпоинты `GET /api/sms/numbers`/`GET /api/sms/messages` под `sms:view`
 * (SMS-scope сужен до команд оператора), но с ИЗОЛИРОВАННЫМ SSO-токеном Mini App
 * (`getMiniAppToken()`), не задевая админский auth-стор. Отдельные queryKey
 * (`sms-miniapp`) — чтобы кэш не пересекался с админской страницей «СМС».
 */

/** GET /api/sms/numbers под SSO-токеном Mini App. Без `sms:view` сервер вернёт 403. */
export function useMiniAppSmsNumbers(enabled: boolean) {
  return useQuery({
    queryKey: ['sms-miniapp', 'numbers'] as const,
    queryFn: ({ signal }) => listSmsNumbers(signal, getMiniAppToken() ?? undefined),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Фаза ленты Mini App: loading — начальная загрузка; ready — получена; error — прочее. */
export type MiniAppFeedPhase = 'loading' | 'ready' | 'error';

export interface MiniAppMessagesResult {
  messages: SmsMessage[];
  phase: MiniAppFeedPhase;
  error: unknown;
  hasMore: boolean;
  isFetchingMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * Бесконечная лента SMS оператора (newest-first, keyset-курсор) под SSO-токеном
 * Mini App. Аналог `useSmsMessages`, но с изолированным токеном и своим queryKey.
 */
export function useMiniAppSmsMessages(enabled: boolean): MiniAppMessagesResult {
  const query = useInfiniteQuery({
    queryKey: ['sms-miniapp', 'messages'] as const,
    queryFn: ({ pageParam, signal }) =>
      listSmsMessages(
        { cursor: pageParam, limit: SMS_PAGE_LIMIT },
        signal,
        getMiniAppToken() ?? undefined,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const messages = useMemo<SmsMessage[]>(() => {
    const byId = new Map<number, SmsMessage>();
    for (const page of query.data?.pages ?? []) {
      for (const m of page.messages) byId.set(m.id, m);
    }
    return [...byId.values()].sort((a, b) => b.id - a.id);
  }, [query.data]);

  let phase: MiniAppFeedPhase = 'ready';
  if (query.status === 'pending') phase = 'loading';
  else if (query.status === 'error') phase = 'error';

  return {
    messages,
    phase,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
    },
    reload: () => {
      void query.refetch();
    },
  };
}
