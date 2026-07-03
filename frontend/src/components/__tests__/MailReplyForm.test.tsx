import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailReplyForm } from '@/components/MailReplyForm';
import { ApiError } from '@/lib/api';
import type { MailMessage } from '@/types/api';

const mutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/mail/hooks', () => ({
  useReplyMail: () => mutation,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeMessage(overrides: Partial<MailMessage> = {}): MailMessage {
  return {
    id: 1042,
    subject: 'Отчёт',
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: { id: 3, email: 'inbox@postapp.store', display_name: 'Входящие' },
    body_text: 'тело',
    body_html: null,
    body_present: true,
    body_truncated: false,
    tags: [],
    ...overrides,
  };
}

describe('MailReplyForm (inline reply, ADR-013)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutation.isPending = false;
  });

  it('disables the send button while body is empty', () => {
    render(<MailReplyForm message={makeMessage()} />);

    expect(screen.getByRole('button', { name: 'Ответить' })).toBeDisabled();
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it('sends the reply, shows a success toast and clears the body', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<MailReplyForm message={makeMessage()} />);

    const body = screen.getByLabelText('Сообщение');
    await user.type(body, 'Спасибо, получил.');
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    // Payload — строго { body } (ADR-013 поправка: to/cc/subject больше не передаются).
    expect(mutation.mutate).toHaveBeenCalledWith({ body: 'Спасибо, получил.' }, expect.any(Object));
    expect(toast.success).toHaveBeenCalledWith('Ответ отправлен');
    // Поле очищено после успешной отправки.
    expect(screen.getByLabelText('Сообщение')).toHaveValue('');
  });

  it('maps a 404 to the "Письмо не найдено" toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(404, 'mail_message_not_found', 'not found')),
    );
    render(<MailReplyForm message={makeMessage()} />);

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    expect(toast.error).toHaveBeenCalledWith('Письмо не найдено');
  });

  it('highlights the body field on a 422 validation error without a toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'unprocessable', 'bad body')),
    );
    render(<MailReplyForm message={makeMessage()} />);

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    expect(screen.getByLabelText('Сообщение')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Введите текст сообщения')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('maps a 502 to the "Почтовый сервис временно недоступен" toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(502, 'mail_unavailable', 'unavailable')),
    );
    render(<MailReplyForm message={makeMessage()} />);

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    expect(toast.error).toHaveBeenCalledWith('Почтовый сервис временно недоступен');
  });

  it('maps a 400 to the body validation error without a toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(400, 'bad_request', 'bad body')),
    );
    render(<MailReplyForm message={makeMessage()} />);

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    expect(screen.getByLabelText('Сообщение')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Введите текст сообщения')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('keeps the send button disabled for a whitespace-only body (body is required)', async () => {
    const user = userEvent.setup();
    render(<MailReplyForm message={makeMessage()} />);

    await user.type(screen.getByLabelText('Сообщение'), '   ');

    expect(screen.getByRole('button', { name: 'Ответить' })).toBeDisabled();
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it('renders only a Textarea and the "Ответить" button (no advanced to/cc/subject fields)', () => {
    render(<MailReplyForm message={makeMessage()} />);

    expect(screen.getByLabelText('Сообщение')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ответить' })).toBeInTheDocument();
    // Блок «Расширенно» и поля to/cc/subject удалены (ADR-013 поправка).
    expect(screen.queryByRole('button', { name: /Расширенно/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Кому')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Копия')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Тема')).not.toBeInTheDocument();
  });
});
