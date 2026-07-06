import { apiRequest } from '@/lib/api';
import type {
  MailListResponse,
  MailMailboxesResponse,
  MailOrder,
  MailReplyRequest,
  MailReplyResponse,
  MailTeamsResponse,
} from '@/types/api';

/** Размер батча ленты страницы «Почты» (04-api.md: limit 1..200; страница шлёт 20). */
export const MAIL_PAGE_LIMIT = 20;

export interface ListMailParams {
  /** Режим пагинации; страница всегда шлёт `desc` явно (04-api.md, ADR-013). */
  order?: MailOrder;
  /** desc-режим: догрузка более старых — письма с `id < before_id`. Только при order=desc. */
  beforeId?: number;
  /** asc-режим (совместимость): письма с `id > since_id`. Только при order=asc. */
  sinceId?: number;
  limit?: number;
  /**
   * Серверный фильтр по почтовому ящику (external ADR-0037, ADR-017).
   * Взаимоисключающ с `groupId` — вызывающий гарантирует, что задан только один.
   */
  mailAccountId?: number;
  /** Серверный фильтр по команде (`groups`). Взаимоисключающ с `mailAccountId`. */
  groupId?: number;
}

/**
 * GET /api/mail/messages — лента писем (read-through-прокси). Основной режим страницы —
 * `desc` (newest-first): без `before_id` → новейшие `limit`; с `before_id` → более старые.
 * CRM всегда передаёт `order` явно (04-api.md). Взаимоисключение курсоров и режимов
 * гарантируется тем, что `before_id` шлётся только при desc, `since_id` — только при asc.
 * Серверные фильтры `mail_account_id`/`group_id` (взаимоисключающи) пробрасываются как есть;
 * вызывающий передаёт максимум один из них (в UI выбор одного сбрасывает другой).
 */
export function listMail(
  params: ListMailParams = {},
  signal?: AbortSignal,
): Promise<MailListResponse> {
  const {
    order = 'desc',
    beforeId,
    sinceId,
    limit = MAIL_PAGE_LIMIT,
    mailAccountId,
    groupId,
  } = params;
  const qs = new URLSearchParams();
  qs.set('order', order);
  if (order === 'desc' && beforeId !== undefined) qs.set('before_id', String(beforeId));
  if (order === 'asc' && sinceId !== undefined) qs.set('since_id', String(sinceId));
  qs.set('limit', String(limit));
  if (mailAccountId !== undefined) qs.set('mail_account_id', String(mailAccountId));
  else if (groupId !== undefined) qs.set('group_id', String(groupId));
  return apiRequest<MailListResponse>(`/mail/messages?${qs.toString()}`, { signal });
}

/** GET /api/mail/teams — список команд (read-through-прокси, без параметров). */
export function listTeams(signal?: AbortSignal): Promise<MailTeamsResponse> {
  return apiRequest<MailTeamsResponse>('/mail/teams', { signal });
}

/** GET /api/mail/mailboxes — список почтовых ящиков (read-through-прокси, без параметров). */
export function listMailboxes(signal?: AbortSignal): Promise<MailMailboxesResponse> {
  return apiRequest<MailMailboxesResponse>('/mail/mailboxes', { signal });
}

/** POST /api/mail/messages/{id}/reply — ответ на письмо (проксируется во внешний сервис). */
export function replyMail(id: number, payload: MailReplyRequest): Promise<MailReplyResponse> {
  return apiRequest<MailReplyResponse>(`/mail/messages/${id}/reply`, {
    method: 'POST',
    body: payload,
  });
}
