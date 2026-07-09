import { useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  applyTagToExisting,
  createMailbox,
  createTag,
  createTagRule,
  deleteMailbox,
  deleteTag,
  deleteTagRule,
  listMail,
  listMailboxes,
  listTags,
  listTeamMailboxes,
  listTeams,
  MAIL_PAGE_LIMIT,
  replyMail,
  syncMailbox,
  testMailbox,
  updateMailbox,
  updateTag,
} from '@/features/mail/api';
import { env } from '@/lib/env';
import { ApiError } from '@/lib/api';
import type {
  MailMailbox,
  MailMailboxCreateRequest,
  MailMailboxSyncResponse,
  MailMailboxTestRequest,
  MailMailboxTestResponse,
  MailMailboxUpdateRequest,
  MailMessage,
  MailReplyRequest,
  MailReplyResponse,
  MailTagApplyResponse,
  MailTagCreateRequest,
  MailTagFull,
  MailTagRule,
  MailTagRuleCreateRequest,
  MailTagUpdateRequest,
} from '@/types/api';

export const mailFeedKey = ['mail', 'feed'] as const;
export const mailTeamsKey = ['mail', 'teams'] as const;
export const mailMailboxesKey = ['mail', 'mailboxes'] as const;
export const mailTagsKey = ['mail', 'tags'] as const;
export const teamMailboxesKey = ['teams', 'mailboxes'] as const;

/**
 * Серверный фильтр ленты (**комбинируемы — AND, ADR-038 §3**; оба опциональны). Часть
 * queryKey ленты: смена фильтра ре-запрашивает ленту (сброс пагинации + авто-выбор
 * свежего письма) — ADR-017, 08-design-system.md.
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
 * `enabled` (default `true`) — фильтр «Команда» рендерится только admin-уровню
 * (`sees_all_mail_teams`, ADR-038 §3); прочим ролям справочник не грузится (анти-энумерация).
 */
export function useMailTeams(enabled = true) {
  return useQuery({
    queryKey: mailTeamsKey,
    queryFn: ({ signal }) => listTeams(signal),
    enabled,
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
    retry: false,
  });
}

/**
 * Справочник почтовых ящиков (без фильтра) — GET /api/mail/mailboxes. Источник дропдауна
 * «Почта» (серверный фильтр ленты) и счётчиков карточки «Почты» на «Дашборде» (клиентский
 * подсчёт is_active). Polling как у списков servers/ai-keys.
 */
export function useMailMailboxes() {
  return useQuery({
    queryKey: mailMailboxesKey,
    queryFn: ({ signal }) => listMailboxes({}, signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
    retry: false,
  });
}

/**
 * Список ящиков для вкладки «Почты» с серверным фильтром активности (04-api.md
 * `is_active`). `isActive` входит в queryKey — смена сегмента ре-запрашивает список.
 * Polling обновляет статус синка (кружок). Мутации ящиков инвалидируют весь префикс
 * `['mail','mailboxes']` (покрывает и этот, и справочник дропдауна).
 */
export function useMailboxesManage(isActive?: boolean) {
  return useQuery({
    queryKey: [...mailMailboxesKey, { is_active: isActive ?? null }] as const,
    queryFn: ({ signal }) => listMailboxes({ isActive }, signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

/** Инвалидация всех представлений ящиков (справочник + фильтрованный список + ящики команды). */
function invalidateMailboxes(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: mailMailboxesKey });
  void queryClient.invalidateQueries({ queryKey: teamMailboxesKey });
}

/** POST /api/mail/mailboxes/test — проверка соединения (мутация без инвалидации). */
export function useTestMailbox() {
  return useMutation<MailMailboxTestResponse, unknown, MailMailboxTestRequest>({
    mutationFn: (payload) => testMailbox(payload),
  });
}

export function useCreateMailbox() {
  const queryClient = useQueryClient();
  return useMutation<MailMailbox, unknown, MailMailboxCreateRequest>({
    mutationFn: (payload) => createMailbox(payload),
    onSuccess: () => invalidateMailboxes(queryClient),
  });
}

export function useUpdateMailbox() {
  const queryClient = useQueryClient();
  return useMutation<MailMailbox, unknown, { id: number; payload: MailMailboxUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateMailbox(id, payload),
    onSuccess: () => invalidateMailboxes(queryClient),
  });
}

export function useDeleteMailbox() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (id) => deleteMailbox(id),
    onSuccess: () => invalidateMailboxes(queryClient),
  });
}

export function useSyncMailbox() {
  const queryClient = useQueryClient();
  return useMutation<MailMailboxSyncResponse, unknown, number>({
    mutationFn: (id) => syncMailbox(id),
    // Синк меняет last_synced_at/consecutive_failures — обновляем список после постановки.
    onSuccess: () => invalidateMailboxes(queryClient),
  });
}

// --- Теги (глобальный каталог, гейт mail:tags) ---

/** Список глобальных тегов с правилами — GET /api/mail/tags. */
export function useMailTags() {
  return useQuery({
    queryKey: mailTagsKey,
    queryFn: ({ signal }) => listTags(signal),
    retry: false,
  });
}

/** Инвалидация каталога тегов и ленты (теги письма меняются при apply/правке правил). */
function invalidateTags(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: mailTagsKey });
  void queryClient.invalidateQueries({ queryKey: mailFeedKey });
}

export function useCreateTag() {
  const queryClient = useQueryClient();
  return useMutation<MailTagFull, unknown, MailTagCreateRequest>({
    mutationFn: (payload) => createTag(payload),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useUpdateTag() {
  const queryClient = useQueryClient();
  return useMutation<MailTagFull, unknown, { id: number; payload: MailTagUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateTag(id, payload),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useDeleteTag() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (id) => deleteTag(id),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useCreateTagRule() {
  const queryClient = useQueryClient();
  return useMutation<MailTagRule, unknown, { tagId: number; payload: MailTagRuleCreateRequest }>({
    mutationFn: ({ tagId, payload }) => createTagRule(tagId, payload),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useDeleteTagRule() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, { tagId: number; ruleId: number }>({
    mutationFn: ({ tagId, ruleId }) => deleteTagRule(tagId, ruleId),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useApplyTag() {
  const queryClient = useQueryClient();
  return useMutation<MailTagApplyResponse, unknown, number>({
    mutationFn: (tagId) => applyTagToExisting(tagId),
    // Применение навешивает тег на существующие письма → лента обновляется.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: mailFeedKey }),
  });
}

/**
 * Ящики команды для detail-панели /teams (ленивая загрузка — запрос идёт только при
 * `enabled`, т.е. когда секция раскрыта). Своё состояние loading/empty/error в панели.
 */
export function useTeamMailboxes(teamId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...teamMailboxesKey, teamId] as const,
    queryFn: ({ signal }) => listTeamMailboxes(teamId, signal),
    enabled,
    retry: false,
  });
}
