import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listMail, MAIL_PAGE_LIMIT, replyMail } from '@/features/mail/api';

const apiMock = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock('@/lib/api', () => apiMock);

/** Разбирает query-строку первого вызова apiRequest в объект пар. */
function firstCallQuery(): URLSearchParams {
  const path = apiMock.apiRequest.mock.calls[0][0] as string;
  return new URLSearchParams(path.split('?')[1] ?? '');
}

/** Опции второго аргумента первого вызова apiRequest. */
function firstCallOptions(): Record<string, unknown> {
  return apiMock.apiRequest.mock.calls[0][1] as Record<string, unknown>;
}

describe('mail api client (ADR-044 compound keyset cursor)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.apiRequest.mockResolvedValue({ messages: [], next_cursor: null });
  });

  it('defaults to limit=20 with no cursor/filters on the first page', async () => {
    await listMail();
    const qs = firstCallQuery();
    expect(qs.get('limit')).toBe(String(MAIL_PAGE_LIMIT));
    expect(qs.get('limit')).toBe('20');
    expect(qs.has('before')).toBe(false);
    expect(qs.has('mail_account_id')).toBe(false);
    expect(qs.has('team_id')).toBe(false);
  });

  it('forwards the opaque `before` cursor for the older page', async () => {
    // Курсор — opaque-токен пары (internal_date, id); клиент шлёт его обратно как есть.
    await listMail({ before: 'MjAyNi0wNy0wMlQwOToxNTowMFp8MTA0Mg', limit: 20 });
    const qs = firstCallQuery();
    expect(qs.get('before')).toBe('MjAyNi0wNy0wMlQwOToxNTowMFp8MTA0Mg');
    expect(qs.get('limit')).toBe('20');
  });

  it('honours a custom limit', async () => {
    await listMail({ limit: 50 });
    expect(firstCallQuery().get('limit')).toBe('50');
  });

  it('forwards mail_account_id and team_id together (AND-combinable, ADR-044 §7)', async () => {
    await listMail({ mailAccountId: 7, teamId: 'team-3' });
    const qs = firstCallQuery();
    expect(qs.get('mail_account_id')).toBe('7');
    expect(qs.get('team_id')).toBe('team-3');
  });

  it('forwards team_id filter when only teamId is passed', async () => {
    await listMail({ teamId: 'team-3' });
    const qs = firstCallQuery();
    expect(qs.get('team_id')).toBe('team-3');
    expect(qs.has('mail_account_id')).toBe(false);
  });

  it('omits both server filters when neither is passed', async () => {
    await listMail();
    const qs = firstCallQuery();
    expect(qs.has('mail_account_id')).toBe(false);
    expect(qs.has('team_id')).toBe(false);
  });

  it('forwards the abort signal to apiRequest', async () => {
    const controller = new AbortController();
    await listMail({}, controller.signal);
    expect(firstCallOptions().signal).toBe(controller.signal);
  });

  it('passes an explicit auth token and skipAuthReset through to apiRequest (Mini App SSO)', async () => {
    // Mini App почты: изолированный SSO-JWT + skipAuthReset=true, чтобы 401 НЕ ронял
    // админ-стор `crm.auth.*` (ADR-044 §7).
    await listMail({ before: 'cur' }, undefined, 'sso-jwt', true);
    const options = firstCallOptions();
    expect(options.authToken).toBe('sso-jwt');
    expect(options.skipAuthReset).toBe(true);
  });

  it('does not set skipAuthReset for the ordinary admin feed request', async () => {
    await listMail();
    expect(firstCallOptions().skipAuthReset).toBeUndefined();
  });

  it('POSTs the reply payload to the message reply endpoint', async () => {
    apiMock.apiRequest.mockResolvedValueOnce({ sent_id: 1, smtp_message_id: '<x@postapp.store>' });
    await replyMail(42, { body: 'ответ' });

    const [path, options] = apiMock.apiRequest.mock.calls[0];
    expect(path).toBe('/mail/messages/42/reply');
    expect(options).toMatchObject({ method: 'POST', body: { body: 'ответ' } });
  });
});
