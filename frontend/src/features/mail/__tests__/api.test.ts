import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listMail, MAIL_PAGE_LIMIT, replyMail } from '@/features/mail/api';

const apiMock = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock('@/lib/api', () => apiMock);

describe('mail api client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.apiRequest.mockResolvedValue({ messages: [], next_since_id: null, has_more: false });
  });

  it('omits since_id on the first page and uses the default limit', async () => {
    await listMail();
    const path = apiMock.apiRequest.mock.calls[0][0] as string;
    expect(path).not.toContain('since_id');
    expect(path).toContain(`limit=${MAIL_PAGE_LIMIT}`);
  });

  it('omits since_id when it is 0 (keyset from start of window)', async () => {
    await listMail(0);
    expect(apiMock.apiRequest.mock.calls[0][0]).not.toContain('since_id');
  });

  it('sends since_id and limit for forward pagination', async () => {
    await listMail(1042, 25);
    const path = apiMock.apiRequest.mock.calls[0][0] as string;
    expect(path).toContain('since_id=1042');
    expect(path).toContain('limit=25');
  });

  it('POSTs the reply payload to the message reply endpoint', async () => {
    apiMock.apiRequest.mockResolvedValueOnce({ sent_id: 1, smtp_message_id: '<x@postapp.store>' });
    await replyMail(42, { body: 'ответ' });

    const [path, options] = apiMock.apiRequest.mock.calls[0];
    expect(path).toBe('/mail/messages/42/reply');
    expect(options).toMatchObject({ method: 'POST', body: { body: 'ответ' } });
  });
});
