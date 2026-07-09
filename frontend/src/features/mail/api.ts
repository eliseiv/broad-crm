import { apiRequest } from '@/lib/api';
import type {
  MailListResponse,
  MailMailbox,
  MailMailboxCreateRequest,
  MailMailboxesResponse,
  MailMailboxSyncResponse,
  MailMailboxTestRequest,
  MailMailboxTestResponse,
  MailMailboxUpdateRequest,
  MailOrder,
  MailReplyRequest,
  MailReplyResponse,
  MailTagApplyResponse,
  MailTagCreateRequest,
  MailTagFull,
  MailTagRule,
  MailTagRuleCreateRequest,
  MailTagsResponse,
  MailTagUpdateRequest,
  MailTeamsResponse,
  TeamMailboxesResponse,
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
   * Серверный фильтр по почтовому ящику (external ADR-0039, ADR-038).
   * **Комбинируем с `groupId`** (AND) — взаимоисключение снято.
   */
  mailAccountId?: number;
  /** Серверный фильтр по команде (`groups`). **Комбинируем с `mailAccountId`** (AND). */
  groupId?: number;
}

/**
 * GET /api/mail/messages — лента писем (read-through-прокси). Основной режим страницы —
 * `desc` (newest-first): без `before_id` → новейшие `limit`; с `before_id` → более старые.
 * CRM всегда передаёт `order` явно (04-api.md). Взаимоисключение курсоров и режимов
 * гарантируется тем, что `before_id` шлётся только при desc, `since_id` — только при asc.
 * Серверные фильтры `mail_account_id`/`group_id` — **комбинируемы (AND, ADR-038 §3)**:
 * передаются независимо, оба или ни один.
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
  if (groupId !== undefined) qs.set('group_id', String(groupId));
  return apiRequest<MailListResponse>(`/mail/messages?${qs.toString()}`, { signal });
}

/** GET /api/mail/teams — список команд (read-through-прокси, без параметров). */
export function listTeams(signal?: AbortSignal): Promise<MailTeamsResponse> {
  return apiRequest<MailTeamsResponse>('/mail/teams', { signal });
}

export interface ListMailboxesParams {
  /** Фильтр активности: `true` — активные, `false` — неактивные, не задан — все (04-api.md). */
  isActive?: boolean;
  /** Фильтр по команде (`MailTeam.id`); пробрасывается во внешний API (пересечение со scope). */
  groupId?: number;
}

/** GET /api/mail/mailboxes — список почтовых ящиков (read-through-прокси, фильтруется MailScope). */
export function listMailboxes(
  params: ListMailboxesParams = {},
  signal?: AbortSignal,
): Promise<MailMailboxesResponse> {
  const { isActive, groupId } = params;
  const qs = new URLSearchParams();
  if (isActive !== undefined) qs.set('is_active', String(isActive));
  if (groupId !== undefined) qs.set('group_id', String(groupId));
  const suffix = qs.toString();
  return apiRequest<MailMailboxesResponse>(`/mail/mailboxes${suffix ? `?${suffix}` : ''}`, {
    signal,
  });
}

/** POST /api/mail/messages/{id}/reply — ответ на письмо (проксируется во внешний сервис). */
export function replyMail(id: number, payload: MailReplyRequest): Promise<MailReplyResponse> {
  return apiRequest<MailReplyResponse>(`/mail/messages/${id}/reply`, {
    method: 'POST',
    body: payload,
  });
}

// --- Запись: почтовые ящики (гейты mail:create/edit/delete/sync, ADR-038 §4) ---

/** POST /api/mail/mailboxes/test — проверка IMAP/SMTP-соединения без сохранения. */
export function testMailbox(payload: MailMailboxTestRequest): Promise<MailMailboxTestResponse> {
  return apiRequest<MailMailboxTestResponse>('/mail/mailboxes/test', {
    method: 'POST',
    body: payload,
  });
}

/** POST /api/mail/mailboxes — создание ящика → 201 MailMailbox (пароль не возвращается). */
export function createMailbox(payload: MailMailboxCreateRequest): Promise<MailMailbox> {
  return apiRequest<MailMailbox>('/mail/mailboxes', { method: 'POST', body: payload });
}

/** PATCH /api/mail/mailboxes/{id} — правка ящика (presence-семантика) → 200 MailMailbox. */
export function updateMailbox(id: number, payload: MailMailboxUpdateRequest): Promise<MailMailbox> {
  return apiRequest<MailMailbox>(`/mail/mailboxes/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/mail/mailboxes/{id} → 204. */
export function deleteMailbox(id: number): Promise<void> {
  return apiRequest<void>(`/mail/mailboxes/${id}`, { method: 'DELETE' });
}

/** POST /api/mail/mailboxes/{id}/sync — форс-синк → 202 { queued }. */
export function syncMailbox(id: number): Promise<MailMailboxSyncResponse> {
  return apiRequest<MailMailboxSyncResponse>(`/mail/mailboxes/${id}/sync`, {
    method: 'POST',
    body: {},
  });
}

// --- Запись: теги (глобальный каталог, гейт mail:tags) ---

/** GET /api/mail/tags — список глобальных тегов с правилами. */
export function listTags(signal?: AbortSignal): Promise<MailTagsResponse> {
  return apiRequest<MailTagsResponse>('/mail/tags', { signal });
}

/** POST /api/mail/tags — создание тега → 201 MailTagFull (без правил). */
export function createTag(payload: MailTagCreateRequest): Promise<MailTagFull> {
  return apiRequest<MailTagFull>('/mail/tags', { method: 'POST', body: payload });
}

/** PATCH /api/mail/tags/{id} — правка тега (имя/цвет/match_mode) → 200 MailTagFull. */
export function updateTag(id: number, payload: MailTagUpdateRequest): Promise<MailTagFull> {
  return apiRequest<MailTagFull>(`/mail/tags/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/mail/tags/{id} → 204 (встроенный тег → 409 mail_conflict). */
export function deleteTag(id: number): Promise<void> {
  return apiRequest<void>(`/mail/tags/${id}`, { method: 'DELETE' });
}

/** POST /api/mail/tags/{id}/rules — добавление правила → 201 MailTagRule. */
export function createTagRule(
  tagId: number,
  payload: MailTagRuleCreateRequest,
): Promise<MailTagRule> {
  return apiRequest<MailTagRule>(`/mail/tags/${tagId}/rules`, { method: 'POST', body: payload });
}

/** DELETE /api/mail/tags/{id}/rules/{rule_id} → 204. */
export function deleteTagRule(tagId: number, ruleId: number): Promise<void> {
  return apiRequest<void>(`/mail/tags/${tagId}/rules/${ruleId}`, { method: 'DELETE' });
}

/** POST /api/mail/tags/{id}/apply-to-existing — применить правила ко всем письмам → 200 { applied_count }. */
export function applyTagToExisting(tagId: number): Promise<MailTagApplyResponse> {
  return apiRequest<MailTagApplyResponse>(`/mail/tags/${tagId}/apply-to-existing`, {
    method: 'POST',
    body: {},
  });
}

/** GET /api/teams/{id}/mailboxes — ящики команды для detail-панели /teams (ленивая загрузка). */
export function listTeamMailboxes(
  teamId: string,
  signal?: AbortSignal,
): Promise<TeamMailboxesResponse> {
  return apiRequest<TeamMailboxesResponse>(`/teams/${teamId}/mailboxes`, { signal });
}
