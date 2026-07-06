import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listMail, MAIL_PAGE_LIMIT, replyMail } from '@/features/mail/api';

const apiMock = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock('@/lib/api', () => apiMock);

/** Разбирает query-строку первого вызова apiRequest в объект пар. */
function firstCallQuery(): URLSearchParams {
  const path = apiMock.apiRequest.mock.calls[0][0] as string;
  return new URLSearchParams(path.split('?')[1] ?? '');
}

describe('mail api client (ADR-013 desc/before_id)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.apiRequest.mockResolvedValue({
      messages: [],
      next_since_id: null,
      next_before_id: null,
      has_more: false,
    });
  });

  it('defaults to order=desc with limit=20 and no cursor on the first page', async () => {
    await listMail();
    const qs = firstCallQuery();
    expect(qs.get('order')).toBe('desc');
    expect(qs.get('limit')).toBe(String(MAIL_PAGE_LIMIT));
    expect(qs.get('limit')).toBe('20');
    expect(qs.has('before_id')).toBe(false);
    expect(qs.has('since_id')).toBe(false);
  });

  it('sends before_id only in desc mode (older-page cursor)', async () => {
    await listMail({ order: 'desc', beforeId: 1042, limit: 20 });
    const qs = firstCallQuery();
    expect(qs.get('order')).toBe('desc');
    expect(qs.get('before_id')).toBe('1042');
    expect(qs.has('since_id')).toBe(false);
  });

  it('never sends since_id in desc mode even if sinceId is passed', async () => {
    await listMail({ order: 'desc', sinceId: 500 });
    const qs = firstCallQuery();
    expect(qs.get('order')).toBe('desc');
    expect(qs.has('since_id')).toBe(false);
    expect(qs.has('before_id')).toBe(false);
  });

  it('sends since_id only in asc mode and never before_id', async () => {
    await listMail({ order: 'asc', sinceId: 1042, beforeId: 7, limit: 25 });
    const qs = firstCallQuery();
    expect(qs.get('order')).toBe('asc');
    expect(qs.get('since_id')).toBe('1042');
    expect(qs.get('limit')).toBe('25');
    expect(qs.has('before_id')).toBe(false);
  });

  it('forwards mail_account_id filter and never group_id when both passed (mutual exclusion)', async () => {
    // Вызывающий гарантирует максимум один фильтр; при обоих — приоритет mail_account_id (ADR-017).
    await listMail({ mailAccountId: 7, groupId: 3 });
    const qs = firstCallQuery();
    expect(qs.get('mail_account_id')).toBe('7');
    expect(qs.has('group_id')).toBe(false);
  });

  it('forwards group_id filter when only groupId is passed', async () => {
    await listMail({ groupId: 3 });
    const qs = firstCallQuery();
    expect(qs.get('group_id')).toBe('3');
    expect(qs.has('mail_account_id')).toBe(false);
  });

  it('omits both server filters when neither is passed', async () => {
    await listMail();
    const qs = firstCallQuery();
    expect(qs.has('mail_account_id')).toBe(false);
    expect(qs.has('group_id')).toBe(false);
  });

  it('forwards the abort signal to apiRequest', async () => {
    const controller = new AbortController();
    await listMail({}, controller.signal);
    expect(apiMock.apiRequest.mock.calls[0][1]).toEqual({ signal: controller.signal });
  });

  it('POSTs the reply payload to the message reply endpoint', async () => {
    apiMock.apiRequest.mockResolvedValueOnce({ sent_id: 1, smtp_message_id: '<x@postapp.store>' });
    await replyMail(42, { body: 'ответ' });

    const [path, options] = apiMock.apiRequest.mock.calls[0];
    expect(path).toBe('/mail/messages/42/reply');
    expect(options).toMatchObject({ method: 'POST', body: { body: 'ответ' } });
  });
});
