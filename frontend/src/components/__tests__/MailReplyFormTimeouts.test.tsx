import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailReplyForm } from '@/components/MailReplyForm';
import { ApiError } from '@/lib/api';
import type { MailMessage } from '@/types/api';

/**
 * ADR-053 §2/§4 (TD-059) — отказы отправки reply:
 *  - `502 mail_send_failed` (удалённый SMTP не принял письмо; агрегатор РАБОТАЛ) ≠ «сервис
 *    недоступен»;
 *  - `504 mail_timeout` — свой текст: письмо МОГЛО уйти, автоповтора нет;
 *  - «Почтовый сервис временно недоступен» — ТОЛЬКО за `502 mail_unavailable` (по `error.code`).
 */

const mutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/mail/hooks', () => ({ useReplyMail: () => mutation }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeMessage(): MailMessage {
  return {
    id: 1042,
    subject: 'Отчёт',
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: {
      id: 3,
      email: 'inbox@postapp.store',
      display_name: 'Входящие',
      number: '5108',
      app_name: 'Klyro Forge',
      team: { id: 'team-1', name: 'Команда Ивана' },
    },
    body_text: 'тело',
    body_html: null,
    body_present: true,
    body_truncated: false,
    is_unread: false,
    tags: [],
  };
}

async function submitWithError(err: unknown) {
  const user = userEvent.setup();
  mutation.mutate.mockImplementation((_payload, options) => options.onError(err));
  render(<MailReplyForm message={makeMessage()} />);

  await user.type(screen.getByLabelText('Сообщение'), 'Ответ');
  await user.click(screen.getByRole('button', { name: 'Ответить' }));
}

describe('MailReplyForm — коды ADR-053', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutation.isPending = false;
  });

  it('502 mail_send_failed → SMTP не принял письмо (НЕ «сервис недоступен»)', async () => {
    await submitWithError(new ApiError(502, 'mail_send_failed', 'ignored'));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Почтовый сервер не принял письмо. Проверьте настройки SMTP ящика.',
      ),
    );
    expect(toast.error).not.toHaveBeenCalledWith('Почтовый сервис временно недоступен');
  });

  it('504 mail_timeout → отправка не подтверждена (письмо могло уйти)', async () => {
    await submitWithError(new ApiError(504, 'mail_timeout', 'ignored'));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Отправка не подтверждена: сервис не ответил вовремя. Письмо могло быть отправлено — проверьте перед повтором.',
      ),
    );
  });

  it('502 mail_unavailable → «сервис недоступен» (единственный код с этим текстом)', async () => {
    await submitWithError(new ApiError(502, 'mail_unavailable', 'ignored'));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Почтовый сервис временно недоступен'),
    );
  });

  it('прочий 502 → сообщение backend’а, а не ложное «сервис недоступен»', async () => {
    await submitWithError(new ApiError(502, 'some_future_code', 'Иная причина'));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Иная причина'));
    expect(toast.error).not.toHaveBeenCalledWith('Почтовый сервис временно недоступен');
  });

  it('во время отправки поле и кнопка заблокированы (долгий вызов до 85 с)', () => {
    mutation.isPending = true;
    render(<MailReplyForm message={makeMessage()} />);

    expect(screen.getByLabelText('Сообщение')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Ответить' })).toBeDisabled();
  });
});
