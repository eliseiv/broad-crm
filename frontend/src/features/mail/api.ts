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
  MailOauthAuthorizeRequest,
  MailOauthAuthorizeResponse,
  MailReplyRequest,
  MailReplyResponse,
  MailTagApplyResponse,
  MailTagCreateRequest,
  MailTagFull,
  MailTagRule,
  MailTagRuleCreateRequest,
  MailTagsResponse,
  MailTagUpdateRequest,
  MailTelegramAuthResponse,
  MailUserSettings,
  MailUserSettingsUpdateRequest,
  TeamMailboxesResponse,
} from '@/types/api';

/** Размер батча ленты страницы «Почты» (ADR-044 §2: limit 1..200; страница шлёт 20). */
export const MAIL_PAGE_LIMIT = 20;

export interface ListMailParams {
  /** Opaque keyset-курсор следующей (более старой) страницы (`next_cursor` прошлой). */
  before?: string;
  limit?: number;
  /** Серверный фильтр по почтовому ящику (`mail_accounts.id`). Комбинируем с `teamId` (AND). */
  mailAccountId?: number;
  /** Серверный фильтр по команде (UUID CRM-команды). Комбинируем с `mailAccountId` (AND). */
  teamId?: string;
  /**
   * Серверный фильтр «только письма ящиков БЕЗ команды» (`no_team=true`, ADR-055 §5.3):
   * «Без команды» — легитимное значение фильтра, а `team_id` (UUID) его выразить не может.
   * **Взаимоисключающ с `teamId`** (оба → `400 validation_error`) — контролы шлют одно из двух
   * (см. `teamFilterParams`). `false`/не задан → параметр НЕ отправляется.
   */
  noTeam?: boolean;
  /**
   * Серверный фильтр «только непрочитанные текущим принципалом» (ADR-050 §2.4, 04-api.md).
   * `true` → `unread=true` в запросе; `false`/не задан → параметр НЕ отправляется (фильтр не
   * применяется; `false` ≠ «только прочитанные» — такого режима в контракте нет).
   * Клиентская фильтрация непрочитанных ЗАПРЕЩЕНА — лента курсорная (сломала бы догрузку).
   */
  unread?: boolean;
}

/**
 * GET /api/mail/messages — лента писем из БД CRM (компаундный keyset, ADR-044 §2/§7).
 * Порядок `internal_date DESC, id DESC`. Без `before` → новейшие `limit`; с `before` →
 * более старые. Фильтры `mail_account_id`/`team_id` AND-комбинируемы; для не-админа
 * пересекаются со `MailScope.team_ids` (вне scope → пустая страница, анти-энумерация).
 * `authToken` — явный Bearer (Mini App использует изолированный SSO-JWT). `skipAuthReset`
 * — для Mini App: на 401 не ронять админ-стор (истёкший SSO-JWT обрабатывается локально).
 */
export function listMail(
  params: ListMailParams = {},
  signal?: AbortSignal,
  authToken?: string,
  skipAuthReset?: boolean,
): Promise<MailListResponse> {
  const { before, limit = MAIL_PAGE_LIMIT, mailAccountId, teamId, noTeam, unread } = params;
  const qs = new URLSearchParams();
  qs.set('limit', String(limit));
  if (before !== undefined) qs.set('before', before);
  if (mailAccountId !== undefined) qs.set('mail_account_id', String(mailAccountId));
  // `team_id` и `no_team` взаимоисключающи (04-api.md; оба → 400): «Без команды» побеждает,
  // а `team_id` в этом случае не отправляется вовсе.
  if (noTeam) qs.set('no_team', 'true');
  else if (teamId !== undefined) qs.set('team_id', teamId);
  if (unread) qs.set('unread', 'true');
  return apiRequest<MailListResponse>(`/mail/messages?${qs.toString()}`, {
    signal,
    authToken,
    skipAuthReset,
  });
}

/**
 * POST /api/mail/messages/{id}/read → 204 (ADR-050 §2.2, 04-api.md). Помечает письмо
 * прочитанным ТЕКУЩИМ пользователем (личная прочитанность). Идемпотентен. Гейт — `mail:view`;
 * супер-админ из `.env` → 403 (у него нет строки в `users`) — UI его контролы не рендерит.
 * `authToken`/`skipAuthReset` — для Mini App `/tg/mail` (тот же эндпоинт под изолированным
 * SSO-JWT: спец-эндпоинта нет и не нужно, ADR-050 §2.6).
 */
export function markMailRead(
  id: number,
  authToken?: string,
  skipAuthReset?: boolean,
): Promise<void> {
  return apiRequest<void>(`/mail/messages/${id}/read`, {
    method: 'POST',
    authToken,
    skipAuthReset,
  });
}

/**
 * DELETE /api/mail/messages/{id}/read → 204 (ADR-050 §2.7). Возвращает письмо в
 * «непрочитано» для текущего пользователя. Идемпотентен. Вызывается кнопкой «Отметить
 * непрочитанным» в шапке детали письма (веб `/mail`); в Mini App этой кнопки НЕТ.
 */
export function unmarkMailRead(id: number): Promise<void> {
  return apiRequest<void>(`/mail/messages/${id}/read`, { method: 'DELETE' });
}

export interface ListMailboxesParams {
  /** Фильтр активности: `true` — активные, `false` — неактивные, не задан — все (ADR-044 §4). */
  isActive?: boolean;
}

