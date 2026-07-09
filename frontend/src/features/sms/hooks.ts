import { useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteSmsNumber,
  listSmsMessages,
  listSmsNumbers,
  listTeamNumbers,
  SMS_PAGE_LIMIT,
  syncSmsNumbers,
  transferSmsNumber,
  updateSmsNumber,
} from '@/features/sms/api';
import { teamsKey } from '@/features/teams/hooks';
import type {
  SmsMessage,
  SmsNumber,
  SmsNumberTransferRequest,
  SmsNumberUpdateRequest,
  SmsSyncResult,
} from '@/types/api';

export const smsMessagesKey = ['sms', 'messages'] as const;
export const smsNumbersKey = ['sms', 'numbers'] as const;
export const teamNumbersKey = ['sms', 'team-numbers'] as const;

/**
 * Серверный фильтр ленты SMS (комбинируемы — AND, оба опциональны). Часть queryKey:
 * смена фильтра ре-запрашивает ленту (сброс пагинации). 04-api.md#sms.
 */
export interface SmsFeedFilter {
  numberId?: number;
  teamId?: string;
}

/** Фаза ленты для UI: loading — начальная загрузка; ready — получена; error — прочее. */
export type SmsPhase = 'loading' | 'ready' | 'error';

export interface SmsFeedResult {
  /** Аккумулированные SMS всех страниц, дедуп по `id`, порядок `id` DESC (newest-first). */
  messages: SmsMessage[];
  phase: SmsPhase;
  error: unknown;
  /** Есть ли ещё более старые (next_cursor не null). */
  hasMore: boolean;
  isFetchingMore: boolean;
  isReloading: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * Бесконечная лента SMS (newest-first) на TanStack `useInfiniteQuery` с opaque
 * keyset-курсором (образец — features/mail). Первая страница без `cursor`; догрузка —
 * `cursor=<next_cursor>`, пока `next_cursor != null`. Дедуп по `id`, сортировка `id` DESC.
 */
export function useSmsMessages(filter: SmsFeedFilter = {}): SmsFeedResult {
  const { numberId, teamId } = filter;
  const query = useInfiniteQuery({
    queryKey: [
      ...smsMessagesKey,
      { number_id: numberId ?? null, team_id: teamId ?? null },
    ] as const,
    queryFn: ({ pageParam, signal }) =>
      listSmsMessages({ numberId, teamId, cursor: pageParam, limit: SMS_PAGE_LIMIT }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
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

  let phase: SmsPhase = 'ready';
  if (query.status === 'pending') phase = 'loading';
  else if (query.status === 'error') phase = 'error';

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

/** Список номеров — источник дропдауна «Все номера» и вкладки «Номера». */
export function useSmsNumbers() {
  return useQuery({
    queryKey: smsNumbersKey,
    queryFn: ({ signal }) => listSmsNumbers(signal),
    retry: false,
  });
}

/**
 * Инвалидация после мутаций номера: сам список номеров, лента (бейджи/пилюли берутся
 * из текущего номера) и команды (`number_count` на карточке /teams).
 */
function invalidateSmsAndTeams(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: smsNumbersKey });
  void queryClient.invalidateQueries({ queryKey: smsMessagesKey });
  void queryClient.invalidateQueries({ queryKey: teamNumbersKey });
  void queryClient.invalidateQueries({ queryKey: teamsKey });
}

export function useUpdateSmsNumber() {
  const queryClient = useQueryClient();
  return useMutation<SmsNumber, unknown, { id: number; payload: SmsNumberUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateSmsNumber(id, payload),
    onSuccess: () => invalidateSmsAndTeams(queryClient),
  });
}

export function useTransferSmsNumber() {
  const queryClient = useQueryClient();
  return useMutation<SmsNumber, unknown, { id: number; payload: SmsNumberTransferRequest }>({
    mutationFn: ({ id, payload }) => transferSmsNumber(id, payload),
    onSuccess: () => invalidateSmsAndTeams(queryClient),
  });
}

export function useDeleteSmsNumber() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (id) => deleteSmsNumber(id),
    onSuccess: () => invalidateSmsAndTeams(queryClient),
  });
}

export function useSyncSmsNumbers() {
  const queryClient = useQueryClient();
  return useMutation<SmsSyncResult, unknown, void>({
    mutationFn: () => syncSmsNumbers(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: smsNumbersKey });
    },
  });
}

/**
 * Номера команды для detail-панели /teams (ленивая загрузка — запрос идёт только при
 * `enabled`, т.е. когда панель раскрыта). Своё состояние loading/empty/error в панели.
 */
export function useTeamNumbers(teamId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...teamNumbersKey, teamId] as const,
    queryFn: ({ signal }) => listTeamNumbers(teamId, signal),
    enabled,
    retry: false,
  });
}
