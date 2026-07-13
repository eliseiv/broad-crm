import { useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { InfiniteData } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  applyTagToExisting,
  createMailbox,
  createTag,
  createTagRule,
  deleteMailbox,
  deleteTag,
  deleteTagRule,
  getMailSettings,
  listMail,
  listMailboxes,
  listTags,
  listTeamMailboxes,
  mailboxOAuthAuthorize,
  markMailRead,
  MAIL_PAGE_LIMIT,
  replyMail,
  syncMailbox,
  testMailbox,
  unmarkMailRead,
  updateMailbox,
  updateMailSettings,
  updateTag,
} from '@/features/mail/api';
import { env } from '@/lib/env';
import { ApiError } from '@/lib/api';
import type {
  MailListResponse,
  MailMailbox,
  MailMailboxCreateRequest,
  MailMailboxSyncResponse,
  MailMailboxTestRequest,
  MailMailboxTestResponse,
  MailMailboxUpdateRequest,
  MailMessage,
  MailOauthAuthorizeResponse,
  MailReplyRequest,
  MailReplyResponse,
  MailTagApplyResponse,
  MailTagCreateRequest,
  MailTagFull,
  MailTagRule,
  MailTagRuleCreateRequest,
  MailTagUpdateRequest,
  MailUserSettings,
} from '@/types/api';

export const mailFeedKey = ['mail', 'feed'] as const;
export const mailMailboxesKey = ['mail', 'mailboxes'] as const;
export const mailTagsKey = ['mail', 'tags'] as const;
export const mailSettingsKey = ['mail', 'settings'] as const;
export const teamMailboxesKey = ['teams', 'mailboxes'] as const;

/**
 * Серверный фильтр ленты (**комбинируемы — AND, ADR-044 §7**; оба опциональны). Часть
 * queryKey ленты: смена фильтра ре-запрашивает ленту (сброс пагинации + авто-выбор
 * свежего письма). `teamId` — UUID CRM-команды (групп агрегатора больше нет).
 */
export interface MailFeedFilter {
  mailAccountId?: number;
  teamId?: string;
  /**
   * Тумблер «Непрочитанные» — **СЕРВЕРНЫЙ** (ADR-050 §2.8): входит в queryKey, включение
   * сбрасывает пагинацию и шлёт первый запрос без `before` с `unread=true`. Клиентская
   * фильтрация непрочитанных ЗАПРЕЩЕНА (сломала бы курсорную догрузку).
   */
  unread?: boolean;
}

/**
 * Фаза ленты для UI: loading — начальная загрузка; ready — лента получена;
 * error — 502/прочее; not_configured — 503 (mail_not_configured).
 */
export type MailPhase = 'loading' | 'ready' | 'error' | 'not_configured';

export interface MailFeedResult {
  /** Аккумулированные письма всех страниц, дедуп по `id`, порядок сервера (internal_date DESC, id DESC). */
  messages: MailMessage[];
  phase: MailPhase;
  /** Ошибка последнего запроса (для различения 401/502 в UI). */
  error: unknown;
  /** Есть ли ещё более старые письма (курсор `next_cursor` не пуст). */
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
 * Аккумуляция страниц с дедупом по `id` и СОХРАНЕНИЕМ порядка сервера
 * (`internal_date DESC, id DESC`). Клиент НЕ сортирует по `id`: `id` отражает порядок
 * прихода push'а, а не дату письма (ADR-044 §2, MAJOR-8) — сортировка по id всплыла бы
 * ре-пушнутое старое письмо в топ. `Map` сохраняет порядок вставки → порядок сервера.
 */
function flattenPages(pages: { messages: MailMessage[] }[] | undefined): MailMessage[] {
  const byId = new Map<number, MailMessage>();
  for (const page of pages ?? []) {
    for (const m of page.messages) {
      if (!byId.has(m.id)) byId.set(m.id, m);
    }
  }
  return [...byId.values()];
}

/**
 * Бесконечная лента писем (newest-first, компаундный keyset-курсор) на TanStack
 * `useInfiniteQuery`. Первая страница — без `before` → новейшие 20; догрузка старых —
 * `before=<next_cursor>`, пока `next_cursor` не `null`.
 */
export function useMailFeed(filter: MailFeedFilter = {}): MailFeedResult {
  const { mailAccountId, teamId, unread } = filter;
  const query = useInfiniteQuery({
    // Фильтр входит в queryKey → его смена запускает новый запрос ленты (сброс пагинации).
    queryKey: [
      ...mailFeedKey,
      {
        mail_account_id: mailAccountId ?? null,
        team_id: teamId ?? null,
        unread: unread ?? false,
      },
    ] as const,
    queryFn: ({ pageParam, signal }) =>
      listMail(
        { before: pageParam, limit: MAIL_PAGE_LIMIT, mailAccountId, teamId, unread },
        signal,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Один заход: 502/503/401 отдаём сразу в UI, без ретрай-задержек.
    retry: false,
    refetchOnWindowFocus: false,
  });

  const messages = useMemo<MailMessage[]>(() => flattenPages(query.data?.pages), [query.data]);

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

// --- Личная прочитанность писем (ADR-050 §2; гейт `mail:view`) ---

/**
 * Локальная правка `is_unread` в УЖЕ ЗАГРУЖЕННОМ кэше ленты (все queryKey префикса
 * `['mail','feed']`, т.е. все комбинации серверных фильтров). **Полный инвалидэйт ленты
 * после отметки ЗАПРЕЩЁН** (ADR-050 §2.6): он перезапрашивал бы все страницы бесконечного
 * скролла на каждый клик по письму. Открытие письма при активном фильтре «Непрочитанные»
 * поэтому НЕ удаляет строку из текущего списка (ADR-050 §2.8) — она остаётся на месте до
 * следующего ре-запроса ленты.
 */
function patchFeedUnread(
  queryClient: ReturnType<typeof useQueryClient>,
  messageId: number,
  isUnread: boolean,
): void {
  queryClient.setQueriesData<InfiniteData<MailListResponse>>({ queryKey: mailFeedKey }, (data) => {
    if (!data) return data;
    return {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        messages: page.messages.map((m) =>
          m.id === messageId ? { ...m, is_unread: isUnread } : m,
        ),
      })),
    };
  });
}

