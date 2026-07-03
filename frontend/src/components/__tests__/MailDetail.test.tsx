import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MailDetail } from '@/components/MailDetail';
import type { MailMessage } from '@/types/api';

// MailDetail рендерит MailReplyForm → useReplyMail; мокаем как no-op мутацию.
vi.mock('@/features/mail/hooks', () => ({
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeMessage(overrides: Partial<MailMessage> = {}): MailMessage {
  return {
    id: 1042,
    subject: 'Отчёт за июнь',
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван Петров',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: { id: 3, email: 'inbox@postapp.store', display_name: 'Входящие' },
    body_text: 'Текст письма',
    body_html: null,
    body_present: true,
    body_truncated: false,
    tags: [{ id: 7, name: 'важное', color: '#EF4444' }],
    ...overrides,
  };
}

describe('MailDetail body isolation & notices', () => {
  it('renders body_html only inside a strict sandbox iframe (no scripts/same-origin)', () => {
    const html = '<p>Привет</p><script>window.__pwned = true;</script>';
    render(<MailDetail message={makeMessage({ body_html: html })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    expect(iframe.getAttribute('sandbox')).toBe('');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(iframe.getAttribute('srcdoc')).toContain('<p>Привет</p>');
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it('renders body_text in a <pre> and no iframe when body_html is null', () => {
    render(<MailDetail message={makeMessage({ body_html: null })} onBack={vi.fn()} />);

    expect(screen.getByText('Текст письма').tagName).toBe('PRE');
    expect(screen.queryByTitle('Тело письма')).not.toBeInTheDocument();
  });

  it('shows a notice when body is truncated', () => {
    render(<MailDetail message={makeMessage({ body_truncated: true })} onBack={vi.fn()} />);

    expect(screen.getByText('Письмо показано не полностью')).toBeInTheDocument();
  });

  it('shows an unavailable notice when body is not present', () => {
    render(
      <MailDetail
        message={makeMessage({ body_present: false, body_html: null })}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText('Тело письма недоступно')).toBeInTheDocument();
  });

  it('falls back to "(без темы)" in the heading when subject is null', () => {
    render(<MailDetail message={makeMessage({ subject: null })} onBack={vi.fn()} />);

    expect(screen.getByRole('heading', { name: '(без темы)' })).toBeInTheDocument();
  });

  it('invokes onBack when the adaptive "Назад" button is clicked', async () => {
    const onBack = vi.fn();
    render(<MailDetail message={makeMessage()} onBack={onBack} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Назад' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
