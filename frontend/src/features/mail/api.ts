import { apiRequest } from '@/lib/api';
import type { MailListResponse, MailReplyRequest, MailReplyResponse } from '@/types/api';

/** Дефолтный размер батча ленты (04-api.md: limit 1..200, default 50). */
export const MAIL_PAGE_LIMIT = 50;

/**
 * GET /api/mail/messages — лента писем (read-through, keyset вперёд по `id`).
 * `sinceId` не задан (или 0) → первый батч от начала окна; иначе письма с `id > sinceId`.
 */
export function listMail(
  sinceId?: number,
  limit: number = MAIL_PAGE_LIMIT,
  signal?: AbortSignal,
): Promise<MailListResponse> {
  const params = new URLSearchParams();
  if (sinceId !== undefined && sinceId > 0) params.set('since_id', String(sinceId));
  params.set('limit', String(limit));
  return apiRequest<MailListResponse>(`/mail/messages?${params.toString()}`, { signal });
}

/** POST /api/mail/messages/{id}/reply — ответ на письмо (проксируется во внешний сервис). */
export function replyMail(id: number, payload: MailReplyRequest): Promise<MailReplyResponse> {
  return apiRequest<MailReplyResponse>(`/mail/messages/${id}/reply`, {
    method: 'POST',
    body: payload,
  });
}