/**
 * POST /api/mail/messages/{id}/read — пометить письмо прочитанным (личная прочитанность).
 * Триггер — **смена выбранного письма** (включая авто-выбор самого свежего), НЕ каждый рендер
 * (ADR-050 §2.6). **Best-effort:** ошибка не блокирует показ письма и не даёт toast-спама —
 * максимум индикатор останется гореть. Успех → локальная правка кэша ленты (без инвалидэйта).
 */
export function useMarkMailRead() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (messageId) => markMailRead(messageId),
    onSuccess: (_data, messageId) => patchFeedUnread(queryClient, messageId, false),
  });
}

/**
 * DELETE /api/mail/messages/{id}/read — вернуть письмо в «непрочитано» (кнопка «Отметить
 * непрочитанным» в шапке детали, ADR-050 §2.7). Письмо остаётся ОТКРЫТЫМ (деталь не
 * закрывается, авто-пометка повторно не срабатывает — триггер = смена письма).
 *
 * **Ошибка → toast (в отличие от `POST …/read`).** Норма «best-effort, без toast-спама»
 * (ADR-050 §2.6) привязана к АВТОМАТИЧЕСКОЙ пометке при смене письма («письмо открыто и
 * читается» — молчание там осмысленно). `DELETE` — **явное одиночное действие пользователя**
 * (§2.7 описывает только успешный путь `204`), и его молчаливый провал оставил бы клик без
 * всякой обратной связи. Кэш при ошибке НЕ трогаем: письмо на сервере осталось прочитанным,
 * поэтому погашенный индикатор — корректное состояние.
 */
export function useUnmarkMailRead() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, number>({
    mutationFn: (messageId) => unmarkMailRead(messageId),
    onSuccess: (_data, messageId) => patchFeedUnread(queryClient, messageId, true),
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : 'Не удалось отметить письмо непрочитанным',
      );
    },
  });
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
 * Список ящиков для вкладки «Почты» с серверным фильтром активности (ADR-044 §4
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

/**
 * POST /api/mail/mailboxes/oauth/authorize — инициировать OAuth-подключение Outlook
 * (ADR-045 §3). Мутация без инвалидации: ящик появится позже (агрегатор → /oauth/ingest),
 * форма пуллит список ящиков, пока открыта панель-ссылка. Variables — `team_id` (UUID|null).
 */
export function useMailboxOAuthAuthorize() {
  return useMutation<MailOauthAuthorizeResponse, unknown, string | null>({
    mutationFn: (teamId) => mailboxOAuthAuthorize(teamId),
  });
}

// --- Теги (глобальный каталог, гейт mail:tags; `id` — UUID) ---

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
  return useMutation<MailTagFull, unknown, { id: string; payload: MailTagUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateTag(id, payload),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useDeleteTag() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => deleteTag(id),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useCreateTagRule() {
  const queryClient = useQueryClient();
  return useMutation<MailTagRule, unknown, { tagId: string; payload: MailTagRuleCreateRequest }>({
    mutationFn: ({ tagId, payload }) => createTagRule(tagId, payload),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useDeleteTagRule() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, { tagId: string; ruleId: string }>({
    mutationFn: ({ tagId, ruleId }) => deleteTagRule(tagId, ruleId),
    onSuccess: () => invalidateTags(queryClient),
  });
}

export function useApplyTag() {
  const queryClient = useQueryClient();
  return useMutation<MailTagApplyResponse, unknown, string>({
    mutationFn: (tagId) => applyTagToExisting(tagId),
    // Применение навешивает тег на существующие письма → лента обновляется.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: mailFeedKey }),
  });
}

// --- Персональные настройки уведомлений (opt-out, гейт mail:view, ADR-044 §2) ---

/**
 * Состояние opt-out Telegram-уведомлений — GET /api/mail/me/settings. `enabled`
 * позволяет не грузить настройки там, где строки нет (супер-админ из `.env` → 403).
 */
export function useMailSettings(enabled = true) {
  return useQuery({
    queryKey: mailSettingsKey,
    queryFn: ({ signal }) => getMailSettings(signal),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** PATCH /api/mail/me/settings — переключить уведомления; обновляет кэш настроек. */
export function useUpdateMailSettings() {
  const queryClient = useQueryClient();
  return useMutation<MailUserSettings, unknown, boolean>({
    mutationFn: (enabled) => updateMailSettings({ tg_notifications_enabled: enabled }),
    onSuccess: (data) => queryClient.setQueryData(mailSettingsKey, data),
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
