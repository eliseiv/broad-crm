import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReplyModal } from '@/components/ReplyModal';
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

function renderModal() {
  return render(<ReplyModal message={makeMessage()} open onOpenChange={vi.fn()} />);
}

describe('ReplyModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutation.isPending = false;
  });

  it('blocks submit and shows an error when body is empty', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(mutation.mutate).not.toHaveBeenCalled();
    expect(screen.getByText('Введите текст сообщения')).toBeInTheDocument();
  });

  it('sends the reply and shows a success toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    renderModal();

    await user.type(screen.getByLabelText('Сообщение'), 'Спасибо, получил.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(mutation.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ body: 'Спасибо, получил.' }),
      expect.any(Object),
    );
    expect(toast.success).toHaveBeenCalledWith('Ответ отправлен');
  });

  it('maps a 404 to "Письмо не найдено" toast', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(404, 'mail_message_not_found', 'not found')),
    );
    renderModal();

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(toast.error).toHaveBeenCalledWith('Письмо не найдено');
  });

  it('highlights the body field on a 422 validation error', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'unprocessable', 'bad body')),
    );
    renderModal();

    await user.type(screen.getByLabelText('Сообщение'), 'ответ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    // Поле подсвечено ошибкой (aria-invalid) + сообщение под полем.
    expect(screen.getByLabelText('Сообщение')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Введите текст сообщения')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Проверьте текст сообщения');
  });
});
