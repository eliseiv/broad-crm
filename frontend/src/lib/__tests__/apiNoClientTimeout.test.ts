import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest } from '@/lib/api';
import { testMailbox } from '@/features/mail/api';
import { useAuthStore } from '@/store/auth';

/**
 * ADR-053 §4 (регресс исходного бага): клиентского таймаута у SPA НЕТ и вводить его нельзя.
 * `apiRequest` принимает только ВНЕШНИЙ `signal` (пользовательский abort) — собственный
 * таймаут короче серверного бюджета (до 105 с) вернул бы исходный баг на уровень браузера:
 * легальный долгий ответ обрывался бы клиентом.
 */
describe('apiRequest — нет клиентского таймаута (ADR-053 §4)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.getState().clearSession();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('без внешнего signal запрос уходит БЕЗ AbortSignal (таймаут не навешивается)', async () => {
    const fetchMock = vi.fn((_url: string, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ ok: true }))),
    );
    vi.stubGlobal('fetch', fetchMock);

    await apiRequest('/mail/mailboxes/test', { method: 'POST', body: {} });

    expect(fetchMock.mock.calls[0][1]?.signal).toBeUndefined();
  });

  it('ответ через 105 с (худший бюджет ЗАПРОСА) доводится до конца, а не обрывается', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          // Легальный долгий ответ backend: create с компенсацией = 85 + 15 + 5 = 105 с.
          setTimeout(() => resolve(new Response(JSON.stringify({ imap_ok: true }))), 105_000);
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const pending = apiRequest<{ imap_ok: boolean }>('/mail/mailboxes/test', {
      method: 'POST',
      body: {},
    });
    await vi.advanceTimersByTimeAsync(105_000);

    await expect(pending).resolves.toEqual({ imap_ok: true });
  });

  it('testMailbox пробрасывает ПОЛЬЗОВАТЕЛЬСКИЙ signal (закрытие формы обрывает проверку)', async () => {
    const fetchMock = vi.fn((_url: string, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({ imap_ok: true }))),
    );
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    await testMailbox(
      {
        email: 'box@example.com',
        imap_host: 'imap.example.com',
        imap_port: 993,
        imap_ssl: true,
        smtp_host: 'smtp.example.com',
        smtp_port: 465,
        smtp_ssl: true,
        smtp_starttls: false,
        smtp_username: null,
        password: 'pass',
        smtp_password: null,
      },
      controller.signal,
    );

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });
});
