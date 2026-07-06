import { useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query';
import {
  listMail,
  listMailboxes,
  listTeams,
  MAIL_PAGE_LIMIT,
  replyMail,
} from '@/features/mail/api';
import { env } from '@/lib/env';
import { ApiError } from '@/lib/api';
import type { MailMessage, MailReplyRequest, MailReplyResponse } from '@/types/api';

export const mailFeedKey = ['mail', 'feed'] as const;
export const mailTeamsKey = ['mail', 'teams'] as const;
export const mailMailboxesKey = ['mail', 'mailboxes'] as const;

/**
 * Серверный фильтр ленты (взаимоисключающи — задан максимум один; в UI выбор одного
 * сбрасывает другой). Часть queryKey ленты: смена фильтра ре-запрашивает ленту
 * (сброс пагинации + авто-выбор свежего письма) — ADR-017, 08-design-system.md.
 */
export interface MailFeedFilter {
  mailAccountId?: number;
  groupId?: number;
}

/**
 * Фаза ленты для UI: loading — начальная загрузка; ready — лента получена;
 * error — 502/прочее; not_configured — 503 (mail_not_configured).
 */
export type MailPhase = 'loading' | 'ready' | 'error' | 'not_configured';

export interface MailFeedResult {
  /** Аккумулированные письма всех страниц, дедуп по `id`, порядок `id` DESC (newest-first). */
  messages: MailMessage[];
  phase: MailPhase;
  /** Ошибка последнего запроса (для различения 401/502 в UI). */
  error: unknown;
  /** Есть ли ещё более старые письма (desc, has_more). */
  hasMore: boolean;
  /** Идёт догрузка более старого батча. */
  isFetchingMore: boolean;
  /** Идёт повторная загрузка после ошибки/reload. */
  isReloading: boolean;
  /** Догрузка более старых (триггерится IntersectionObserver на sentinel). */
  loadMore: () => void;
  /** Полная перезагрузка ленты (кнопка «Повторить»). */
  reload: () => void;
}

/**
 * Бесконечная лента писем (desc, newest-first) на TanStack `useInfiniteQuery`
 * (образец — features/servers/ai-keys; ADR-013, modules/mail «Пагинация»).
 *
 * - Первая страница — `order=desc&limit=20` (без `before_id`) → новейшие 20.
 * - Догрузка старых — `order=desc&before_id=<next_before_id>&limit=20`, пока `has_more`.
 * - Курсор следующей страницы — `next_before_id`; стоп при `has_more=false`.
 * - Дедуп по `id` и сортировка `id` DESC — на этапе flatten (страховка от пересечений).
 *
 * Опциональный фоновый poll новых (prepend `id > max`) НЕ реализован (v1-опция ADR-013):
 * useInfiniteQuery не даёт безопасного prepend без риска регрессии порядка/выбора письма.
 * Свежие письма подтягиваются при перезагрузке ленты (reload). См. summary отчёта frontend.
 */
export function useMailFeed(filter: MailFeedFilter = {}): MailFeedResult {
  const { mailAccountId, groupId } = filter;
  const query = useInfiniteQuery({
    // Фильтр входит в queryKey → его смена запускает новый запрос ленты (сброс пагинации).
    queryKey: [
      ...mailFeedKey,
      { mail_account_id: mailAccountId ?? null, group_id: groupId ?? null },
    ] as const,
    queryFn: ({ pageParam, signal }) =>
      listMail(
        { order: 'desc', beforeId: pageParam, limit: MAIL_PAGE_LIMIT, mailAccountId, groupId },
        signal,
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_before_id ?? undefined) : undefined,
    // Один заход: 502/503/401 отдаём сразу в UI, без ретрай-задержек (внешний rate-limit).
    retry: false,
    refetchOnWindowFocus: false,
  });

  const messages = useMemo<MailMessage[]>(() => {
    const byId = new Map<number, MailMessage>();
    for (const page of query.data?.pages ?? []) {
      for (const m of page.messages) byId.set(m.id, m);
    }
    return [...byId.values()].sort((a, b) => b.id - a.id);
  }, [query.data]);

  let phase: MailPhase = 'ready';
  if (query.status === 'pending') {
    phase = 'loading';
  } else if (query.status === 'error') {
    phase =
      query.error instanceof ApiError && query.error.status === 503 ? 'not_configured' : 'error';
  }

  return {
    messages,
    phase,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    isReloading: query.isRefetching,
    loadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
    },
    reload: () => {
      void query.refetch();
    },
  };
}

/**
 * Отправка ответа на письмо (inline-reply). Маппинг кодов ошибок (404/422/400/502) —
 * на стороне формы (MailReplyForm), т.к. привязан к полю `body`. Инвалидация ленты не нужна:
 * исходящий ответ во входящую ленту не попадает.
 */
export function useReplyMail(messageId: number) {
  return useMutation<MailReplyResponse, unknown, MailReplyRequest>({
    mutationFn: (payload) => replyMail(messageId, payload),
  });
}

/**
 * Справочник команд для дропдауна «Команда» (серверный фильтр) — GET /api/mail/teams.
 * Также используется вне «Почт»; polling как у списков servers/ai-keys.
 */
export function useMailTeams() {
  return useQuery({
    queryKey: mailTeamsKey,
    queryFn: ({ signal }) => listTeams(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
    retry: false,
  });
}

/**
 * Справочник почтовых ящиков — GET /api/mail/mailboxes. Источник дропдауна «Почта»
 * (серверный фильтр) и счётчиков карточки «Почты» на «Дашборде» (клиентский подсчёт
 * is_active). Polling как у списков servers/ai-keys.
 */
export function useMailMailboxes() {
  return useQuery({
    queryKey: mailMailboxesKey,
    queryFn: ({ signal }) => listMailboxes(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
    retry: false,
  });
}