/** GET /api/mail/mailboxes — каталог ящиков из БД CRM (фильтруется MailScope по `team_id`). */
export function listMailboxes(
  params: ListMailboxesParams = {},
  signal?: AbortSignal,
): Promise<MailMailboxesResponse> {
  const { isActive } = params;
  const qs = new URLSearchParams();
  if (isActive !== undefined) qs.set('is_active', String(isActive));
  const suffix = qs.toString();
  return apiRequest<MailMailboxesResponse>(`/mail/mailboxes${suffix ? `?${suffix}` : ''}`, {
    signal,
  });
}

/** POST /api/mail/messages/{id}/reply — ответ на письмо (SMTP-отправка транзитом в агрегатор). */
export function replyMail(id: number, payload: MailReplyRequest): Promise<MailReplyResponse> {
  return apiRequest<MailReplyResponse>(`/mail/messages/${id}/reply`, {
    method: 'POST',
    body: payload,
  });
}

// --- Запись: почтовые ящики (гейты mail:create/edit/delete/sync, ADR-044 §4) ---

/**
 * POST /api/mail/mailboxes/test — проверка IMAP/SMTP-соединения без сохранения.
 * `signal` — ПОЛЬЗОВАТЕЛЬСКИЙ abort (закрытие формы во время долгой проверки, ADR-053 §4).
 * Клиентского ТАЙМАУТА нет и вводить нельзя: проверка легально идёт десятки секунд.
 */
export function testMailbox(
  payload: MailMailboxTestRequest,
  signal?: AbortSignal,
): Promise<MailMailboxTestResponse> {
  return apiRequest<MailMailboxTestResponse>('/mail/mailboxes/test', {
    method: 'POST',
    body: payload,
    signal,
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

/**
 * POST /api/mail/mailboxes/oauth/authorize — инициировать OAuth-подключение Outlook-ящика
 * (ADR-045 §3). CRM формирует непрозрачный `crm_state` и получает от агрегатора Microsoft
 * authorize URL. `teamId` — UUID команды-владельца (`null` — без команды, только admin).
 * → 200 { authorize_url }. 503 mail_not_configured — Outlook-OAuth выключен (кнопку скрыть).
 */
export function mailboxOAuthAuthorize(teamId: string | null): Promise<MailOauthAuthorizeResponse> {
  const body: MailOauthAuthorizeRequest = { team_id: teamId };
  return apiRequest<MailOauthAuthorizeResponse>('/mail/mailboxes/oauth/authorize', {
    method: 'POST',
    body,
  });
}

// --- Запись: теги (глобальный каталог, гейт mail:tags; `id` — UUID) ---

/** GET /api/mail/tags — список глобальных тегов с правилами. */
export function listTags(signal?: AbortSignal): Promise<MailTagsResponse> {
  return apiRequest<MailTagsResponse>('/mail/tags', { signal });
}

/** POST /api/mail/tags — создание тега → 201 MailTagFull (без правил). */
export function createTag(payload: MailTagCreateRequest): Promise<MailTagFull> {
  return apiRequest<MailTagFull>('/mail/tags', { method: 'POST', body: payload });
}

/** PATCH /api/mail/tags/{id} — правка тега (имя/цвет/match_mode) → 200 MailTagFull. */
export function updateTag(id: string, payload: MailTagUpdateRequest): Promise<MailTagFull> {
  return apiRequest<MailTagFull>(`/mail/tags/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/mail/tags/{id} → 204 (встроенный тег → 409 mail_conflict). */
export function deleteTag(id: string): Promise<void> {
  return apiRequest<void>(`/mail/tags/${id}`, { method: 'DELETE' });
}

/** POST /api/mail/tags/{id}/rules — добавление правила → 201 MailTagRule. */
export function createTagRule(
  tagId: string,
  payload: MailTagRuleCreateRequest,
): Promise<MailTagRule> {
  return apiRequest<MailTagRule>(`/mail/tags/${tagId}/rules`, { method: 'POST', body: payload });
}

/** DELETE /api/mail/tags/{id}/rules/{rule_id} → 204. */
export function deleteTagRule(tagId: string, ruleId: string): Promise<void> {
  return apiRequest<void>(`/mail/tags/${tagId}/rules/${ruleId}`, { method: 'DELETE' });
}

/** POST /api/mail/tags/{id}/apply-to-existing — применить правила ко всем письмам → 200. */
export function applyTagToExisting(tagId: string): Promise<MailTagApplyResponse> {
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

// --- Персональные настройки уведомлений (opt-out, гейт mail:view, ADR-044 §2) ---

/** GET /api/mail/me/settings — текущее состояние opt-out Telegram-уведомлений. */
export function getMailSettings(signal?: AbortSignal): Promise<MailUserSettings> {
  return apiRequest<MailUserSettings>('/mail/me/settings', { signal });
}

/** PATCH /api/mail/me/settings — включить/выключить уведомления → 200 MailUserSettings. */
export function updateMailSettings(
  payload: MailUserSettingsUpdateRequest,
): Promise<MailUserSettings> {
  return apiRequest<MailUserSettings>('/mail/me/settings', { method: 'PATCH', body: payload });
}

// --- Mini App SSO (`/tg/mail`, ADR-044 §7) ---

/**
 * POST /api/mail/telegram/auth — беспарольный Telegram-SSO Mini App почты. Публичный
 * (гейт — HMAC `init_data`), поэтому `skipAuth: true` (без Bearer, без сброса сессии
 * админ-стора на 401). При успехе отдаёт CRM access-JWT; Mini App держит его изолированно.
 */
export function mailTelegramAuth(
  initData: string,
  signal?: AbortSignal,
): Promise<MailTelegramAuthResponse> {
  return apiRequest<MailTelegramAuthResponse>('/mail/telegram/auth', {
    method: 'POST',
    body: { init_data: initData },
    skipAuth: true,
    signal,
  });
}
