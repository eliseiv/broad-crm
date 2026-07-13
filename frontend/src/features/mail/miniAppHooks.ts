import { useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { InfiniteData } from '@tanstack/react-query';
import { listMail, markMailRead, MAIL_PAGE_LIMIT } from '@/features/mail/api';
import { getMailMiniAppToken } from '@/features/mail/miniAppAuth';
import type { MailListResponse, MailMessage } from '@/types/api';

/** queryKey ленты Mini App (изолирован от админской страницы «Почты»). */
const miniAppFeedKey = ['mail-miniapp', 'messages'] as const;

/**
 * Хуки просмотра ленты для Mini App почты (`/tg/mail`, ADR-044 §7). Ходят на тот же
 * JWT-эндпоинт `GET /api/mail/messages` под `mail:view` (MailScope сужен до команд
 * пользователя), но с ИЗОЛИРОВАННЫМ SSO-токеном Mini App (`getMailMiniAppToken()`),
 * не задевая админский auth-стор. Отдельный queryKey (`mail-miniapp`) — чтобы кэш не
 * пересекался с админской страницей «Почты».
 */

/** Фаза ленты Mini App: loading — начальная загрузка; ready — получена; error — прочее. */
export type MailMiniAppFeedPhase = 'loading' | 'ready' | 'error';

export interface MailMiniAppMessagesResult {
  messages: MailMessage[];
  phase: MailMiniAppFeedPhase;
  error: unknown;
  hasMore: boolean;
  isFetchingMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * Бесконечная лента писем оператора (newest-first, компаундный keyset-курсор) под
 * SSO-токеном Mini App. Аккумуляция с дедупом по `id` и сохранением порядка сервера
 * (`internal_date DESC, id DESC`) — НЕ сортируем по `id` (порядок push'а, ADR-044 §2).
 */
export function useMailMiniAppFeed(enabled: boolean): MailMiniAppMessagesResult {
  const query = useInfiniteQuery({
    queryKey: miniAppFeedKey,
    queryFn: ({ pageParam, signal }) =>
      // skipAuthReset: 401 (истёкший SSO-JWT Mini App) обрабатывается локально через
      // phase='error', НЕ вызывая clearSession() — изолированная Mini-App-сессия не
      // должна стирать админский стор `crm.auth.*` (ADR-044 §7).
      listMail(
        { before: pageParam, limit: MAIL_PAGE_LIMIT },
        signal,
        getMailMiniAppToken() ?? undefined,
        true,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const messages = useMemo<MailMessage[]>(() => {
    const byId = new Map<number, MailMessage>();
    for (const page of query.data?.pages ?? []) {
      for (const m of page.messages) {
        if (!byId.has(m.id)) byId.set(m.id, m);
      }
    }
    return [...byId.values()];
  }, [query.data]);

  let phase: MailMiniAppFeedPhase = 'ready';
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

/**
 * Пометка письма прочитанным ПРИ ОТКРЫТИИ в Mini App (`POST /api/mail/messages/{id}/read`,
 * ADR-050 §2.6) — **тем же эндпоинтом**, что и веб: Mini App несёт обычный CRM access-JWT с
 * `uid` реального пользователя (SSO), поэтому личная прочитанность работает идентично.
 * Спец-эндпоинта для Mini App нет и не нужно. `skipAuthReset` — истёкший SSO-JWT не роняет
 * админ-стор. Best-effort: ошибка не ломает показ письма. Кэш ленты Mini App правится
 * локально (без инвалидэйта — иначе перезапрос всех страниц бесконечного скролла).
 */
export function useMarkMailMiniAppRead() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (messageId) => markMailRead(messageId, getMailMiniAppToken() ?? undefined, true),
    onSuccess: (_data, messageId) => {
      queryClient.setQueryData<InfiniteData<MailListResponse>>(miniAppFeedKey, (data) => {
        if (!data) return data;
        return {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            messages: page.messages.map((m) =>
              m.id === messageId ? { ...m, is_unread: false } : m,
            ),
          })),
        };
      });
    },
  });
}
