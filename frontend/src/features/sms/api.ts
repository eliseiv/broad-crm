import { apiRequest } from '@/lib/api';
import type {
  SmsMessagesResponse,
  SmsNumber,
  SmsNumbersResponse,
  SmsNumberTransferRequest,
  SmsNumberUpdateRequest,
  SmsSyncResult,
  TeamNumbersResponse,
} from '@/types/api';

/** Размер страницы ленты SMS (04-api.md: limit 1..100, default 50). */
export const SMS_PAGE_LIMIT = 50;

export interface ListSmsMessagesParams {
  /** Фильтр по номеру (`sms_phone_numbers.id`). Комбинируем с `teamId` (AND). */
  numberId?: number;
  /** Фильтр по команде (uuid) — по текущей принадлежности номера. Комбинируем с `numberId`. */
  teamId?: string;
  /** Opaque keyset-курсор следующей (более старой) страницы. */
  cursor?: string;
  limit?: number;
}

/**
 * GET /api/sms/messages — лента входящих SMS (newest-first, keyset-курсор).
 * Фильтры `number_id`/`team_id` комбинируемы (AND); оба опциональны. Курсор `cursor`
 * (next_cursor прошлой страницы) → более старая страница. 04-api.md#sms.
 */
export function listSmsMessages(
  params: ListSmsMessagesParams = {},
  signal?: AbortSignal,
): Promise<SmsMessagesResponse> {
  const { numberId, teamId, cursor, limit = SMS_PAGE_LIMIT } = params;
  const qs = new URLSearchParams();
  if (numberId !== undefined) qs.set('number_id', String(numberId));
  if (teamId !== undefined) qs.set('team_id', teamId);
  if (cursor !== undefined) qs.set('cursor', cursor);
  qs.set('limit', String(limit));
  return apiRequest<SmsMessagesResponse>(`/sms/messages?${qs.toString()}`, { signal });
}

/** GET /api/sms/numbers — список номеров (без пагинации; клиентский поиск). */
export function listSmsNumbers(signal?: AbortSignal): Promise<SmsNumbersResponse> {
  return apiRequest<SmsNumbersResponse>('/sms/numbers', { signal });
}

/** PATCH /api/sms/numbers/{id} — правка login/app_name/note (presence-семантика). */
export function updateSmsNumber(id: number, payload: SmsNumberUpdateRequest): Promise<SmsNumber> {
  return apiRequest<SmsNumber>(`/sms/numbers/${id}`, { method: 'PATCH', body: payload });
}

/** POST /api/sms/numbers/{id}/transfer — назначить/снять команду (team_id=null → снять). */
export function transferSmsNumber(
  id: number,
  payload: SmsNumberTransferRequest,
): Promise<SmsNumber> {
  return apiRequest<SmsNumber>(`/sms/numbers/${id}/transfer`, { method: 'POST', body: payload });
}

/** DELETE /api/sms/numbers/{id} → 204. История SMS сохраняется (sms_inbound не трогается). */
export function deleteSmsNumber(id: number): Promise<void> {
  return apiRequest<void>(`/sms/numbers/${id}`, { method: 'DELETE' });
}

/** POST /api/sms/numbers/sync — синхронизация входящих номеров из Twilio (тело пустое). */
export function syncSmsNumbers(): Promise<SmsSyncResult> {
  return apiRequest<SmsSyncResult>('/sms/numbers/sync', { method: 'POST', body: {} });
}

/** GET /api/teams/{id}/numbers — номера команды (для detail-панели /teams, ленивая загрузка). */
export function listTeamNumbers(
  teamId: string,
  signal?: AbortSignal,
): Promise<TeamNumbersResponse> {
  return apiRequest<TeamNumbersResponse>(`/teams/${teamId}/numbers`, { signal });
}
