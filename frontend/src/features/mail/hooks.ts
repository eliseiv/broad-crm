import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { listMail, MAIL_PAGE_LIMIT, replyMail } from '@/features/mail/api';
import { ApiError } from '@/lib/api';
import { env } from '@/lib/env';
import type { MailMessage, MailReplyRequest, MailReplyResponse } from '@/types/api';

type MailPhase = 'loading' | 'ready' | 'error' | 'not_configured';

export interface MailFeedResult {
  messages: MailMessage[];
  /** loading — начальная загрузка; ready — лента получена; error — 502/прочее; not_configured — 503. */
  phase: MailPhase;
  error: unknown;
  hasMore: boolean;
  isFetchingMore: boolean;
  isRefreshing: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * Слияние батчей: дедуп по `id` (свежий батч перезаписывает), сортировка `id` DESC —
 * новые сверху в пределах загруженных писем (modules/mail «Пагинация», 04-api.md).
 */
function mergeById(prev: MailMessage[], incoming: MailMessage[]): MailMessage[] {
  if (incoming.length === 0) return prev;
  const byId = new Map<number, MailMessage>();
  for (const m of prev) byId.set(m.id, m);
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => b.id - a.id);
}

/**
 * Лента писем с keyset-пагинацией вперёд (read-through, server-side поиска/фильтров нет —
 * TD-024). Аккумулирует батчи локально; курсор `next_since_id` растёт вперёд.
 *
 * - `loadMore` — ручная догрузка следующего батча (`since_id = next_since_id`), пока `has_more`.
 * - Фоновый polling (`env.pollIntervalMs`) подтягивает новоприбывшие письма, но ТОЛЬКО когда
 *   `has_more=false` (пользователь дочитал до конца) — чтобы не подменять ручную пагинацию
 *   и не опрашивать внешний сервис агрессивнее необходимого (rate-limit, modules/mail).
 * - Ответ (reply) не влияет на входящую ленту (исходящее письмо в inbox не попадает),
 *   поэтому инвалидация ленты после reply не требуется.
 */
export function useMailFeed(): MailFeedResult {
  const [messages, setMessages] = useState<MailMessage[]>([]);
  const [phase, setPhase] = useState<MailPhase>('loading');
  const [error, setError] = useState<unknown>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const cursorRef = useRef(0); // максимальный next_since_id (keyset вперёд)
  const epochRef = useRef(0); // защита от устаревших ответов после reload
  const inFlightRef = useRef(false); // защита от параллельных forward-запросов
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runInitial = useCallback(async (isRefresh: boolean) => {
    const epoch = ++epochRef.current;
    cursorRef.current = 0;
    if (isRefresh) setIsRefreshing(true);
    else setPhase('loading');
    try {
      const res = await listMail(undefined, MAIL_PAGE_LIMIT);
      if (!mountedRef.current || epoch !== epochRef.current) return;
      cursorRef.current = res.next_since_id ?? 0;
      setMessages(mergeById([], res.messages));
      setHasMore(res.has_more);
      setError(null);
      setPhase('ready');
    } catch (err) {
      if (!mountedRef.current || epoch !== epochRef.current) return;
      setError(err);
      if (err instanceof ApiError && err.status === 503) setPhase('not_configured');
      else if (err instanceof ApiError && err.status === 401) setPhase('error');
      else setPhase('error');
    } finally {
      if (mountedRef.current && epoch === epochRef.current) setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void runInitial(false);
  }, [runInitial]);

  const loadMore = useCallback(async () => {
    if (inFlightRef.current || !hasMore) return;
    inFlightRef.current = true;
    setIsFetchingMore(true);
    const epoch = epochRef.current;
    try {
      const res = await listMail(cursorRef.current, MAIL_PAGE_LIMIT);
      if (!mountedRef.current || epoch !== epochRef.current) return;
      cursorRef.current = Math.max(cursorRef.current, res.next_since_id ?? cursorRef.current);
      setMessages((prev) => mergeById(prev, res.messages));
      setHasMore(res.has_more);
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof ApiError && err.status === 401) return; // сессия сброшена — редиректит роутер
      toast.error(
        err instanceof ApiError && err.status === 502
          ? 'Почтовый сервис временно недоступен'
          : 'Не удалось загрузить письма',
      );
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) setIsFetchingMore(false);
    }
  }, [hasMore]);

  // Фоновый опрос новых писем — только когда лента дочитана до конца (has_more=false).
  useEffect(() => {
    if (phase !== 'ready') return;
    const intervalId = window.setInterval(() => {
      if (inFlightRef.current || hasMore) return;
      inFlightRef.current = true;
      const epoch = epochRef.current;
      listMail(cursorRef.current, MAIL_PAGE_LIMIT)
        .then((res) => {
          if (!mountedRef.current || epoch !== epochRef.current) return;
          cursorRef.current = Math.max(cursorRef.current, res.next_since_id ?? cursorRef.current);
          setMessages((prev) => mergeById(prev, res.messages));
          setHasMore(res.has_more);
        })
        .catch(() => {
          // Фоновый опрос: ошибки не показываем toast-спамом (modules/mail «Состояния UI»).
        })
        .finally(() => {
          inFlightRef.current = false;
        });
    }, env.pollIntervalMs);
    return () => window.clearInterval(intervalId);
  }, [phase, hasMore]);

  const reload = useCallback(() => {
    void runInitial(true);
  }, [runInitial]);

  return { messages, phase, error, hasMore, isFetchingMore, isRefreshing, loadMore, reload };
}

/**
 * Отправка ответа на письмо. Маппинг кодов ошибок (404/422/502/…) в понятные сообщения —
 * на стороне формы (ReplyModal), т.к. привязан к полю `body`. Инвалидация ленты не нужна:
 * исходящий ответ во входящую ленту не попадает.
 */
export function useReplyMail(messageId: number) {
  return useMutation<MailReplyResponse, unknown, MailReplyRequest>({
    mutationFn: (payload) => replyMail(messageId, payload),
  });
}
